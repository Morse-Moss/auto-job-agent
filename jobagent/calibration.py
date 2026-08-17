"""Resume-first candidate calibration using only local evidence."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from jobagent.learning import TOPICS
from jobagent.matching import migrate_matching_schema, normalize_text, run_match_preview


CALIBRATION_SCHEMA_VERSION = 4
MAX_RESUME_CHARS = 200_000

SKILL_ALIASES = {
    "agent_workflows": ["Agent", "智能体", "LangChain", "LangGraph", "Dify", "Coze", "n8n", "工作流"],
    "rag_knowledge": ["RAG", "检索增强", "知识库", "向量检索", "pgvector", "Milvus", "Embedding"],
    "python_api": ["Python", "FastAPI", "Flask", "Django", "API"],
    "java_enterprise": ["Java", "Spring Boot", "Spring Cloud", "Nacos", "微服务"],
    "platform_engineering": ["Linux", "Docker", "Kubernetes", "K8s", "Nginx", "CI/CD"],
    "data_messaging": ["MySQL", "PostgreSQL", "Redis", "Kafka", "RabbitMQ", "消息队列"],
    "ecommerce_integration": ["跨境电商", "Amazon", "Shopify", "ERP", "供应链", "订单系统"],
    "delivery_fde": ["FDE", "前线部署", "客户需求", "需求调研", "解决方案", "部署", "交付"],
    "fullstack_web": ["React", "Vue", "TypeScript", "JavaScript", "Next.js", "前端", "全栈"],
    "model_serving": ["vLLM", "SGLang", "CUDA", "模型部署", "推理优化", "量化"],
    "ai_security": ["AI 安全", "大模型安全", "Prompt Injection", "提示词注入", "安全测评"],
    "prompt_evaluation": ["Prompt", "提示词", "评测", "效果评估", "模型微调"],
}
TRACK_LABELS = {
    "fde": "FDE / 前线部署",
    "agent_engineering": "Agent 开发",
    "rag_engineering": "RAG 工程",
    "ai_application": "AI 应用",
    "solution_delivery": "方案交付",
    "ai_product_delivery": "AI 产品与交付",
    "product": "产品",
    "sales": "销售 / 商务",
    "pure_java": "传统 Java",
    "other": "其他",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _sentences(text: str) -> list[str]:
    values = []
    for part in re.split(r"[。！？!?；;\n]", normalize_text(text)):
        cleaned = re.sub(r"^\s*(?:#{1,6}|[-*+]\s*)", "", part).strip(" -:：")
        if cleaned:
            values.append(cleaned)
    return values


def _skill_evidence(text: str, topic: dict, limit: int = 2) -> list[str]:
    return [
        sentence[:220]
        for sentence in _sentences(text)
        if _matches(sentence, topic["patterns"])
    ][:limit]


def _education_level(text: str) -> str:
    for level, pattern in (
        ("doctor", r"博士"),
        ("master", r"硕士|研究生"),
        ("bachelor", r"本科|学士"),
        ("associate", r"大专|专科"),
        ("high_school", r"高中|中专"),
    ):
        if re.search(pattern, text):
            return level
    return "unknown"


def _section(text: str, names: tuple[str, ...]) -> str:
    pattern = r"(?im)^##\s*(?:" + "|".join(re.escape(name) for name in names) + r")\s*$"
    match = re.search(pattern, text)
    if not match:
        return ""
    remainder = text[match.end() :]
    next_heading = re.search(r"(?m)^##\s+", remainder)
    return remainder[: next_heading.start()] if next_heading else remainder


def _experience_years(text: str, as_of: date) -> float:
    work_text = _section(text, ("工作经历", "工作经验", "职业经历")) or text
    ranges = re.findall(
        r"(20\d{2})[./年-](\d{1,2})\s*(?:-|–|—|至|~)\s*"
        r"(?:(20\d{2})[./年-](\d{1,2})|至今|现在|今)",
        work_text,
        re.I,
    )
    intervals: list[tuple[int, int]] = []
    for start_year, start_month, end_year, end_month in ranges:
        start = int(start_year) * 12 + int(start_month) - 1
        end = (
            int(end_year) * 12 + int(end_month) - 1
            if end_year and end_month
            else as_of.year * 12 + as_of.month - 1
        )
        if 1 <= int(start_month) <= 12 and start <= end:
            intervals.append((start, end))
    if not intervals:
        explicit = re.search(r"(\d+(?:\.\d+)?)\s*年(?:工作|开发|项目)?经验", work_text)
        return float(explicit.group(1)) if explicit else 0.0
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    months = sum(end - start + 1 for start, end in merged)
    return round(months / 12, 1)


def _display_name(text: str, fallback: str) -> str:
    heading = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    if not heading:
        return fallback
    value = heading.group(1).strip()
    if value in {"个人简历", "简历", "Resume", "RESUME"}:
        return fallback
    return value[:80]


def _graduation_year(text: str) -> int | None:
    education = _section(text, ("教育背景", "教育经历", "学历")) or text
    match = re.search(r"(20\d{2})\s*年?(?:毕业|届)", education)
    if match:
        return int(match.group(1))
    years = [int(value) for value in re.findall(r"20\d{2}", education)]
    return max(years) if years else None


def _target_tracks(skill_ids: set[str], text: str) -> list[str]:
    tracks: set[str] = set()
    if "agent_workflows" in skill_ids:
        tracks.add("agent_engineering")
    if "rag_knowledge" in skill_ids:
        tracks.add("rag_engineering")
    if skill_ids.intersection(
        {
            "python_api",
            "agent_workflows",
            "rag_knowledge",
            "fullstack_web",
            "model_serving",
            "speech_ai",
            "prompt_evaluation",
        }
    ):
        tracks.add("ai_application")
    if "delivery_fde" in skill_ids:
        tracks.update(("fde", "solution_delivery"))
    if re.search(r"AI.{0,12}产品|产品.{0,12}(?:AI|大模型|Agent)", text, re.I):
        tracks.add("ai_product_delivery")
    return sorted(tracks)


def extract_resume_profile(
    resume_text: str,
    candidate_id: str = "local-candidate",
    filename: str = "resume.md",
    as_of: date | None = None,
) -> dict:
    """Build a conservative profile from facts explicitly present in a resume."""
    text = normalize_text(resume_text)
    if len(text) < 80:
        raise ValueError("简历内容过短，请提供包含教育、经历或项目的完整文本")
    if len(text) > MAX_RESUME_CHARS:
        raise ValueError(f"简历内容不能超过 {MAX_RESUME_CHARS} 个字符")
    normalized_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate_id.strip()).strip("-")
    if not normalized_id:
        normalized_id = "local-candidate"
    normalized_id = normalized_id[:80]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    skills = []
    adjacent: set[str] = set()
    for topic in TOPICS:
        evidence = _skill_evidence(text, topic)
        if not evidence:
            continue
        aliases = SKILL_ALIASES.get(topic["id"], [topic["name"]])
        weak_only = all(
            re.search(r"学习|了解|入门|较少|不熟|欠缺|待补|计划", value, re.I)
            for value in evidence
        )
        if weak_only:
            adjacent.update(aliases)
            continue
        skills.append(
            {
                "id": topic["id"],
                "name": topic["name"],
                "aliases": aliases,
                "evidence": evidence,
            }
        )

    education_text = _section(text, ("教育背景", "教育经历", "学历")) or text
    graduation_year = _graduation_year(text)
    education = {"level": _education_level(education_text)}
    if graduation_year:
        education["graduation_year"] = graduation_year
    skill_ids = {item["id"] for item in skills}
    return {
        "profile_id": normalized_id,
        "version": f"resume-{digest[:12]}",
        "display_name": _display_name(text, normalized_id),
        "experience_years": _experience_years(text, as_of or date.today()),
        "education": education,
        "target_tracks": _target_tracks(skill_ids, text),
        "skills": skills,
        "adjacent_skills": sorted(adjacent),
        "hard_constraints": {},
        "preferences": {},
        "source": {
            "kind": "resume",
            "filename": Path(filename or "resume.md").name[:120],
            "sha256": digest,
        },
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _evidence(row: sqlite3.Row) -> dict:
    return {
        "job_id": row["job_id"],
        "title": row["title"] or "未命名岗位",
        "company": row["company"] or "公司信息缺失",
        "decision": row["interest"],
        "note": re.sub(r"\s+", " ", row["note"] or "").strip()[:160],
    }


def infer_preference_calibration(connection: sqlite3.Connection) -> dict:
    """Infer soft, reviewable signals; never create hard constraints."""
    unknowns = [
        {
            "id": "preferred_cities",
            "label": "目标城市",
            "reason": "岗位选择同时受方向、薪资和公司影响，不能只凭点击行为推断城市偏好。",
        },
        {
            "id": "salary_expectation",
            "label": "薪资底线与目标",
            "reason": "简历不等于薪资意愿，需要候选人确认。",
        },
        {
            "id": "work_arrangement",
            "label": "远程、出差与驻场边界",
            "reason": "没有足够明确的个人陈述，暂不作为筛选条件。",
        },
    ]
    if not _table_exists(connection, "job_decisions") or not _table_exists(
        connection, "job_positioning_analysis"
    ):
        return {"reviewed_jobs": 0, "signals": [], "unknowns": unknowns}

    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT decision.interest, decision.rejection_reason, decision.note,
               d.job_id, d.title, d.company, d.location, d.salary_raw,
               d.detail_text, a.track
          FROM job_decisions decision
          JOIN job_details d
            ON d.platform = decision.platform AND d.job_id = decision.job_id
          JOIN job_positioning_analysis a
            ON a.platform = decision.platform AND a.job_id = decision.job_id
         ORDER BY decision.decided_at, d.job_id
        """
    ).fetchall()
    signals = []
    by_track: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_track.setdefault(str(row["track"] or "other"), []).append(row)
    for track, track_rows in sorted(by_track.items()):
        positive = [row for row in track_rows if row["interest"] in {"want", "maybe"}]
        wanted = [row for row in track_rows if row["interest"] == "want"]
        rejected = [row for row in track_rows if row["interest"] == "reject"]
        if len(positive) < 2 or not wanted or len(positive) <= len(rejected):
            continue
        net_support = len(positive) - len(rejected)
        adjustment = min(6, 2 + len(wanted) + net_support // 3)
        confidence = min(
            0.9,
            0.45 + len(positive) * 0.08 + len(wanted) * 0.04 - len(rejected) * 0.05,
        )
        signals.append(
            {
                "id": f"track:{track}",
                "label": f"倾向 {TRACK_LABELS.get(track, track)}",
                "dimension": "track",
                "value": track,
                "direction": "prefer",
                "effect": "soft",
                "score_adjustment": adjustment,
                "confidence": round(confidence, 2),
                "evidence": [_evidence(row) for row in positive[:3]],
                "counter_evidence": [_evidence(row) for row in rejected[:3]],
                "support": {
                    "want": len(wanted),
                    "maybe": len(positive) - len(wanted),
                    "reject": len(rejected),
                },
            }
        )

    transparency_rows = [
        row
        for row in rows
        if re.search(
            r"JD\s*太少|描述过少|信息不透明|公司规模小|规模小|有坑|工商|成立时间",
            row["note"] or "",
            re.I,
        )
    ]
    if transparency_rows:
        signals.append(
            {
                "id": "company_transparency",
                "label": "关注公司信息透明度与稳定性",
                "dimension": "company_transparency",
                "value": "sufficient_company_context",
                "direction": "avoid_risk",
                "effect": "soft",
                "score_adjustment": -3,
                "confidence": round(min(0.9, 0.55 + len(transparency_rows) * 0.08), 2),
                "evidence": [_evidence(row) for row in transparency_rows[:3]],
            }
        )

    employment_rows = [
        row
        for row in rows
        if row["rejection_reason"] == "employment"
        or re.search(r"外包|派遣|第三方|驻场", row["note"] or "", re.I)
    ]
    if employment_rows:
        signals.append(
            {
                "id": "employment_risk",
                "label": "谨慎对待外包、派遣与第三方用工",
                "dimension": "employment",
                "value": "direct_employment_preferred",
                "direction": "avoid_risk",
                "effect": "soft",
                "score_adjustment": -5,
                "confidence": round(min(0.9, 0.58 + len(employment_rows) * 0.08), 2),
                "evidence": [_evidence(row) for row in employment_rows[:3]],
            }
        )

    return {"reviewed_jobs": len(rows), "signals": signals, "unknowns": unknowns}


def build_collection_plan(connection: sqlite3.Connection, profile: dict, unknowns: list[dict]) -> dict:
    local_jobs = 0
    collected_records = 0
    if _table_exists(connection, "job_details"):
        collected_records = int(
            connection.execute(
                "SELECT COUNT(*) FROM job_details"
            ).fetchone()[0]
        )
        local_jobs = int(
            connection.execute(
                "SELECT COUNT(*) FROM job_details WHERE detail_status = 'collected' AND COALESCE(jd_text, '') <> ''"
            ).fetchone()[0]
        )
    skill_ids = {item["id"] for item in profile.get("skills") or []}
    queries = []
    for topic_id, values in (
        ("agent_workflows", ("AI Agent 开发",)),
        ("rag_knowledge", ("RAG 工程师",)),
        ("delivery_fde", ("FDE", "AI 解决方案工程师")),
        ("python_api", ("AI 应用工程师",)),
        ("model_serving", ("大模型应用开发",)),
    ):
        if topic_id in skill_ids:
            queries.extend(value for value in values if value not in queries)
    if not queries:
        queries.append("AI 应用工程师")
    return {
        "local_jobs": local_jobs,
        "collected_records": collected_records,
        "queries": queries[:6],
        "pending_confirmations": [item["id"] for item in unknowns],
        "external_collection": "not_run",
        "source": "local_job_snapshot",
    }


def migrate_calibration_schema(connection: sqlite3.Connection) -> None:
    migrate_matching_schema(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_calibrations (
          profile_id TEXT NOT NULL,
          profile_version TEXT NOT NULL,
          calibrated_at TEXT NOT NULL,
          resume_sha256 TEXT NOT NULL,
          filename TEXT NOT NULL,
          analysis_version TEXT NOT NULL,
          dashboard_json TEXT NOT NULL,
          PRIMARY KEY(profile_id, profile_version)
        )
        """
    )
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if current_version < CALIBRATION_SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version = {CALIBRATION_SCHEMA_VERSION}")
    connection.commit()


def save_calibration_dashboard(db_path: str | Path, dashboard: dict) -> None:
    """Persist one derived calibration snapshot without storing resume text."""
    database = Path(db_path).resolve()
    profile = dashboard.get("profile") or {}
    source = profile.get("source") or {}
    match = dashboard.get("match") or {}
    calibrated_at = str(dashboard.get("calibrated_at") or _now())
    analysis_version = str(match.get("analysis_version") or "resume-match-v1")
    required = {
        "profile_id": profile.get("profile_id"),
        "profile_version": profile.get("version"),
        "resume_sha256": source.get("sha256"),
        "filename": source.get("filename"),
    }
    if not all(required.values()):
        raise ValueError("校准结果缺少候选人版本或简历来源信息")

    connection = sqlite3.connect(database)
    try:
        migrate_calibration_schema(connection)
        with connection:
            connection.execute(
                """
                INSERT INTO candidate_calibrations (
                  profile_id, profile_version, calibrated_at, resume_sha256,
                  filename, analysis_version, dashboard_json
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(profile_id, profile_version) DO UPDATE SET
                  calibrated_at = excluded.calibrated_at,
                  analysis_version = excluded.analysis_version,
                  dashboard_json = excluded.dashboard_json
                """,
                (
                    required["profile_id"],
                    required["profile_version"],
                    calibrated_at,
                    required["resume_sha256"],
                    required["filename"],
                    analysis_version,
                    json.dumps(dashboard, ensure_ascii=False),
                ),
            )
    finally:
        connection.close()


def calibrate_resume(
    db_path: str | Path,
    resume_text: str,
    filename: str = "resume.md",
    candidate_id: str = "local-candidate",
    analysis_version: str = "resume-match-v1",
    as_of: date | None = None,
) -> dict:
    database = Path(db_path).resolve()
    if not database.is_file():
        raise ValueError(f"岗位数据库不存在：{database}")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        migrate_calibration_schema(connection)
        preference_calibration = infer_preference_calibration(connection)
        profile = extract_resume_profile(
            resume_text,
            candidate_id=candidate_id,
            filename=filename,
            as_of=as_of,
        )
        profile["preferences"] = {"calibration": preference_calibration}
        preference_hash = hashlib.sha256(
            json.dumps(preference_calibration, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        profile["version"] = f"{profile['version']}-p{preference_hash}"
        collection_plan = build_collection_plan(
            connection, profile, preference_calibration["unknowns"]
        )
    finally:
        connection.close()

    match_result = run_match_preview(database, profile, analysis_version)
    calibrated_at = _now()
    dashboard = {
        "profile": profile,
        "preference_calibration": preference_calibration,
        "collection_plan": collection_plan,
        "match": {
            "total": match_result.total,
            "analysis_version": match_result.analysis_version,
            "distribution": match_result.distribution,
        },
        "calibrated_at": calibrated_at,
        "privacy": {"raw_resume_stored": False, "external_calls": False},
    }
    save_calibration_dashboard(database, dashboard)
    return dashboard


def build_calibration_dashboard(connection: sqlite3.Connection) -> dict:
    migrate_calibration_schema(connection)
    row = connection.execute(
        """
        SELECT dashboard_json
          FROM candidate_calibrations
         ORDER BY calibrated_at DESC, rowid DESC
         LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {
            "profile": None,
            "preference_calibration": {"reviewed_jobs": 0, "signals": [], "unknowns": []},
            "collection_plan": {"local_jobs": 0, "queries": [], "pending_confirmations": []},
            "match": {"total": 0, "distribution": {}},
            "empty_message": "请选择本地 Markdown 或文本简历开始校准。",
        }
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        raise ValueError("最近一次简历校准结果无法读取，请重新校准")

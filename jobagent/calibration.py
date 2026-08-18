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


CALIBRATION_SCHEMA_VERSION = 5
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
CONFIRMABLE_TRACKS = frozenset(TRACK_LABELS) - {"other"}
CONFIRMABLE_CONSTRAINTS = (
    "reject_third_party",
    "reject_dispatch",
    "reject_long_term_overseas",
)


def normalize_preference_settings(payload: dict) -> dict:
    """Validate the small, explicit preference surface that can affect matching."""
    if not isinstance(payload, dict):
        raise ValueError("偏好设置必须是对象")
    raw_tracks = payload.get("target_tracks")
    if not isinstance(raw_tracks, list):
        raise ValueError("请至少选择一个目标方向")
    tracks = sorted({str(value).strip() for value in raw_tracks if str(value).strip()})
    unknown_tracks = set(tracks) - CONFIRMABLE_TRACKS
    if unknown_tracks:
        raise ValueError(f"目标方向不支持：{', '.join(sorted(unknown_tracks))}")
    if not tracks:
        raise ValueError("请至少选择一个目标方向")

    raw_constraints = payload.get("hard_constraints") or {}
    if not isinstance(raw_constraints, dict):
        raise ValueError("边界设置必须是对象")
    allowed = {
        "maximum_required_experience_years",
        *CONFIRMABLE_CONSTRAINTS,
    }
    unknown_constraints = set(raw_constraints) - allowed
    if unknown_constraints:
        raise ValueError(f"边界设置不支持：{', '.join(sorted(unknown_constraints))}")

    maximum = raw_constraints.get("maximum_required_experience_years")
    if maximum is not None:
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 30:
            raise ValueError("最高经验门槛必须是 1 到 30 之间的整数")

    constraints: dict[str, object] = {}
    if maximum is not None:
        constraints["maximum_required_experience_years"] = maximum
    for key in CONFIRMABLE_CONSTRAINTS:
        value = raw_constraints.get(key)
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{key} 必须是布尔值或空值")
        if value is not None:
            constraints[key] = value
    return {"target_tracks": tracks, "hard_constraints": constraints}


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


def _preference_version(settings: dict) -> str:
    digest = hashlib.sha256(
        json.dumps(settings, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return f"preferences-{digest}"


def _load_confirmed_preferences(
    connection: sqlite3.Connection, profile_id: str
) -> dict | None:
    if not _table_exists(connection, "candidate_preference_settings"):
        return None
    row = connection.execute(
        """
        SELECT settings_version, updated_at, settings_json
          FROM candidate_preference_settings
         WHERE profile_id = ?
         ORDER BY updated_at DESC, rowid DESC
         LIMIT 1
        """,
        (profile_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        settings = normalize_preference_settings(json.loads(row["settings_json"]))
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("已保存的偏好设置无法读取，请重新确认") from exc
    return {
        "version": row["settings_version"],
        "updated_at": row["updated_at"],
        "settings": settings,
    }


def _apply_confirmed_preferences(profile: dict, confirmed: dict) -> dict:
    settings = normalize_preference_settings(confirmed)
    profile = json.loads(json.dumps(profile, ensure_ascii=False))
    profile["target_tracks"] = list(settings["target_tracks"])
    constraints = settings["hard_constraints"]
    profile["hard_constraints"] = {
        key: value
        for key, value in constraints.items()
        if key == "maximum_required_experience_years" or value is True
    }
    calibration = (profile.get("preferences") or {}).get("calibration") or {}
    profile["preferences"] = {
        "calibration": calibration,
        "confirmed": settings,
    }
    source_sha = str((profile.get("source") or {}).get("sha256") or "")
    if not source_sha:
        raise ValueError("候选人档案缺少简历版本，无法保存偏好")
    calibration_hash = hashlib.sha256(
        json.dumps(calibration, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    profile["version"] = (
        f"resume-{source_sha[:12]}-p{calibration_hash}-c"
        f"{_preference_version(settings).removeprefix('preferences-')}"
    )
    return profile


def _save_confirmed_preferences(
    connection: sqlite3.Connection, profile_id: str, settings: dict
) -> dict:
    normalized = normalize_preference_settings(settings)
    record = {
        "version": _preference_version(normalized),
        "updated_at": _now(),
        "settings": normalized,
    }
    with connection:
        connection.execute(
            """
            INSERT INTO candidate_preference_settings (
              profile_id, settings_version, updated_at, settings_json
            ) VALUES (?,?,?,?)
            ON CONFLICT(profile_id, settings_version) DO UPDATE SET
              updated_at = excluded.updated_at,
              settings_json = excluded.settings_json
            """,
            (
                profile_id,
                record["version"],
                record["updated_at"],
                json.dumps(normalized, ensure_ascii=False, sort_keys=True),
            ),
        )
    return record


def _alignment_snapshot(
    connection: sqlite3.Connection, previous_strong_conflicts: int | None = None
) -> dict:
    latest = connection.execute(
        """
        SELECT analysis_version, profile_id, profile_version
          FROM job_match_analyses
         ORDER BY analyzed_at DESC, rowid DESC
         LIMIT 1
        """
    ).fetchone()
    if latest is None:
        return {
            "reviewed_jobs": 0,
            "strong_conflicts": 0,
            "decision_conflicts": 0,
            "previous_strong_conflicts": previous_strong_conflicts,
        }
    rows = connection.execute(
        """
        SELECT match.recommendation, decision.interest
          FROM job_match_analyses match
          JOIN job_decisions decision
            ON decision.platform = match.platform AND decision.job_id = match.job_id
         WHERE match.analysis_version = ?
           AND match.profile_id = ?
           AND match.profile_version = ?
        """,
        (latest["analysis_version"], latest["profile_id"], latest["profile_version"]),
    ).fetchall()
    strong = sum(
        (row["recommendation"] == "priority" and row["interest"] == "reject")
        or (row["recommendation"] == "not_fit" and row["interest"] == "want")
        for row in rows
    )
    return {
        "reviewed_jobs": len(rows),
        "strong_conflicts": strong,
        "decision_conflicts": strong,
        "previous_strong_conflicts": previous_strong_conflicts,
    }


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
    target_tracks = set(profile.get("target_tracks") or [])
    track_topics = {
        "agent_workflows": {"agent_engineering"},
        "rag_knowledge": {"rag_engineering"},
        "delivery_fde": {"fde", "solution_delivery", "ai_product_delivery"},
        "python_api": {"ai_application"},
        "model_serving": {"ai_application"},
    }
    for topic_id, values in (
        ("agent_workflows", ("AI Agent 开发",)),
        ("rag_knowledge", ("RAG 工程师",)),
        ("delivery_fde", ("FDE", "AI 解决方案工程师")),
        ("python_api", ("AI 应用工程师",)),
        ("model_serving", ("大模型应用开发",)),
    ):
        if topic_id in skill_ids or target_tracks.intersection(track_topics[topic_id]):
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_preference_settings (
          profile_id TEXT NOT NULL,
          settings_version TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          settings_json TEXT NOT NULL,
          PRIMARY KEY(profile_id, settings_version)
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
        confirmed_preferences = _load_confirmed_preferences(
            connection, profile["profile_id"]
        )
        if confirmed_preferences:
            profile = _apply_confirmed_preferences(
                profile, confirmed_preferences["settings"]
            )
        else:
            preference_hash = hashlib.sha256(
                json.dumps(
                    preference_calibration, ensure_ascii=False, sort_keys=True
                ).encode("utf-8")
            ).hexdigest()[:8]
            profile["version"] = f"{profile['version']}-p{preference_hash}"
        collection_plan = build_collection_plan(
            connection, profile, preference_calibration["unknowns"]
        )
    finally:
        connection.close()

    match_result = run_match_preview(database, profile, analysis_version)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        alignment = _alignment_snapshot(connection)
    finally:
        connection.close()
    calibrated_at = _now()
    dashboard = {
        "profile": profile,
        "preference_calibration": preference_calibration,
        "confirmed_preferences": confirmed_preferences,
        "collection_plan": collection_plan,
        "match": {
            "total": match_result.total,
            "analysis_version": match_result.analysis_version,
            "distribution": match_result.distribution,
        },
        "alignment": alignment,
        "calibrated_at": calibrated_at,
        "privacy": {"raw_resume_stored": False, "external_calls": False},
    }
    save_calibration_dashboard(database, dashboard)
    return dashboard


def confirm_preference_settings(
    db_path: str | Path,
    payload: dict,
    candidate_id: str = "local-candidate",
    analysis_version: str = "resume-match-v1",
) -> dict:
    """Persist explicit user boundaries and recalculate the local match snapshot."""
    database = Path(db_path).resolve()
    if not database.is_file():
        raise ValueError(f"岗位数据库不存在：{database}")
    settings = normalize_preference_settings(payload)
    normalized_candidate_id = re.sub(
        r"[^A-Za-z0-9_.-]+", "-", candidate_id.strip()
    ).strip("-") or "local-candidate"

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        migrate_calibration_schema(connection)
        row = connection.execute(
            """
            SELECT dashboard_json
              FROM candidate_calibrations
             WHERE profile_id = ?
             ORDER BY calibrated_at DESC, rowid DESC
             LIMIT 1
            """,
            (normalized_candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError("请先完成一次简历校准，再确认个人偏好")
        try:
            previous_dashboard = json.loads(row["dashboard_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("最近一次简历校准结果无法读取，请重新校准") from exc
        profile = previous_dashboard.get("profile") or {}
        preference_calibration = previous_dashboard.get("preference_calibration") or {
            "reviewed_jobs": 0,
            "signals": [],
            "unknowns": [],
        }
        profile["preferences"] = {"calibration": preference_calibration}
        profile = _apply_confirmed_preferences(profile, settings)
        old_alignment = previous_dashboard.get("alignment") or _alignment_snapshot(
            connection
        )
        collection_plan = build_collection_plan(
            connection, profile, preference_calibration.get("unknowns") or []
        )
        previous_plan = previous_dashboard.get("collection_plan") or {}
        for key in (
            "external_collection",
            "external_collection_error",
            "collection_run_id",
            "collected_new_jobs",
        ):
            if key in previous_plan:
                collection_plan[key] = previous_plan[key]
    finally:
        connection.close()

    match_result = run_match_preview(database, profile, analysis_version)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        migrate_calibration_schema(connection)
        confirmed_preferences = _save_confirmed_preferences(
            connection, normalized_candidate_id, settings
        )
        alignment = _alignment_snapshot(
            connection,
            previous_strong_conflicts=int(old_alignment.get("strong_conflicts") or 0),
        )
    finally:
        connection.close()

    dashboard = {
        "profile": profile,
        "preference_calibration": preference_calibration,
        "confirmed_preferences": confirmed_preferences,
        "collection_plan": collection_plan,
        "match": {
            "total": match_result.total,
            "analysis_version": match_result.analysis_version,
            "distribution": match_result.distribution,
        },
        "alignment": alignment,
        "calibrated_at": _now(),
        "privacy": previous_dashboard.get("privacy")
        or {"raw_resume_stored": False, "external_calls": False},
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
            "confirmed_preferences": None,
            "collection_plan": {"local_jobs": 0, "queries": [], "pending_confirmations": []},
            "match": {"total": 0, "distribution": {}},
            "alignment": {"reviewed_jobs": 0, "strong_conflicts": 0},
            "empty_message": "请选择本地 Markdown 或文本简历开始校准。",
        }
    try:
        dashboard = json.loads(row[0])
        dashboard.setdefault("confirmed_preferences", None)
        dashboard.setdefault("alignment", {"reviewed_jobs": 0, "strong_conflicts": 0})
        return dashboard
    except (TypeError, json.JSONDecodeError):
        raise ValueError("最近一次简历校准结果无法读取，请重新校准")

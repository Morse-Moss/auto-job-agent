"""Persistent samples, system judgments, and user decisions for collected jobs."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path

from jobagent.analysis import parse_salary
from jobagent.learning import build_learning_dashboard, note_requests_learning
from jobagent.calibration import build_calibration_dashboard, calibrate_resume
from jobagent.boundary import REVIEWED_DECISIONS_SAMPLE, build_boundary_dashboard
from jobagent.matching import (
    build_match_dashboard,
    get_match_detail,
    migrate_matching_schema,
)


DEFAULT_SAMPLE_QUOTAS = {
    "favorites": 20,
    "core": 15,
    "stretch": 15,
    "exclude": 10,
}
INTEREST_VALUES = {"want", "maybe", "reject"}
CORRECTION_CATEGORIES = {
    "direction",
    "technical_match",
    "business_value",
    "attainability",
    "salary_reachability",
    "employment_risk",
    "other",
}
SCHEMA_VERSION = 2
TRACK_LABELS = {
    "fde": "FDE / 前线部署",
    "agent_engineering": "Agent 开发",
    "rag_engineering": "RAG 工程",
    "ai_application": "AI 应用",
    "solution_delivery": "AI 方案交付",
    "pure_java": "传统 Java",
    "product": "产品岗位",
    "sales": "销售 / 商务",
    "other": "其他方向",
}
COMPANY_SIZE_RE = re.compile(r"^(?:\d+-\d+人|\d+人以上)$")
FINANCING_RE = re.compile(
    r"^(?:不需要融资|未融资|天使轮|种子轮|Pre-A轮|A\+?轮|B\+?轮|C\+?轮|D\+?轮|E\+?轮|战略融资|已上市)$",
    re.I,
)


class ReviewValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _unique_job_row(rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    if len(rows) > 1:
        raise ReviewValidationError("同一岗位编号存在于多个平台，请指定平台")
    return rows[0] if rows else None


def _company_age(founded_date: str, as_of: date) -> str:
    try:
        founded = date.fromisoformat(founded_date)
    except ValueError:
        return ""
    if founded > as_of:
        return "日期待核对"
    months = (as_of.year - founded.year) * 12 + as_of.month - founded.month
    if as_of.day < founded.day:
        months -= 1
    if months <= 0:
        return "不足1个月"
    years, remaining_months = divmod(months, 12)
    if not years:
        return f"{remaining_months}个月"
    if not remaining_months:
        return f"{years}年"
    return f"{years}年{remaining_months}个月"


def extract_company_profile(job: dict, as_of: date | None = None) -> dict[str, str]:
    """Recover company metadata already present in the collected Boss page."""
    detail_text = str(job.get("detail_text") or "")
    parsed = {"company_size": "", "industry": "", "financing": ""}
    block = re.search(r"公司基本信息\s+(.*?)\s+查看全部职位", detail_text, re.S)
    if block:
        values = [value.strip() for value in block.group(1).splitlines() if value.strip()]
        for value in values[1:]:
            if not parsed["company_size"] and COMPANY_SIZE_RE.fullmatch(value):
                parsed["company_size"] = value
            elif not parsed["financing"] and FINANCING_RE.fullmatch(value):
                parsed["financing"] = value
            elif not parsed["industry"]:
                parsed["industry"] = value

    fallback_size = str(job.get("company_size") or "").strip()
    fallback_financing = str(job.get("financing") or "").strip()
    fallback_industry = str(job.get("industry") or "").strip()
    company_size = parsed["company_size"] or (
        fallback_size if COMPANY_SIZE_RE.fullmatch(fallback_size) else ""
    )
    financing = parsed["financing"] or (
        fallback_financing if FINANCING_RE.fullmatch(fallback_financing) else ""
    )
    industry = parsed["industry"] or (
        fallback_industry
        if not COMPANY_SIZE_RE.fullmatch(fallback_industry)
        and not FINANCING_RE.fullmatch(fallback_industry)
        else ""
    )

    registry_text = detail_text.split("工商信息", 1)[1] if "工商信息" in detail_text else ""

    def registry_value(pattern: str) -> str:
        match = re.search(pattern, registry_text, re.S)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

    founded_date = registry_value(r"成立日期\s+(\d{4}-\d{1,2}-\d{1,2})")
    return {
        "company_size": company_size,
        "industry": industry,
        "financing": financing,
        "legal_name": registry_value(r"公司名称\s+(.+?)\s+法定代表人"),
        "founded_date": founded_date,
        "company_age": _company_age(founded_date, as_of or date.today()) if founded_date else "",
        "operating_status": registry_value(r"经营状态\s+(\S+)"),
        "registered_capital": registry_value(r"注册资金\s+(.+?)\s+查看全部"),
    }


def _matching_sentences(text: str, patterns: tuple[str, ...], limit: int = 2) -> list[str]:
    sentences = [part.strip(" -:：") for part in re.split(r"[。！？!?\n]", text) if part.strip()]
    matched = [sentence for sentence in sentences if any(re.search(pattern, sentence, re.I) for pattern in patterns)]
    return [sentence[:180] for sentence in matched[:limit]]


def extract_evidence(job: dict) -> dict[str, list[str]]:
    """Extract short source snippets without claiming more than the JD provides."""
    title = str(job.get("title") or "")
    jd = str(job.get("jd_text") or job.get("jd") or "")
    recruiter = " ".join(str(job.get(field) or "") for field in ("recruiter_name", "recruiter_role"))
    text = f"{title}\n{jd}"
    technical = _matching_sentences(
        text,
        (r"Agent", r"智能体", r"RAG", r"大模型", r"LLM", r"Python", r"API", r"工作流", r"模型"),
    )
    business = _matching_sentences(
        text,
        (r"客户", r"业务", r"需求", r"交付", r"部署", r"实施", r"解决方案", r"现场", r"复盘"),
    )
    relationship = _matching_sentences(
        f"{text}\n{recruiter}",
        (r"第三方", r"外包", r"派遣", r"猎头", r"驻场", r"RPO", r"招聘顾问"),
    )
    risks = _json_list(str(job.get("risks_json") or "[]"))
    caution = [
        {
            "third_party": "招聘渠道或用工关系需要确认",
            "outsourcing_risk": "JD 出现外包 / 派遣 / 驻场线索",
            "seniority_gap": "经验门槛可能高于当前阶段",
            "salary_stretch": "薪资区间可能对应更高责任范围",
            "pure_java": "技术方向可能偏传统 Java",
            "onsite_heavy": "驻场或出差要求需要确认",
            "graduate_only": "应届资格需要单独确认",
        }.get(risk, risk)
        for risk in risks
    ]
    return {
        "technical": technical,
        "business": business,
        "relationship": relationship,
        "caution": caution,
    }


def _judgment(value: str, tone: str, evidence: list[str], score: int | None = None) -> dict:
    result = {"value": value, "tone": tone, "evidence": evidence[:3]}
    if score is not None:
        result["score"] = score
    return result


def build_system_analysis(job: dict) -> dict[str, dict]:
    """Project persisted rules into separate, evidence-backed UI judgments."""
    evidence = job.get("evidence") or extract_evidence(job)
    risks = set(job.get("risks") or _json_list(job.get("risks_json")))
    reasons = [str(item) for item in (job.get("reasons") or _json_list(job.get("reasons_json")))]
    labels = set(job.get("system_labels") or _json_list(job.get("labels_json")))
    track = str(job.get("track") or "other")
    relevance = int(job.get("relevance_score") or 0)
    fde_score = int(job.get("fde_value_score") or 0)

    direction = _judgment(
        TRACK_LABELS.get(track, track or "信息不足"),
        "danger" if track in {"pure_java", "product", "sales", "other"} else "positive",
        [str(job.get("title") or "")],
    )

    technical_evidence = list(evidence.get("technical") or [])
    if {"pure_java", "non_ai_role"}.intersection(risks):
        technical_value, technical_tone = "低", "danger"
    elif technical_evidence and relevance >= 75:
        technical_value, technical_tone = "高", "positive"
    elif technical_evidence:
        technical_value, technical_tone = "中", "neutral"
    else:
        technical_value, technical_tone = "信息不足", "muted"
    technical_match = _judgment(
        technical_value,
        technical_tone,
        technical_evidence,
        relevance,
    )

    business_evidence = list(evidence.get("business") or [])
    if fde_score >= 60:
        business_value, business_tone = "高", "positive"
    elif fde_score >= 30:
        business_value, business_tone = "中", "neutral"
    elif business_evidence:
        business_value, business_tone = "低", "warning"
    else:
        business_value, business_tone = "信息不足", "muted"
    business_judgment = _judgment(
        business_value,
        business_tone,
        business_evidence,
        fde_score,
    )

    tier = str(job.get("fit_tier") or "")
    attainability_values = {
        "core": ("当前可投", "positive"),
        "adjacent": ("相邻可投", "neutral"),
        "stretch": ("有门槛", "warning"),
        "exclude": ("不建议投", "danger"),
    }
    attainability_value, attainability_tone = attainability_values.get(
        tier, ("信息不足", "muted")
    )
    attainability_evidence = [
        value
        for value in [str(job.get("experience_raw") or ""), *reasons]
        if value
    ]
    attainability = _judgment(
        attainability_value,
        attainability_tone,
        attainability_evidence,
    )

    salary_kind = str(job.get("salary_kind") or "unknown")
    salary_max = job.get("salary_max_k")
    salary_evidence = [
        value
        for value in [
            f"薪资：{job.get('salary_raw')}" if job.get("salary_raw") else "",
            f"经验：{job.get('experience_raw')}" if job.get("experience_raw") else "",
        ]
        if value
    ]
    if salary_kind != "monthly" or salary_max is None:
        salary_value, salary_tone = "信息不足", "muted"
    elif {"salary_stretch", "seniority_gap"}.intersection(risks):
        salary_value, salary_tone = "偏挑战", "warning"
    elif float(salary_max) <= 30:
        salary_value, salary_tone = "较可达", "positive"
    elif float(salary_max) <= 45:
        salary_value, salary_tone = "可争取", "neutral"
    else:
        salary_value, salary_tone = "偏挑战", "warning"
    salary_reachability = _judgment(
        salary_value,
        salary_tone,
        salary_evidence,
    )

    relationship_evidence = list(evidence.get("relationship") or [])
    if "outsourcing_risk" in risks:
        employment_value, employment_tone = "疑似外包 / 派遣", "danger"
    elif "third_party" in risks:
        employment_value, employment_tone = "第三方招聘，关系待确认", "warning"
    elif "direct" in labels:
        employment_value, employment_tone = "企业直招线索", "positive"
    else:
        employment_value, employment_tone = "信息不足", "muted"
    employment_risk = _judgment(
        employment_value,
        employment_tone,
        relationship_evidence,
    )

    return {
        "direction": direction,
        "technical_match": technical_match,
        "business_value": business_judgment,
        "attainability": attainability,
        "salary_reachability": salary_reachability,
        "employment_risk": employment_risk,
    }


def migrate_review_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS job_review_samples (
          sample_name TEXT NOT NULL,
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          sample_bucket TEXT NOT NULL,
          position INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(sample_name, platform, job_id),
          UNIQUE(sample_name, position)
        );
        CREATE TABLE IF NOT EXISTS job_reviews (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          interest TEXT NOT NULL,
          role_labels_json TEXT NOT NULL,
          technical_depth INTEGER NOT NULL,
          business_value INTEGER NOT NULL,
          attainability INTEGER NOT NULL,
          salary_realism INTEGER NOT NULL,
          employment_model TEXT NOT NULL,
          rejection_reason TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          reviewed_at TEXT NOT NULL,
          PRIMARY KEY(platform, job_id)
        );
        CREATE TABLE IF NOT EXISTS job_decisions (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          interest TEXT NOT NULL CHECK(interest IN ('want', 'maybe', 'reject')),
          rejection_reason TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          learning_intent INTEGER CHECK(learning_intent IN (0, 1) OR learning_intent IS NULL),
          decided_at TEXT NOT NULL,
          PRIMARY KEY(platform, job_id)
        );
        CREATE TABLE IF NOT EXISTS job_corrections (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          category TEXT NOT NULL,
          note TEXT NOT NULL,
          analysis_version TEXT NOT NULL DEFAULT '',
          corrected_at TEXT NOT NULL,
          PRIMARY KEY(platform, job_id, category)
        );
        """
    )
    decision_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(job_decisions)").fetchall()
    }
    if "learning_intent" not in decision_columns:
        connection.execute(
            """
            ALTER TABLE job_decisions
            ADD COLUMN learning_intent INTEGER
            CHECK(learning_intent IN (0, 1) OR learning_intent IS NULL)
            """
        )
    migrate_matching_schema(connection)
    connection.execute(
        """
        INSERT INTO job_decisions (
          platform, job_id, interest, rejection_reason, note, decided_at
        )
        SELECT platform, job_id, interest, rejection_reason, note, reviewed_at
        FROM job_reviews
        WHERE 1 = 1
        ON CONFLICT(platform, job_id) DO NOTHING;
        """
    )
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if current_version < SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()


def migrate_review_database(db_path: str | Path) -> Path:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ReviewValidationError(f"岗位数据库不存在：{path}")
    connection = sqlite3.connect(path)
    try:
        migrate_review_schema(connection)
    finally:
        connection.close()
    return path


def _balanced_take(rows: list[sqlite3.Row], count: int) -> list[sqlite3.Row]:
    grouped: dict[str, deque[sqlite3.Row]] = defaultdict(deque)
    for row in sorted(
        rows,
        key=lambda item: (-int(item["relevance_score"]), str(item["job_id"])),
    ):
        grouped[str(row["track"])].append(row)

    selected: list[sqlite3.Row] = []
    tracks = sorted(grouped)
    while tracks and len(selected) < count:
        remaining: list[str] = []
        for track in tracks:
            if len(selected) >= count:
                break
            queue = grouped[track]
            if queue:
                selected.append(queue.popleft())
            if queue:
                remaining.append(track)
        tracks = remaining
    return selected


class ReviewStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.is_file():
            raise ReviewValidationError(f"岗位数据库不存在：{self.db_path}")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        try:
            migrate_review_schema(self.conn)
        except Exception:
            self.conn.close()
            raise

    def ensure_sample(
        self,
        sample_name: str = "positioning-v1",
        quotas: dict[str, int] | None = None,
    ) -> list[dict]:
        existing = self.conn.execute(
            "SELECT 1 FROM job_review_samples WHERE sample_name = ? LIMIT 1",
            (sample_name,),
        ).fetchone()
        if existing:
            return self.list_jobs(sample_name)

        requested = dict(quotas or DEFAULT_SAMPLE_QUOTAS)
        allowed = set(DEFAULT_SAMPLE_QUOTAS)
        if not requested or set(requested) - allowed or any(value < 0 for value in requested.values()):
            raise ReviewValidationError("样本分层配置无效")

        candidates = self.conn.execute(
            """
            SELECT d.platform, d.job_id, d.source_key, a.fit_tier,
                   a.track, a.relevance_score
            FROM job_details d
            JOIN job_positioning_analysis a
              ON a.platform = d.platform AND a.job_id = d.job_id
            WHERE d.detail_status = 'collected'
              AND COALESCE(d.jd_text, '') <> ''
            """
        ).fetchall()
        selected_ids: set[tuple[str, str]] = set()
        selected: list[tuple[str, sqlite3.Row]] = []

        for bucket, count in requested.items():
            if bucket == "favorites":
                pool = [row for row in candidates if row["source_key"] == "favorites"]
            else:
                pool = [row for row in candidates if row["fit_tier"] == bucket]
            pool = [row for row in pool if (row["platform"], row["job_id"]) not in selected_ids]
            taken = _balanced_take(pool, count)
            if len(taken) != count:
                raise ReviewValidationError(f"{bucket} 样本不足：需要 {count}，只有 {len(taken)}")
            for row in taken:
                selected_ids.add((row["platform"], row["job_id"]))
                selected.append((bucket, row))

        created_at = _now()
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO job_review_samples (
                  sample_name, platform, job_id, sample_bucket, position, created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                [
                    (sample_name, row["platform"], row["job_id"], bucket, index, created_at)
                    for index, (bucket, row) in enumerate(selected)
                ],
            )
        return self.list_jobs(sample_name)

    def list_jobs(self, sample_name: str = "positioning-v1") -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT s.position, s.sample_bucket, d.platform, d.job_id, d.title,
                   d.company, d.salary_raw, d.location, d.experience_raw,
                   d.source_key, a.fit_tier, a.track, a.relevance_score,
                   a.fde_value_score, decision.interest, decision.decided_at
            FROM job_review_samples s
            JOIN job_details d
              ON d.platform = s.platform AND d.job_id = s.job_id
            JOIN job_positioning_analysis a
              ON a.platform = d.platform AND a.job_id = d.job_id
            LEFT JOIN job_decisions decision
              ON decision.platform = d.platform AND decision.job_id = d.job_id
            WHERE s.sample_name = ?
            ORDER BY s.position
            """,
            (sample_name,),
        ).fetchall()
        return [
            {
                **dict(row),
                "reviewed": row["decided_at"] is not None,
            }
            for row in rows
        ]

    def get_job(
        self,
        job_id: str,
        sample_name: str = "positioning-v1",
        platform: str | None = None,
    ) -> dict | None:
        if sample_name == REVIEWED_DECISIONS_SAMPLE:
            snapshot = self.conn.execute(
                """
                SELECT analysis_version, profile_id, profile_version
                  FROM job_match_analyses
                 ORDER BY analyzed_at DESC, rowid DESC
                 LIMIT 1
                """
            ).fetchone()
            if snapshot is None:
                return None
            platform_filter = " AND match.platform = ?" if platform else ""
            params = [
                snapshot["analysis_version"],
                snapshot["profile_id"],
                snapshot["profile_version"],
                job_id,
            ]
            if platform:
                params.append(platform)
            rows = self.conn.execute(
                """
                SELECT '' AS sample_bucket, d.*, match.analyzed_at, match.track,
                       match.recommendation, match.score, match.confidence,
                       match.information_status, match.hard_requirements_json,
                       match.strengths_json, match.gaps_json,
                       match.uncertainties_json, match.preference_signals_json,
                       decision.interest,
                       decision.rejection_reason, decision.note, decision.learning_intent,
                       decision.decided_at
                  FROM job_match_analyses match
                  JOIN job_details d
                    ON d.platform = match.platform AND d.job_id = match.job_id
                  JOIN job_decisions decision
                    ON decision.platform = match.platform
                   AND decision.job_id = match.job_id
                 WHERE match.analysis_version = ?
                   AND match.profile_id = ?
                   AND match.profile_version = ?
                   AND match.job_id = ?
                """
                + platform_filter
                + " ORDER BY match.platform",
                params,
            ).fetchall()
            row = _unique_job_row(rows)
        else:
            platform_filter = " AND d.platform = ?" if platform else ""
            params = [sample_name, job_id]
            if platform:
                params.append(platform)
            rows = self.conn.execute(
                """
                SELECT s.sample_bucket, s.position, d.*, a.analyzed_at, a.track,
                    a.fit_tier, a.relevance_score, a.fde_value_score,
                    a.salary_kind, a.salary_min_k, a.salary_max_k, a.salary_months,
                    a.risks_json, a.reasons_json, decision.interest,
                    decision.rejection_reason, decision.note, decision.learning_intent,
                    decision.decided_at
            FROM job_review_samples s
            JOIN job_details d
              ON d.platform = s.platform AND d.job_id = s.job_id
            JOIN job_positioning_analysis a
              ON a.platform = d.platform AND a.job_id = d.job_id
            LEFT JOIN job_decisions decision
              ON decision.platform = d.platform AND decision.job_id = d.job_id
            WHERE s.sample_name = ? AND d.job_id = ?
                """
                + platform_filter
                + " ORDER BY d.platform",
                params,
            ).fetchall()
            row = _unique_job_row(rows)
        if row is None:
            return None

        result = dict(row)
        if sample_name == REVIEWED_DECISIONS_SAMPLE:
            result["fit_tier"] = {
                "priority": "core",
                "challenge": "stretch",
                "not_fit": "exclude",
            }.get(str(result.get("recommendation") or ""), "")
            result["relevance_score"] = int(result.get("score") or 0)
            result["fde_value_score"] = 0
            salary = parse_salary(str(result.get("salary_raw") or ""))
            result["salary_kind"] = salary.kind
            result["salary_min_k"] = salary.minimum_k
            result["salary_max_k"] = salary.maximum_k
            result["salary_months"] = salary.pay_months
            result["risks_json"] = "[]"
            result["reasons_json"] = result.get("uncertainties_json") or "[]"
            result["sample_bucket"] = {
                "core": "core",
                "adjacent": "core",
                "stretch": "stretch",
                "exclude": "exclude",
            }.get(str(result.get("fit_tier") or ""), "core")
        result["skills"] = _json_list(result.get("skills_json"))
        result["system_labels"] = _json_list(result.get("labels_json"))
        result["risks"] = _json_list(result.get("risks_json"))
        result["reasons"] = _json_list(result.get("reasons_json"))
        result["company_profile"] = extract_company_profile(result)
        result["evidence"] = extract_evidence(result)
        result["system_analysis"] = build_system_analysis(result)
        if result["decided_at"] is None:
            result["decision"] = None
        else:
            result["decision"] = {
                "interest": result["interest"],
                "rejection_reason": result["rejection_reason"],
                "note": result["note"],
                "learning_intent": (
                    None
                    if result["learning_intent"] is None
                    else bool(result["learning_intent"])
                ),
                "learning_suggested": (
                    result["learning_intent"] is None
                    and note_requests_learning(result["note"])
                ),
                "decided_at": result["decided_at"],
            }
        corrections = self.conn.execute(
            """
            SELECT category, note, analysis_version, corrected_at
            FROM job_corrections
            WHERE platform = ? AND job_id = ?
            ORDER BY corrected_at, category
            """,
            (result["platform"], result["job_id"]),
        ).fetchall()
        result["corrections"] = [dict(correction) for correction in corrections]
        return result

    def _sample_job(
        self,
        job_id: str,
        sample_name: str,
        platform: str | None = None,
    ) -> sqlite3.Row:
        if sample_name == REVIEWED_DECISIONS_SAMPLE:
            platform_filter = " AND decision.platform = ?" if platform else ""
            params = [job_id]
            if platform:
                params.append(platform)
            rows = self.conn.execute(
                """
                WITH latest AS (
                  SELECT analysis_version, profile_id, profile_version
                    FROM job_match_analyses
                   ORDER BY analyzed_at DESC, rowid DESC
                   LIMIT 1
                )
                SELECT decision.platform, match.analyzed_at
                  FROM job_decisions decision
                  JOIN job_match_analyses match
                    ON match.platform = decision.platform
                   AND match.job_id = decision.job_id
                  JOIN latest
                    ON latest.analysis_version = match.analysis_version
                   AND latest.profile_id = match.profile_id
                   AND latest.profile_version = match.profile_version
                 WHERE decision.job_id = ?
                """
                + platform_filter
                + " ORDER BY decision.platform",
                params,
            ).fetchall()
            job = _unique_job_row(rows)
            if job is None:
                raise ReviewValidationError("岗位没有可返回的历史审阅")
            return job
        platform_filter = " AND s.platform = ?" if platform else ""
        params = [sample_name, job_id]
        if platform:
            params.append(platform)
        rows = self.conn.execute(
            """
            SELECT s.platform, a.analyzed_at
            FROM job_review_samples s
            JOIN job_positioning_analysis a
              ON a.platform = s.platform AND a.job_id = s.job_id
            WHERE s.sample_name = ? AND s.job_id = ?
            """
            + platform_filter
            + " ORDER BY s.platform",
            params,
        ).fetchall()
        job = _unique_job_row(rows)
        if job is None:
            raise ReviewValidationError("岗位不在当前审阅样本中")
        return job

    def save_decision(
        self,
        job_id: str,
        payload: dict,
        sample_name: str = "positioning-v1",
        platform: str | None = None,
    ) -> dict:
        interest = str(payload.get("interest") or "")
        if interest not in INTEREST_VALUES:
            raise ReviewValidationError("请选择想投、再看或放弃")
        learning_value = payload.get("learning_intent")
        if learning_value is not None and not isinstance(learning_value, bool):
            raise ReviewValidationError("学习清单选项格式不正确")
        decision = {
            "interest": interest,
            "rejection_reason": str(payload.get("rejection_reason") or "").strip(),
            "note": str(payload.get("note") or "").strip(),
            "learning_intent": None if learning_value is None else int(learning_value),
        }
        job = self._sample_job(job_id, sample_name, platform=platform)
        decided_at = _now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO job_decisions (
                  platform, job_id, interest, rejection_reason, note,
                  learning_intent, decided_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(platform, job_id) DO UPDATE SET
                  interest = excluded.interest,
                  rejection_reason = excluded.rejection_reason,
                  note = excluded.note,
                  learning_intent = COALESCE(
                    excluded.learning_intent, job_decisions.learning_intent
                  ),
                  decided_at = excluded.decided_at
                """,
                (
                    job["platform"],
                    job_id,
                    decision["interest"],
                    decision["rejection_reason"],
                    decision["note"],
                    decision["learning_intent"],
                    decided_at,
                ),
            )
        stored_learning = self.conn.execute(
            "SELECT learning_intent FROM job_decisions WHERE platform = ? AND job_id = ?",
            (job["platform"], job_id),
        ).fetchone()[0]
        return {
            **decision,
            "learning_intent": None if stored_learning is None else bool(stored_learning),
            "decided_at": decided_at,
        }

    def save_correction(
        self,
        job_id: str,
        payload: dict,
        sample_name: str = "positioning-v1",
        platform: str | None = None,
    ) -> dict:
        category = str(payload.get("category") or "")
        if category not in CORRECTION_CATEGORIES:
            raise ReviewValidationError("请选择需要纠正的系统判断")
        note = str(payload.get("note") or "").strip()
        if not note:
            raise ReviewValidationError("请说明系统判断哪里不准确")
        job = self._sample_job(job_id, sample_name, platform=platform)
        corrected_at = _now()
        correction = {
            "category": category,
            "note": note,
            "analysis_version": str(job["analyzed_at"] or ""),
            "corrected_at": corrected_at,
        }
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO job_corrections (
                  platform, job_id, category, note, analysis_version, corrected_at
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(platform, job_id, category) DO UPDATE SET
                  note = excluded.note,
                  analysis_version = excluded.analysis_version,
                  corrected_at = excluded.corrected_at
                """,
                (
                    job["platform"],
                    job_id,
                    category,
                    note,
                    correction["analysis_version"],
                    corrected_at,
                ),
            )
        return correction

    def learning_dashboard(self) -> dict:
        return build_learning_dashboard(self.conn)

    def match_dashboard(self) -> dict:
        return build_match_dashboard(self.conn)

    def match_detail(self, job_id: str, platform: str | None = None) -> dict | None:
        return get_match_detail(self.conn, job_id, platform=platform)

    def boundary_dashboard(self, limit_per_group: int = 10) -> dict:
        return build_boundary_dashboard(self.conn, limit_per_group=limit_per_group)

    def calibration_dashboard(self) -> dict:
        return build_calibration_dashboard(self.conn)

    def calibrate_resume(
        self,
        resume_text: str,
        filename: str = "resume.md",
        candidate_id: str = "local-candidate",
        analysis_version: str = "resume-match-v1",
    ) -> dict:
        self.conn.close()
        try:
            return calibrate_resume(
                self.db_path,
                resume_text,
                filename=filename,
                candidate_id=candidate_id,
                analysis_version=analysis_version,
            )
        finally:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row

    def stats(self, sample_name: str = "positioning-v1") -> dict:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(decision.decided_at) AS reviewed,
                   SUM(CASE WHEN decision.interest = 'want' THEN 1 ELSE 0 END) AS wanted,
                   SUM(CASE WHEN decision.interest = 'maybe' THEN 1 ELSE 0 END) AS maybe,
                   SUM(CASE WHEN decision.interest = 'reject' THEN 1 ELSE 0 END) AS rejected
            FROM job_review_samples s
            LEFT JOIN job_decisions decision
              ON decision.platform = s.platform AND decision.job_id = s.job_id
            WHERE s.sample_name = ?
            """,
            (sample_name,),
        ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}

    def close(self) -> None:
        self.conn.close()

"""Local, resumable storage for read-only job collection."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from jobagent.freshness import (
    mark_detail_result,
    mark_observation_seen,
    mark_scan_missing,
    migrate_freshness_schema,
    sync_legacy_freshness,
)
from urllib.parse import urlsplit, urlunsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BOSS_DIGIT_TRANSLATION = str.maketrans(
    {chr(0xE031 + digit): str(digit) for digit in range(10)}
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_job_url(url: str) -> str:
    """Drop signed query parameters before persisting a Boss job URL."""
    value = str(url or "")
    if not value:
        return ""
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def normalize_search_card(card: dict) -> dict:
    """Normalize the compact search-card layout and its private-use salary digits."""
    result = dict(card)
    result["salary_raw"] = str(result.get("salary_raw") or "").translate(
        _BOSS_DIGIT_TRANSLATION
    )
    lines = [
        line.strip()
        for line in str(result.get("card_text") or "").splitlines()
        if line.strip()
    ]
    for field, index in (
        ("title", 0),
        ("salary_raw", 1),
        ("experience_raw", 2),
        ("education_raw", 3),
        ("company", 4),
        ("location", 5),
    ):
        if not str(result.get(field) or "").strip() and len(lines) > index:
            result[field] = lines[index]
    result["salary_raw"] = str(result.get("salary_raw") or "").translate(
        _BOSS_DIGIT_TRANSLATION
    )
    return result


def _contains(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def classify_job(job: dict) -> list[str]:
    """Create coarse offline labels without deciding whether to apply."""
    title = str(job.get("title") or "")
    jd = str(job.get("jd") or job.get("jd_text") or "")
    recruiter = " ".join(
        str(job.get(field) or "")
        for field in ("recruiter_name", "recruiter_role", "recruiter_context")
    )
    text = f"{title} {jd}"
    labels: list[str] = []

    if _contains((r"\bFDE\b", r"前线部署", r"Forward Deployed"), text):
        labels.append("fde")
    if _contains((r"\bAgent\b", r"智能体", r"\bRAG\b", r"大模型"), text):
        labels.append("agent_rag")

    has_ai = _contains((r"\bAI\b", r"人工智能", r"大模型", r"智能体", r"\bAgent\b", r"\bRAG\b"), text)
    if _contains((r"\bJava\b", r"Spring\s*Boot", r"MyBatis"), text) and not has_ai:
        labels.append("pure_java")

    outsourcing = _contains(
        (
            r"外包",
            r"驻场",
            r"劳务派遣",
            r"外派",
            r"客户现场(?:办公|工作|驻场)",
            r"第三方(?:用工|签约|劳动合同)",
        ),
        f"{text} {recruiter}",
    )
    third_party = _contains(
        (r"猎头", r"代招", r"\bRPO\b", r"招聘顾问", r"人力资源顾问"),
        f"{text} {recruiter}",
    )
    if outsourcing:
        labels.append("outsourcing_risk")
    if third_party:
        labels.append("third_party")
    if not outsourcing and not third_party:
        labels.append("direct")
    return labels


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,、]", value) if part.strip()]
    return []


def job_from_detail_payload(job: dict, payload: dict) -> dict:
    """Extract only positioning-relevant fields from Boss detail.json."""
    data = payload.get("zpData") or {}
    job_info = data.get("jobInfo") or {}
    brand = data.get("brandComInfo") or {}
    boss = data.get("bossInfo") or {}

    job_id = str(job_info.get("encryptId") or job.get("job_id") or "")
    location = "·".join(
        str(value).strip()
        for value in (
            job_info.get("cityName"),
            job_info.get("areaDistrict"),
            job_info.get("businessDistrict"),
        )
        if str(value or "").strip()
    )
    result = {
        **job,
        "job_id": job_id,
        "title": str(job_info.get("jobName") or job.get("title") or "").strip(),
        "company": str(brand.get("brandName") or job.get("company") or "").strip(),
        "salary_raw": str(job_info.get("salaryDesc") or job.get("salary_raw") or "").strip(),
        "location": location or str(job.get("location") or "").strip(),
        "experience_raw": str(job_info.get("experienceName") or job.get("experience_raw") or "").strip(),
        "education_raw": str(job_info.get("degreeName") or job.get("education_raw") or "").strip(),
        "jd": str(job_info.get("postDescription") or "").strip(),
        "skills": _as_list(job_info.get("skills") or job_info.get("showSkills")),
        "welfare": _as_list(job_info.get("welfareList")),
        "industry": str(brand.get("industryName") or job.get("industry") or "").strip(),
        "financing": str(brand.get("stageName") or job.get("financing") or "").strip(),
        "company_size": str(brand.get("scaleName") or job.get("company_size") or "").strip(),
        "recruiter_name": str(boss.get("name") or job.get("recruiter_name") or "").strip(),
        "recruiter_role": str(boss.get("title") or job.get("recruiter_role") or "").strip(),
        "recruiter_active": str(boss.get("activeTimeDesc") or "").strip(),
    }
    if job_id:
        result["source_url"] = canonical_job_url(
            job.get("source_url") or f"https://www.zhipin.com/job_detail/{job_id}.html"
        )
    return result


class CollectionStore:
    def __init__(self, db_path: str | Path = "output/jobagent.db"):
        path = Path(db_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS job_observations (
              platform TEXT NOT NULL,
              job_id TEXT NOT NULL,
              source TEXT NOT NULL,
              source_key TEXT,
              source_run_id TEXT NOT NULL,
              source_page INTEGER NOT NULL,
              source_url TEXT NOT NULL,
              title TEXT,
              location TEXT,
              salary_raw TEXT,
              experience_raw TEXT,
              education_raw TEXT,
              company TEXT,
              industry TEXT,
              financing TEXT,
              company_size TEXT,
              recruiter_name TEXT,
              recruiter_role TEXT,
              card_text TEXT,
              collected_at TEXT NOT NULL,
              PRIMARY KEY (platform, job_id)
            );
            CREATE TABLE IF NOT EXISTS job_details (
              platform TEXT NOT NULL,
              job_id TEXT NOT NULL,
              source_run_id TEXT NOT NULL,
              source_url TEXT NOT NULL,
              source_page INTEGER NOT NULL,
              title TEXT,
              company TEXT,
              detail_status TEXT NOT NULL,
              jd_text TEXT,
              detail_text TEXT,
              collected_at TEXT NOT NULL,
              PRIMARY KEY(platform, job_id)
            );
            CREATE TABLE IF NOT EXISTS detail_collection_runs (
              run_id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              records_collected INTEGER DEFAULT 0,
              status TEXT NOT NULL,
              error TEXT
            );
            CREATE TABLE IF NOT EXISTS collection_cursors (
              source_key TEXT PRIMARY KEY,
              page_number INTEGER NOT NULL DEFAULT 1,
              item_index INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        self._add_columns(
            "job_observations",
            {
                "source_key": "TEXT",
            },
        )
        self._add_columns(
            "job_details",
            {
                "source_key": "TEXT",
                "salary_raw": "TEXT",
                "location": "TEXT",
                "experience_raw": "TEXT",
                "education_raw": "TEXT",
                "industry": "TEXT",
                "financing": "TEXT",
                "company_size": "TEXT",
                "recruiter_name": "TEXT",
                "recruiter_role": "TEXT",
                "recruiter_active": "TEXT",
                "skills_json": "TEXT",
                "welfare_json": "TEXT",
                "labels_json": "TEXT",
                "last_error": "TEXT",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "updated_at": "TEXT",
            },
        )
        self._add_columns(
            "detail_collection_runs",
            {
                "target_count": "INTEGER",
                "starting_count": "INTEGER",
                "finished_count": "INTEGER",
            },
        )
        migrate_freshness_schema(self.conn)
        sync_legacy_freshness(self.conn)
        self.conn.commit()

    def _add_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def start_run(self, target_count: int, source: str = "boss-collect-only") -> str:
        run_id = f"job-details-{datetime.now().astimezone():%Y%m%d-%H%M%S-%f}"
        starting_count = self.detail_inventory_count()
        self.conn.execute(
            """
            INSERT INTO detail_collection_runs (
              run_id, source, started_at, records_collected, status,
              target_count, starting_count, finished_count
            ) VALUES (?, ?, ?, 0, 'running', ?, ?, ?)
            """,
            (run_id, source, _now(), int(target_count), starting_count, starting_count),
        )
        self.conn.commit()
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        error: str | None = None,
        records_collected: int | None = None,
    ) -> None:
        row = self.get_run(run_id)
        starting_count = int(row["starting_count"] or 0) if row else 0
        finished_count = self.detail_inventory_count()
        records = (
            max(0, finished_count - starting_count)
            if records_collected is None
            else max(0, int(records_collected))
        )
        self.conn.execute(
            """
            UPDATE detail_collection_runs
               SET finished_at = ?, records_collected = ?, status = ?, error = ?,
                   finished_count = ?
             WHERE run_id = ?
            """,
            (
                _now(),
                records,
                status,
                error,
                finished_count,
                run_id,
            ),
        )
        self.conn.commit()

    def get_run(self, run_id: str):
        return self.conn.execute(
            "SELECT * FROM detail_collection_runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def run_metrics(self, run_id: str) -> dict:
        """Return auditable counts for one collection run without inferring missing data."""
        try:
            rows = self.conn.execute(
                "SELECT detail_status, COUNT(*) AS count FROM job_details WHERE source_run_id=? GROUP BY detail_status",
                (run_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        counts = {str(row["detail_status"]): int(row["count"]) for row in rows}
        events = self.conn.execute(
            "SELECT result, COUNT(*) AS count FROM job_refresh_events WHERE run_id=? GROUP BY result",
            (run_id,),
        ).fetchall() if self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_refresh_events'"
        ).fetchone() else []
        results = {str(row["result"]): int(row["count"]) for row in events if row["result"]}
        return {
            "attempted": sum(counts.values()),
            "detail_success": counts.get("collected", 0),
            "detail_failed": counts.get("failed", 0),
            "detail_closed": counts.get("closed", 0),
            "changed": results.get("changed", 0),
            "unchanged": results.get("unchanged", 0),
            "refresh_result_unknown": results.get("unknown", 0),
        }

    def save_observation(self, job: dict, run_id: str) -> None:
        source_url = canonical_job_url(job.get("source_url") or job.get("href") or "")
        values = (
            job.get("platform", "boss"),
            job["job_id"],
            job.get("source", "boss-search"),
            job.get("source_key") or job.get("source", "boss-search"),
            run_id,
            int(job.get("source_page", 1)),
            source_url,
            job.get("title", ""),
            job.get("location", ""),
            job.get("salary_raw", ""),
            job.get("experience_raw", ""),
            job.get("education_raw", ""),
            job.get("company", ""),
            job.get("industry", ""),
            job.get("financing", ""),
            job.get("company_size", ""),
            job.get("recruiter_name", ""),
            job.get("recruiter_role", ""),
            job.get("card_text", ""),
            _now(),
        )
        try:
            self.conn.execute(
                """
            INSERT INTO job_observations (
              platform, job_id, source, source_key, source_run_id, source_page,
              source_url, title, location, salary_raw, experience_raw,
              education_raw, company, industry, financing, company_size,
              recruiter_name, recruiter_role, card_text, collected_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform, job_id) DO UPDATE SET
              source=excluded.source,
              source_key=excluded.source_key,
              source_run_id=excluded.source_run_id,
              source_page=excluded.source_page,
              source_url=excluded.source_url,
              title=excluded.title,
              location=excluded.location,
              salary_raw=excluded.salary_raw,
              experience_raw=excluded.experience_raw,
              education_raw=excluded.education_raw,
              company=excluded.company,
              recruiter_name=excluded.recruiter_name,
              recruiter_role=excluded.recruiter_role,
              card_text=excluded.card_text,
              collected_at=excluded.collected_at
                """,
                values,
            )
            mark_observation_seen(self.conn, job, run_id, commit=False)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def save_detail(
        self,
        job: dict,
        run_id: str,
        detail_status: str = "collected",
        error: str = "",
    ) -> None:
        now = _now()
        labels = classify_job(job)
        try:
            mark_detail_result(
                self.conn,
                job,
                run_id,
                detail_status,
                error=error,
                observed_at=now,
                commit=False,
            )
            self.conn.execute(
                """
            INSERT INTO job_details (
              platform, job_id, source_run_id, source_url, source_page,
              title, company, detail_status, jd_text, detail_text, collected_at,
              source_key, salary_raw, location, experience_raw, education_raw,
              industry, financing, company_size, recruiter_name, recruiter_role,
              recruiter_active, skills_json, welfare_json, labels_json,
              last_error, attempt_count, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform, job_id) DO UPDATE SET
              source_run_id=excluded.source_run_id,
              source_url=excluded.source_url,
              source_page=excluded.source_page,
              title=excluded.title,
              company=excluded.company,
              detail_status=excluded.detail_status,
              jd_text=CASE WHEN excluded.detail_status IN ('failed', 'closed') AND job_details.detail_status = 'collected' THEN job_details.jd_text ELSE excluded.jd_text END,
              detail_text=CASE WHEN excluded.detail_status IN ('failed', 'closed') AND job_details.detail_status = 'collected' THEN job_details.detail_text ELSE excluded.detail_text END,
              source_key=excluded.source_key,
              salary_raw=excluded.salary_raw,
              location=excluded.location,
              experience_raw=excluded.experience_raw,
              education_raw=excluded.education_raw,
              industry=excluded.industry,
              financing=excluded.financing,
              company_size=excluded.company_size,
              recruiter_name=excluded.recruiter_name,
              recruiter_role=excluded.recruiter_role,
              recruiter_active=excluded.recruiter_active,
              skills_json=CASE WHEN excluded.detail_status IN ('failed', 'closed') AND job_details.detail_status = 'collected' THEN job_details.skills_json ELSE excluded.skills_json END,
              welfare_json=CASE WHEN excluded.detail_status IN ('failed', 'closed') AND job_details.detail_status = 'collected' THEN job_details.welfare_json ELSE excluded.welfare_json END,
              labels_json=CASE WHEN excluded.detail_status IN ('failed', 'closed') AND job_details.detail_status = 'collected' THEN job_details.labels_json ELSE excluded.labels_json END,
              last_error=NULL,
              attempt_count=job_details.attempt_count + 1,
              updated_at=excluded.updated_at
                """,
                (
                job.get("platform", "boss"),
                job["job_id"],
                run_id,
                canonical_job_url(job.get("source_url") or job.get("href") or ""),
                int(job.get("source_page", 1)),
                job.get("title", ""),
                job.get("company", ""),
                detail_status,
                job.get("jd", ""),
                job.get("detail_text", ""),
                now,
                job.get("source_key", ""),
                job.get("salary_raw", ""),
                job.get("location", ""),
                job.get("experience_raw", ""),
                job.get("education_raw", ""),
                job.get("industry", ""),
                job.get("financing", ""),
                job.get("company_size", ""),
                job.get("recruiter_name", ""),
                job.get("recruiter_role", ""),
                job.get("recruiter_active", ""),
                json.dumps(job.get("skills", []), ensure_ascii=False),
                json.dumps(job.get("welfare", []), ensure_ascii=False),
                json.dumps(labels, ensure_ascii=False),
                None,
                1,
                now,
                ),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def save_failure(self, job: dict, run_id: str, error: str) -> None:
        failed = {**job, "jd": "", "detail_text": job.get("detail_text", "")}
        self.save_detail(failed, run_id, detail_status="failed", error=error)
        self.conn.execute(
            "UPDATE job_details SET last_error = ? WHERE platform = ? AND job_id = ?",
            (str(error)[:1000], job.get("platform", "boss"), job["job_id"]),
        )
        self.conn.commit()

    def finalize_scan(
        self,
        source_key: str,
        listed_job_ids: set[str],
        run_id: str,
        *,
        observed_at: str | None = None,
    ) -> None:
        """Record one completed source scan without deleting absent jobs."""
        rows = self.conn.execute(
            "SELECT * FROM job_details WHERE source_key = ?",
            (source_key,),
        ).fetchall()
        listed_ids = {str(job_id) for job_id in listed_job_ids}
        try:
            for row in rows:
                if str(row["job_id"]) in listed_ids:
                    continue
                mark_scan_missing(
                    self.conn,
                    dict(row),
                    run_id,
                    observed_at=observed_at,
                    commit=False,
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def update_cursor(
        self, source_key: str, page_number: int, item_index: int, status: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO collection_cursors VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
              page_number=excluded.page_number,
              item_index=excluded.item_index,
              status=excluded.status,
              updated_at=excluded.updated_at
            """,
            (source_key, int(page_number), int(item_index), status, _now()),
        )
        self.conn.commit()

    def get_cursor(self, source_key: str):
        return self.conn.execute(
            "SELECT * FROM collection_cursors WHERE source_key = ?", (source_key,)
        ).fetchone()

    def get_detail(self, job_id: str):
        return self.conn.execute(
            "SELECT * FROM job_details WHERE platform = 'boss' AND job_id = ?", (job_id,)
        ).fetchone()

    def get_refresh_target(self, job_id: str):
        """Return a detail-shaped row for an existing detail or card observation."""
        normalized_id = str(job_id or "").strip()
        if not normalized_id:
            return None
        detail = self.get_detail(normalized_id)
        if detail is not None:
            return detail
        observation = self.conn.execute(
            "SELECT * FROM job_observations WHERE platform = 'boss' AND job_id = ?",
            (normalized_id,),
        ).fetchone()
        if observation is None:
            return None
        source_url = canonical_job_url(
            observation["source_url"]
            or f"https://www.zhipin.com/job_detail/{normalized_id}.html"
        )
        return {
            "platform": observation["platform"],
            "job_id": observation["job_id"],
            "source_run_id": observation["source_run_id"],
            "source_url": source_url,
            "source_page": observation["source_page"],
            "title": observation["title"] or "",
            "company": observation["company"] or "",
            "detail_status": "missing",
            "jd_text": "",
            "detail_text": observation["card_text"] or "",
            "collected_at": observation["collected_at"],
            "source_key": observation["source_key"] or observation["source"] or "refresh",
            "salary_raw": observation["salary_raw"] or "",
            "location": observation["location"] or "",
            "experience_raw": observation["experience_raw"] or "",
            "education_raw": observation["education_raw"] or "",
            "industry": observation["industry"] or "",
            "financing": observation["financing"] or "",
            "company_size": observation["company_size"] or "",
            "recruiter_name": observation["recruiter_name"] or "",
            "recruiter_role": observation["recruiter_role"] or "",
            "recruiter_active": "",
            "skills_json": "[]",
            "welfare_json": "[]",
            "labels_json": "[]",
            "last_error": None,
            "attempt_count": 0,
            "updated_at": observation["collected_at"],
        }

    def has_collected_detail(self, job_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM job_details
             WHERE platform = 'boss' AND job_id = ? AND detail_status = 'collected'
            """,
            (job_id,),
        ).fetchone()
        return row is not None

    def collected_detail_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM job_details WHERE detail_status = 'collected'"
        ).fetchone()
        return int(row[0])

    def detail_inventory_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM job_details").fetchone()
        return int(row[0])

    def close(self) -> None:
        self.conn.close()

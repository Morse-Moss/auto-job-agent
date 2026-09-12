"""Local evidence and refresh planning for collected job descriptions."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Iterable


VALID_STATES = {"active", "stale", "closed", "inaccessible", "archived"}
TERMINAL_STATES = {"closed", "archived"}
LIVE_STATES = {"active", "stale", "inaccessible"}
SCAN_STATES = {"listed", "unseen", "likely_closed"}
ROLE_TRACK_STATES = {"active", "dormant", "sunset", "archived"}
ROLE_TRACK_DORMANT_DAYS = 30


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse(value: str | None, fallback: datetime | None = None) -> datetime | None:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hash_text(value: str | None) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _normalize_jd(value: str | None) -> str:
    """Ignore formatting-only changes when comparing JD content."""
    return re.sub(r"\s+", "", str(value or ""))


def _jd_hash(value: str | None) -> str:
    return _hash_text(_normalize_jd(value))


def jd_content_hash(value: str | None) -> str:
    """Return the canonical hash used to decide whether a JD materially changed."""
    return _jd_hash(value)


def migrate_freshness_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS job_freshness (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          evidence_state TEXT NOT NULL DEFAULT 'active',
          last_seen_at TEXT,
          last_checked_at TEXT,
          last_success_at TEXT,
          last_failure_at TEXT,
          content_hash TEXT,
          content_changed_at TEXT,
          last_error TEXT,
          closure_reason TEXT,
          scan_state TEXT NOT NULL DEFAULT 'listed',
          scan_miss_count INTEGER NOT NULL DEFAULT 0,
          last_listed_at TEXT,
          last_missing_at TEXT,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(platform, job_id),
          CHECK(evidence_state IN ('active', 'stale', 'closed', 'inaccessible', 'archived'))
        );
        CREATE TABLE IF NOT EXISTS job_refresh_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          run_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          from_state TEXT,
          to_state TEXT,
          occurred_at TEXT NOT NULL,
          result TEXT,
          content_hash TEXT,
          changed_fields_json TEXT NOT NULL DEFAULT '[]',
          message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS job_detail_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          source_run_id TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          title TEXT,
          company TEXT,
          salary_raw TEXT,
          location TEXT,
          experience_raw TEXT,
          education_raw TEXT,
          company_size TEXT,
          industry TEXT,
          financing TEXT,
          jd_text TEXT,
          detail_text TEXT,
          changed_fields_json TEXT NOT NULL DEFAULT '[]',
          UNIQUE(platform, job_id, content_hash)
        );
        CREATE TABLE IF NOT EXISTS company_role_tracks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          platform TEXT NOT NULL,
          company_key TEXT NOT NULL,
          company_name TEXT NOT NULL,
          role_family TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          first_seen_at TEXT,
          last_seen_at TEXT,
          missing_scan_count INTEGER NOT NULL DEFAULT 0,
          missing_since TEXT,
          reason TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL,
          UNIQUE(platform, company_key, role_family)
        );
        CREATE TABLE IF NOT EXISTS job_role_tracks (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          role_track_id INTEGER NOT NULL,
          linked_at TEXT NOT NULL,
          PRIMARY KEY(platform, job_id),
          FOREIGN KEY(role_track_id) REFERENCES company_role_tracks(id)
        );
        """
    )
    _add_columns(connection, "job_freshness", {
        "scan_state": "TEXT NOT NULL DEFAULT 'listed'",
        "scan_miss_count": "INTEGER NOT NULL DEFAULT 0",
        "last_listed_at": "TEXT",
        "last_missing_at": "TEXT",
    })
    _add_columns(connection, "job_detail_versions", {
        "salary_raw": "TEXT", "location": "TEXT", "experience_raw": "TEXT",
        "education_raw": "TEXT", "company_size": "TEXT", "industry": "TEXT",
        "financing": "TEXT",
    })
    connection.commit()


def _add_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def company_key_for_job(job: dict) -> str:
    value = str(job.get("company_key") or job.get("company") or "").strip().lower()
    return re.sub(r"\W+", "", value, flags=re.UNICODE) or "unknown-company"


def role_family_for_job(job: dict) -> str:
    text = " ".join(str(job.get(field) or "") for field in ("title", "jd", "labels"))
    if re.search(r"FDE|前线部署|AI交付|解决方案", text, re.IGNORECASE):
        return "fde_delivery"
    if re.search(r"Agent|智能体|RAG|大模型应用", text, re.IGNORECASE):
        return "ai_agent_application"
    if re.search(r"Java|Spring|MyBatis", text, re.IGNORECASE) and not re.search(
        r"AI|Agent|智能体|RAG|大模型", text, re.IGNORECASE
    ):
        return "java_backend"
    title = re.sub(r"\s+", " ", str(job.get("title") or "").strip().lower())
    return title or "other"


def upsert_role_track(
    connection: sqlite3.Connection,
    job: dict,
    *,
    observed_at: str | None = None,
    listed: bool = True,
) -> int:
    when = observed_at or _now()
    platform = str(job.get("platform") or "boss")
    company_key = company_key_for_job(job)
    company_name = str(job.get("company") or "未知公司").strip() or "未知公司"
    role_family = role_family_for_job(job)
    existing = connection.execute(
        "SELECT * FROM company_role_tracks WHERE platform = ? AND company_key = ? AND role_family = ?",
        (platform, company_key, role_family),
    ).fetchone()
    if existing:
        if listed:
            connection.execute(
                """UPDATE company_role_tracks
                   SET company_name = ?, status = 'active', last_seen_at = ?,
                       missing_scan_count = 0, missing_since = NULL, reason = '', updated_at = ?
                 WHERE id = ?""",
                (company_name, when, when, existing["id"]),
            )
        track_id = int(existing["id"])
    else:
        connection.execute(
            """INSERT INTO company_role_tracks
               (platform, company_key, company_name, role_family, status,
                first_seen_at, last_seen_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
            (platform, company_key, company_name, role_family, when if listed else None, when if listed else None, when),
        )
        track_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        """INSERT INTO job_role_tracks(platform, job_id, role_track_id, linked_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(platform, job_id) DO UPDATE SET role_track_id = excluded.role_track_id""",
        (platform, str(job["job_id"]), track_id, when),
    )
    return track_id


def mark_scan_missing(
    connection: sqlite3.Connection,
    job: dict,
    run_id: str,
    observed_at: str | None = None,
    *,
    commit: bool = True,
) -> None:
    platform = str(job.get("platform") or "boss")
    job_id = str(job["job_id"])
    when = observed_at or _now()
    current = _ensure_row(connection, platform, job_id, when)
    previous = current["scan_state"] if current and "scan_state" in current.keys() else "listed"
    count = int(current["scan_miss_count"] or 0) + 1 if current else 1
    next_scan_state = "likely_closed" if count >= 2 else "unseen"
    connection.execute(
        """UPDATE job_freshness
              SET scan_state = ?, scan_miss_count = ?, last_missing_at = ?, updated_at = ?
            WHERE platform = ? AND job_id = ?""",
        (next_scan_state, count, when, when, platform, job_id),
    )
    track_id = upsert_role_track(connection, job, observed_at=when, listed=False)
    connection.execute(
        """UPDATE company_role_tracks
              SET missing_scan_count = missing_scan_count + 1,
                  missing_since = COALESCE(missing_since, ?), updated_at = ?
            WHERE id = ?""",
        (when, when, track_id),
    )
    _append_event(connection, platform, job_id, run_id, "scan_missing", when,
                  from_state=previous, to_state=next_scan_state,
                  message="岗位未出现在本轮覆盖列表")
    if commit:
        connection.commit()


def build_role_track_dashboard(connection: sqlite3.Connection, *, now: str | None = None) -> dict:
    migrate_freshness_schema(connection)
    as_of = _parse(now, datetime.now().astimezone()) or datetime.now().astimezone()
    tracks = []
    rows = connection.execute("SELECT * FROM company_role_tracks ORDER BY company_name, role_family").fetchall()
    for row in rows:
        jobs = connection.execute(
            """SELECT jrt.job_id, jf.evidence_state, jf.scan_state
                 FROM job_role_tracks jrt
                 LEFT JOIN job_freshness jf ON jf.platform = jrt.platform AND jf.job_id = jrt.job_id
                WHERE jrt.role_track_id = ? ORDER BY jrt.job_id""",
            (row["id"],),
        ).fetchall()
        last_seen = _parse(row["last_seen_at"])
        age_days = (as_of - last_seen).total_seconds() / 86400 if last_seen else None
        status = row["status"]
        evidence_states = [job["evidence_state"] for job in jobs]
        if evidence_states and all(state in TERMINAL_STATES for state in evidence_states):
            status = "dormant"
        elif status == "active" and (age_days is None or age_days > ROLE_TRACK_DORMANT_DAYS):
            status = "dormant"
        tracks.append({**dict(row), "status": status, "age_days": None if age_days is None else round(max(0, age_days), 1),
                       "job_count": len(jobs), "job_ids": [job["job_id"] for job in jobs],
                       "closed_job_count": sum(job["evidence_state"] in {"closed", "archived"} for job in jobs)})
    return {"tracks": tracks, "summary": {state: sum(track["status"] == state for track in tracks) for state in ROLE_TRACK_STATES}, "as_of": as_of.isoformat(timespec="seconds")}


def _append_event(
    connection: sqlite3.Connection,
    platform: str,
    job_id: str,
    run_id: str,
    event_type: str,
    occurred_at: str,
    *,
    from_state: str | None = None,
    to_state: str | None = None,
    result: str | None = None,
    content_hash: str | None = None,
    changed_fields: Iterable[str] = (),
    message: str = "",
) -> None:
    connection.execute(
        """
        INSERT INTO job_refresh_events (
          platform, job_id, run_id, event_type, from_state, to_state,
          occurred_at, result, content_hash, changed_fields_json, message
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            platform,
            job_id,
            run_id,
            event_type,
            from_state,
            to_state,
            occurred_at,
            result,
            content_hash,
            json.dumps(sorted(set(changed_fields)), ensure_ascii=False),
            message[:1000],
        ),
    )


def _ensure_row(
    connection: sqlite3.Connection,
    platform: str,
    job_id: str,
    now: str | None = None,
) -> sqlite3.Row | None:
    row = connection.execute(
        "SELECT * FROM job_freshness WHERE platform = ? AND job_id = ?",
        (platform, job_id),
    ).fetchone()
    if row is not None:
        return row
    detail = None
    if _table_exists(connection, "job_details"):
        detail = connection.execute(
            "SELECT * FROM job_details WHERE platform = ? AND job_id = ?",
            (platform, job_id),
        ).fetchone()
    observation = None
    if detail is None and _table_exists(connection, "job_observations"):
        observation = connection.execute(
            "SELECT * FROM job_observations WHERE platform = ? AND job_id = ?",
            (platform, job_id),
        ).fetchone()
    timestamp = now or _now()
    state = "active" if (detail and detail["detail_status"] == "collected") or observation else "inaccessible"
    collected_at = detail["collected_at"] if detail else (observation["collected_at"] if observation else None)
    content_hash = _jd_hash(detail["jd_text"]) if detail and detail["jd_text"] else None
    connection.execute(
        """
        INSERT INTO job_freshness (
          platform, job_id, evidence_state, last_checked_at, last_success_at,
          content_hash, scan_state, last_listed_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (platform, job_id, state, collected_at if detail else None, collected_at if detail and state == "active" else None,
         content_hash, "listed", collected_at, timestamp),
    )
    return connection.execute(
        "SELECT * FROM job_freshness WHERE platform = ? AND job_id = ?",
        (platform, job_id),
    ).fetchone()


def sync_legacy_freshness(connection: sqlite3.Connection) -> None:
    """Backfill evidence rows from the current inventory without emitting events."""
    migrate_freshness_schema(connection)
    if not _table_exists(connection, "job_details") and not _table_exists(connection, "job_observations"):
        return
    rows = connection.execute("SELECT * FROM job_details").fetchall() if _table_exists(connection, "job_details") else []
    for row in rows:
        _ensure_row(connection, row["platform"], row["job_id"])
        linked = connection.execute(
            "SELECT 1 FROM job_role_tracks WHERE platform = ? AND job_id = ?",
            (row["platform"], row["job_id"]),
        ).fetchone()
        if not linked:
            job = dict(row)
            job["jd"] = job.get("jd_text", "")
            upsert_role_track(
                connection,
                job,
                observed_at=job.get("collected_at") or _now(),
                listed=True,
            )
    if _table_exists(connection, "job_observations"):
        observation_rows = connection.execute(
            """SELECT o.* FROM job_observations o
               LEFT JOIN job_details d ON d.platform = o.platform AND d.job_id = o.job_id
              WHERE d.job_id IS NULL"""
        ).fetchall()
        for row in observation_rows:
            _ensure_row(connection, row["platform"], row["job_id"])
            job = dict(row)
            job["jd"] = ""
            upsert_role_track(connection, job, observed_at=job.get("collected_at") or _now(), listed=True)
    connection.commit()


def set_lifecycle_state(
    connection: sqlite3.Connection,
    platform: str,
    job_id: str,
    state: str,
    reason: str = "",
    *,
    run_id: str = "manual",
    occurred_at: str | None = None,
) -> None:
    if state not in VALID_STATES:
        raise ValueError(f"unknown freshness state: {state}")
    when = occurred_at or _now()
    current = _ensure_row(connection, platform, job_id, when)
    previous = current["evidence_state"] if current else None
    connection.execute(
        """
        UPDATE job_freshness
           SET evidence_state = ?, closure_reason = ?, updated_at = ?
         WHERE platform = ? AND job_id = ?
        """,
        (state, reason[:1000] if state in TERMINAL_STATES else "", when, platform, job_id),
    )
    if previous != state:
        _append_event(
            connection, platform, job_id, run_id, "state_changed", when,
            from_state=previous, to_state=state, message=reason,
        )
    connection.commit()


def mark_observation_seen(
    connection: sqlite3.Connection,
    job: dict,
    run_id: str,
    observed_at: str | None = None,
    *,
    commit: bool = True,
) -> None:
    platform = str(job.get("platform") or "boss")
    job_id = str(job["job_id"])
    when = observed_at or _now()
    current = _ensure_row(connection, platform, job_id, when)
    previous = current["evidence_state"] if current else None
    next_state = "active"
    connection.execute(
        """
        UPDATE job_freshness
           SET evidence_state = ?, last_seen_at = ?, updated_at = ?,
               closure_reason = '', scan_state = 'listed', scan_miss_count = 0,
               last_listed_at = ?, last_missing_at = NULL
         WHERE platform = ? AND job_id = ?
        """,
        (next_state, when, when, when, platform, job_id),
    )
    upsert_role_track(connection, job, observed_at=when, listed=True)
    if previous != next_state:
        _append_event(
            connection, platform, job_id, run_id, "state_changed", when,
            from_state=previous, to_state=next_state,
        )
    _append_event(connection, platform, job_id, run_id, "seen", when, to_state=next_state)
    if commit:
        connection.commit()


def mark_detail_result(
    connection: sqlite3.Connection,
    job: dict,
    run_id: str,
    result: str,
    *,
    error: str = "",
    observed_at: str | None = None,
    commit: bool = True,
) -> None:
    platform = str(job.get("platform") or "boss")
    job_id = str(job["job_id"])
    when = observed_at or _now()
    current = _ensure_row(connection, platform, job_id, when)
    previous_state = current["evidence_state"] if current else None
    if result == "collected":
        next_state = "active"
        content_hash = _jd_hash(job.get("jd"))
        changed_fields: list[str] = []
        old_detail = None
        if _table_exists(connection, "job_details"):
            old_detail = connection.execute(
                "SELECT * FROM job_details WHERE platform = ? AND job_id = ?",
                (platform, job_id),
            ).fetchone()
        field_map = {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "salary_raw": job.get("salary_raw", ""),
            "location": job.get("location", ""),
            "experience_raw": job.get("experience_raw", ""),
            "education_raw": job.get("education_raw", ""),
            "company_size": job.get("company_size", ""),
            "industry": job.get("industry", ""),
            "financing": job.get("financing", ""),
            "jd_text": job.get("jd", ""),
            "detail_text": job.get("detail_text", ""),
        }
        if old_detail:
            for field, value in field_map.items():
                old_value = old_detail[field] if field in old_detail.keys() else None
                changed = (
                    _normalize_jd(old_value) != _normalize_jd(value)
                    if field == "jd_text"
                    else str(old_value or "") != str(value or "")
                )
                if field in old_detail.keys() and changed:
                    changed_fields.append(field)
            old_hash = _jd_hash(old_detail["jd_text"])
            if old_detail["jd_text"] and old_hash != content_hash:
                old_columns = set(old_detail.keys())
                connection.execute(
                    """
                    INSERT OR IGNORE INTO job_detail_versions (
                      platform, job_id, source_run_id, captured_at, content_hash,
                      title, company, salary_raw, location, experience_raw, education_raw,
                      company_size, industry, financing, jd_text, detail_text, changed_fields_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        platform,
                        job_id,
                        old_detail["source_run_id"] if "source_run_id" in old_columns else "legacy",
                        old_detail["collected_at"] if "collected_at" in old_columns else when,
                        old_hash,
                        old_detail["title"],
                        old_detail["company"],
                        old_detail["salary_raw"] if "salary_raw" in old_columns else "",
                        old_detail["location"] if "location" in old_columns else "",
                        old_detail["experience_raw"] if "experience_raw" in old_columns else "",
                        old_detail["education_raw"] if "education_raw" in old_columns else "",
                        old_detail["company_size"] if "company_size" in old_columns else "",
                        old_detail["industry"] if "industry" in old_columns else "",
                        old_detail["financing"] if "financing" in old_columns else "",
                        old_detail["jd_text"],
                        old_detail["detail_text"],
                        "[]",
                    ),
                )
        connection.execute(
            """
            UPDATE job_freshness
               SET evidence_state = ?, last_checked_at = ?, last_success_at = ?,
                   last_failure_at = NULL, last_error = '', content_hash = ?,
                   content_changed_at = CASE WHEN COALESCE(content_hash, '') <> ? THEN ? ELSE content_changed_at END,
                   updated_at = ?, closure_reason = '', scan_state = 'listed', scan_miss_count = 0,
                   last_listed_at = ?, last_missing_at = NULL
             WHERE platform = ? AND job_id = ?
            """,
            (next_state, when, when, content_hash, content_hash, when, when,
             when, platform, job_id),
        )
        if _table_exists(connection, "job_detail_versions"):
            connection.execute(
                """
                INSERT OR IGNORE INTO job_detail_versions (
                  platform, job_id, source_run_id, captured_at, content_hash,
                  title, company, salary_raw, location, experience_raw, education_raw,
                  company_size, industry, financing, jd_text, detail_text, changed_fields_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (platform, job_id, run_id, when, content_hash, field_map["title"],
                 field_map["company"], field_map["salary_raw"], field_map["location"],
                 field_map["experience_raw"], field_map["education_raw"], field_map["company_size"],
                 field_map["industry"], field_map["financing"], field_map["jd_text"], field_map["detail_text"],
                 json.dumps(changed_fields, ensure_ascii=False)),
            )
        upsert_role_track(connection, job, observed_at=when, listed=True)
        event_type = "detail_collected"
        message = ""
    elif result == "closed":
        next_state = "closed"
        content_hash = None
        changed_fields = []
        message = error or "招聘页面明确显示岗位已关闭"
        connection.execute(
            """
            UPDATE job_freshness
               SET evidence_state = 'closed', last_checked_at = ?, last_error = '',
                   closure_reason = ?, updated_at = ?
             WHERE platform = ? AND job_id = ?
            """,
            (when, message[:1000], when, platform, job_id),
        )
        event_type = "detail_closed"
    else:
        next_state = previous_state if previous_state in TERMINAL_STATES else "inaccessible"
        content_hash = None
        changed_fields = []
        message = error or "详情刷新失败"
        connection.execute(
            """
            UPDATE job_freshness
               SET evidence_state = ?, last_checked_at = ?, last_failure_at = ?,
                   last_error = ?, updated_at = ?
             WHERE platform = ? AND job_id = ?
            """,
            (next_state, when, when, message[:1000], when, platform, job_id),
        )
        event_type = "detail_failed"
    if previous_state != next_state and event_type != "detail_closed":
        _append_event(
            connection, platform, job_id, run_id, "state_changed", when,
            from_state=previous_state, to_state=next_state, message=message,
        )
    _append_event(
        connection, platform, job_id, run_id, event_type, when,
        from_state=previous_state, to_state=next_state, result=result,
        content_hash=content_hash, changed_fields=changed_fields, message=message,
    )
    if commit:
        connection.commit()


def live_job_condition(alias: str = "d") -> str:
    return (
        f"NOT EXISTS (SELECT 1 FROM job_freshness jf "
        f"WHERE jf.platform = {alias}.platform AND jf.job_id = {alias}.job_id "
        "AND jf.evidence_state IN ('closed', 'archived'))"
    )


def _ttl_days(interest: str | None, fit_tier: str | None) -> int:
    if interest == "want":
        return 2
    if interest == "maybe":
        return 4
    if interest == "reject":
        return 30
    # 实测死亡率（2026-09-12：9/13 岗位在采后 1-3 天内关闭）表明 core 岗
    # 7 天 TTL 意味着大半岗位死在复核之前；收敛到 3 天贴住高发死亡窗口。
    if fit_tier == "core":
        return 3
    if fit_tier == "stretch":
        return 14
    return 30


def _priority(interest: str | None, fit_tier: str | None) -> tuple[int, str]:
    if interest == "want":
        return 1, "已标记想投"
    if interest == "maybe":
        return 2, "已标记再看"
    if interest == "reject":
        return 5, "已标记放弃"
    if fit_tier == "core":
        return 3, "未审阅的优先岗位"
    if fit_tier == "stretch":
        return 4, "挑战岗位"
    return 5, "低优先级或已放弃"


def classify_refresh_attention(job: dict) -> str | None:
    """Return one actionable freshness reason instead of one ambiguous due flag."""
    if not job.get("has_detail", True):
        return "detail_missing"
    if job.get("lifecycle_state") == "inaccessible":
        return "retry_due"
    if job.get("lifecycle_state") in {"aging", "stale"}:
        return "detail_due"
    if job.get("scan_state") in {"unseen", "likely_closed"}:
        return "scan_attention"
    return None


def refresh_action_for_job(job: dict) -> str | None:
    """Map freshness attention to the only safe next operation."""
    attention = str(job.get("refresh_attention") or "").strip()
    if attention in {"detail_due", "retry_due", "detail_missing"}:
        return "detail_refresh"
    if attention == "scan_attention":
        return "coverage_scan"
    return None


def build_freshness_dashboard(
    connection: sqlite3.Connection,
    *,
    now: str | None = None,
) -> dict:
    migrate_freshness_schema(connection)
    sync_legacy_freshness(connection)
    as_of = _parse(now, datetime.now().astimezone()) or datetime.now().astimezone()
    decision_join = ""
    decision_select = "NULL AS interest"
    if _table_exists(connection, "job_decisions"):
        decision_join = "LEFT JOIN job_decisions decision ON decision.platform = d.platform AND decision.job_id = d.job_id"
        decision_select = "decision.interest"
    analysis_join = ""
    analysis_select = "NULL AS fit_tier"
    if _table_exists(connection, "job_positioning_analysis"):
        analysis_join = "LEFT JOIN job_positioning_analysis analysis ON analysis.platform = d.platform AND analysis.job_id = d.job_id"
        analysis_select = "analysis.fit_tier"
    latest_recommendations: dict[tuple[str, str], str] = {}
    if _table_exists(connection, "job_match_analyses"):
        snapshot = connection.execute(
            """
            SELECT analysis_version, profile_id, profile_version
              FROM job_match_analyses
             ORDER BY analyzed_at DESC, rowid DESC
             LIMIT 1
            """
        ).fetchone()
        if snapshot:
            latest_recommendations = {
                (row["platform"], row["job_id"]): row["recommendation"]
                for row in connection.execute(
                    """
                    SELECT platform, job_id, recommendation
                      FROM job_match_analyses
                     WHERE analysis_version = ? AND profile_id = ? AND profile_version = ?
                    """,
                    (snapshot["analysis_version"], snapshot["profile_id"], snapshot["profile_version"]),
                )
            }
    rows = connection.execute(
        f"""
        SELECT d.platform, d.job_id, d.title, d.company, d.source_url, d.detail_status,
               d.collected_at, {decision_select}, {analysis_select},
               jf.evidence_state, jf.scan_state, jf.scan_miss_count,
               jf.last_listed_at, jf.last_missing_at,
               jf.last_seen_at, jf.last_checked_at, jf.last_success_at,
               jf.last_failure_at, jf.last_error, jf.closure_reason, jf.content_changed_at
          FROM job_details d
          JOIN job_freshness jf ON jf.platform = d.platform AND jf.job_id = d.job_id
          {decision_join}
          {analysis_join}
         ORDER BY d.job_id
        """
    ).fetchall()
    inventory_rows = [dict(row, has_detail=True) for row in rows]
    if _table_exists(connection, "job_observations"):
        observation_rows = connection.execute(
            """SELECT o.* FROM job_observations o
               LEFT JOIN job_details d ON d.platform = o.platform AND d.job_id = o.job_id
              WHERE d.job_id IS NULL ORDER BY o.job_id"""
        ).fetchall()
        for observation in observation_rows:
            freshness = _ensure_row(connection, observation["platform"], observation["job_id"], as_of.isoformat())
            inventory_rows.append({
                "platform": observation["platform"], "job_id": observation["job_id"],
                "title": observation["title"], "company": observation["company"],
                "source_url": observation["source_url"], "detail_status": "missing",
                "collected_at": observation["collected_at"], "interest": None, "fit_tier": None,
                "evidence_state": freshness["evidence_state"], "scan_state": freshness["scan_state"],
                "scan_miss_count": freshness["scan_miss_count"], "last_listed_at": freshness["last_listed_at"],
                "last_missing_at": freshness["last_missing_at"], "last_seen_at": freshness["last_seen_at"],
                "last_checked_at": freshness["last_checked_at"], "last_success_at": freshness["last_success_at"],
                "last_failure_at": freshness["last_failure_at"], "last_error": freshness["last_error"],
                "closure_reason": freshness["closure_reason"], "content_changed_at": freshness["content_changed_at"],
                "has_detail": False,
            })
    jobs = []
    for row in inventory_rows:
        recommendation = latest_recommendations.get((row["platform"], row["job_id"]))
        effective_tier = {
            "priority": "core",
            "challenge": "stretch",
            "not_fit": "exclude",
        }.get(recommendation, row["fit_tier"])
        evidence_state = str(row["evidence_state"] or "inaccessible")
        evidence_times = [
            parsed
            for parsed in (
                _parse(row["last_success_at"]),
                _parse(row["collected_at"]),
            )
            if parsed is not None and row.get("has_detail", True)
        ]
        reference_dt = max(evidence_times) if evidence_times else as_of
        age_days = max(0, (as_of - reference_dt).total_seconds() / 86400)
        ttl_days = _ttl_days(row["interest"], effective_tier)
        lifecycle_state = evidence_state
        if evidence_state == "active" and age_days > ttl_days:
            lifecycle_state = "aging"
        priority, reason = _priority(row["interest"], effective_tier)
        jobs.append(
            {
                **dict(row),
                "recommendation": recommendation,
                "lifecycle_state": lifecycle_state,
                "age_days": round(age_days, 1),
                "ttl_days": ttl_days,
                "refresh_priority": priority,
                "refresh_reason": reason,
                "has_detail": row.get("has_detail", True),
                "detail_missing": not row.get("has_detail", True),
            }
        )
        jobs[-1]["refresh_attention"] = classify_refresh_attention(jobs[-1])
        jobs[-1]["refresh_action"] = refresh_action_for_job(jobs[-1])
        jobs[-1]["refresh_due"] = jobs[-1]["refresh_attention"] is not None
        if row["interest"] == "reject":
            # 用户已明确放弃：下架不会重新上架，复核预算不花在 reject 上。
            jobs[-1]["refresh_attention"] = None
            jobs[-1]["refresh_action"] = None
            jobs[-1]["refresh_due"] = False
    state_order = {"stale": 0, "inaccessible": 1, "aging": 2, "active": 3, "closed": 4, "archived": 5}
    jobs.sort(key=lambda job: (state_order.get(job["lifecycle_state"], 9), job["refresh_priority"], -job["age_days"], job["job_id"]))
    refresh_plan = sorted(
        [job for job in jobs if job["lifecycle_state"] not in TERMINAL_STATES],
        key=lambda job: (job["refresh_priority"], 0 if job["refresh_due"] else 1, -job["age_days"], job["job_id"]),
    )
    summary = {state: sum(job["lifecycle_state"] == state for job in jobs) for state in ("active", "aging", "stale", "closed", "inaccessible", "archived")}
    summary.update({
        "total": len(jobs),
        "due": sum(job["refresh_due"] for job in jobs),
        "live": sum(job["lifecycle_state"] in LIVE_STATES or job["lifecycle_state"] == "aging" for job in jobs),
        "unseen": sum(job["scan_state"] == "unseen" for job in jobs),
        "likely_closed": sum(job["scan_state"] == "likely_closed" for job in jobs),
        "detail_due": sum(job["refresh_attention"] == "detail_due" for job in jobs),
        "retry_due": sum(job["refresh_attention"] == "retry_due" for job in jobs),
        "scan_attention": sum(job["refresh_attention"] == "scan_attention" for job in jobs),
        "detail_missing": sum(job["refresh_attention"] == "detail_missing" for job in jobs),
        "as_of": as_of.isoformat(timespec="seconds"),
    })
    return {
        "summary": summary,
        "jobs": jobs,
        "refresh_plan": refresh_plan,
        "role_tracks": build_role_track_dashboard(connection, now=as_of.isoformat()),
    }

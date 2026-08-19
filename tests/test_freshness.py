import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobagent.freshness import (
    build_freshness_dashboard,
    build_role_track_dashboard,
    classify_refresh_attention,
    mark_detail_result,
    mark_observation_seen,
    mark_scan_missing,
    migrate_freshness_schema,
    role_family_for_job,
    set_lifecycle_state,
    sync_legacy_freshness,
)
from jobagent.matching import migrate_matching_schema
from jobagent.review import ReviewStore, ReviewValidationError


def _seed_jobs(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE job_details (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          source_url TEXT NOT NULL,
          source_key TEXT,
          title TEXT,
          company TEXT,
          salary_raw TEXT,
          location TEXT,
          experience_raw TEXT,
          education_raw TEXT,
          detail_status TEXT NOT NULL,
          jd_text TEXT,
          detail_text TEXT,
          skills_json TEXT,
          collected_at TEXT NOT NULL,
          PRIMARY KEY(platform, job_id)
        );
        CREATE TABLE job_positioning_analysis (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          analyzed_at TEXT NOT NULL,
          track TEXT NOT NULL,
          fit_tier TEXT NOT NULL,
          relevance_score INTEGER NOT NULL,
          fde_value_score INTEGER NOT NULL,
          salary_kind TEXT NOT NULL,
          salary_min_k REAL,
          salary_max_k REAL,
          salary_months INTEGER,
          risks_json TEXT NOT NULL,
          reasons_json TEXT NOT NULL,
          PRIMARY KEY(platform, job_id)
        );
        CREATE TABLE job_decisions (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          interest TEXT NOT NULL,
          rejection_reason TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          learning_intent INTEGER,
          decided_at TEXT NOT NULL,
          PRIMARY KEY(platform, job_id)
        );
        """
    )
    migrate_matching_schema(connection)
    jobs = [
        ("old-want", "want", "core", 90, "2026-08-01T00:00:00+08:00"),
        ("old-maybe", "maybe", "core", 80, "2026-08-01T00:00:00+08:00"),
        ("fresh-priority", None, "core", 95, "2026-08-16T00:00:00+08:00"),
        ("challenge", None, "stretch", 70, "2026-08-01T00:00:00+08:00"),
        ("old-reject", "reject", "core", 88, "2026-08-01T00:00:00+08:00"),
        ("match-priority", None, "stretch", 75, "2026-08-08T00:00:00+08:00"),
    ]
    for job_id, _interest, tier, score, collected_at in jobs:
        connection.execute(
            "INSERT INTO job_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "boss", job_id, f"https://example.invalid/{job_id}", "search:test",
                job_id, "公司", "20-30K", "上海", "1-3年", "本科",
                "collected", "Python Agent JD", "详情", "[\"Python\", \"Agent\"]", collected_at,
            ),
        )
        connection.execute(
            "INSERT INTO job_positioning_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("boss", job_id, collected_at, "agent_engineering", tier, score, score,
             "monthly", 20, 30, 12, "[]", "[]"),
        )
        if _interest:
            connection.execute(
                "INSERT INTO job_decisions VALUES (?,?,?,?,?,?,?)",
                ("boss", job_id, _interest, "", "", None, collected_at),
            )
    connection.execute(
        """
        INSERT INTO job_match_analyses (
          analysis_version, profile_id, profile_version, platform, job_id,
          analyzed_at, recommendation, track, score, confidence,
          information_status, hard_requirements_json, strengths_json,
          gaps_json, uncertainties_json, previous_fit_tier,
          previous_relevance_score, preference_signals_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "resume-match-v1", "candidate", "v1", "boss", "match-priority",
            "2026-08-17T00:00:00+08:00", "priority", "agent_engineering", 80,
            0.8, "sufficient", "[]", "[]", "[]", "[]", "stretch", 75, "[]",
        ),
    )
    connection.commit()
    connection.close()


class FreshnessStateTests(unittest.TestCase):
    def test_refresh_attention_separates_due_failure_scan_and_missing_detail(self):
        self.assertEqual(
            classify_refresh_attention({"lifecycle_state": "aging", "scan_state": "listed", "has_detail": True}),
            "detail_due",
        )
        self.assertEqual(
            classify_refresh_attention({"lifecycle_state": "inaccessible", "scan_state": "listed", "has_detail": True}),
            "retry_due",
        )
        self.assertEqual(
            classify_refresh_attention({"lifecycle_state": "active", "scan_state": "unseen", "has_detail": True}),
            "scan_attention",
        )
        self.assertEqual(
            classify_refresh_attention({"lifecycle_state": "active", "scan_state": "listed", "has_detail": False}),
            "detail_missing",
        )

    def test_dashboard_includes_observation_only_jobs_as_missing_detail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                CREATE TABLE job_observations (
                  platform TEXT NOT NULL,
                  job_id TEXT NOT NULL,
                  source TEXT NOT NULL,
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
                  PRIMARY KEY(platform, job_id)
                )
                """
            )
            connection.execute(
                "INSERT INTO job_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("boss", "card-only", "boss-search", "scan", 1, "https://example.invalid/card-only",
                 "卡片岗位", "上海", "20-30K", "1-3年", "本科", "公司", "人工智能", "A轮", "20-99人",
                 "HR", "招聘", "卡片岗位", "2026-08-18T00:00:00+08:00"),
            )
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            dashboard = build_freshness_dashboard(connection, now="2026-08-19T00:00:00+08:00")
            card = next(job for job in dashboard["jobs"] if job["job_id"] == "card-only")
            self.assertTrue(card["detail_missing"])
            self.assertEqual(card["refresh_attention"], "detail_missing")
            self.assertEqual(dashboard["summary"]["detail_missing"], 1)
            connection.close()

    def test_jd_only_whitespace_change_is_not_marked_as_content_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            mark_detail_result(
                connection,
                {"platform": "boss", "job_id": "old-want", "title": "old-want", "company": "公司", "jd": "Python Agent JD\n"},
                run_id="detail-whitespace",
                result="collected",
                observed_at="2026-08-18T02:00:00+08:00",
            )
            event = connection.execute(
                "SELECT changed_fields_json FROM job_refresh_events WHERE run_id = ? AND result = 'collected' ORDER BY id DESC LIMIT 1",
                ("detail-whitespace",),
            ).fetchone()
            self.assertNotIn("jd_text", json.loads(event["changed_fields_json"]))
            connection.close()

    def test_single_scan_miss_is_unseen_without_closing_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            mark_scan_missing(
                connection,
                {"platform": "boss", "job_id": "old-want", "company": "公司", "title": "Agent工程师"},
                run_id="scan-1",
                observed_at="2026-08-18T00:00:00+08:00",
            )
            row = connection.execute(
                "SELECT evidence_state, scan_state, scan_miss_count FROM job_freshness WHERE job_id = 'old-want'"
            ).fetchone()
            self.assertEqual(row["evidence_state"], "active")
            self.assertEqual(row["scan_state"], "unseen")
            self.assertEqual(row["scan_miss_count"], 1)
            connection.close()

    def test_consecutive_scan_misses_become_likely_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            job = {"platform": "boss", "job_id": "old-want", "company": "公司", "title": "Agent工程师"}
            mark_scan_missing(connection, job, run_id="scan-1", observed_at="2026-08-18T00:00:00+08:00")
            mark_scan_missing(connection, job, run_id="scan-2", observed_at="2026-08-19T00:00:00+08:00")
            row = connection.execute(
                "SELECT evidence_state, scan_state, scan_miss_count FROM job_freshness WHERE job_id = 'old-want'"
            ).fetchone()
            self.assertEqual(row["evidence_state"], "active")
            self.assertEqual(row["scan_state"], "likely_closed")
            self.assertEqual(row["scan_miss_count"], 2)
            track = connection.execute(
                "SELECT missing_scan_count, missing_since FROM company_role_tracks"
            ).fetchone()
            self.assertEqual(track["missing_scan_count"], 2)
            self.assertEqual(track["missing_since"], "2026-08-18T00:00:00+08:00")
            connection.close()

    def test_closed_job_reappearing_in_scan_restores_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            job = {"platform": "boss", "job_id": "old-want", "company": "公司", "title": "Agent工程师"}
            set_lifecycle_state(connection, "boss", "old-want", "closed", "explicit closure")
            mark_observation_seen(connection, job, run_id="scan-3", observed_at="2026-08-20T00:00:00+08:00")
            row = connection.execute(
                "SELECT evidence_state, scan_state, scan_miss_count FROM job_freshness WHERE job_id = 'old-want'"
            ).fetchone()
            self.assertEqual(row["evidence_state"], "active")
            self.assertEqual(row["scan_state"], "listed")
            self.assertEqual(row["scan_miss_count"], 0)
            connection.close()

    def test_jobs_share_company_role_track_and_dormant_track_keeps_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            first = {"platform": "boss", "job_id": "old-want", "company": "公司", "title": "Agent工程师"}
            second = {"platform": "boss", "job_id": "old-maybe", "company": "公司", "title": "Agent应用工程师"}
            mark_observation_seen(connection, first, run_id="scan-1", observed_at="2026-07-01T00:00:00+08:00")
            mark_observation_seen(connection, second, run_id="scan-1", observed_at="2026-07-01T00:00:00+08:00")
            set_lifecycle_state(connection, "boss", "old-want", "closed", "岗位已关闭")
            dashboard = build_role_track_dashboard(connection, now="2026-08-19T00:00:00+08:00")
            self.assertEqual(len(dashboard["tracks"]), 1)
            track = dashboard["tracks"][0]
            self.assertEqual(track["status"], "dormant")
            self.assertGreaterEqual(track["job_count"], 2)
            self.assertIn("old-want", track["job_ids"])
            self.assertEqual(role_family_for_job(first), role_family_for_job(second))
            connection.close()

    def test_role_track_with_only_closed_jobs_is_not_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            first = {"platform": "boss", "job_id": "old-want", "company": "公司", "title": "Agent工程师"}
            second = {"platform": "boss", "job_id": "old-maybe", "company": "公司", "title": "Agent应用工程师"}
            mark_observation_seen(connection, first, run_id="scan-1", observed_at="2026-08-19T00:00:00+08:00")
            mark_observation_seen(connection, second, run_id="scan-1", observed_at="2026-08-19T00:00:00+08:00")
            for job_id in ("old-want", "old-maybe", "fresh-priority", "challenge", "old-reject", "match-priority"):
                set_lifecycle_state(connection, "boss", job_id, "closed", "岗位已关闭")
            track = build_role_track_dashboard(connection, now="2026-08-19T00:00:00+08:00")["tracks"][0]
            self.assertNotEqual(track["status"], "active")
            self.assertEqual(track["closed_job_count"], 6)
            connection.close()

    def test_legacy_collected_job_is_aging_after_value_based_ttl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            dashboard = build_freshness_dashboard(
                connection, now="2026-08-18T00:00:00+08:00"
            )
            states = {job["job_id"]: job["lifecycle_state"] for job in dashboard["jobs"]}
            self.assertEqual(states["old-want"], "aging")
            self.assertEqual(states["old-maybe"], "aging")
            self.assertEqual(states["fresh-priority"], "active")
            self.assertEqual(states["challenge"], "aging")
            self.assertEqual(states["old-reject"], "active")
            self.assertEqual(states["match-priority"], "aging")
            self.assertEqual(dashboard["summary"]["aging"], 4)
            connection.close()

    def test_seen_restores_listing_but_only_detail_success_refreshes_jd_age(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            set_lifecycle_state(connection, "boss", "old-want", "stale", "missing from covered scan")
            mark_observation_seen(
                connection,
                {"platform": "boss", "job_id": "old-want", "title": "old-want"},
                run_id="scan-1",
                observed_at="2026-08-18T01:00:00+08:00",
            )
            seen_dashboard = build_freshness_dashboard(
                connection, now="2026-08-18T01:00:00+08:00"
            )
            seen_job = next(job for job in seen_dashboard["jobs"] if job["job_id"] == "old-want")
            self.assertEqual(seen_job["lifecycle_state"], "aging")
            self.assertEqual(seen_job["scan_state"], "listed")
            self.assertEqual(seen_job["refresh_attention"], "detail_due")
            self.assertGreater(seen_job["age_days"], 0.0)
            mark_detail_result(
                connection,
                {"platform": "boss", "job_id": "old-want", "title": "old-want", "jd": "Updated JD"},
                run_id="detail-1",
                result="collected",
                observed_at="2026-08-18T01:01:00+08:00",
            )
            row = connection.execute(
                "SELECT evidence_state, last_seen_at, last_success_at FROM job_freshness WHERE job_id = 'old-want'"
            ).fetchone()
            self.assertEqual(row["evidence_state"], "active")
            self.assertEqual(row["last_seen_at"], "2026-08-18T01:00:00+08:00")
            self.assertEqual(row["last_success_at"], "2026-08-18T01:01:00+08:00")
            events = connection.execute(
                "SELECT event_type FROM job_refresh_events WHERE job_id = 'old-want' ORDER BY id"
            ).fetchall()
            self.assertEqual(
                [row[0] for row in events],
                ["state_changed", "state_changed", "seen", "detail_collected"],
            )
            connection.close()

    def test_inaccessible_never_becomes_closed_without_explicit_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            mark_detail_result(
                connection,
                {"platform": "boss", "job_id": "old-want", "title": "old-want"},
                run_id="detail-2",
                result="failed",
                error="CAPTCHA required",
                observed_at="2026-08-18T01:00:00+08:00",
            )
            state = connection.execute(
                "SELECT evidence_state FROM job_freshness WHERE job_id = 'old-want'"
            ).fetchone()[0]
            self.assertEqual(state, "inaccessible")
            self.assertNotEqual(state, "closed")
            set_lifecycle_state(connection, "boss", "old-want", "closed", "explicit page says position closed")
            self.assertEqual(
                connection.execute("SELECT evidence_state FROM job_freshness WHERE job_id = 'old-want'").fetchone()[0],
                "closed",
            )
            connection.close()

    def test_refresh_plan_orders_review_value_before_unreviewed_priority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            dashboard = build_freshness_dashboard(
                connection, now="2026-08-18T00:00:00+08:00"
            )
            self.assertEqual(
                [job["job_id"] for job in dashboard["refresh_plan"][:4]],
                ["old-want", "old-maybe", "match-priority", "fresh-priority"],
            )
            connection.close()

    def test_detail_change_preserves_old_and_new_jd_versions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            mark_detail_result(
                connection,
                {
                    "platform": "boss",
                    "job_id": "old-want",
                    "title": "old-want",
                    "company": "公司",
                    "jd": "Updated JD",
                    "detail_text": "更新详情",
                },
                run_id="detail-change",
                result="collected",
                observed_at="2026-08-18T02:00:00+08:00",
            )
            versions = connection.execute(
                "SELECT jd_text FROM job_detail_versions WHERE job_id = 'old-want' ORDER BY id"
            ).fetchall()
            self.assertEqual([row[0] for row in versions], ["Python Agent JD", "Updated JD"])
            connection.close()


class FreshnessFilteringTests(unittest.TestCase):
    def test_closed_job_is_excluded_from_new_review_sample_and_learning_market(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            _seed_jobs(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            migrate_freshness_schema(connection)
            sync_legacy_freshness(connection)
            set_lifecycle_state(connection, "boss", "old-want", "closed", "explicit closure")
            connection.close()
            store = ReviewStore(path)
            try:
                with self.assertRaises(ReviewValidationError):
                    store.ensure_sample("fresh-sample", quotas={"core": 4, "favorites": 0, "stretch": 0, "exclude": 0})
                learning = store.learning_dashboard()
                self.assertNotIn("old-want", json.dumps(learning, ensure_ascii=False))
                freshness = store.freshness_dashboard(now="2026-08-18T00:00:00+08:00")
                self.assertNotIn(
                    "old-want",
                    [job["job_id"] for job in freshness["refresh_plan"]],
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()

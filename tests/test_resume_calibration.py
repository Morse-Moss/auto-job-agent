import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import main as main_module
from jobagent.config import ROOT as PROJECT_ROOT
from jobagent.calibration import (
    build_calibration_dashboard,
    calibrate_resume,
    confirm_preference_settings,
    extract_resume_profile,
    infer_preference_calibration,
)
from jobagent.review_server import create_server
from jobagent.resume_collection import collect_resume_market_data


RESUME_TEXT = """# 示例候选人

## 教育背景

本科，软件工程，2025 年毕业。

## 工作经历

### AI 应用工程师 | 2025.06 - 2025.12

- 使用 Python、FastAPI 和 React 交付企业 AI 应用。

### AI 应用工程师 | 2026.03 - 至今

- 使用 LangGraph 构建 Agent 工作流，完成失败重试和效果评测。
- 使用 RAG、pgvector 构建带来源引用的知识库，并负责 Docker 部署。
"""


def create_calibration_db(path: Path) -> None:
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
          detail_status TEXT NOT NULL,
          jd_text TEXT,
          detail_text TEXT,
          salary_raw TEXT,
          location TEXT,
          experience_raw TEXT,
          education_raw TEXT,
          recruiter_name TEXT,
          recruiter_role TEXT,
          skills_json TEXT,
          labels_json TEXT,
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
    rows = (
        ("agent-1", "AI Agent 工程师", "甲公司", "深圳", "want", "", ""),
        ("agent-2", "Agent 应用工程师", "乙公司", "广州", "want", "", ""),
        ("agent-3", "RAG 与 Agent 工程师", "丙公司", "深圳", "maybe", "", ""),
        (
            "product-1",
            "AI 产品经理",
            "丁公司",
            "上海",
            "reject",
            "other",
            "JD 太少，公司规模小，信息不透明，担心有坑。",
        ),
    )
    for index, (job_id, title, company, location, interest, reason, note) in enumerate(rows):
        track = "agent_engineering" if job_id.startswith("agent") else "product"
        jd = (
            "负责 Python、Agent 和 RAG 应用开发，使用 FastAPI 构建接口，"
            "完成客户需求、部署交付和效果评测。"
        )
        connection.execute(
            """
            INSERT INTO job_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "boss",
                job_id,
                f"https://example.invalid/{job_id}",
                "favorites",
                title,
                company,
                "collected",
                jd,
                "公司基本信息\n20-99人\n人工智能\n查看全部职位",
                "15-25K",
                location,
                "1-3年",
                "本科",
                "招聘者",
                "HR",
                '["Python", "Agent"]',
                '["direct"]',
                "2026-08-13T00:00:00+08:00",
            ),
        )
        connection.execute(
            "INSERT INTO job_positioning_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "boss",
                job_id,
                "2026-08-13T00:00:00+08:00",
                track,
                "core" if track == "agent_engineering" else "exclude",
                90 - index,
                70,
                "monthly",
                15,
                25,
                12,
                "[]",
                "[]",
            ),
        )
        connection.execute(
            "INSERT INTO job_decisions VALUES (?,?,?,?,?,?,?)",
            ("boss", job_id, interest, reason, note, 0, "2026-08-13T01:00:00+08:00"),
        )
    connection.commit()
    connection.close()


class ResumeProfileExtractionTests(unittest.TestCase):
    def test_resume_builds_evidence_backed_profile_without_inventing_preferences(self):
        profile = extract_resume_profile(
            RESUME_TEXT,
            candidate_id="candidate-demo",
            filename="resume.md",
            as_of=date(2026, 8, 13),
        )

        self.assertEqual(profile["profile_id"], "candidate-demo")
        self.assertEqual(profile["education"]["level"], "bachelor")
        self.assertGreaterEqual(profile["experience_years"], 1.0)
        skills = {item["id"]: item for item in profile["skills"]}
        self.assertIn("agent_workflows", skills)
        self.assertIn("rag_knowledge", skills)
        self.assertIn("Python", skills["python_api"]["evidence"][0])
        self.assertIn("agent_engineering", profile["target_tracks"])
        self.assertEqual(profile["hard_constraints"], {})
        self.assertNotIn("preferred_cities", profile["preferences"])
        self.assertEqual(profile["source"]["kind"], "resume")


class PreferenceCalibrationTests(unittest.TestCase):
    def test_review_behavior_creates_only_explainable_soft_signals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_calibration_db(db_path)
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            try:
                calibration = infer_preference_calibration(connection)
            finally:
                connection.close()

        signals = {item["id"]: item for item in calibration["signals"]}
        self.assertEqual(calibration["reviewed_jobs"], 4)
        self.assertEqual(signals["track:agent_engineering"]["effect"], "soft")
        self.assertGreater(signals["track:agent_engineering"]["score_adjustment"], 0)
        self.assertEqual(signals["company_transparency"]["effect"], "soft")
        self.assertIn("preferred_cities", {item["id"] for item in calibration["unknowns"]})
        self.assertNotIn("hard_constraints", calibration)

    def test_reject_heavy_track_is_not_inferred_as_a_preference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_calibration_db(db_path)
            connection = sqlite3.connect(db_path)
            try:
                interests = ("want", "maybe", "reject", "reject", "reject", "reject")
                for index, interest in enumerate(interests, start=1):
                    job_id = f"rag-{index}"
                    connection.execute(
                        "INSERT INTO job_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            "boss",
                            job_id,
                            f"https://example.invalid/{job_id}",
                            "favorites",
                            "RAG 工程师",
                            f"样例公司 {index}",
                            "collected",
                            "负责 RAG 知识库、向量检索和 Python API 开发。",
                            "公司基本信息\n100-499人",
                            "15-25K",
                            "深圳",
                            "1-3年",
                            "本科",
                            "招聘者",
                            "HR",
                            '["Python", "RAG"]',
                            '["direct"]',
                            "2026-08-13T00:00:00+08:00",
                        ),
                    )
                    connection.execute(
                        "INSERT INTO job_positioning_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            "boss",
                            job_id,
                            "2026-08-13T00:00:00+08:00",
                            "rag_engineering",
                            "core",
                            80,
                            50,
                            "monthly",
                            15,
                            25,
                            12,
                            "[]",
                            "[]",
                        ),
                    )
                    connection.execute(
                        "INSERT INTO job_decisions VALUES (?,?,?,?,?,?,?)",
                        (
                            "boss",
                            job_id,
                            interest,
                            "other" if interest == "reject" else "",
                            "",
                            0,
                            f"2026-08-13T02:{index:02d}:00+08:00",
                        ),
                    )
                connection.commit()
                connection.row_factory = sqlite3.Row
                calibration = infer_preference_calibration(connection)
            finally:
                connection.close()

        signal_ids = {item["id"] for item in calibration["signals"]}
        self.assertNotIn("track:rag_engineering", signal_ids)

    def test_confirmed_preferences_create_a_new_version_and_are_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_calibration_db(db_path)
            initial = calibrate_resume(
                db_path,
                RESUME_TEXT,
                filename="resume.md",
                candidate_id="candidate-demo",
                analysis_version="resume-match-v1",
                as_of=date(2026, 8, 13),
            )

            updated = confirm_preference_settings(
                db_path,
                {
                    "target_tracks": ["fde", "agent_engineering"],
                    "hard_constraints": {
                        "maximum_required_experience_years": 3,
                        "reject_third_party": True,
                        "reject_dispatch": False,
                        "reject_long_term_overseas": True,
                    },
                },
                candidate_id="candidate-demo",
                analysis_version="resume-match-v1",
            )

            self.assertNotEqual(
                initial["profile"]["version"], updated["profile"]["version"]
            )
            self.assertEqual(
                updated["profile"]["target_tracks"],
                ["agent_engineering", "fde"],
            )
            self.assertEqual(
                updated["profile"]["hard_constraints"],
                {
                    "maximum_required_experience_years": 3,
                    "reject_third_party": True,
                    "reject_long_term_overseas": True,
                },
            )
            self.assertEqual(
                updated["confirmed_preferences"]["settings"]["target_tracks"],
                ["agent_engineering", "fde"],
            )
            self.assertEqual(updated["match"]["total"], 4)
            self.assertIn("reviewed_jobs", updated["alignment"])

            reloaded = calibrate_resume(
                db_path,
                RESUME_TEXT,
                filename="resume.md",
                candidate_id="candidate-demo",
                analysis_version="resume-match-v1",
                as_of=date(2026, 8, 13),
            )
            self.assertEqual(
                reloaded["profile"]["version"], updated["profile"]["version"]
            )
            self.assertEqual(
                reloaded["profile"]["hard_constraints"]["maximum_required_experience_years"],
                3,
            )

            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM job_decisions"
                    ).fetchone()[0],
                    4,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM candidate_preference_settings"
                    ).fetchone()[0],
                    1,
                )
            finally:
                connection.close()

    def test_confirmed_preferences_reject_unknown_tracks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_calibration_db(db_path)
            calibrate_resume(db_path, RESUME_TEXT, candidate_id="candidate-demo")

            with self.assertRaises(ValueError):
                confirm_preference_settings(
                    db_path,
                    {"target_tracks": ["made_up_track"], "hard_constraints": {}},
                    candidate_id="candidate-demo",
                )


class ResumeCalibrationFlowTests(unittest.TestCase):
    def test_calibration_uses_local_jobs_and_preserves_existing_decisions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_calibration_db(db_path)

            result = calibrate_resume(
                db_path,
                RESUME_TEXT,
                filename="resume.md",
                candidate_id="candidate-demo",
                analysis_version="resume-match-v1",
                as_of=date(2026, 8, 13),
            )

            self.assertEqual(result["match"]["total"], 4)
            self.assertEqual(result["collection_plan"]["local_jobs"], 4)
            self.assertTrue(result["collection_plan"]["queries"])
            self.assertEqual(result["profile"]["source"]["kind"], "resume")

            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM job_decisions").fetchone()[0],
                    4,
                )
                dashboard = build_calibration_dashboard(connection)
            finally:
                connection.close()
            self.assertEqual(dashboard["profile"]["profile_id"], "candidate-demo")
            self.assertEqual(dashboard["match"]["total"], 4)


class ResumeDrivenCollectionTests(unittest.TestCase):
    def test_collection_increment_uses_collector_inventory_not_only_matchable_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.db"
            create_calibration_db(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "INSERT INTO job_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "boss",
                        "empty-jd",
                        "https://example.invalid/empty-jd",
                        "search:深圳:AI 应用工程师",
                        "信息待补岗位",
                        "样例公司",
                        "collected",
                        "",
                        "",
                        "",
                        "深圳",
                        "",
                        "",
                        "",
                        "",
                        "[]",
                        "[]",
                        "2026-08-13T00:00:00+08:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            targets = []

            class InventoryCollector:
                def __init__(self, cfg, store):
                    self.store = store

                def run(self, target, source="all"):
                    targets.append(target)
                    return SimpleNamespace(
                        run_id="inventory-run",
                        collected=0,
                        total=self.store.collected_detail_count(),
                        status="exhausted",
                        error=None,
                    )

            result = collect_resume_market_data(
                db_path,
                {"collection": {"resume_collection_increment": 2}},
                RESUME_TEXT,
                analysis_output_dir=root / "analysis",
                collector_factory=InventoryCollector,
            )

            self.assertEqual(targets, [7])
            self.assertEqual(result["collection_plan"]["starting_collected_records"], 5)
            self.assertEqual(result["collection_plan"]["requested_target"], 7)

    def test_collection_increment_includes_failed_inventory_records_in_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.db"
            create_calibration_db(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "INSERT INTO job_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "boss",
                        "failed-detail",
                        "https://example.invalid/failed-detail",
                        "search:深圳:AI 应用工程师",
                        "详情失败岗位",
                        "样例公司",
                        "failed",
                        "",
                        "",
                        "",
                        "深圳",
                        "",
                        "",
                        "",
                        "",
                        "[]",
                        "[]",
                        "2026-08-13T00:00:00+08:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            targets = []

            class InventoryCollector:
                def __init__(self, cfg, store):
                    self.store = store

                def run(self, target, source="all"):
                    targets.append(target)
                    return SimpleNamespace(
                        run_id="failed-inventory-run",
                        collected=0,
                        total=self.store.collected_detail_count(),
                        status="exhausted",
                        error=None,
                    )

            result = collect_resume_market_data(
                db_path,
                {"collection": {"resume_collection_increment": 2}},
                RESUME_TEXT,
                analysis_output_dir=root / "analysis",
                collector_factory=InventoryCollector,
            )

            self.assertEqual(targets, [7])
            self.assertEqual(result["collection_plan"]["starting_collected_records"], 5)
            self.assertEqual(result["collection_plan"]["requested_target"], 7)

    def test_collection_uses_resume_queries_then_reanalyzes_and_recalibrates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.db"
            create_calibration_db(db_path)
            instances = []

            class FakeCollector:
                def __init__(self, cfg, store):
                    self.cfg = cfg
                    self.store = store
                    self.calls = []
                    instances.append(self)

                def run(self, target, source="all"):
                    self.calls.append((target, source))
                    self.store.conn.execute(
                        """
                        INSERT INTO job_details (
                          platform, job_id, source_url, source_key, title, company,
                          detail_status, jd_text, detail_text, salary_raw, location,
                          experience_raw, education_raw, recruiter_name, recruiter_role,
                          skills_json, labels_json, collected_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            "boss",
                            "resume-collected-1",
                            "https://example.invalid/resume-collected-1",
                            "search:深圳:AI Agent 开发",
                            "AI Agent 平台工程师",
                            "新增样例公司",
                            "collected",
                            "负责 Python、Agent、RAG、FastAPI 与客户交付，要求 1-3 年经验。",
                            "公司基本信息\n100-499人\n人工智能\nA轮\n查看全部职位",
                            "18-28K",
                            "深圳",
                            "1-3年",
                            "本科",
                            "招聘者",
                            "HR",
                            '["Python", "Agent", "RAG"]',
                            '["direct"]',
                            "2026-08-13T03:00:00+08:00",
                        ),
                    )
                    self.store.conn.commit()
                    return SimpleNamespace(
                        run_id="resume-run-1",
                        collected=1,
                        total=5,
                        status="exhausted",
                        error=None,
                    )

            result = collect_resume_market_data(
                db_path,
                {
                    "collection": {
                        "resume_collection_increment": 2,
                        "cities": ["深圳"],
                    }
                },
                RESUME_TEXT,
                filename="resume.md",
                candidate_id="candidate-demo",
                analysis_version="resume-match-v1",
                analysis_output_dir=root / "analysis",
                collector_factory=FakeCollector,
            )

            self.assertEqual(len(instances), 1)
            collector = instances[0]
            self.assertEqual(
                collector.cfg["collection"]["keywords"],
                result["collection_plan"]["queries"],
            )
            self.assertEqual(collector.calls, [(6, "search")])
            self.assertEqual(result["collection_plan"]["external_collection"], "exhausted")
            self.assertEqual(result["collection_plan"]["collected_new_jobs"], 1)
            self.assertEqual(result["match"]["total"], 5)
            self.assertTrue(result["privacy"]["external_calls"])

            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM job_decisions").fetchone()[0],
                    4,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM job_positioning_analysis").fetchone()[0],
                    5,
                )
            finally:
                connection.close()

    def test_loopback_api_starts_collection_in_background_and_reports_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.db"
            create_calibration_db(db_path)
            calls = []

            def fake_runner(database, cfg, resume_text, **kwargs):
                calls.append(
                    {
                        "database": Path(database),
                        "cfg": cfg,
                        "resume_text": resume_text,
                        **kwargs,
                    }
                )
                dashboard = calibrate_resume(
                    database,
                    resume_text,
                    filename=kwargs["filename"],
                    candidate_id=kwargs["candidate_id"],
                    analysis_version=kwargs["analysis_version"],
                )
                dashboard["collection_plan"].update(
                    {
                        "external_collection": "completed",
                        "collected_new_jobs": 2,
                        "external_collection_error": "",
                    }
                )
                dashboard["privacy"]["external_calls"] = True
                return dashboard

            server = create_server(
                db_path,
                host="127.0.0.1",
                port=0,
                config={"collection": {"resume_collection_increment": 2}},
                collection_runner=fake_runner,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                request = Request(
                    f"{base_url}/api/calibration/collect",
                    data=json.dumps(
                        {
                            "resume_text": RESUME_TEXT,
                            "filename": "resume.md",
                            "candidate_id": "candidate-demo",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request) as response:
                    started = json.load(response)
                    self.assertEqual(response.status, 202)
                self.assertEqual(started["collection"]["status"], "running")

                status = started["collection"]
                for _ in range(100):
                    with urlopen(f"{base_url}/api/calibration/collection-status") as response:
                        status = json.load(response)["collection"]
                    if status["status"] != "running":
                        break
                    time.sleep(0.01)

                self.assertEqual(status["status"], "completed")
                self.assertEqual(status["collected_new_jobs"], 2)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["cfg"]["collection"]["resume_collection_increment"], 2)
                self.assertEqual(calls[0]["analysis_output_dir"].name, "analysis")
                self.assertTrue(calls[0]["analysis_output_dir"].parent.samefile(root))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM job_decisions").fetchone()[0],
                    4,
                )
            finally:
                connection.close()

    def test_loopback_api_rejects_local_calibration_while_collection_is_running(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.db"
            create_calibration_db(db_path)
            collection_started = threading.Event()
            release_collection = threading.Event()

            def blocking_runner(database, cfg, resume_text, **kwargs):
                collection_started.set()
                if not release_collection.wait(timeout=2):
                    raise TimeoutError("test did not release collection")
                dashboard = calibrate_resume(
                    database,
                    resume_text,
                    filename=kwargs["filename"],
                    candidate_id=kwargs["candidate_id"],
                    analysis_version=kwargs["analysis_version"],
                )
                dashboard["collection_plan"].update(
                    {
                        "external_collection": "completed",
                        "collected_new_jobs": 0,
                        "external_collection_error": "",
                    }
                )
                return dashboard

            server = create_server(
                db_path,
                host="127.0.0.1",
                port=0,
                collection_runner=blocking_runner,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                collect_request = Request(
                    f"{base_url}/api/calibration/collect",
                    data=json.dumps(
                        {"resume_text": RESUME_TEXT, "filename": "resume.md"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(collect_request) as response:
                    self.assertEqual(response.status, 202)
                self.assertTrue(collection_started.wait(timeout=1))

                calibration_request = Request(
                    f"{base_url}/api/calibration",
                    data=json.dumps(
                        {
                            "resume_text": RESUME_TEXT + "\n另一份简历",
                            "filename": "another-resume.md",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(calibration_request)
                self.assertEqual(raised.exception.code, 400)
                error = json.loads(raised.exception.read().decode("utf-8"))
                self.assertIn("岗位采集正在运行", error["error"])
            finally:
                release_collection.set()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_stopped_collection_keeps_local_calibration_and_reports_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.db"
            create_calibration_db(db_path)

            class StoppedCollector:
                def __init__(self, cfg, store):
                    self.store = store

                def run(self, target, source="all"):
                    return SimpleNamespace(
                        run_id="resume-run-stopped",
                        collected=0,
                        total=self.store.collected_detail_count(),
                        status="stopped",
                        error="Boss login was not detected in the CDP session",
                    )

            result = collect_resume_market_data(
                db_path,
                {"collection": {"resume_collection_increment": 2}},
                RESUME_TEXT,
                filename="resume.md",
                analysis_output_dir=root / "analysis",
                collector_factory=StoppedCollector,
            )

            self.assertEqual(result["match"]["total"], 4)
            self.assertEqual(result["collection_plan"]["external_collection"], "stopped")
            self.assertIn("login", result["collection_plan"]["external_collection_error"])
            self.assertTrue(result["privacy"]["external_calls"])

    def test_loopback_api_accepts_resume_text_and_returns_calibration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_calibration_db(db_path)
            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                request = Request(
                    f"{base_url}/api/calibration",
                    data=json.dumps(
                        {
                            "resume_text": RESUME_TEXT,
                            "filename": "resume.md",
                            "candidate_id": "candidate-demo",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request) as response:
                    payload = json.load(response)
                self.assertEqual(payload["profile"]["profile_id"], "candidate-demo")
                self.assertEqual(payload["match"]["total"], 4)

                with urlopen(f"{base_url}/api/calibration") as response:
                    persisted = json.load(response)
                self.assertEqual(persisted["profile"]["profile_id"], "candidate-demo")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_loopback_api_confirms_preferences_and_returns_recalculated_dashboard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_calibration_db(db_path)
            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                calibration_request = Request(
                    f"{base_url}/api/calibration",
                    data=json.dumps(
                        {"resume_text": RESUME_TEXT, "filename": "resume.md"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(calibration_request) as response:
                    self.assertEqual(response.status, 200)

                preference_request = Request(
                    f"{base_url}/api/calibration/preferences",
                    data=json.dumps(
                        {
                            "target_tracks": ["fde", "agent_engineering"],
                            "hard_constraints": {
                                "maximum_required_experience_years": 3,
                                "reject_long_term_overseas": True,
                            },
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urlopen(preference_request) as response:
                    payload = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["profile"]["target_tracks"], ["agent_engineering", "fde"])
                self.assertEqual(
                    payload["profile"]["hard_constraints"]["maximum_required_experience_years"],
                    3,
                )
                self.assertEqual(payload["match"]["total"], 4)
                self.assertIsNotNone(payload["confirmed_preferences"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class ResumeCalibrationCliTests(unittest.TestCase):
    def test_cli_calibrates_configured_resume_without_ai_or_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resume_path = Path(temp_dir) / "resume.md"
            resume_path.write_text(RESUME_TEXT, encoding="utf-8")
            config = {
                "collection": {"db_path": "output/jobagent.db"},
                "resume": {"master_file": str(resume_path)},
                "matching": {"analysis_version": "resume-match-v1"},
            }
            calibrated = {
                "profile": {"profile_id": "candidate-demo", "version": "resume-v1"},
                "match": {
                    "total": 4,
                    "distribution": {"priority": 2, "challenge": 1, "not_fit": 1},
                },
            }
            with (
                patch.object(main_module, "load_config", return_value=config) as load_config,
                patch.object(
                    main_module, "calibrate_resume", return_value=calibrated
                ) as calibrate,
                patch.object(main_module, "OpenAICompatibleClient") as ai,
                patch.object(main_module, "BossCollector") as collector,
                patch("builtins.print"),
                patch("sys.argv", ["main.py", "--calibrate-resume"]),
            ):
                main_module.main()

            load_config.assert_called_once_with(require_ai=False)
            calibrate.assert_called_once()
            self.assertEqual(calibrate.call_args.kwargs["filename"], "resume.md")
            ai.assert_not_called()
            collector.assert_not_called()

    def test_cli_can_run_resume_driven_collection_without_ai_or_application_actions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resume_path = Path(temp_dir) / "resume.md"
            resume_path.write_text(RESUME_TEXT, encoding="utf-8")
            config = {
                "collection": {"db_path": "output/jobagent.db"},
                "resume": {"master_file": str(resume_path)},
                "matching": {"resume_analysis_version": "resume-match-v1"},
            }
            collected = {
                "match": {"total": 6, "distribution": {}},
                "collection_plan": {
                    "external_collection": "completed",
                    "collected_new_jobs": 2,
                },
            }
            with (
                patch.object(main_module, "load_config", return_value=config) as load_config,
                patch.object(
                    main_module,
                    "collect_resume_market_data",
                    return_value=collected,
                ) as collect,
                patch.object(main_module, "OpenAICompatibleClient") as ai,
                patch.object(main_module, "BossPlatform") as platform,
                patch("builtins.print"),
                patch("sys.argv", ["main.py", "--collect-from-resume"]),
            ):
                main_module.main()

            load_config.assert_called_once_with(require_ai=False)
            collect.assert_called_once()
            self.assertEqual(collect.call_args.kwargs["filename"], "resume.md")
            ai.assert_not_called()
            platform.assert_not_called()


class ResumeCalibrationUiContractTests(unittest.TestCase):
    def test_workbench_exposes_resume_first_calibration_flow(self):
        page = (PROJECT_ROOT / "review_ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("简历校准", page)
        self.assertIn('id="calibrationView"', page)
        self.assertIn('id="resumeFile"', page)
        self.assertIn("待确认", page)
        self.assertIn('id="preferenceConfirmation"', page)
        self.assertIn("/api/calibration", script)
        self.assertIn("/api/calibration/preferences", script)
        self.assertIn("confirmed_preferences", script)
        self.assertIn("confirmedRecord?.version", script)
        self.assertIn("alignment", script)
        self.assertIn("file.text()", script)

    def test_preference_confirmation_uses_inferred_defaults_and_locks_during_work(self):
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("renderPreferenceConfirmation", script)
        self.assertIn("preference_calibration", script)
        self.assertIn('signal.dimension === "track"', script)
        self.assertIn("setPreferenceFormDisabled", script)
        self.assertIn("state.collectionRunning", script)
        self.assertIn("previous_strong_conflicts", script)
        self.assertIn("strong_conflicts", script)

    def test_preference_confirmation_submits_explicit_settings_only(self):
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn('name="target_tracks"', script)
        self.assertIn('method: "PUT"', script)
        self.assertIn("maximum_required_experience_years", script)
        self.assertIn("reject_third_party", script)
        self.assertIn("reject_dispatch", script)
        self.assertIn("reject_long_term_overseas", script)
        self.assertIn("invalidateMatchResults()", script)

    def test_workbench_exposes_non_blocking_resume_driven_collection(self):
        page = (PROJECT_ROOT / "review_ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="collectResumeJobsButton"', page)
        self.assertIn("/api/calibration/collect", script)
        self.assertIn("/api/calibration/collection-status", script)
        self.assertIn("pollCollectionStatus", script)
        self.assertIn("elements.resumeFile.disabled = running", script)
        self.assertIn("elements.calibrateResumeButton.disabled = running", script)

    def test_collection_status_failure_does_not_unlock_resume_controls(self):
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")
        polling_error_handler = script.split(
            "function showCollectionPollingError", 1
        )[1].split("async function collectResumeJobs", 1)[0]
        collection_handler = script.split(
            "async function collectResumeJobs", 1
        )[1].split("function renderLearningDashboard", 1)[0]

        self.assertNotIn("setCollectionRunning(false)", polling_error_handler)
        self.assertIn("let collectionStarted = false", collection_handler)
        self.assertIn("if (!collectionStarted)", collection_handler)


if __name__ == "__main__":
    unittest.main()

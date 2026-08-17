import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import main as main_module
import jobagent.review as review_module
from jobagent.config import ROOT as PROJECT_ROOT
from jobagent.review import ReviewStore, ReviewValidationError, extract_evidence
from jobagent.review_server import create_server


def create_fixture_db(path: Path) -> None:
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
        """
    )
    for index in range(24):
        source_key = "favorites" if index < 8 else "search:深圳:AI Agent 开发"
        fit_tier = ("core", "stretch", "exclude")[index % 3]
        track = ("agent_engineering", "fde", "ai_application", "rag_engineering")[index % 4]
        job_id = f"job-{index:02d}"
        salary_raw = "35-60K" if index == 1 else "12-20K"
        recruiter_role = "猎头顾问" if index == 2 else "HR"
        labels = ["third_party"] if index == 2 else ["direct"]
        risks = ["third_party"] if index == 2 else (["salary_stretch"] if index == 1 else [])
        jd_text = (
            "负责 Agent 和 RAG 应用开发，使用 Python 构建工作流。"
            "需要对接客户业务需求并完成部署交付。"
        )
        connection.execute(
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
                job_id,
                f"https://example.invalid/{job_id}",
                source_key,
                f"AI Agent 工程师 {index}",
                f"公司 {index}",
                "collected",
                jd_text,
                f"详情 {index}",
                salary_raw,
                "深圳",
                "1-3年",
                "本科",
                "招聘者",
                recruiter_role,
                json.dumps(["Python", "Agent"], ensure_ascii=False),
                json.dumps(labels, ensure_ascii=False),
                "2026-08-05T00:00:00+08:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO job_positioning_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "boss",
                job_id,
                "2026-08-05T00:00:00+08:00",
                track,
                fit_tier,
                90 - index,
                80 - index,
                "monthly",
                35 if index == 1 else 12,
                60 if index == 1 else 20,
                12,
                json.dumps(risks, ensure_ascii=False),
                json.dumps(["包含 Agent/RAG/大模型工程内容"], ensure_ascii=False),
            ),
        )
    connection.commit()
    connection.close()


def seed_boundary_snapshot(path: Path) -> None:
    store = ReviewStore(path)
    store.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO candidate_profiles (
              profile_id, profile_version, display_name, profile_json, created_at
            ) VALUES (?,?,?,?,?)
            """,
            (
                "candidate-test",
                "profile-test",
                "测试候选人",
                json.dumps(
                    {"target_tracks": ["ai_application", "fde"]},
                    ensure_ascii=False,
                ),
                "2026-08-13T11:00:00+08:00",
            ),
        )
        cases = (
            ("job-00", "priority", 92, "sufficient", "reject", "seniority", "", 0),
            ("job-01", "priority", 78, "insufficient", "maybe", "", "JD 太少，需要确认公司。", 0),
            ("job-02", "priority", 88, "sufficient", "want", "", "", 0),
            ("job-03", "challenge", 76, "sufficient", "want", "", "想投 FDE。", 1),
            ("job-04", "challenge", 70, "sufficient", "maybe", "", "", 0),
            ("job-05", "challenge", 68, "sufficient", "reject", "seniority", "", 0),
            ("job-06", "not_fit", 41, "sufficient", "want", "", "方向符合。", 0),
            ("job-07", "not_fit", 38, "sufficient", "maybe", "", "这个能力需要学习。", 1),
            ("job-08", "not_fit", 30, "sufficient", "reject", "role_mismatch", "", 0),
            ("job-09", "priority", 84, "sufficient", "reject", "other", "误判了否定要求。", 0),
            ("job-10", "challenge", 50, "sufficient", "reject", "other", "", 0),
            ("job-11", "not_fit", 43, "sufficient", "reject", "seniority", "", 0),
        )
        for position, (
            job_id,
            recommendation,
            score,
            information,
            interest,
            reason,
            note,
            learning,
        ) in enumerate(cases):
            hard_requirements = []
            strengths = []
            gaps = []
            if job_id == "job-00":
                gaps = [
                    {
                        "id": "experience_depth",
                        "label": "经验年限",
                        "jd_evidence": "5-10年",
                        "candidate_evidence": "当前档案：约 1 年",
                        "severity": "moderate",
                    }
                ]
            elif job_id == "job-02":
                strengths = [
                    {
                        "id": "python_api",
                        "label": "Python 与 API 工程",
                        "jd_evidence": "使用 Python 构建工作流",
                        "candidate_evidence": "候选人有 Python API 项目经验",
                    }
                ]
            elif job_id == "job-08":
                hard_requirements = [
                    {
                        "id": "education_mismatch",
                        "label": "学历门槛",
                        "jd_evidence": "学历：硕士",
                        "candidate_evidence": "当前档案：本科",
                        "severity": "hard",
                    }
                ]
            elif job_id == "job-09":
                gaps = [
                    {
                        "id": "mcu_hardware",
                        "label": "MCU 与嵌入式硬件",
                        "jd_evidence": "无需编写或调试 MCU 固件",
                        "candidate_evidence": "当前档案没有直接项目证据",
                        "severity": "major",
                    }
                ]
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
                    "resume-match-test",
                    "candidate-test",
                    "profile-test",
                    "boss",
                    job_id,
                    "2026-08-13T12:00:00+08:00",
                    recommendation,
                    "fde" if job_id in {"job-03", "job-06"} else "ai_application",
                    score,
                    0.65 if information == "insufficient" else 0.95,
                    information,
                    json.dumps(hard_requirements, ensure_ascii=False),
                    json.dumps(strengths, ensure_ascii=False),
                    json.dumps(gaps, ensure_ascii=False),
                    json.dumps(["JD 正文过短"] if information == "insufficient" else [], ensure_ascii=False),
                    {"priority": "core", "challenge": "stretch", "not_fit": "exclude"}[recommendation],
                    score,
                    "[]",
                ),
            )
            connection.execute(
                """
                INSERT INTO job_decisions (
                  platform, job_id, interest, rejection_reason, note,
                  learning_intent, decided_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    "boss",
                    job_id,
                    interest,
                    reason,
                    note,
                    learning,
                    f"2026-08-13T12:{int(job_id[-2:]):02d}:00+08:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO job_review_samples (
                  sample_name, platform, job_id, sample_bucket, position, created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    "calibration-review",
                    "boss",
                    job_id,
                    {
                        "priority": "core",
                        "challenge": "stretch",
                        "not_fit": "exclude",
                    }[recommendation],
                    position,
                    "2026-08-13T11:30:00+08:00",
                ),
            )
        connection.execute(
            """
            INSERT INTO job_corrections (
              platform, job_id, category, note, analysis_version, corrected_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                "boss",
                "job-03",
                "attainability",
                "FDE 不应只因方向未写入简历而降级。",
                "resume-match-test",
                "2026-08-13T13:00:00+08:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def add_legacy_review(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE job_reviews (
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
        """
    )
    connection.execute(
        "INSERT INTO job_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "boss",
            "job-00",
            "maybe",
            '["agent"]',
            4,
            4,
            3,
            3,
            "direct",
            "",
            "旧审阅数据",
            "2026-08-06T00:00:00+08:00",
        ),
    )
    connection.commit()
    connection.close()


class ReviewSampleTests(unittest.TestCase):
    def test_sample_is_stratified_and_stable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                quotas = {"favorites": 4, "core": 3, "stretch": 3, "exclude": 2}
                first = store.ensure_sample("calibration", quotas=quotas)
                second = store.ensure_sample("calibration", quotas=quotas)

                self.assertEqual(len(first), 12)
                self.assertEqual(
                    {bucket: sum(job["sample_bucket"] == bucket for job in first) for bucket in quotas},
                    quotas,
                )
                self.assertEqual(
                    [job["job_id"] for job in first],
                    [job["job_id"] for job in second],
                )
            finally:
                store.close()


class ReviewMigrationTests(unittest.TestCase):
    def test_legacy_reviews_are_preserved_and_backfilled_as_decisions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            add_legacy_review(db_path)

            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 1})
                job = store.get_job("job-00")
                tables = {
                    row[0]
                    for row in store.conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }

                self.assertIn("job_reviews", tables)
                self.assertIn("job_decisions", tables)
                self.assertIn("job_corrections", tables)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM job_reviews").fetchone()[0], 1)
                self.assertEqual(job["decision"]["interest"], "maybe")
                self.assertEqual(job["decision"]["note"], "旧审阅数据")
                self.assertGreaterEqual(store.conn.execute("PRAGMA user_version").fetchone()[0], 1)
            finally:
                store.close()


class DecisionPersistenceTests(unittest.TestCase):
    def test_reject_decision_needs_only_interest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 1})
                job_id = store.list_jobs()[0]["job_id"]
                decision = store.save_decision(job_id, {"interest": "reject"})

                self.assertEqual(decision["interest"], "reject")
                self.assertEqual(decision["rejection_reason"], "")
                self.assertNotIn("technical_depth", decision)
                self.assertEqual(store.stats()["reviewed"], 1)
            finally:
                store.close()

    def test_want_decision_round_trip_preserves_optional_notes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("calibration", quotas={"favorites": 1})
                job_id = store.list_jobs("calibration")[0]["job_id"]
                store.save_decision(
                    job_id,
                    {"interest": "want", "note": "确认团队和业务。"},
                    sample_name="calibration",
                )
                loaded = store.get_job(job_id, "calibration")

                self.assertEqual(loaded["decision"]["interest"], "want")
                self.assertEqual(loaded["decision"]["note"], "确认团队和业务。")
                self.assertEqual(store.stats("calibration")["wanted"], 1)
            finally:
                store.close()

    def test_learning_intent_round_trip_preserves_explicit_opt_out(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 1})
                saved = store.save_decision(
                    "job-00",
                    {
                        "interest": "maybe",
                        "note": "RAG 全链路需要学习",
                        "learning_intent": False,
                    },
                )
                loaded = store.get_job("job-00")["decision"]

                self.assertIs(saved.get("learning_intent"), False)
                self.assertIs(loaded.get("learning_intent"), False)
                self.assertGreaterEqual(store.conn.execute("PRAGMA user_version").fetchone()[0], 2)
            finally:
                store.close()

    def test_review_migration_does_not_lower_a_newer_shared_schema_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            connection = sqlite3.connect(db_path)
            connection.execute("PRAGMA user_version = 9")
            connection.close()

            store = ReviewStore(db_path)
            try:
                self.assertEqual(store.conn.execute("PRAGMA user_version").fetchone()[0], 9)
            finally:
                store.close()

    def test_correction_is_saved_without_creating_a_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 1})
                job_id = store.list_jobs()[0]["job_id"]
                correction = store.save_correction(
                    job_id,
                    {"category": "salary_reachability", "note": "这个薪资对我并非完全不可达。"},
                )
                loaded = store.get_job(job_id)

                self.assertEqual(correction["category"], "salary_reachability")
                self.assertIsNone(loaded["decision"])
                self.assertEqual(loaded["corrections"][0]["note"], "这个薪资对我并非完全不可达。")
                self.assertEqual(loaded["corrections"][0]["analysis_version"], "2026-08-05T00:00:00+08:00")
            finally:
                store.close()

    def test_invalid_decision_does_not_write_partial_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("calibration", quotas={"favorites": 1})
                job_id = store.list_jobs("calibration")[0]["job_id"]
                with self.assertRaises(ReviewValidationError):
                    store.save_decision(job_id, {"interest": "later"}, sample_name="calibration")

                self.assertIsNone(store.get_job(job_id, "calibration")["decision"])
            finally:
                store.close()

    def test_decision_cannot_write_outside_the_active_sample(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 1})
                with self.assertRaises(ReviewValidationError):
                    store.save_decision("job-23", {"interest": "reject"})
            finally:
                store.close()


class SystemAnalysisTests(unittest.TestCase):
    def test_system_analysis_exposes_separate_judgments_with_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 8})
                direct_job = store.get_job("job-00")
                agency_job = store.get_job("job-02")

                analysis = direct_job["system_analysis"]
                self.assertEqual(analysis["direction"]["value"], "Agent 开发")
                self.assertEqual(analysis["salary_reachability"]["value"], "较可达")
                self.assertEqual(analysis["employment_risk"]["value"], "企业直招线索")
                self.assertTrue(analysis["technical_match"]["evidence"])
                self.assertEqual(
                    agency_job["system_analysis"]["employment_risk"]["value"],
                    "第三方招聘，关系待确认",
                )
                self.assertNotIn("外包", agency_job["system_analysis"]["employment_risk"]["value"])
            finally:
                store.close()

    def test_extracts_short_jd_evidence_for_core_judgments(self):
        evidence = extract_evidence(
            {
                "title": "AI Agent FDE 工程师",
                "jd_text": "负责 Agent 和 RAG 应用开发。\n需要走访客户，梳理业务需求并完成部署交付。\n岗位由第三方招聘，要求 5 年经验。",
                "recruiter_role": "猎头顾问",
                "risks_json": '["third_party", "seniority_gap"]',
            }
        )
        self.assertTrue(any("Agent 和 RAG 应用开发" in item for item in evidence["technical"]))
        self.assertTrue(any("梳理业务需求并完成部署交付" in item for item in evidence["business"]))
        self.assertTrue(any("第三方招聘" in item for item in evidence["relationship"]))
        self.assertTrue(evidence["caution"])


class CompanyProfileTests(unittest.TestCase):
    def test_company_profile_recovers_metadata_and_founded_age_from_collected_text(self):
        build_profile = getattr(review_module, "extract_company_profile", None)
        self.assertTrue(callable(build_profile), "公司画像解析尚未实现")

        profile = build_profile(
            {
                "company": "有才品牌营销",
                "company_size": "",
                "industry": "在线教育",
                "financing": "20-99人",
                "detail_text": """
                公司基本信息
                有才品牌营销
                20-99人
                在线教育
                查看全部职位
                工商信息 公司名称 广州有才品牌营销管理有限公司
                法定代表人 刘立夫 成立日期 2023-12-28
                企业类型 有限责任公司 经营状态 在营 注册资金 51万元 查看全部
                """,
            },
            as_of=date(2026, 8, 11),
        )

        self.assertEqual(profile["company_size"], "20-99人")
        self.assertEqual(profile["industry"], "在线教育")
        self.assertEqual(profile["financing"], "")
        self.assertEqual(profile["legal_name"], "广州有才品牌营销管理有限公司")
        self.assertEqual(profile["founded_date"], "2023-12-28")
        self.assertEqual(profile["company_age"], "2年7个月")
        self.assertEqual(profile["operating_status"], "在营")
        self.assertEqual(profile["registered_capital"], "51万元")

    def test_job_detail_exposes_company_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                UPDATE job_details
                   SET detail_text = ?
                 WHERE platform = 'boss' AND job_id = 'job-00'
                """,
                (
                    "公司基本信息\n公司 0\nA轮\n100-499人\n人工智能\n查看全部职位\n"
                    "工商信息 公司名称 公司零有限公司 法定代表人 张三 "
                    "成立日期 2020-01-02 企业类型 有限责任公司 "
                    "经营状态 存续 注册资金 100万元 查看全部",
                ),
            )
            connection.commit()
            connection.close()

            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 1})
                profile = store.get_job("job-00").get("company_profile")
                self.assertIsNotNone(profile, "岗位详情尚未返回公司画像")
                self.assertEqual(profile["company_size"], "100-499人")
                self.assertEqual(profile["financing"], "A轮")
                self.assertEqual(profile["industry"], "人工智能")
                self.assertEqual(profile["legal_name"], "公司零有限公司")
            finally:
                store.close()


class LearningDashboardTests(unittest.TestCase):
    def test_learning_dashboard_combines_explicit_notes_with_target_market_frequency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            connection = sqlite3.connect(db_path)
            connection.executemany(
                "UPDATE job_details SET jd_text = jd_text || ? WHERE job_id = ?",
                [
                    (" 需要 Java、Spring Cloud 企业后端经验。", "job-00"),
                    (" 需要 Java、Spring Cloud 企业后端经验。", "job-01"),
                    (" 需要 Java、Spring Cloud 企业后端经验。", "job-02"),
                ],
            )
            connection.commit()
            connection.close()

            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 4})
                store.save_decision(
                    "job-00",
                    {
                        "interest": "maybe",
                        "note": "我最近都是Python多，JAVA比较少",
                        "learning_intent": True,
                    },
                )
                store.save_decision(
                    "job-01",
                    {
                        "interest": "reject",
                        "note": "JD 太少，公司规模小，担心有坑。",
                        "learning_intent": False,
                    },
                )
                store.save_decision(
                    "job-03",
                    {
                        "interest": "want",
                        "note": "了解tRPC\n熟悉Linux操作系统\n这些技术栈可以记录下来，我要往这方面学习",
                        "learning_intent": True,
                    },
                )

                build_dashboard = getattr(store, "learning_dashboard", None)
                self.assertTrue(callable(build_dashboard), "学习雷达聚合尚未实现")
                dashboard = build_dashboard()

                self.assertEqual(dashboard["snapshot"]["collected_jobs"], 24)
                self.assertEqual(dashboard["snapshot"]["target_jobs"], 16)
                self.assertEqual(dashboard["snapshot"]["reviewed_jobs"], 3)
                self.assertEqual(dashboard["notes"]["total"], 3)
                self.assertEqual(dashboard["notes"]["learning"], 2)
                self.assertEqual(dashboard["notes"]["risk"], 1)
                self.assertEqual(dashboard["priorities"][0]["id"], "java_enterprise")
                self.assertEqual(dashboard["priorities"][0]["note_count"], 1)
                self.assertEqual(dashboard["priorities"][0]["market_job_count"], 2)
                priority_ids = {item["id"] for item in dashboard["priorities"]}
                self.assertIn("platform_engineering", priority_ids)
                self.assertIn("fullstack_web", priority_ids)
                self.assertFalse(
                    any(item["id"] == "python_api" for item in dashboard["priorities"]),
                    "Python 已被备注为强项，不应误判为学习缺口",
                )
                agent_demand = next(
                    item for item in dashboard["market_demands"] if item["id"] == "agent_workflows"
                )
                self.assertEqual(agent_demand["job_count"], 16)
                self.assertEqual(agent_demand["share_percent"], 100.0)
            finally:
                store.close()

    def test_learning_signal_has_no_review_link_after_job_leaves_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "DELETE FROM job_review_samples WHERE platform = 'boss' AND job_id = 'job-07'"
                )
                connection.execute(
                    "DELETE FROM job_match_analyses WHERE platform = 'boss' AND job_id = 'job-07'"
                )
                connection.commit()
            finally:
                connection.close()

            store = ReviewStore(db_path)
            try:
                signal = next(
                    item
                    for item in store.learning_dashboard()["notes"]["signals"]
                    if item["job_id"] == "job-07"
                )
                self.assertEqual(signal["excerpt"], "这个能力需要学习。")
                self.assertIsNone(signal["sample_name"])
            finally:
                store.close()


class BoundaryCalibrationTests(unittest.TestCase):
    def test_boundary_report_selects_deterministic_reviewed_examples_by_recommendation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            store = ReviewStore(db_path)
            try:
                first = store.boundary_dashboard(limit_per_group=3)
                second = store.boundary_dashboard(limit_per_group=3)

                self.assertEqual(first, second)
                self.assertEqual(first["snapshot"]["reviewed_jobs"], 12)
                self.assertEqual(first["snapshot"]["strong_conflicts"], 3)
                self.assertEqual(first["snapshot"]["corrections"], 1)
                self.assertEqual(
                    {group["recommendation"]: len(group["jobs"]) for group in first["groups"]},
                    {"priority": 3, "challenge": 3, "not_fit": 3},
                )
                priority_ids = [
                    job["job_id"]
                    for group in first["groups"]
                    if group["recommendation"] == "priority"
                    for job in group["jobs"]
                ]
                self.assertEqual(priority_ids[:2], ["job-00", "job-09"])
                self.assertEqual(first["groups"][0]["jobs"][0]["conflict_level"], "strong")
                self.assertTrue(first["groups"][0]["jobs"][0]["reasons"])
                self.assertTrue(first["groups"][0]["jobs"][0]["gaps"])
            finally:
                store.close()

    def test_boundary_report_exposes_hard_requirement_and_strength_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            store = ReviewStore(db_path)
            try:
                dashboard = store.boundary_dashboard(limit_per_group=10)
                jobs = {
                    job["job_id"]: job
                    for group in dashboard["groups"]
                    for job in group["jobs"]
                }

                self.assertEqual(
                    jobs["job-08"]["hard_requirements"][0]["jd_evidence"],
                    "学历：硕士",
                )
                self.assertTrue(
                    any("硬门槛" in reason for reason in jobs["job-08"]["reasons"])
                )
                self.assertEqual(
                    jobs["job-02"]["strengths"][0]["jd_evidence"],
                    "使用 Python 构建工作流",
                )
                self.assertTrue(
                    any("匹配优势" in reason for reason in jobs["job-02"]["reasons"])
                )
            finally:
                store.close()

    def test_boundary_report_separates_selected_metrics_from_all_reviewed_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            store = ReviewStore(db_path)
            try:
                dashboard = store.boundary_dashboard(limit_per_group=2)
                selected_jobs = [
                    job for group in dashboard["groups"] for job in group["jobs"]
                ]
                selected = dashboard["snapshot"]["selected_metrics"]

                self.assertEqual(selected["jobs"], len(selected_jobs))
                self.assertEqual(
                    selected["strong_conflicts"],
                    sum(job["conflict_level"] == "strong" for job in selected_jobs),
                )
                self.assertEqual(
                    selected["learning_signals"],
                    sum(bool(job["learning_intent"]) for job in selected_jobs),
                )
                self.assertGreaterEqual(
                    dashboard["snapshot"]["soft_conflicts"],
                    selected["soft_conflicts"],
                )
            finally:
                store.close()

    def test_boundary_signal_uses_the_actual_recommendation_rule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    UPDATE job_match_analyses
                       SET track = 'product', score = 80, recommendation = 'challenge'
                     WHERE job_id = 'job-04'
                    """
                )
                connection.commit()
            finally:
                connection.close()
            store = ReviewStore(db_path)
            try:
                dashboard = store.boundary_dashboard(limit_per_group=10)
                job = next(
                    job
                    for group in dashboard["groups"]
                    for job in group["jobs"]
                    if job["job_id"] == "job-04"
                )
                self.assertFalse(
                    job["near_boundary"],
                    "AI 产品岗固定进入挑战投，不应按 75 分阈值标为临近边界",
                )
                self.assertIsNone(
                    job["boundary_distance"],
                    "没有数值分类边界的岗位不应参与边界距离排序",
                )
            finally:
                store.close()

    def test_boundary_signal_normalizes_fullwidth_ai_product_titles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    UPDATE job_match_analyses
                       SET track = 'product', score = 80, recommendation = 'challenge'
                     WHERE platform = 'boss' AND job_id = 'job-04'
                    """
                )
                connection.execute(
                    """
                    UPDATE job_details
                       SET title = 'ＡＩ产品经理', jd_text = '负责产品规划和需求管理。'
                     WHERE platform = 'boss' AND job_id = 'job-04'
                    """
                )
                connection.commit()
            finally:
                connection.close()

            store = ReviewStore(db_path)
            try:
                dashboard = store.boundary_dashboard(limit_per_group=10)
                job = next(
                    job
                    for group in dashboard["groups"]
                    for job in group["jobs"]
                    if job["platform"] == "boss" and job["job_id"] == "job-04"
                )
                self.assertIsNone(job["boundary_distance"])
                self.assertFalse(job["near_boundary"])
            finally:
                store.close()

    def test_reviewed_fallback_uses_latest_match_snapshot_without_legacy_analysis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    DELETE FROM job_positioning_analysis
                     WHERE platform = 'boss' AND job_id = 'job-00'
                    """
                )
                connection.commit()
            finally:
                connection.close()

            store = ReviewStore(db_path)
            try:
                job = store.get_job(
                    "job-00",
                    sample_name="__reviewed__",
                    platform="boss",
                )

                self.assertIsNotNone(job)
                self.assertEqual(job["platform"], "boss")
                self.assertEqual(job["fit_tier"], "core")
                self.assertEqual(job["relevance_score"], 92)
                self.assertEqual(job["decision"]["interest"], "reject")
                self.assertEqual(job["analyzed_at"], "2026-08-13T12:00:00+08:00")
            finally:
                store.close()

    def test_boundary_report_surfaces_soft_conflicts_information_and_learning_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            store = ReviewStore(db_path)
            try:
                dashboard = store.boundary_dashboard(limit_per_group=10)
                jobs = {
                    job["job_id"]: job
                    for group in dashboard["groups"]
                    for job in group["jobs"]
                }

                self.assertEqual(jobs["job-03"]["conflict_level"], "soft")
                self.assertEqual(jobs["job-03"]["correction_count"], 1)
                self.assertIn("correction", jobs["job-03"]["signals"])
                self.assertEqual(jobs["job-01"]["information_status"], "insufficient")
                self.assertIn("information", jobs["job-01"]["signals"])
                self.assertIn("learning", jobs["job-07"]["signals"])
                self.assertEqual(dashboard["insights"][0]["severity"], "high")
                self.assertIn("优先投", dashboard["insights"][0]["title"])
                self.assertFalse(dashboard["actions"]["threshold_change_recommended"])
            finally:
                store.close()


class ReviewApiTests(unittest.TestCase):
    def test_api_returns_analysis_and_persists_decision_and_correction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 2})
                job_id = store.list_jobs()[0]["job_id"]
            finally:
                store.close()

            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base_url) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("岗位决策台", page)

                with urlopen(f"{base_url}/api/jobs/{job_id}") as response:
                    detail = json.load(response)["job"]
                self.assertIn("salary_reachability", detail["system_analysis"])

                decision_request = Request(
                    f"{base_url}/api/jobs/{job_id}",
                    data=json.dumps({"interest": "maybe", "note": "确认团队。"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urlopen(decision_request) as response:
                    saved = json.load(response)
                self.assertEqual(saved["decision"]["interest"], "maybe")

                correction_request = Request(
                    f"{base_url}/api/jobs/{job_id}/correction",
                    data=json.dumps({"category": "direction", "note": "更偏 AI 应用。"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urlopen(correction_request) as response:
                    corrected = json.load(response)
                self.assertEqual(corrected["correction"]["category"], "direction")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_learning_api_returns_local_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                try:
                    with urlopen(
                        f"http://127.0.0.1:{server.server_port}/api/learning"
                    ) as response:
                        payload = json.load(response)
                except HTTPError as exc:
                    self.fail(f"学习雷达接口不可用：HTTP {exc.code}")

                self.assertEqual(payload["snapshot"]["collected_jobs"], 24)
                self.assertEqual(payload["snapshot"]["target_jobs"], 16)
                self.assertTrue(payload["market_demands"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_boundary_api_is_read_only_and_returns_calibration_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            connection = sqlite3.connect(db_path)
            try:
                before = connection.execute(
                    "SELECT platform, job_id, interest, note FROM job_decisions ORDER BY job_id"
                ).fetchall()
            finally:
                connection.close()
            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/boundary-calibration"
                ) as response:
                    payload = json.load(response)
                self.assertEqual(len(payload["groups"]), 3)
                self.assertEqual(payload["snapshot"]["reviewed_jobs"], 12)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            connection = sqlite3.connect(db_path)
            try:
                after = connection.execute(
                    "SELECT platform, job_id, interest, note FROM job_decisions ORDER BY job_id"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(before, after)

    def test_boundary_job_can_return_to_its_non_default_review_sample(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base_url}/api/boundary-calibration") as response:
                    dashboard = json.load(response)
                job = dashboard["groups"][0]["jobs"][0]
                self.assertEqual(job["sample_name"], "calibration-review")

                with urlopen(
                    f"{base_url}/api/jobs/{job['job_id']}?sample_name=calibration-review"
                ) as response:
                    payload = json.load(response)
                self.assertEqual(payload["job"]["job_id"], job["job_id"])
                self.assertEqual(payload["job"]["sample_bucket"], "core")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_boundary_review_round_trip_keeps_platform_for_duplicate_job_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            seed_boundary_snapshot(db_path)
            connection = sqlite3.connect(db_path)
            try:
                source = connection.execute(
                    """
                    SELECT source_key, jd_text, detail_text, salary_raw, location,
                           experience_raw, education_raw, recruiter_name,
                           recruiter_role, skills_json, labels_json, collected_at
                      FROM job_details
                     WHERE platform = 'boss' AND job_id = 'job-00'
                    """
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO job_details (
                      platform, job_id, source_url, source_key, title, company,
                      detail_status, jd_text, detail_text, salary_raw, location,
                      experience_raw, education_raw, recruiter_name, recruiter_role,
                      skills_json, labels_json, collected_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "lagou",
                        "job-00",
                        "https://example.invalid/lagou/job-00",
                        source[0],
                        "拉勾 AI 产品经理",
                        "拉勾测试公司",
                        "collected",
                        *source[1:],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO job_match_analyses (
                      analysis_version, profile_id, profile_version, platform, job_id,
                      analyzed_at, recommendation, track, score, confidence,
                      information_status, hard_requirements_json, strengths_json,
                      gaps_json, uncertainties_json, previous_fit_tier,
                      previous_relevance_score, preference_signals_json
                    )
                    SELECT analysis_version, profile_id, profile_version, 'lagou', job_id,
                           analyzed_at, recommendation, track, score, confidence,
                           information_status, hard_requirements_json, strengths_json,
                           gaps_json, uncertainties_json, previous_fit_tier,
                           previous_relevance_score, preference_signals_json
                      FROM job_match_analyses
                     WHERE platform = 'boss' AND job_id = 'job-00'
                    """
                )
                connection.execute(
                    """
                    INSERT INTO job_decisions (
                      platform, job_id, interest, rejection_reason, note,
                      learning_intent, decided_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        "lagou",
                        "job-00",
                        "maybe",
                        "",
                        "拉勾原备注",
                        0,
                        "2026-08-13T14:00:00+08:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            reviewed_query = "sample_name=__reviewed__&platform=lagou"
            try:
                with urlopen(f"{base_url}/api/learning") as response:
                    learning = json.load(response)
                lagou_note = next(
                    signal
                    for signal in learning["notes"]["signals"]
                    if signal["excerpt"] == "拉勾原备注"
                )
                self.assertEqual(lagou_note["platform"], "lagou")
                self.assertEqual(lagou_note["sample_name"], "__reviewed__")

                with urlopen(
                    f"{base_url}/api/matches/job-00?platform=lagou"
                ) as response:
                    match_detail = json.load(response)["match"]
                self.assertEqual(match_detail["platform"], "lagou")
                self.assertEqual(match_detail["company"], "拉勾测试公司")

                with self.assertRaises(HTTPError) as caught:
                    urlopen(f"{base_url}/api/matches/job-00")
                self.assertEqual(caught.exception.code, 400)
                ambiguous = json.loads(caught.exception.read().decode("utf-8"))
                self.assertIn("多个平台", ambiguous["error"])

                with urlopen(f"{base_url}/api/boundary-calibration") as response:
                    dashboard = json.load(response)
                lagou_job = next(
                    job
                    for group in dashboard["groups"]
                    for job in group["jobs"]
                    if job["platform"] == "lagou" and job["job_id"] == "job-00"
                )
                self.assertEqual(lagou_job["sample_name"], "__reviewed__")

                with urlopen(
                    f"{base_url}/api/jobs/job-00?{reviewed_query}"
                ) as response:
                    detail = json.load(response)["job"]
                self.assertEqual(detail["platform"], "lagou")
                self.assertEqual(detail["company"], "拉勾测试公司")
                self.assertEqual(detail["decision"]["note"], "拉勾原备注")

                decision_request = Request(
                    f"{base_url}/api/jobs/job-00?{reviewed_query}",
                    data=json.dumps(
                        {"interest": "want", "note": "只更新拉勾"},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urlopen(decision_request) as response:
                    self.assertEqual(json.load(response)["decision"]["interest"], "want")

                correction_request = Request(
                    f"{base_url}/api/jobs/job-00/correction?{reviewed_query}",
                    data=json.dumps(
                        {"category": "direction", "note": "拉勾岗位方向应单独判断"},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urlopen(correction_request) as response:
                    self.assertEqual(
                        json.load(response)["correction"]["category"],
                        "direction",
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            connection = sqlite3.connect(db_path)
            try:
                decisions = dict(
                    connection.execute(
                        """
                        SELECT platform, note FROM job_decisions
                         WHERE job_id = 'job-00'
                         ORDER BY platform
                        """
                    ).fetchall()
                )
                correction_platforms = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT platform FROM job_corrections
                         WHERE job_id = 'job-00' AND category = 'direction'
                        """
                    ).fetchall()
                ]
            finally:
                connection.close()
            self.assertNotEqual(decisions["boss"], "只更新拉勾")
            self.assertEqual(decisions["lagou"], "只更新拉勾")
            self.assertEqual(correction_platforms, ["lagou"])

    def test_api_rejects_missing_interest_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_fixture_db(db_path)
            store = ReviewStore(db_path)
            try:
                store.ensure_sample("positioning-v1", quotas={"favorites": 1})
                job_id = store.list_jobs()[0]["job_id"]
            finally:
                store.close()

            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/jobs/{job_id}",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request)
                self.assertEqual(caught.exception.code, 400)
                error = json.loads(caught.exception.read().decode("utf-8"))
                self.assertIn("想投、再看或放弃", error["error"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class ReviewUiContractTests(unittest.TestCase):
    def test_ui_only_asks_for_user_decisions_and_optional_correction(self):
        page = (PROJECT_ROOT / "review_ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("岗位决策台", page)
        self.assertIn("薪资可达性", page)
        self.assertIn("系统判断有误", page)
        self.assertNotIn('name="role_labels"', page)
        self.assertNotIn('name="employment_model"', page)
        self.assertNotIn("score-control", page)
        self.assertNotIn("薪资现实度", page)
        self.assertNotIn("createScoreControls", script)
        self.assertIn("/correction", script)

    def test_ui_exposes_company_profile_and_learning_radar(self):
        page = (PROJECT_ROOT / "review_ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("学习雷达", page)
        self.assertIn('id="learningView"', page)
        self.assertIn('id="companyProfile"', page)
        self.assertIn('name="learning_intent"', page)
        self.assertIn("/api/learning", script)
        self.assertIn("renderLearningDashboard", script)
        self.assertIn("company_profile", script)

    def test_ui_exposes_versioned_match_calibration_view(self):
        page = (PROJECT_ROOT / "review_ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("匹配校准", page)
        self.assertIn('id="matchingView"', page)
        self.assertIn('id="matchJobList"', page)
        self.assertIn('id="matchDetail"', page)
        self.assertIn("/api/matches", script)
        self.assertIn("renderMatchingDashboard", script)
        self.assertIn("renderMatchDetail", script)
        self.assertIn("information_status", script)
        self.assertIn("previous_fit_tier", script)

    def test_ui_exposes_boundary_calibration_report(self):
        page = (PROJECT_ROOT / "review_ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("边界复核", page)
        self.assertIn('id="boundaryView"', page)
        self.assertIn('id="boundaryGroups"', page)
        self.assertIn("/api/boundary-calibration", script)
        self.assertIn("renderBoundaryDashboard", script)
        self.assertIn("个人备注", script)
        self.assertIn("JD 证据", script)

    def test_ui_invalidates_boundary_cache_and_renders_all_decision_evidence(self):
        page = (PROJECT_ROOT / "review_ui" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function invalidateMatchResults()", script)
        self.assertEqual(script.count("invalidateMatchResults();"), 2)
        self.assertIn("job.hard_requirements", script)
        self.assertIn("job.strengths", script)
        self.assertIn("本轮强冲突", page)
        self.assertIn("本轮学习信号", page)

    def test_ui_keeps_platform_in_review_urls_and_refreshes_learning_after_collection(self):
        script = (PROJECT_ROOT / "review_ui" / "app.js").read_text(encoding="utf-8")
        invalidator = script.split("function invalidateMatchResults()", 1)[1].split(
            "function renderCalibrationDashboard", 1
        )[0]
        collection_poll = script.split("async function pollCollectionStatus()", 1)[1].split(
            "function showCollectionPollingError", 1
        )[0]

        self.assertIn("query.set(\"platform\", platform)", script)
        self.assertIn("open.dataset.platform = job.platform", script)
        self.assertIn("jobButton.dataset.platform = signal.platform", script)
        self.assertIn("if (signal.sample_name)", script)
        self.assertIn("reviewButton.dataset.platform = match.platform", script)
        self.assertIn("selectMatch(job.job_id, job.platform)", script)
        self.assertIn("button.dataset.platform", script)
        self.assertNotIn("state.learning = null", invalidator)
        self.assertIn("state.learning = null", collection_poll)


class ReviewCliTests(unittest.TestCase):
    def test_review_cli_resolves_project_database_without_ai_or_browser(self):
        config = {"collection": {"db_path": "output/jobagent.db"}}
        with (
            patch.object(main_module, "load_config", return_value=config),
            patch.object(main_module, "run_review_server") as run_server,
            patch.object(main_module, "OpenAICompatibleClient") as ai,
            patch.object(main_module, "BossCollector") as collector,
            patch("sys.argv", ["main.py", "--review-jobs", "--review-port", "8899"]),
        ):
            main_module.main()

        run_server.assert_called_once_with(
            (PROJECT_ROOT / "output" / "jobagent.db").resolve(),
            host="127.0.0.1",
            port=8899,
            config=config,
            analysis_output_dir=(PROJECT_ROOT / "output" / "analysis").resolve(),
        )
        ai.assert_not_called()
        collector.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen

import main as main_module
from jobagent.review_server import create_server
from jobagent.matching import (
    analyze_job_match,
    salary_match,
    build_match_dashboard,
    migrate_matching_schema,
    refresh_stale_matches,
    normalize_text,
    run_match_preview,
)


class SalaryMatchingTests(unittest.TestCase):
    def test_salary_match_scores_target_around_ten_k(self):
        result = salary_match("10-15K", {"target_min_k": 10, "target_max_k": 12})
        self.assertEqual(result["level"], "high")
        self.assertGreaterEqual(result["score"], 85)

    def test_salary_match_marks_unknown_without_penalizing_as_rejection(self):
        result = salary_match("", {"target_min_k": 10, "target_max_k": 12})
        self.assertEqual(result["level"], "unknown")
        self.assertEqual(result["score"], 50)

    def test_match_analysis_contains_salary_signal(self):
        result = analyze_job_match(
            {"title": "AI 应用工程师", "jd_text": "使用 Python 和 Agent 开发。", "salary_raw": "10-15K"},
            {"profile_id": "p", "version": "v1", "target_tracks": ["ai_application"], "skills": [], "hard_constraints": {}, "preferences": {"salary_target": {"target_min_k": 10, "target_max_k": 12}}},
        )
        self.assertEqual(result.salary_match["level"], "high")
def candidate_profile(version: str = "profile-v1") -> dict:
    return {
        "profile_id": "candidate-chen",
        "version": version,
        "display_name": "首位候选人",
        "experience_years": 1.0,
        "education": {
            "level": "bachelor",
            "school_tiers": ["本科"],
            "graduation_year": 2025,
        },
        "target_tracks": [
            "fde",
            "agent_engineering",
            "rag_engineering",
            "ai_application",
            "solution_delivery",
            "ai_product_delivery",
        ],
        "skills": [
            {
                "id": "python_api",
                "name": "Python 与 API 工程",
                "aliases": ["Python", "FastAPI", "API"],
                "evidence": ["使用 Python 和 FastAPI 交付多个 AI 应用。"],
            },
            {
                "id": "agent_workflows",
                "name": "Agent 与工作流",
                "aliases": ["Agent", "智能体", "LangGraph", "工作流"],
                "evidence": ["从 0 到 1 搭建 Agent 工作流。"],
            },
            {
                "id": "rag_knowledge",
                "name": "RAG 与知识库",
                "aliases": ["RAG", "pgvector", "向量检索", "知识库"],
                "evidence": ["构建 BGE + pgvector RAG。"],
            },
            {
                "id": "delivery_fde",
                "name": "FDE 与方案交付",
                "aliases": ["客户需求", "方案", "部署", "交付", "FDE"],
                "evidence": ["独立负责需求、开发、部署和持续维护。"],
            },
            {
                "id": "fullstack_web",
                "name": "前端与全栈",
                "aliases": ["React", "TypeScript", "Next.js", "全栈"],
                "evidence": ["独立完成多个系统的前后端。"],
            },
        ],
        "adjacent_skills": ["Java", "Linux", "Docker", "MySQL", "Redis"],
        "hard_constraints": {
            "maximum_required_experience_years": 3,
            "reject_third_party": True,
            "reject_dispatch": True,
            "reject_long_term_overseas": True,
            "blocked_core_domains": ["mcu_hardware", "go_rust_core"],
        },
        "preferences": {
            "preferred_cities": ["上海", "深圳", "广州", "杭州"],
            "positive_domains": ["跨境电商", "供应链", "AI 产品"],
        },
    }


class TextNormalizationTests(unittest.TestCase):
    def test_nfkc_normalizes_compatibility_characters_and_whitespace(self):
        self.assertEqual(normalize_text("硕⼠\u3000及以上\r\n学历"), "硕士 及以上\n学历")


class PersonalMatchDecisionTests(unittest.TestCase):
    def test_hard_masters_requirement_is_not_fit(self):
        result = analyze_job_match(
            {
                "title": "大模型应用开发工程师",
                "jd_text": "计算机相关专业硕⼠及以上学历，985/211 可放宽至优秀本科毕业生。",
                "education_raw": "硕士",
                "experience_raw": "1-3年",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertEqual(result.recommendation, "not_fit")
        self.assertTrue(any(item["id"] == "education_mismatch" for item in result.hard_requirements))
        self.assertTrue(any("硕士" in item["jd_evidence"] for item in result.hard_requirements))

    def test_mcu_hardware_core_requirement_is_not_fit(self):
        result = analyze_job_match(
            {
                "title": "AI RAG 数据知识库工程师",
                "jd_text": (
                    "负责 RAG 知识库开发。必须熟练阅读电路原理图，识别常见 MCU 型号，"
                    "深入理解 MCU 硬件架构，熟悉 UART/SPI/I2C/CAN 接口。"
                ),
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "深圳",
            },
            candidate_profile(),
        )

        self.assertEqual(result.recommendation, "not_fit")
        self.assertTrue(any(item["id"] == "mcu_hardware" for item in result.hard_requirements))

    def test_ai_product_fde_is_not_excluded_by_product_title(self):
        result = analyze_job_match(
            {
                "title": "AI 产品经理（FDE）",
                "jd_text": (
                    "面向客户完成需求调研，亲自使用 Python 和 Agent 工作流搭建原型，"
                    "推动 AI 解决方案部署交付和效果复盘。"
                ),
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "深圳",
            },
            candidate_profile(),
        )

        self.assertEqual(result.track, "ai_product_delivery")
        self.assertEqual(result.recommendation, "challenge")
        self.assertFalse(result.hard_requirements)
        self.assertGreaterEqual(len(result.strengths), 3)

    def test_missing_adjacent_tool_becomes_challenge_with_evidence(self):
        result = analyze_job_match(
            {
                "title": "AI Agent 开发工程师",
                "jd_text": "必须具备多个 n8n 或 Coze Agent 项目的完整实战经验，负责部署交付。",
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "深圳",
            },
            candidate_profile(),
        )

        self.assertEqual(result.recommendation, "challenge")
        self.assertTrue(any(item["id"] == "low_code_agent" for item in result.gaps))
        self.assertTrue(all(item.get("jd_evidence") for item in result.gaps))

    def test_short_jd_keeps_decision_but_marks_information_gap(self):
        result = analyze_job_match(
            {
                "title": "AI 应用工程师",
                "jd_text": "负责 AI 应用开发。",
                "education_raw": "",
                "experience_raw": "",
                "location": "广州",
            },
            candidate_profile(),
        )

        self.assertNotEqual(result.recommendation, "not_fit")
        self.assertEqual(result.information_status, "insufficient")
        self.assertTrue(result.uncertainties)
        self.assertLess(result.confidence, 0.7)

    def test_information_insufficient_never_recommends_priority(self):
        result = analyze_job_match(
            {
                "title": "AI Agent 应用工程师",
                "jd_text": "负责 Agent、RAG、Python、FastAPI 应用开发。",
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "上海",
                "salary_raw": "15-25K",
                "company": "样例公司",
            },
            candidate_profile(),
        )

        self.assertEqual(result.information_status, "insufficient")
        self.assertNotEqual(result.recommendation, "priority")

    def test_negated_skill_requirement_does_not_create_a_learning_gap(self):
        result = analyze_job_match(
            {
                "title": "AI 应用工程师",
                "jd_text": "负责 AI 应用开发，无需模型部署与推理经验。",
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "上海",
                "salary_raw": "15-25K",
                "company": "样例公司",
            },
            candidate_profile(),
        )

        self.assertFalse(any(item["id"] == "model_serving" for item in result.gaps))

    def test_alternative_languages_do_not_make_go_a_hard_requirement(self):
        result = analyze_job_match(
            {
                "title": "AI Agent 开发工程师",
                "jd_text": (
                    "熟练掌握 Python、JavaScript/TypeScript、Java 或 Go 中的至少一种语言，"
                    "具备独立开发和调试能力。"
                ),
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "深圳",
            },
            candidate_profile(),
        )

        self.assertFalse(
            any(item["id"] == "go_rust_core" for item in result.hard_requirements)
        )
        self.assertNotEqual(result.recommendation, "not_fit")

    def test_structured_experience_range_takes_precedence_over_other_jd_years(self):
        result = analyze_job_match(
            {
                "title": "前沿部署工程师（FDE）",
                "jd_text": "2 年以上软件开发经验，负责 AI Agent 和 RAG 项目交付。",
                "education_raw": "本科",
                "experience_raw": "3-5年",
                "location": "上海",
            },
            candidate_profile(),
        )

        requirement = next(
            item
            for item in result.gaps
            if item["id"] == "experience_depth"
        )
        self.assertEqual(requirement["jd_evidence"], "3-5年")
        self.assertEqual(requirement["severity"], "major")
        self.assertEqual(result.recommendation, "challenge")

    def test_unlimited_experience_label_uses_explicit_jd_requirement_as_evidence(self):
        result = analyze_job_match(
            {
                "title": "RAG 工程师",
                "jd_text": "本科及以上学历，3 年以上 NLP 或 RAG 技术研发经验。",
                "education_raw": "本科",
                "experience_raw": "经验不限",
                "location": "上海",
            },
            candidate_profile(),
        )

        requirement = next(
            item
            for item in result.gaps
            if item["id"] == "experience_depth"
        )
        self.assertIn("3 年以上", requirement["jd_evidence"])
        self.assertNotIn("经验不限", requirement["jd_evidence"])

    def test_software_and_hardware_architecture_is_not_an_mcu_requirement(self):
        result = analyze_job_match(
            {
                "title": "大模型算法工程师",
                "jd_text": (
                    "负责超大模型的部署和调优，确保模型在各种软硬件架构上的功能、"
                    "性能和稳定性。"
                ),
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertFalse(
            any(item["id"] == "mcu_hardware" for item in result.hard_requirements)
        )

    def test_overseas_posting_in_title_is_a_hard_constraint(self):
        result = analyze_job_match(
            {
                "title": "AI 应用工程师（驻英国）",
                "jd_text": "base 伦敦，负责 AI Agent 工作流开发和部署。",
                "education_raw": "本科",
                "experience_raw": "经验不限",
                "location": "伦敦",
            },
            candidate_profile(),
        )

        self.assertTrue(
            any(item["id"] == "long_term_overseas" for item in result.hard_requirements)
        )
        self.assertEqual(result.recommendation, "not_fit")

    def test_experience_at_personal_limit_is_a_major_gap_not_a_hard_gate(self):
        result = analyze_job_match(
            {
                "title": "AI Agent 开发工程师",
                "jd_text": "负责 Agent 工作流开发，要求 3 年以上相关经验。",
                "education_raw": "本科",
                "experience_raw": "3-5年",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertFalse(
            any(item["id"] == "experience_requirement" for item in result.hard_requirements)
        )
        self.assertTrue(
            any(
                item["id"] == "experience_depth" and item["severity"] == "major"
                for item in result.gaps
            )
        )
        self.assertEqual(result.recommendation, "challenge")

    def test_experience_above_personal_limit_is_a_hard_gate(self):
        result = analyze_job_match(
            {
                "title": "AI Agent 开发工程师",
                "jd_text": "负责 Agent 平台核心研发。",
                "education_raw": "本科",
                "experience_raw": "5-10年",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertTrue(
            any(item["id"] == "experience_requirement" for item in result.hard_requirements)
        )
        self.assertEqual(result.recommendation, "not_fit")

    def test_language_list_with_known_alternative_does_not_require_go(self):
        result = analyze_job_match(
            {
                "title": "AI Agent 研发工程师",
                "jd_text": "熟悉前后端技术栈（Java/Go/Node.js + React/Vue）。",
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertFalse(
            any(item["id"] == "go_rust_core" for item in result.hard_requirements)
        )

    def test_english_go_verb_is_not_the_go_language(self):
        result = analyze_job_match(
            {
                "title": "FDE Intern - AI Solutions Engineering",
                "jd_text": "Use Python to go from client requirements to production systems.",
                "education_raw": "本科",
                "experience_raw": "经验不限",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertFalse(
            any(item["id"] == "go_rust_core" for item in result.hard_requirements)
        )

    def test_rust_core_stack_remains_a_hard_gate(self):
        result = analyze_job_match(
            {
                "title": "大模型应用开发",
                "jd_text": "基于 Python/Rust 技术栈搭建核心系统。",
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertTrue(
            any(item["id"] == "go_rust_core" for item in result.hard_requirements)
        )

    def test_preferred_school_tier_is_not_a_hard_gate(self):
        result = analyze_job_match(
            {
                "title": "AI 前线部署工程师",
                "jd_text": "任职要求：全日制本科及以上学历，985、211、双一流优先。",
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertFalse(
            any(item["id"] == "school_tier_mismatch" for item in result.hard_requirements)
        )

    def test_overseas_keyword_and_uae_posting_are_a_hard_constraint(self):
        result = analyze_job_match(
            {
                "title": "海外前沿部署工程师（FDE）（驻阿联酋）",
                "jd_text": "负责客户现场的 AI 解决方案交付。",
                "education_raw": "本科",
                "experience_raw": "1-3年",
                "location": "上海",
            },
            candidate_profile(),
        )

        self.assertTrue(
            any(item["id"] == "long_term_overseas" for item in result.hard_requirements)
        )


def create_preview_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
        CREATE TABLE job_details (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          title TEXT,
          company TEXT,
          detail_status TEXT,
          jd_text TEXT,
          detail_text TEXT,
          salary_raw TEXT,
          location TEXT,
          experience_raw TEXT,
          education_raw TEXT,
          recruiter_name TEXT,
          recruiter_role TEXT,
          labels_json TEXT,
          source_url TEXT,
          collected_at TEXT,
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
          rejection_reason TEXT NOT NULL,
          note TEXT NOT NULL,
          learning_intent INTEGER,
          decided_at TEXT NOT NULL,
          PRIMARY KEY(platform, job_id)
        );
            """
        )
        connection.execute(
            "INSERT INTO job_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "boss",
                "job-1",
                "AI 产品经理（FDE）",
                "示例公司",
                "collected",
                "面向客户使用 Python 和 Agent 搭建原型并部署交付。",
                "",
                "15-25K",
                "深圳",
                "1-3年",
                "本科",
                "招聘者",
                "HR",
                "[]",
                "https://example.invalid/job-1",
                "2026-08-01T00:00:00+08:00",
            ),
        )
        connection.execute(
            "INSERT INTO job_positioning_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "boss",
                "job-1",
                "2026-08-01T00:00:00+08:00",
                "product",
                "exclude",
                35,
                20,
                "monthly",
                15,
                25,
                12,
                '["product_role"]',
                '["产品岗位"]',
            ),
        )
        connection.execute(
            "INSERT INTO job_decisions VALUES (?,?,?,?,?,?,?)",
            (
                "boss",
                "job-1",
                "want",
                "",
                "保留这条原始备注",
                1,
                "2026-08-02T00:00:00+08:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()


class MatchPersistenceTests(unittest.TestCase):
    def test_missing_jd_hash_is_baseline_pending_not_jd_changed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            run_match_preview(db_path, candidate_profile(), "match-v2.0")
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                "UPDATE job_match_analyses SET jd_content_hash = NULL WHERE job_id = 'job-1'"
            )
            connection.commit()
            try:
                job = build_match_dashboard(connection)["jobs"][0]
            finally:
                connection.close()

            self.assertTrue(job["match_stale"])
            self.assertEqual(job["match_refresh_reason"], "baseline_pending")

    def test_changed_jd_hash_is_jd_changed_not_baseline_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            run_match_preview(db_path, candidate_profile(), "match-v2.0")
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                "UPDATE job_details SET jd_text = jd_text || ' 新增 Kubernetes 门槛' WHERE job_id = 'job-1'"
            )
            connection.commit()
            try:
                job = build_match_dashboard(connection)["jobs"][0]
            finally:
                connection.close()

            self.assertTrue(job["match_stale"])
            self.assertEqual(job["match_refresh_reason"], "jd_changed")

    def test_baseline_refresh_history_is_marked_as_initialization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            run_match_preview(db_path, candidate_profile(), "match-v2.0")
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                "UPDATE job_match_analyses SET jd_content_hash = NULL WHERE job_id = 'job-1'"
            )
            connection.commit()
            connection.close()

            refresh_stale_matches(db_path, limit=1)
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    "SELECT jd_content_hash FROM job_match_history WHERE job_id = 'job-1'"
                ).fetchone()
                job = build_match_dashboard(connection)["jobs"][0]
            finally:
                connection.close()

            self.assertIsNone(row["jd_content_hash"])
            self.assertEqual(
                job["refresh_comparison"]["refresh_kind"],
                "baseline_initialization",
            )

    def test_refresh_stale_matches_updates_only_changed_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            run_match_preview(db_path, candidate_profile(), "match-v2.0")
            connection = sqlite3.connect(db_path)
            connection.execute(
                "UPDATE job_details SET jd_text = jd_text || ' 新增 Kubernetes 门槛' WHERE job_id = 'job-1'"
            )
            connection.commit()
            connection.close()

            result = refresh_stale_matches(db_path, limit=1)
            self.assertEqual(result["refreshed"], 1)
            self.assertEqual(result["baseline_initialized"], 0)
            self.assertEqual(result["jd_changed_refreshed"], 1)
            self.assertEqual(result["remaining"], 0)
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM job_match_history").fetchone()[0],
                    1,
                )
                row = connection.execute(
                    "SELECT jd_content_hash FROM job_match_analyses WHERE job_id = 'job-1'"
                ).fetchone()
                self.assertTrue(row["jd_content_hash"])
                job = build_match_dashboard(connection)["jobs"][0]
                self.assertFalse(job["match_stale"])
                self.assertIn("previous_match", job)
                self.assertTrue(job["previous_match"]["recommendation"])
                self.assertIn("refresh_changed", job)
            finally:
                connection.close()

    def test_refresh_queue_prioritizes_wanted_priority_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                INSERT INTO job_details (
                  platform, job_id, title, company, detail_status, jd_text, detail_text,
                  salary_raw, location, experience_raw, education_raw, recruiter_name,
                  recruiter_role, labels_json, source_url, collected_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "boss", "job-2", "AI Agent 工程师", "另一家公司", "collected",
                    "使用 Python 和 Agent 开发应用。", "", "15-25K", "深圳",
                    "1-3年", "本科", "招聘者", "HR", "[]",
                    "https://example.invalid/job-2", "2026-08-01T00:00:00+08:00",
                ),
            )
            connection.execute(
                "INSERT INTO job_decisions VALUES (?,?,?,?,?,?,?)",
                ("boss", "job-2", "reject", "方向不符", "", 0, "2026-08-02T00:00:00+08:00"),
            )
            connection.execute(
                """
                UPDATE job_details
                   SET title = 'AI Agent 应用工程师',
                       jd_text = '负责使用 Python、Agent、RAG 开发 AI 应用，完成部署交付。熟悉 FastAPI、Docker，学历本科，1-3年经验。'
                 WHERE job_id = 'job-1'
                """
            )
            connection.execute(
                "UPDATE job_details SET jd_text = jd_text || ' 新增 Kubernetes 门槛'"
            )
            connection.commit()
            connection.close()

            run_match_preview(db_path, candidate_profile(), "match-v2.0")
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                "UPDATE job_details SET jd_text = jd_text || ' 再次更新职责'"
            )
            connection.commit()
            try:
                dashboard = build_match_dashboard(connection)
                stale = [job for job in dashboard["jobs"] if job["match_stale"]]
            finally:
                connection.close()

            stale.sort(key=lambda job: (job["refresh_priority"], job["job_id"]))
            self.assertEqual([job["job_id"] for job in stale], ["job-1", "job-2"])
            self.assertEqual(stale[0]["refresh_priority"], 1)
            self.assertEqual(stale[0]["refresh_priority_reason"], "想投且匹配为优先投")
            self.assertEqual(stale[1]["refresh_priority"], 7)

            result = refresh_stale_matches(db_path, limit=1)
            self.assertEqual(result["refreshed_job_ids"], ["job-1"])

    def test_jd_content_change_marks_existing_match_stale_without_recomputing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            run_match_preview(db_path, candidate_profile(), "match-v2.0")
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                "UPDATE job_details SET jd_text = jd_text || ' 必须熟练阅读电路原理图，识别常见 MCU 型号，深入理解 MCU 硬件架构。' WHERE job_id = 'job-1'"
            )
            connection.commit()
            try:
                dashboard = build_match_dashboard(connection)
            finally:
                connection.close()
            self.assertTrue(dashboard["jobs"][0]["match_stale"])
            self.assertEqual(dashboard["snapshot"]["stale"], 1)

    def test_profile_and_match_versions_coexist_without_touching_v1_or_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)

            first = run_match_preview(db_path, candidate_profile("profile-v1"), "match-v2.0")
            second = run_match_preview(db_path, candidate_profile("profile-v2"), "match-v2.1")

            self.assertEqual(first.total, 1)
            self.assertEqual(second.total, 1)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM candidate_profiles").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM job_match_analyses").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM job_positioning_analysis").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute("SELECT interest, note FROM job_decisions").fetchone(),
                    ("want", "保留这条原始备注"),
                )
                versions = {
                    row[0]
                    for row in connection.execute(
                        "SELECT analysis_version FROM job_match_analyses ORDER BY analysis_version"
                    )
                }
                self.assertEqual(versions, {"match-v2.0", "match-v2.1"})
            finally:
                connection.close()

    def test_rerun_replaces_same_version_snapshot_without_touching_decisions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            profile = candidate_profile()

            first = run_match_preview(db_path, profile, "match-v2.0")
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "UPDATE job_details SET detail_status = 'failed' WHERE job_id = 'job-1'"
                )
                connection.commit()
            finally:
                connection.close()

            second = run_match_preview(db_path, profile, "match-v2.0")

            self.assertEqual(first.total, 1)
            self.assertEqual(second.total, 0)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM job_match_analyses WHERE analysis_version = 'match-v2.0'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT interest, note FROM job_decisions"
                    ).fetchone(),
                    ("want", "保留这条原始备注"),
                )
            finally:
                connection.close()

    def test_schema_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            sqlite3.connect(db_path).close()
            connection = sqlite3.connect(db_path)
            try:
                migrate_matching_schema(connection)
                migrate_matching_schema(connection)
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("candidate_profiles", tables)
                self.assertIn("job_match_analyses", tables)
            finally:
                connection.close()


class MatchPreviewApiTests(unittest.TestCase):
    def test_api_returns_latest_preview_and_evidence_detail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            run_match_preview(db_path, candidate_profile(), "match-v2.0")
            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(f"{base_url}/api/matches") as response:
                    dashboard = json.load(response)
                self.assertEqual(dashboard["snapshot"]["analysis_version"], "match-v2.0")
                self.assertEqual(dashboard["snapshot"]["total"], 1)
                self.assertEqual(dashboard["snapshot"]["priority"], 0)
                self.assertEqual(dashboard["snapshot"]["conflicts"], 1)
                self.assertEqual(dashboard["jobs"][0]["previous_fit_tier"], "exclude")
                self.assertTrue(dashboard["jobs"][0]["changed"])
                self.assertEqual(dashboard["jobs"][0]["refresh_priority"], 2)
                self.assertEqual(
                    dashboard["jobs"][0]["refresh_priority_reason"],
                    "想投但匹配为挑战投",
                )

                with urlopen(f"{base_url}/api/matches/job-1") as response:
                    detail = json.load(response)["match"]
                self.assertEqual(detail["recommendation"], "challenge")
                self.assertEqual(detail["decision"]["interest"], "want")
                self.assertTrue(detail["strengths"])
                self.assertIn("jd_evidence", detail["strengths"][0])
                self.assertEqual(detail["company"], "示例公司")
                self.assertEqual(detail["refresh_priority"], 2)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_api_refreshes_stale_matches_only_after_explicit_post(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            run_match_preview(db_path, candidate_profile(), "match-v2.0")
            connection = sqlite3.connect(db_path)
            connection.execute(
                "UPDATE job_details SET jd_text = jd_text || ' 必须熟练阅读电路原理图，识别常见 MCU 型号，深入理解 MCU 硬件架构。' WHERE job_id = 'job-1'"
            )
            connection.commit()
            connection.close()
            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/matches/refresh-stale",
                    data=json.dumps({"limit": 1}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request) as response:
                    payload = json.load(response)
                self.assertEqual(payload["refreshed"], 1)
                self.assertEqual(payload["remaining"], 0)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/matches") as response:
                    dashboard = json.load(response)
                self.assertEqual(dashboard["snapshot"]["recently_refreshed"], 1)
                self.assertIsNotNone(dashboard["jobs"][0]["previous_match"])

                with urlopen(f"{base_url}/api/matches/job-1") as response:
                    detail = json.load(response)["match"]
                comparison = detail["refresh_comparison"]
                self.assertEqual(
                    [item["id"] for item in comparison["hard_requirements_added"]],
                    ["mcu_hardware"],
                )
                self.assertEqual(comparison["hard_requirements_removed"], [])
                self.assertIn("score_delta", comparison)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_api_returns_actionable_empty_state_without_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            create_preview_database(db_path)
            server = create_server(db_path, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/matches"
                ) as response:
                    dashboard = json.load(response)
                self.assertIsNone(dashboard["snapshot"])
                self.assertEqual(dashboard["jobs"], [])
                self.assertIn("--match-preview", dashboard["empty_message"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class MatchPreviewCliTests(unittest.TestCase):
    def test_match_preview_cli_is_offline_and_uses_configured_profile(self):
        cfg = {
            "collection": {"db_path": "output/jobagent.db"},
            "matching": {
                "profile_file": "private/candidate-profile.json",
                "analysis_version": "match-v2.0",
            },
        }
        profile = candidate_profile()
        result = SimpleNamespace(
            total=300,
            analysis_version="match-v2.0",
            profile_id="candidate-chen",
            profile_version="profile-v1",
            distribution={"priority": 80, "challenge": 120, "not_fit": 100},
        )
        with (
            patch.object(main_module, "load_config", return_value=cfg) as load_config,
            patch.object(main_module, "load_candidate_profile", return_value=profile) as load_profile,
            patch.object(main_module, "run_match_preview", return_value=result) as run_preview,
            patch.object(main_module, "OpenAICompatibleClient") as ai,
            patch.object(main_module, "BossCollector") as collector,
            patch.object(main_module, "BossPlatform") as platform,
            patch("builtins.print"),
            patch("sys.argv", ["main.py", "--match-preview"]),
        ):
            main_module.main()

        load_config.assert_called_once_with(require_ai=False)
        load_profile.assert_called_once()
        run_preview.assert_called_once()
        self.assertEqual(run_preview.call_args.args[2], "match-v2.0")
        ai.assert_not_called()
        collector.assert_not_called()
        platform.assert_not_called()


if __name__ == "__main__":
    unittest.main()

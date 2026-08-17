import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main as main_module
from jobagent.analysis import analyze_job, parse_salary, run_analysis
from jobagent.collection import CollectionStore


class SalaryParsingTests(unittest.TestCase):
    def test_parses_monthly_salary_and_pay_months(self):
        salary = parse_salary("15-25K·14薪")

        self.assertEqual(salary.kind, "monthly")
        self.assertEqual((salary.minimum_k, salary.maximum_k), (15.0, 25.0))
        self.assertEqual(salary.pay_months, 14)

    def test_keeps_daily_intern_pay_out_of_monthly_salary(self):
        salary = parse_salary("250-400元/天")

        self.assertEqual(salary.kind, "daily")
        self.assertIsNone(salary.minimum_k)
        self.assertIsNone(salary.maximum_k)


class PositioningAnalysisTests(unittest.TestCase):
    def test_direct_entry_level_agent_fde_is_core(self):
        result = analyze_job(
            {
                "title": "AI 前线部署工程师 FDE",
                "jd_text": (
                    "负责客户业务需求调研，使用 Python、LangGraph 和 RAG 开发 Agent，"
                    "完成方案设计、系统集成、部署交付和效果复盘。"
                ),
                "salary_raw": "15-25K·14薪",
                "experience_raw": "1-3年",
                "recruiter_role": "HR",
                "labels_json": '["fde", "agent_rag", "direct"]',
            }
        )

        self.assertEqual(result.track, "fde")
        self.assertEqual(result.fit_tier, "core")
        self.assertGreaterEqual(result.relevance_score, 75)
        self.assertGreaterEqual(result.fde_value_score, 70)
        self.assertEqual(result.risks, ())

    def test_high_salary_headhunter_fde_is_relevant_but_excluded(self):
        result = analyze_job(
            {
                "title": "前线部署工程师 Forward Deployed Engineer",
                "jd_text": "负责客户需求分析、AI 解决方案设计和现场交付。",
                "salary_raw": "50-70K·14薪",
                "experience_raw": "3-5年",
                "recruiter_role": "猎头顾问",
                "labels_json": '["fde", "agent_rag", "third_party"]',
            }
        )

        self.assertGreaterEqual(result.relevance_score, 60)
        self.assertEqual(result.fit_tier, "exclude")
        self.assertIn("third_party", result.risks)
        self.assertIn("salary_stretch", result.risks)
        self.assertIn("seniority_gap", result.risks)

    def test_pure_java_and_sales_roles_are_excluded(self):
        java = analyze_job(
            {
                "title": "Java 后端开发工程师",
                "jd_text": "Spring Boot、MyBatis 和微服务开发。",
                "salary_raw": "10-15K",
                "experience_raw": "1-3年",
                "labels_json": '["pure_java", "direct"]',
            }
        )
        sales = analyze_job(
            {
                "title": "AI 解决方案 BD",
                "jd_text": "负责销售指标、客户拓展和商务谈判。",
                "salary_raw": "10-20K",
                "experience_raw": "1-3年",
                "labels_json": '["agent_rag", "direct"]',
            }
        )

        self.assertEqual(java.fit_tier, "exclude")
        self.assertIn("pure_java", java.risks)
        self.assertEqual(sales.fit_tier, "exclude")
        self.assertIn("non_engineering_role", sales.risks)

    def test_product_title_is_not_promoted_by_fde_or_agent_keywords(self):
        result = analyze_job(
            {
                "title": "AI 产品经理（FDE / Agent 培养方向）",
                "jd_text": "负责客户需求、产品规划和 Agent 方案设计。",
                "salary_raw": "15-25K",
                "experience_raw": "1-3年",
                "recruiter_role": "HR",
                "labels_json": '["fde", "agent_rag", "direct"]',
            }
        )

        self.assertEqual(result.track, "product")
        self.assertEqual(result.fit_tier, "exclude")
        self.assertIn("product_role", result.risks)

    def test_compact_fde_title_wins_over_agent_subdirection(self):
        result = analyze_job(
            {
                "title": "FDE工程师（AI Agent方向）",
                "jd_text": "负责智能体开发和客户交付。",
                "salary_raw": "15-25K",
                "experience_raw": "1-3年",
                "labels_json": '["fde", "agent_rag", "direct"]',
            }
        )

        self.assertEqual(result.track, "fde")

    def test_solution_role_wins_over_embedded_agent_keyword(self):
        result = analyze_job(
            {
                "title": "售前解决方案专家（AI智能体方向）",
                "jd_text": "负责客户需求分析、POC 和方案交付。",
                "salary_raw": "10-15K",
                "experience_raw": "1-3年",
                "labels_json": '["agent_rag", "direct"]',
            }
        )

        self.assertEqual(result.track, "solution_delivery")

    def test_normal_fde_customer_work_is_not_treated_as_outsourcing(self):
        result = analyze_job(
            {
                "title": "FDE 前线部署工程师",
                "jd_text": (
                    "你不是驻场外包，而是负责驻场调研行业痛点、客户共创、"
                    "Agent 方案开发与落地。"
                ),
                "salary_raw": "15-25K",
                "experience_raw": "1-3年",
                "recruiter_role": "HR",
                "labels_json": '["fde", "agent_rag", "outsourcing_risk"]',
            }
        )

        self.assertEqual(result.fit_tier, "core")
        self.assertNotIn("outsourcing_risk", result.risks)

    def test_long_term_onsite_is_stretch_but_explicit_dispatch_is_excluded(self):
        onsite = analyze_job(
            {
                "title": "AI FDE 工程师",
                "jd_text": "负责 Agent 开发和客户交付，需长期驻场客户现场办公。",
                "salary_raw": "15-25K",
                "experience_raw": "1-3年",
                "recruiter_role": "HR",
                "labels_json": '["fde", "agent_rag", "outsourcing_risk"]',
            }
        )
        dispatched = analyze_job(
            {
                "title": "AI 实施工程师",
                "jd_text": "与第三方签约，以劳务派遣形式进入客户项目。",
                "salary_raw": "15-25K",
                "experience_raw": "1-3年",
                "recruiter_role": "HR",
                "labels_json": '["agent_rag", "outsourcing_risk"]',
            }
        )

        self.assertEqual(onsite.fit_tier, "stretch")
        self.assertIn("onsite_heavy", onsite.risks)
        self.assertNotIn("outsourcing_risk", onsite.risks)
        self.assertEqual(dispatched.fit_tier, "exclude")
        self.assertIn("outsourcing_risk", dispatched.risks)

    def test_graduate_only_is_excluded_and_overseas_assignment_is_stretch(self):
        graduate = analyze_job(
            {
                "title": "RAG 应用开发工程师",
                "jd_text": "负责 RAG 和 Agent 开发。",
                "salary_raw": "20-30K",
                "experience_raw": "在校/应届",
                "labels_json": '["agent_rag", "direct"]',
            }
        )
        overseas = analyze_job(
            {
                "title": "AI 应用工程师（驻英国）",
                "jd_text": "负责 Agent 应用开发和业务交付。",
                "salary_raw": "20-30K",
                "experience_raw": "1-3年",
                "labels_json": '["agent_rag", "direct"]',
            }
        )

        self.assertEqual(graduate.fit_tier, "exclude")
        self.assertIn("graduate_only", graduate.risks)
        self.assertEqual(overseas.fit_tier, "stretch")
        self.assertIn("overseas_assignment", overseas.risks)


class AnalysisPersistenceTests(unittest.TestCase):
    def test_run_analysis_persists_rows_and_exports_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.db"
            store = CollectionStore(db_path)
            run_id = store.start_run(2)
            for index, title in enumerate(("AI Agent 开发工程师", "Java 后端开发工程师"), 1):
                store.save_detail(
                    {
                        "platform": "boss",
                        "job_id": f"job-{index}",
                        "source_key": "search:上海:AI Agent 开发",
                        "source_page": 1,
                        "source_url": f"https://example.invalid/{index}",
                        "title": title,
                        "company": "Example",
                        "jd": "Agent RAG Python" if index == 1 else "Spring Boot MyBatis",
                        "salary_raw": "15-25K",
                        "location": "上海",
                        "experience_raw": "1-3年",
                        "education_raw": "本科",
                        "recruiter_role": "HR",
                    },
                    run_id,
                )
            store.close()

            result = run_analysis(db_path, root / "analysis")

            self.assertEqual(result.total, 2)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM job_positioning_analysis").fetchone()[0],
                    2,
                )
            finally:
                connection.close()
            self.assertTrue(result.csv_path.exists())
            self.assertTrue(result.report_path.exists())
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("## 核心池城市", report)
            self.assertIn("## 薪资与经验", report)
            with result.csv_path.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 2)

            connection = sqlite3.connect(db_path)
            connection.execute(
                "UPDATE job_details SET detail_status = 'failed' WHERE job_id = 'job-2'"
            )
            connection.commit()
            connection.close()

            run_analysis(db_path, root / "analysis")
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM job_positioning_analysis").fetchone()[0],
                    1,
                )
            finally:
                connection.close()


class AnalysisCliTests(unittest.TestCase):
    def test_analysis_cli_does_not_construct_paid_or_application_services(self):
        result = SimpleNamespace(
            total=300,
            csv_path=Path("analysis/jobs.csv"),
            report_path=Path("analysis/report.md"),
        )
        cfg = {"collection": {"db_path": "../output/jobagent.db"}}

        with (
            patch.object(main_module, "load_config", return_value=cfg) as load_config,
            patch.object(main_module, "run_analysis", return_value=result) as analyze,
            patch.object(main_module, "OpenAICompatibleClient") as ai,
            patch.object(main_module, "ResumeManager") as resume,
            patch.object(main_module, "create_provider") as knowledge,
            patch.object(main_module, "Store") as application_store,
            patch("builtins.print"),
            patch("sys.argv", ["main.py", "--analyze-collection"]),
        ):
            main_module.main()

        load_config.assert_called_once_with(require_ai=False)
        analyze.assert_called_once()
        ai.assert_not_called()
        resume.assert_not_called()
        knowledge.assert_not_called()
        application_store.assert_not_called()


if __name__ == "__main__":
    unittest.main()

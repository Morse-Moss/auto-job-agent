import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import main as main_module
from jobagent.collection import (
    CollectionStore,
    classify_job,
    job_from_detail_payload,
    normalize_search_card,
)
from jobagent import config as config_module
from jobagent.platforms.boss_collector import BossCollector


class CollectionStoreTests(unittest.TestCase):
    def test_detail_collection_is_incremental_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=3)
            job = {
                "platform": "boss",
                "job_id": "job-1",
                "source": "boss-favorites",
                "source_key": "favorites",
                "source_page": 1,
                "source_url": "https://www.zhipin.com/job_detail/job-1.html",
                "title": "AI FDE",
                "company": "Example",
                "jd": "负责客户需求诊断、Agent/RAG 开发与部署交付。",
            }

            store.save_observation(job, run_id)
            store.save_detail(job, run_id)
            store.save_detail({**job, "jd": job["jd"] + "持续复盘效果。"}, run_id)
            store.update_cursor("favorites", page_number=2, item_index=4, status="running")

            self.assertEqual(store.collected_detail_count(), 1)
            self.assertTrue(store.has_collected_detail("job-1"))
            self.assertEqual(store.get_cursor("favorites")["page_number"], 2)
            self.assertEqual(
                json.loads(store.get_detail("job-1")["labels_json"]),
                ["fde", "agent_rag", "direct"],
            )
            store.finish_run(run_id, "sample_completed")
            self.assertEqual(store.get_run(run_id)["status"], "sample_completed")
            store.close()

    def test_inventory_count_includes_failed_detail_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=2)
            base_job = {
                "platform": "boss",
                "source": "boss-search",
                "source_key": "search:上海:AI 应用工程师",
                "source_page": 1,
                "company": "Example",
            }
            store.save_detail(
                {
                    **base_job,
                    "job_id": "collected",
                    "source_url": "https://example.invalid/collected",
                    "title": "Collected",
                    "jd": "完整 JD",
                },
                run_id,
            )
            store.save_failure(
                {
                    **base_job,
                    "job_id": "failed",
                    "source_url": "https://example.invalid/failed",
                    "title": "Failed",
                },
                run_id,
                "detail unavailable",
            )

            self.assertEqual(store.collected_detail_count(), 1)
            self.assertEqual(store.detail_inventory_count(), 2)
            store.close()

    def test_failed_refresh_preserves_last_successful_jd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=1)
            job = {
                "platform": "boss",
                "source": "boss-search",
                "source_key": "search:上海:AI Agent",
                "source_page": 1,
                "job_id": "stable-job",
                "source_url": "https://example.invalid/stable-job",
                "title": "Stable",
                "company": "Example",
                "jd": "原始 JD 正文",
            }
            store.save_detail(job, run_id)
            store.save_failure({**job, "jd": ""}, run_id, "CAPTCHA required")
            detail = store.get_detail("stable-job")
            self.assertEqual(detail["jd_text"], "原始 JD 正文")
            self.assertEqual(detail["detail_status"], "failed")
            store.close()

    def test_finalize_scan_marks_inventory_jobs_missing_from_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=1)
            base = {
                "platform": "boss",
                "source": "boss-search",
                "source_key": "search:上海:AI Agent",
                "source_page": 1,
                "company": "Example",
            }
            store.save_detail({**base, "job_id": "still-listed", "source_url": "https://example.invalid/1", "title": "Listed", "jd": "JD"}, run_id)
            store.save_detail({**base, "job_id": "missing", "source_url": "https://example.invalid/2", "title": "Missing", "jd": "JD"}, run_id)
            store.finalize_scan(base["source_key"], {"still-listed"}, run_id, observed_at="2026-08-19T00:00:00+08:00")
            row = store.conn.execute(
                "SELECT scan_state, scan_miss_count FROM job_freshness WHERE job_id = 'missing'"
            ).fetchone()
            self.assertEqual(row["scan_state"], "unseen")
            self.assertEqual(row["scan_miss_count"], 1)
            store.close()

    def test_refresh_target_falls_back_to_observation_only_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=1)
            store.save_observation(
                {
                    "platform": "boss",
                    "job_id": "card-only",
                    "source": "boss-search",
                    "source_key": "search:上海:AI Agent",
                    "source_page": 2,
                    "source_url": "https://www.zhipin.com/job_detail/card-only.html",
                    "title": "卡片岗位",
                    "company": "示例公司",
                    "location": "上海",
                    "salary_raw": "20-30K",
                    "experience_raw": "1-3年",
                    "education_raw": "本科",
                    "industry": "人工智能",
                    "financing": "A轮",
                    "company_size": "20-99人",
                    "recruiter_name": "招聘者",
                    "recruiter_role": "HR",
                    "card_text": "卡片岗位",
                },
                run_id,
            )

            self.assertIsNone(store.get_detail("card-only"))
            target = store.get_refresh_target("card-only")

            self.assertIsNotNone(target)
            self.assertEqual(target["job_id"], "card-only")
            self.assertEqual(target["title"], "卡片岗位")
            self.assertEqual(target["company"], "示例公司")
            self.assertEqual(target["source_page"], 2)
            self.assertEqual(target["source_key"], "search:上海:AI Agent")
            self.assertEqual(target["detail_status"], "missing")
            self.assertEqual(target["jd_text"], "")
            store.close()

    def test_closed_refresh_preserves_last_successful_jd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=1)
            job = {
                "platform": "boss",
                "source": "boss-search",
                "source_key": "search:上海:AI Agent",
                "source_page": 1,
                "job_id": "closed-job",
                "source_url": "https://example.invalid/closed-job",
                "title": "Closed",
                "company": "Example",
                "jd": "原始 JD 正文",
            }
            store.save_detail(job, run_id)
            store.save_detail(
                {**job, "jd": "", "detail_text": "职位已关闭"},
                run_id,
                detail_status="closed",
                error="职位已关闭",
            )
            detail = store.get_detail("closed-job")
            self.assertEqual(detail["jd_text"], "原始 JD 正文")
            self.assertEqual(detail["detail_status"], "closed")
            store.close()

    def test_detail_write_failure_rolls_back_freshness_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=1)
            job = {
                "platform": "boss",
                "source": "boss-search",
                "source_key": "search:上海:AI Agent",
                "source_page": 1,
                "job_id": "atomic-job",
                "source_url": "https://example.invalid/atomic-job",
                "title": "Atomic",
                "company": "Example",
                "jd": "原始 JD",
            }
            store.save_detail(job, run_id)
            before = store.conn.execute(
                "SELECT COUNT(*) FROM job_refresh_events WHERE job_id = 'atomic-job'"
            ).fetchone()[0]
            store.conn.execute(
                """
                CREATE TRIGGER reject_atomic_update
                BEFORE UPDATE ON job_details
                WHEN OLD.job_id = 'atomic-job'
                BEGIN
                  SELECT RAISE(FAIL, 'forced detail failure');
                END;
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                store.save_detail({**job, "jd": "更新 JD"}, run_id)
            after = store.conn.execute(
                "SELECT COUNT(*) FROM job_refresh_events WHERE job_id = 'atomic-job'"
            ).fetchone()[0]
            self.assertEqual(after, before)
            store.close()

    def test_collect_only_config_does_not_require_an_ai_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "collection:\n  target: 300\nboss:\n  cdp_endpoint: http://127.0.0.1:17332\n",
                encoding="utf-8",
            )
            with patch.object(config_module, "ROOT", Path(temp_dir)):
                cfg = config_module.load_config(require_ai=False)

        self.assertEqual(cfg["collection"]["target"], 300)

    def test_refresh_delay_uses_detail_delay_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            collector = BossCollector(
                {"collection": {"detail_delay_range_seconds": [35, 65]}},
                CollectionStore(Path(temp_dir) / "jobs.db"),
            )
            self.assertEqual(collector._refresh_delay(), [35, 65])
            collector.store.close()

    def test_existing_detail_schema_is_migrated_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE job_details (
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
                  PRIMARY KEY(platform,job_id)
                )
                """
            )
            connection.execute(
                "INSERT INTO job_details VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "boss",
                    "existing",
                    "old-run",
                    "https://example.invalid/existing",
                    1,
                    "Existing",
                    "Example",
                    "collected",
                    "Existing JD",
                    "Existing detail",
                    "2026-08-05T00:00:00+08:00",
                ),
            )
            connection.commit()
            connection.close()

            store = CollectionStore(db_path)

            self.assertEqual(store.collected_detail_count(), 1)
            self.assertIn("labels_json", store.get_detail("existing").keys())
            store.close()

    def test_scan_coverage_cli_uses_dedicated_read_only_scan(self):
        result = SimpleNamespace(status="completed", scanned_sources=8, error=None)
        with patch.object(main_module, "BossCollector") as collector_cls, patch.object(
            main_module, "load_config", return_value={"collection": {"db_path": "output/jobagent.db"}}
        ), patch.object(main_module.CollectionStore, "close"), patch.object(
            main_module, "resolve_project_path", side_effect=lambda value: Path(value)
        ), patch("sys.argv", ["main.py", "--scan-coverage", "--scan-source", "search"]):
            collector_cls.return_value.scan_coverage.return_value = result
            main_module.main()
            collector_cls.return_value.scan_coverage.assert_called_once_with(source="search", max_sources=8)


class CollectionClassificationTests(unittest.TestCase):
    def test_detail_result_marks_closed_even_when_page_still_contains_jd(self):
        page = Mock()
        page.locator.return_value.inner_text.return_value = (
            "职位已关闭\n职位描述\n负责 Agent 与 RAG 应用交付"
        )
        result = BossCollector._detail_result(
            page,
            {"platform": "boss", "job_id": "closed-with-jd"},
            {
                "zpData": {
                    "jobInfo": {
                        "encryptId": "closed-with-jd",
                        "postDescription": "负责 Agent 与 RAG 应用交付",
                    }
                }
            },
        )

        self.assertEqual(result["_detail_status"], "closed")
        self.assertEqual(result["_detail_message"], "职位已关闭")

    def test_search_card_fallback_decodes_salary_and_recovers_company(self):
        card = normalize_search_card(
            {
                "title": "AI智能应用（FDE）工程师",
                "salary_raw": "\ue032\ue036-\ue033\ue036K",
                "experience_raw": "经验不限",
                "education_raw": "本科",
                "company": "",
                "location": "",
                "card_text": (
                    "AI智能应用（FDE）工程师\n"
                    "\ue032\ue036-\ue033\ue036K\n经验不限\n本科\n泛微网络\n"
                    "上海·闵行区·浦江"
                ),
            }
        )

        self.assertEqual(card["salary_raw"], "15-25K")
        self.assertEqual(card["company"], "泛微网络")
        self.assertEqual(card["location"], "上海·闵行区·浦江")

    def test_classification_keeps_fde_and_risk_labels_separate(self):
        fde = classify_job(
            {
                "title": "AI 前线部署工程师 FDE",
                "jd": "前往客户现场调研业务需求，完成 Agent 和 RAG 编码、系统集成、上线交付。",
                "company": "产品公司",
                "recruiter_role": "HR",
            }
        )
        java = classify_job(
            {
                "title": "Java 后端开发",
                "jd": "Spring Boot、MyBatis、微服务 CRUD，长期驻场客户现场。",
                "company": "某技术服务公司",
                "recruiter_role": "猎头顾问",
            }
        )

        self.assertEqual(fde, ["fde", "agent_rag", "direct"])
        self.assertIn("pure_java", java)
        self.assertIn("outsourcing_risk", java)
        self.assertIn("third_party", java)

    def test_detail_payload_preserves_positioning_fields(self):
        job = job_from_detail_payload(
            {"platform": "boss", "job_id": "old", "source_url": ""},
            {
                "zpData": {
                    "jobInfo": {
                        "encryptId": "new",
                        "jobName": "AI FDE",
                        "salaryDesc": "15-25K",
                        "postDescription": "负责 Agent 交付",
                        "cityName": "上海",
                        "experienceName": "1-3年",
                        "degreeName": "本科",
                        "skills": ["RAG", "Python"],
                    },
                    "brandComInfo": {
                        "brandName": "产品公司",
                        "industryName": "人工智能",
                        "stageName": "A轮",
                        "scaleName": "20-99人",
                    },
                    "bossInfo": {"name": "李女士", "title": "HR"},
                }
            },
        )

        self.assertEqual(job["job_id"], "new")
        self.assertEqual(job["location"], "上海")
        self.assertEqual(job["skills"], ["RAG", "Python"])
        self.assertEqual(job["industry"], "人工智能")


class CollectionCliTests(unittest.TestCase):
    def test_refresh_due_uses_bounded_refresh_without_ai(self):
        cfg = {
            "boss": {"cdp_endpoint": "http://127.0.0.1:17332"},
            "collection": {"db_path": "jobs.db", "target": 300},
        }
        board = {
            "refresh_plan": [
                {"job_id": "job-1", "refresh_due": True, "refresh_action": "detail_refresh"},
                {"job_id": "job-2", "refresh_due": True, "refresh_action": "detail_refresh"},
                {"job_id": "job-3", "refresh_due": True, "refresh_action": "coverage_scan"},
                {"job_id": "job-4", "refresh_due": False, "refresh_action": None},
            ]
        }
        result = SimpleNamespace(collected=2, total=3, status="completed", error=None)
        collector = Mock()
        collector.refresh.return_value = result
        collection_store = Mock()

        with (
            patch.object(main_module, "load_config", return_value=cfg),
            patch.object(main_module, "OpenAICompatibleClient") as ai,
            patch.object(main_module, "ResumeManager") as resume,
            patch.object(main_module, "create_provider") as knowledge,
            patch.object(main_module, "Store") as application_store,
            patch.object(main_module, "CollectionStore", return_value=collection_store),
            patch.object(main_module, "build_freshness_dashboard", return_value=board),
            patch.object(main_module, "BossCollector", return_value=collector),
            patch("builtins.print"),
            patch("sys.argv", ["main.py", "--refresh-due", "--refresh-target", "3"]),
        ):
            main_module.main()

        ai.assert_not_called()
        resume.assert_not_called()
        knowledge.assert_not_called()
        application_store.assert_not_called()
        collector.refresh.assert_called_once_with(["job-1", "job-2"], max_jobs=2)
        collection_store.close.assert_called_once()

    def test_refresh_due_explains_when_only_coverage_scan_is_due(self):
        cfg = {
            "boss": {"cdp_endpoint": "http://127.0.0.1:17332"},
            "collection": {"db_path": "jobs.db", "target": 300},
        }
        board = {
            "refresh_plan": [
                {"job_id": "job-coverage", "refresh_due": True, "refresh_action": "coverage_scan"},
            ]
        }
        collection_store = Mock()

        with (
            patch.object(main_module, "load_config", return_value=cfg),
            patch.object(main_module, "CollectionStore", return_value=collection_store),
            patch.object(main_module, "build_freshness_dashboard", return_value=board),
            patch.object(main_module, "BossCollector") as collector,
            patch("builtins.print") as printer,
            patch("sys.argv", ["main.py", "--refresh-due"]),
        ):
            main_module.main()

        collector.assert_not_called()
        messages = " ".join(str(call.args[0]) for call in printer.call_args_list if call.args)
        self.assertIn("--scan-coverage", messages)
        self.assertNotIn("没有到期岗位需要复核", messages)
        collection_store.close.assert_called_once()

    def test_collect_only_never_constructs_ai_resume_or_knowledge_provider(self):
        cfg = {
            "boss": {"cdp_endpoint": "http://127.0.0.1:17332"},
            "collection": {"db_path": "jobs.db", "target": 300},
        }
        result = SimpleNamespace(collected=3, total=3, status="sample_completed")
        collector = Mock()
        collector.run.return_value = result
        collection_store = Mock()

        with (
            patch.object(main_module, "load_config", return_value=cfg),
            patch.object(main_module, "OpenAICompatibleClient") as ai,
            patch.object(main_module, "ResumeManager") as resume,
            patch.object(main_module, "create_provider") as knowledge,
            patch.object(main_module, "Store") as application_store,
            patch.object(main_module, "CollectionStore", return_value=collection_store),
            patch.object(main_module, "BossCollector", return_value=collector),
            patch("builtins.print"),
            patch("sys.argv", ["main.py", "--collect-only", "--collect-target", "3"]),
        ):
            main_module.main()

        ai.assert_not_called()
        resume.assert_not_called()
        knowledge.assert_not_called()
        application_store.assert_not_called()
        collector.run.assert_called_once_with(target=3)
        collection_store.close.assert_called_once()


class CollectionBrowserLifecycleTests(unittest.TestCase):
    def test_refresh_ids_are_deduplicated_and_bounded(self):
        self.assertEqual(
            BossCollector._bounded_job_ids(["job-1", "job-1", "job-2"], max_jobs=2),
            ["job-1", "job-2"],
        )

    def test_collector_uses_all_detail_records_for_target_without_opening_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=2)
            base_job = {
                "platform": "boss",
                "source": "boss-search",
                "source_key": "search:上海:AI 应用工程师",
                "source_page": 1,
                "company": "Example",
            }
            store.save_detail(
                {
                    **base_job,
                    "job_id": "collected",
                    "source_url": "https://example.invalid/collected",
                    "title": "Collected",
                    "jd": "完整 JD",
                },
                run_id,
            )
            store.save_failure(
                {
                    **base_job,
                    "job_id": "failed",
                    "source_url": "https://example.invalid/failed",
                    "title": "Failed",
                },
                run_id,
                "detail unavailable",
            )
            collector = BossCollector(
                {
                    "boss": {"cdp_endpoint": "http://127.0.0.1:17332"},
                    "collection": {},
                },
                store,
            )

            try:
                with patch(
                    "jobagent.platforms.boss_collector.sync_playwright"
                ) as playwright:
                    result = collector.run(target=2)

                playwright.assert_not_called()
                self.assertEqual(result.collected, 0)
                self.assertEqual(result.total, 2)
                self.assertEqual(result.status, "sample_completed")
            finally:
                store.close()

    def test_collector_closes_its_pages_before_playwright_disconnects(self):
        class OwnedPage:
            def __init__(self, manager):
                self.manager = manager
                self.closed = False

            def close(self):
                if self.manager.exited:
                    raise RuntimeError("Playwright already disconnected")
                self.closed = True

        class Manager:
            def __init__(self, browser):
                self.exited = False
                self.playwright = SimpleNamespace(
                    chromium=SimpleNamespace(connect_over_cdp=Mock(return_value=browser))
                )

            def __enter__(self):
                return self.playwright

            def __exit__(self, exc_type, exc_value, traceback):
                self.exited = True
                return False

        with tempfile.TemporaryDirectory() as temp_dir:
            original = Mock()
            original.url = "https://www.zhipin.com/web/geek/jobs"
            context = Mock()
            context.pages = [original]
            browser = Mock()
            browser.contexts = [context]
            manager = Manager(browser)
            owned = [OwnedPage(manager), OwnedPage(manager)]
            context.new_page.side_effect = owned
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            collector = BossCollector(
                {
                    "boss": {"cdp_endpoint": "http://127.0.0.1:17332"},
                    "collection": {},
                },
                store,
            )

            try:
                with (
                    patch(
                        "jobagent.platforms.boss_collector.sync_playwright",
                        return_value=manager,
                    ),
                    patch.object(collector, "_collect_favorites"),
                ):
                    collector.run(target=1)

                self.assertTrue(all(page.closed for page in owned))
            finally:
                store.close()


class _FakeDetailPage:
    """Minimal page double exposing only what _dead_detail_page inspects."""

    def __init__(self, url, title):
        self.url = url
        self._title = title

    def title(self):
        return self._title


class BossCollectorDeadDetailTests(unittest.TestCase):
    HOME_TITLE = "BOSS直聘-找工作BOSS直聘直接谈！招聘求职找工作！"

    def test_dead_detail_page_signatures(self):
        redirected = BossCollector._dead_detail_page(
            _FakeDetailPage("https://www.zhipin.com/", self.HOME_TITLE)
        )
        homepage_shell = BossCollector._dead_detail_page(
            _FakeDetailPage(
                "https://www.zhipin.com/job_detail/job-1.html", self.HOME_TITLE
            )
        )
        alive = BossCollector._dead_detail_page(
            _FakeDetailPage(
                "https://www.zhipin.com/job_detail/job-1.html",
                "「AI工程师」_某公司招聘_BOSS直聘",
            )
        )
        self.assertTrue(redirected)
        self.assertTrue(homepage_shell)
        self.assertFalse(alive)

    def _seed_refreshable_job(self, store):
        run_id = store.start_run(target_count=1)
        job = {
            "platform": "boss",
            "job_id": "job-1",
            "source": "boss-search",
            "source_key": "search:上海:AI 应用工程师",
            "source_page": 1,
            "source_url": "https://www.zhipin.com/job_detail/job-1.html",
            "title": "AI FDE",
            "company": "Example",
            "jd": "负责客户需求诊断、Agent/RAG 开发与部署交付。",
        }
        store.save_observation(job, run_id)
        store.save_detail(job, run_id)
        return job

    def _manager_for(self, context):
        playwright = SimpleNamespace(
            chromium=SimpleNamespace(
                connect_over_cdp=Mock(return_value=SimpleNamespace(contexts=[context]))
            )
        )
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        manager.__exit__.return_value = False
        return manager

    def test_refresh_marks_dead_homepage_shell_as_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            self._seed_refreshable_job(store)
            collector = BossCollector(
                {"boss": {"cdp_endpoint": "http://127.0.0.1:17332"}, "collection": {}},
                store,
            )
            context = Mock()
            context.pages = []
            detail_page = Mock()
            detail_page.url = "https://www.zhipin.com/job_detail/job-1.html"
            context.new_page.return_value = detail_page

            with (
                patch(
                    "jobagent.platforms.boss_collector.sync_playwright",
                    return_value=self._manager_for(context),
                ),
                patch.object(collector, "_navigate"),
                patch.object(collector, "_require_login"),
                patch.object(
                    collector,
                    "_load_detail_page",
                    return_value={
                        "platform": "boss",
                        "job_id": "job-1",
                        "source": "boss-refresh",
                        "source_key": "refresh",
                        "source_url": "https://www.zhipin.com/job_detail/job-1.html",
                        "title": "AI FDE",
                        "company": "Example",
                        "jd": "",
                    },
                ),
                patch.object(collector, "_dead_detail_page", return_value=True),
                patch.object(collector, "_wait"),
            ):
                result = collector.refresh(["job-1"], max_jobs=5)

            self.assertEqual(result.status, "completed")
            row = store.get_detail("job-1")
            self.assertEqual(row["detail_status"], "closed")
            state = store.conn.execute(
                "SELECT evidence_state, closure_reason FROM job_freshness WHERE job_id = 'job-1'"
            ).fetchone()
            self.assertEqual(state["evidence_state"], "closed")
            self.assertIn("已下线", state["closure_reason"] or "")
            store.close()

    def test_refresh_still_fails_when_no_dead_signature(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            self._seed_refreshable_job(store)
            collector = BossCollector(
                {"boss": {"cdp_endpoint": "http://127.0.0.1:17332"}, "collection": {}},
                store,
            )
            context = Mock()
            context.pages = []
            detail_page = Mock()
            detail_page.url = "https://www.zhipin.com/job_detail/job-1.html"
            context.new_page.return_value = detail_page

            with (
                patch(
                    "jobagent.platforms.boss_collector.sync_playwright",
                    return_value=self._manager_for(context),
                ),
                patch.object(collector, "_navigate"),
                patch.object(collector, "_require_login"),
                patch.object(
                    collector,
                    "_load_detail_page",
                    return_value={
                        "platform": "boss",
                        "job_id": "job-1",
                        "source": "boss-refresh",
                        "source_key": "refresh",
                        "source_url": "https://www.zhipin.com/job_detail/job-1.html",
                        "title": "AI FDE",
                        "company": "Example",
                        "jd": "",
                    },
                ),
                patch.object(collector, "_dead_detail_page", return_value=False),
                patch.object(collector, "_wait"),
            ):
                result = collector.refresh(["job-1"], max_jobs=5)

            self.assertEqual(result.status, "completed")
            row = store.get_detail("job-1")
            self.assertEqual(row["detail_status"], "failed")
            state = store.conn.execute(
                "SELECT evidence_state FROM job_freshness WHERE job_id = 'job-1'"
            ).fetchone()
            self.assertEqual(state["evidence_state"], "inaccessible")
            store.close()

    def test_scan_all_skips_favorites_when_recently_scanned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=0, source="boss-scan-coverage")
            store.finish_run(run_id, "completed", records_collected=0)
            collector = BossCollector(
                {"boss": {"cdp_endpoint": "http://127.0.0.1:17332"}, "collection": {}},
                store,
            )
            context = Mock()
            context.pages = []
            context.new_page.return_value = Mock()

            with (
                patch(
                    "jobagent.platforms.boss_collector.sync_playwright",
                    return_value=self._manager_for(context),
                ),
                patch.object(collector, "_scan_favorites_coverage") as favorites,
                patch.object(collector, "_scan_search_coverage", return_value=1),
            ):
                result = collector.scan_coverage(source="all", max_sources=3)

            favorites.assert_not_called()
            self.assertEqual(result.status, "completed")
            store.close()

    def test_scan_favorites_explicit_request_bypasses_recency_skip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CollectionStore(Path(temp_dir) / "jobs.db")
            run_id = store.start_run(target_count=0, source="boss-scan-coverage")
            store.finish_run(run_id, "completed", records_collected=0)
            collector = BossCollector(
                {"boss": {"cdp_endpoint": "http://127.0.0.1:17332"}, "collection": {}},
                store,
            )
            context = Mock()
            context.pages = []
            context.new_page.return_value = Mock()

            with (
                patch(
                    "jobagent.platforms.boss_collector.sync_playwright",
                    return_value=self._manager_for(context),
                ),
                patch.object(collector, "_scan_favorites_coverage", return_value=1),
            ):
                result = collector.scan_coverage(source="favorites")

            self.assertEqual(result.scanned_sources, 1)
            store.close()


if __name__ == "__main__":
    unittest.main()

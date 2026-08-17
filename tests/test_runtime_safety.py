import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main as main_module
from playwright.sync_api import TimeoutError as PWTimeout
from jobagent import ai as ai_module
from jobagent import config as config_module
from jobagent.platforms.boss import BossPlatform, BossSecurityCheck, SELECTORS


ROOT = Path(__file__).resolve().parents[1]


class _PlaywrightManager:
    def __init__(self, browser):
        self._playwright = SimpleNamespace(
            chromium=SimpleNamespace(
                launch=Mock(return_value=browser),
                connect_over_cdp=Mock(return_value=browser),
            )
        )

    def __enter__(self):
        return self._playwright

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class RuntimeSafetyTests(unittest.TestCase):
    def test_ai_module_is_valid_python(self):
        source = (ROOT / "jobagent" / "ai.py").read_text(encoding="utf-8")
        try:
            ast.parse(source, filename="jobagent/ai.py")
        except SyntaxError as exc:
            self.fail(f"jobagent/ai.py must parse: {exc}")

    def test_shipped_api_key_placeholder_is_rejected(self):
        for placeholder in ("sk-yourDeepSeekKey", "sk-your-api-key"):
            with self.subTest(placeholder=placeholder), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.yaml"
                config_path.write_text(
                    f'ai:\n  api_key: "{placeholder}"\n', encoding="utf-8"
                )
                with patch.object(config_module, "ROOT", Path(temp_dir)):
                    with self.assertRaisesRegex(SystemExit, "OpenAI-compatible API key"):
                        config_module.load_config()

    def test_config_loads_one_openai_compatible_ai_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "ai:\n"
                "  api_key: \"sk-test\"\n"
                "  base_url: \"https://example.invalid/v1\"\n"
                "  model: \"gpt-test\"\n",
                encoding="utf-8",
            )
            with patch.object(config_module, "ROOT", Path(temp_dir)):
                cfg = config_module.load_config()

        self.assertEqual(
            cfg["ai"],
            {
                "api_key": "sk-test",
                "base_url": "https://example.invalid/v1",
                "model": "gpt-test",
            },
        )

    def test_openai_compatible_client_is_canonical_and_keeps_legacy_alias(self):
        client_cls = getattr(ai_module, "OpenAICompatibleClient", None)
        self.assertIsNotNone(client_cls)
        if client_cls is None:
            return
        self.assertIs(getattr(ai_module, "DeepSeekClient", None), client_cls)

        with patch.object(ai_module, "OpenAI", return_value=Mock()) as openai_ctor:
            with patch("builtins.print") as printed:
                client = client_cls(
                    {
                        "api_key": "sk-test-secret",
                        "base_url": "https://example.invalid",
                        "model": "gpt-test",
                    }
                )

        openai_ctor.assert_called_once_with(
            api_key="sk-test-secret",
            base_url="https://example.invalid",
            max_retries=0,
        )
        self.assertEqual(client.model, "gpt-test")
        printed.assert_not_called()

    def test_chat_completion_uses_configured_output_limit(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )
        create = Mock(return_value=completion)
        sdk = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch.object(ai_module, "OpenAI", return_value=sdk):
            client = ai_module.DeepSeekClient(
                {
                    "api_key": "sk-test",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-test",
                    "max_output_tokens": 256,
                }
            )
            client._chat("system", "user")

        self.assertEqual(create.call_args.kwargs.get("max_tokens"), 256)

    def test_scoring_prompt_has_bounded_private_inputs(self):
        with patch.object(ai_module, "OpenAI", return_value=Mock()):
            client = ai_module.DeepSeekClient(
                {
                    "api_key": "sk-test",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-test",
                }
            )
        with patch.object(
            client,
            "_chat",
            return_value='{"score": 80, "reason": "fit"}',
        ) as chat:
            client.match_score(
                "R" * 20_000,
                {
                    "title": "backend",
                    "company": "example",
                    "salary": "",
                    "jd": "X" * 10_000,
                },
                "P" * 5_000,
            )

        user_prompt = chat.call_args.args[1]
        self.assertLessEqual(user_prompt.count("R"), 12_000)
        self.assertLessEqual(user_prompt.count("P"), 3_000)
        self.assertLessEqual(user_prompt.count("X"), 6_000)

    def test_recruitment_channel_classifies_third_party_and_outsourcing(self):
        cases = [
            (
                {
                    "title": "AI Agent 开发工程师",
                    "company": "科锐国际",
                    "jd": "负责企业智能体产品研发",
                    "recruiter_context": "李女士\n猎头顾问",
                },
                "third_party",
            ),
            (
                {
                    "title": "大模型应用工程师（代招）",
                    "company": "某人力资源公司",
                    "jd": "为客户代招，入职后驻场办公",
                    "recruiter_context": "招聘顾问",
                },
                "outsourcing_risk",
            ),
            (
                {
                    "title": "AI Agent 开发工程师",
                    "company": "产品公司",
                    "jd": "负责 RAG、Agent 工作流和模型 API 集成",
                    "recruiter_context": "王女士\n产品公司·技术招聘",
                },
                "direct",
            ),
        ]

        for job, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(BossPlatform._recruitment_channel(job), expected)

    def test_detail_payload_captures_narrow_recruiter_context(self):
        job = BossPlatform._job_from_detail_payload(
            {
                "platform": "boss",
                "job_id": "old-id",
                "title": "AI Agent 开发工程师",
                "company": "",
                "salary": "",
            },
            {
                "zpData": {
                    "jobInfo": {
                        "encryptId": "new-id",
                        "jobName": "AI Agent 开发工程师",
                        "salaryDesc": "25-40K",
                        "postDescription": "负责 RAG 与 Agent 工作流",
                    },
                    "brandComInfo": {"brandName": "产品公司"},
                    "bossInfo": {
                        "name": "李女士",
                        "title": "猎头顾问",
                        "activeTimeDesc": "刚刚活跃",
                    },
                }
            },
        )

        self.assertEqual(job["job_id"], "new-id")
        self.assertEqual(job["jd"], "负责 RAG 与 Agent 工作流")
        self.assertEqual(job["recruiter_context"], "李女士\n猎头顾问\n刚刚活跃")
        self.assertEqual(BossPlatform._recruitment_channel(job), "third_party")

    def test_current_boss_detail_selector_includes_desc_fallback(self):
        self.assertIn(".job-detail-body .desc", SELECTORS["jd_text"])

    def test_cdp_session_reuses_existing_context_and_boss_page(self):
        page = Mock()
        page.url = "https://www.zhipin.com/web/geek/jobs"
        context = Mock()
        context.pages = [page]
        browser = Mock()
        browser.contexts = [context]
        chromium = Mock()
        chromium.connect_over_cdp.return_value = browser

        session = BossPlatform._connect_browser_session(
            chromium,
            {"cdp_endpoint": "ws://127.0.0.1:17332/devtools/browser/test"},
        )

        chromium.connect_over_cdp.assert_called_once_with(
            "ws://127.0.0.1:17332/devtools/browser/test",
            timeout=15_000,
        )
        self.assertIs(session[0], browser)
        self.assertIs(session[1], context)
        self.assertIs(session[2], page)
        context.new_page.assert_not_called()

    def test_cdp_session_prefers_safe_boss_page_over_security_page(self):
        security_page = Mock()
        security_page.url = (
            "https://www.zhipin.com/web/geek/jobs?_security_check=1_123"
        )
        safe_page = Mock()
        safe_page.url = "https://www.zhipin.com/web/geek/jobs"
        context = Mock()
        context.pages = [security_page, safe_page]
        browser = Mock()
        browser.contexts = [context]
        chromium = Mock()
        chromium.connect_over_cdp.return_value = browser

        session = BossPlatform._connect_browser_session(
            chromium,
            {"cdp_endpoint": "http://127.0.0.1:17332"},
        )

        self.assertIs(session[2], safe_page)
        context.new_page.assert_not_called()

    def test_security_url_is_a_hard_stop(self):
        self.assertTrue(
            BossPlatform._is_security_url(
                "https://www.zhipin.com/web/geek/jobs?_security_check=1_123"
            )
        )
        self.assertTrue(
            BossPlatform._is_security_url(
                "https://www.zhipin.com/web/passport/zp/verify.html?code=36"
            )
        )
        self.assertFalse(
            BossPlatform._is_security_url(
                "https://www.zhipin.com/web/geek/jobs?city=101020100"
            )
        )

    def test_same_search_page_does_not_navigate_for_query_order_only(self):
        page = Mock()
        page.url = (
            "https://www.zhipin.com/web/geek/jobs?"
            "city=101020100&query=AI%20Agent%20%E5%BC%80%E5%8F%91"
        )
        page.query_selector_all.return_value = []
        platform = BossPlatform.__new__(BossPlatform)

        jobs = platform._search(
            page,
            "AI Agent 开发",
            {
                "city": "上海",
                "max_jobs_per_keyword": 3,
                "page_settle_range_seconds": [0, 0],
            },
        )

        self.assertEqual(jobs, [])
        page.goto.assert_not_called()

    def test_safety_aware_wait_stops_on_mid_delay_verification_redirect(self):
        class RedirectingPage:
            def __init__(self):
                self.urls = [
                    "https://www.zhipin.com/web/geek/jobs?city=101020100",
                    "https://www.zhipin.com/web/passport/zp/verify.html?code=36",
                ]
                self.reads = 0
                self.waits = []

            @property
            def url(self):
                value = self.urls[min(self.reads, len(self.urls) - 1)]
                self.reads += 1
                return value

            def wait_for_timeout(self, milliseconds):
                self.waits.append(milliseconds)

        page = RedirectingPage()

        with self.assertRaises(BossSecurityCheck):
            BossPlatform._wait_with_safety(page, [1, 1])

        self.assertEqual(page.waits, [500])

    def test_send_greeting_accepts_visible_delivery_after_click_navigation_timeout(self):
        platform = BossPlatform.__new__(BossPlatform)
        platform.cfg = {"boss": {"chat_settle_range_seconds": [0, 0]}}
        page = Mock()
        page.url = "https://www.zhipin.com/web/geek/jobs?city=101020100"
        greeting = "我做过企业级 AI Agent 和 RAG 落地，希望进一步沟通。"

        chat_button = Mock()
        chat_button.inner_text.return_value = "立即沟通"
        chat_input = Mock()
        send_button = Mock()

        def timeout_after_delivery():
            page.url = "https://www.zhipin.com/web/geek/chat"
            raise PWTimeout("navigation timed out after the message was sent")

        send_button.click.side_effect = timeout_after_delivery
        page.wait_for_selector.side_effect = [chat_button, chat_input]
        page.query_selector.side_effect = lambda selector: (
            send_button if selector == SELECTORS["send_btn"] else None
        )
        page.inner_text.return_value = f"张先生\n[送达]\n{greeting}"

        with patch.object(BossPlatform, "_wait_with_safety"):
            delivered = platform._send_greeting(page, greeting)

        self.assertTrue(delivered)

    def test_run_returns_from_chat_before_loading_the_next_job(self):
        cfg = {
            "boss": {
                "keywords": ["AI Agent 开发"],
                "cdp_endpoint": "ws://127.0.0.1:17332/devtools/browser/test",
                "max_apply_per_run": 2,
                "blacklist_keywords": [],
                "blacklist_companies": [],
                "delay_range_seconds": [0, 0],
                "page_settle_range_seconds": [0, 0],
            },
            "match": {"min_score": 65},
            "preferences": "AI Agent / RAG",
        }
        ai = Mock()
        ai.match_score.return_value = (88, "fit")
        ai.tailor_resume.return_value = "# tailored"
        ai.greeting.return_value = "你好，希望进一步沟通。"
        resume_mgr = SimpleNamespace(master="# Resume", save_tailored=Mock(return_value="resume.md"))
        store = Mock()
        store.seen.return_value = False
        browser = Mock()
        context = Mock()
        page = Mock()
        search_url = "https://www.zhipin.com/web/geek/jobs?city=101020100&query=AI%20Agent"
        page.url = search_url
        context.pages = [page]
        browser.contexts = [context]
        platform = BossPlatform(cfg, ai, resume_mgr, store)
        jobs = [
            {"platform": "boss", "job_id": "one", "title": "AI Agent 1", "company": "甲", "salary": ""},
            {"platform": "boss", "job_id": "two", "title": "AI Agent 2", "company": "乙", "salary": ""},
        ]

        def load_detail(current_page, job):
            self.assertEqual(current_page.url, search_url)
            return {**job, "jd": "RAG Agent 工作流", "recruiter_context": "产品公司"}

        def send_greeting(current_page, greeting):
            current_page.url = "https://www.zhipin.com/web/geek/chat"
            return True

        def go_back(**kwargs):
            page.url = search_url

        page.go_back.side_effect = go_back
        manager = _PlaywrightManager(browser)

        with (
            patch("jobagent.platforms.boss.sync_playwright", return_value=manager),
            patch.object(platform, "_ensure_login"),
            patch.object(platform, "_search", return_value=jobs),
            patch.object(platform, "_load_detail", side_effect=load_detail) as detail,
            patch.object(platform, "_send_greeting", side_effect=send_greeting),
            patch.object(platform, "_sleep"),
            patch("builtins.print"),
        ):
            platform.run(dry_run=False)

        self.assertEqual(detail.call_count, 2)
        self.assertEqual(page.go_back.call_count, 2)

    def test_cdp_run_reuses_page_and_never_opens_job_tabs(self):
        cfg = {
            "boss": {
                "keywords": ["AI Agent 开发"],
                "cdp_endpoint": "ws://127.0.0.1:17332/devtools/browser/test",
                "max_apply_per_run": 1,
                "blacklist_keywords": [],
                "blacklist_companies": [],
                "delay_range_seconds": [0, 0],
            },
            "match": {"min_score": 65},
            "preferences": "AI Agent / RAG",
        }
        ai = Mock()
        ai.match_score.return_value = (88, "fit")
        store = Mock()
        store.seen.return_value = False
        browser = Mock()
        context = Mock()
        page = Mock()
        page.url = "https://www.zhipin.com/web/geek/jobs"
        context.pages = [page]
        browser.contexts = [context]
        platform = BossPlatform(
            cfg,
            ai,
            SimpleNamespace(master="# Resume"),
            store,
        )
        job = {
            "platform": "boss",
            "job_id": "direct",
            "title": "AI Agent 开发工程师",
            "company": "产品公司",
            "salary": "",
            "href": "https://example.invalid/direct",
        }
        manager = _PlaywrightManager(browser)

        with (
            patch(
                "jobagent.platforms.boss.sync_playwright",
                return_value=manager,
            ),
            patch.object(platform, "_ensure_login"),
            patch.object(platform, "_search", return_value=[job]),
            patch.object(
                platform,
                "_load_detail",
                return_value={**job, "jd": "RAG Agent 工作流", "recruiter_context": "产品公司"},
            ),
            patch.object(platform, "_sleep"),
            patch("builtins.print"),
        ):
            platform.run(dry_run=True)

        browser.contexts[0].new_page.assert_not_called()
        manager._playwright.chromium.connect_over_cdp.assert_called_once_with(
            "ws://127.0.0.1:17332/devtools/browser/test",
            timeout=15_000,
        )
        ai.match_score.assert_called_once()

    def test_security_check_stops_before_gpt_send_or_persistence(self):
        cfg = {
            "boss": {
                "keywords": ["AI Agent 开发"],
                "cdp_endpoint": "ws://127.0.0.1:17332/devtools/browser/test",
                "max_apply_per_run": 1,
                "blacklist_keywords": [],
                "blacklist_companies": [],
                "delay_range_seconds": [0, 0],
            },
            "match": {"min_score": 65},
            "preferences": "AI Agent / RAG",
        }
        ai = Mock()
        store = Mock()
        store.seen.return_value = False
        browser = Mock()
        context = Mock()
        page = Mock()
        page.url = "https://www.zhipin.com/web/geek/jobs"
        context.pages = [page]
        browser.contexts = [context]
        platform = BossPlatform(
            cfg,
            ai,
            SimpleNamespace(master="# Resume"),
            store,
        )
        job = {
            "platform": "boss",
            "job_id": "direct",
            "title": "AI Agent 开发工程师",
            "company": "产品公司",
            "salary": "",
            "href": "https://example.invalid/direct",
        }
        manager = _PlaywrightManager(browser)

        with (
            patch(
                "jobagent.platforms.boss.sync_playwright",
                return_value=manager,
            ),
            patch.object(platform, "_ensure_login"),
            patch.object(platform, "_search", return_value=[job]),
            patch.object(
                platform,
                "_load_detail",
                side_effect=BossSecurityCheck("检测到 Boss 安全校验"),
            ),
            patch.object(platform, "_send_greeting") as send_greeting,
            patch.object(platform, "_sleep"),
            patch("builtins.print"),
        ):
            platform.run(dry_run=False)

        ai.match_score.assert_not_called()
        send_greeting.assert_not_called()
        store.record.assert_not_called()

    def test_dry_run_skips_recruitment_risks_before_gpt(self):
        cfg = {
            "boss": {
                "keywords": ["AI Agent 开发"],
                "max_apply_per_run": 3,
                "blacklist_keywords": [],
                "blacklist_companies": [],
                "delay_range_seconds": [0, 0],
            },
            "match": {"min_score": 65},
            "preferences": "AI Agent / RAG",
        }
        ai = Mock()
        ai.match_score.return_value = (88, "AI Agent 经验匹配")
        store = Mock()
        store.seen.return_value = False
        browser = Mock()
        context = Mock()
        browser.new_context.return_value = context
        context.new_page.return_value = Mock()
        platform = BossPlatform(
            cfg,
            ai,
            SimpleNamespace(master="# AI 应用工程师"),
            store,
        )
        jobs = [
            {
                "platform": "boss",
                "job_id": "headhunter",
                "title": "AI Agent 开发工程师",
                "company": "科锐国际",
                "salary": "",
                "href": "https://example.invalid/headhunter",
            },
            {
                "platform": "boss",
                "job_id": "outsourcing",
                "title": "大模型应用工程师",
                "company": "服务商",
                "salary": "",
                "href": "https://example.invalid/outsourcing",
            },
            {
                "platform": "boss",
                "job_id": "direct",
                "title": "AI Agent 开发工程师",
                "company": "产品公司",
                "salary": "",
                "href": "https://example.invalid/direct",
            },
        ]
        details = {
            "headhunter": {
                "jd": "负责企业智能体研发",
                "recruiter_context": "李女士\n猎头顾问",
            },
            "outsourcing": {
                "jd": "项目外包，需在客户现场驻场",
                "recruiter_context": "招聘专员",
            },
            "direct": {
                "jd": "负责 RAG、Agent 工作流和模型 API 集成",
                "recruiter_context": "产品公司·技术招聘",
            },
        }

        def load_detail(page, job):
            return {**job, **details[job["job_id"]]}

        with (
            patch(
                "jobagent.platforms.boss.sync_playwright",
                return_value=_PlaywrightManager(browser),
            ),
            patch.object(platform, "_ensure_login"),
            patch.object(platform, "_search", return_value=jobs),
            patch.object(platform, "_load_detail", side_effect=load_detail),
            patch.object(platform, "_send_greeting") as send_greeting,
            patch.object(platform, "_sleep"),
            patch("builtins.print"),
        ):
            platform.run(dry_run=True)

        ai.match_score.assert_called_once()
        self.assertEqual(ai.match_score.call_args.args[1]["job_id"], "direct")
        send_greeting.assert_not_called()
        store.record.assert_not_called()

    def test_main_uses_canonical_client_and_warns_before_platform_run(self):
        cfg = {
            "ai": {
                "api_key": "sk-test",
                "base_url": "https://example.invalid/v1",
                "model": "gpt-test",
            },
            "resume": {},
            "knowledge": {"provider": "none"},
            "preferences": "",
            "boss": {},
        }
        events = []
        configure_stdio = Mock(side_effect=lambda: events.append(("configure", "")))

        class FakePlatform:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, dry_run=False):
                events.append(("run", dry_run))

        generic_client = Mock(name="generic_client")
        legacy_client = Mock(name="legacy_client")
        store = Mock()

        def record_print(*args, **kwargs):
            events.append(("print", " ".join(str(arg) for arg in args)))

        with (
            patch.dict(
                main_module.__dict__,
                {
                    "_configure_stdio": configure_stdio,
                    "OpenAICompatibleClient": generic_client,
                    "DeepSeekClient": legacy_client,
                    "PLATFORMS": {"boss": FakePlatform},
                },
            ),
            patch.object(main_module, "load_config", return_value=cfg),
            patch.object(main_module, "ResumeManager", return_value=Mock()),
            patch.object(main_module, "Store", return_value=store),
            patch.object(
                main_module,
                "create_provider",
                return_value=SimpleNamespace(name="none"),
            ),
            patch("builtins.print", side_effect=record_print),
            patch("sys.argv", ["main.py", "--dry-run"]),
        ):
            main_module.main()

        generic_client.assert_called_once_with(cfg["ai"])
        legacy_client.assert_not_called()
        configure_index = next(
            index for index, event in enumerate(events) if event[0] == "configure"
        )
        warning_index = next(
            index
            for index, event in enumerate(events)
            if event[0] == "print" and "账号封禁风险" in event[1]
        )
        run_index = next(index for index, event in enumerate(events) if event[0] == "run")
        self.assertLess(configure_index, warning_index)
        self.assertLess(warning_index, run_index)

    def test_dry_run_never_sends_or_persists_job_history(self):
        cfg = {
            "boss": {
                "keywords": ["Python"],
                "max_apply_per_run": 1,
                "blacklist_keywords": ["blocked"],
                "blacklist_companies": [],
                "delay_range_seconds": [0, 0],
            },
            "match": {"min_score": 65},
            "preferences": "backend",
        }
        ai = Mock()
        ai.match_score.return_value = (90, "PRIVATE_RESUME_FACT")
        resume_manager = SimpleNamespace(master="# Resume")
        store = Mock()
        store.seen.return_value = False
        browser = Mock()
        context = Mock()
        browser.new_context.return_value = context
        context.new_page.side_effect = [Mock(), Mock()]

        platform = BossPlatform(cfg, ai, resume_manager, store)
        jobs = [
            {
                "platform": "boss",
                "job_id": "blocked-job",
                "title": "blocked role",
                "company": "A",
                "salary": "",
                "href": "https://example.invalid/blocked",
            },
            {
                "platform": "boss",
                "job_id": "scored-job",
                "title": "backend role",
                "company": "B",
                "salary": "",
                "href": "https://example.invalid/scored",
            },
        ]

        with (
            patch(
                "jobagent.platforms.boss.sync_playwright",
                return_value=_PlaywrightManager(browser),
            ),
            patch.object(platform, "_ensure_login"),
            patch.object(platform, "_search", return_value=jobs),
            patch.object(
                platform,
                "_load_detail",
                side_effect=lambda page, job: {**job, "jd": "Python backend"},
            ),
            patch.object(platform, "_send_greeting") as send_greeting,
            patch.object(platform, "_sleep"),
        ):
            with patch("builtins.print") as printed:
                platform.run(dry_run=True)

        send_greeting.assert_not_called()
        store.record.assert_not_called()
        self.assertNotIn(
            "PRIVATE_RESUME_FACT",
            " ".join(str(call) for call in printed.call_args_list),
        )
        self.assertIn(
            "共完成 AI 评分 1 个岗位",
            " ".join(str(call) for call in printed.call_args_list),
        )


if __name__ == "__main__":
    unittest.main()

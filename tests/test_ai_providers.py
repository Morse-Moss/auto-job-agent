"""AI provider 配置解析与工厂测试：不联网、不调用任何模型。"""

import json
import tempfile
import unittest
from pathlib import Path

from jobagent.ai import (
    ManualOutreachProvider,
    OpenAICompatibleClient,
    create_ai_client,
    register_provider,
)
from jobagent.config import (
    ai_provider_is_usable,
    normalize_ai_config,
    provider_type,
    resolve_ai_provider,
)


class NormalizeAiConfigTests(unittest.TestCase):
    def test_legacy_flat_block_becomes_single_provider(self):
        normalized = normalize_ai_config(
            {"api_key": "sk-x", "base_url": "https://e/v1", "model": "m"}
        )
        self.assertEqual(normalized["active"], "default")
        self.assertEqual(set(normalized["providers"]), {"default"})
        self.assertEqual(provider_type(normalized["providers"]["default"]), "openai")

    def test_multi_provider_uses_declared_active(self):
        normalized = normalize_ai_config(
            {
                "active": "deepseek-reasoner",
                "providers": {
                    "deepseek-chat": {"type": "openai", "model": "deepseek-chat"},
                    "deepseek-reasoner": {"type": "openai", "model": "deepseek-reasoner"},
                },
            }
        )
        self.assertEqual(normalized["active"], "deepseek-reasoner")
        self.assertEqual(
            resolve_ai_provider(
                {
                    "active": "deepseek-reasoner",
                    "providers": {
                        "deepseek-chat": {"model": "deepseek-chat"},
                        "deepseek-reasoner": {"model": "deepseek-reasoner"},
                    },
                },
                "deepseek-chat",
            )["model"],
            "deepseek-chat",
        )

    def test_unknown_active_or_provider_raises(self):
        with self.assertRaises(ValueError):
            normalize_ai_config({"active": "nope", "providers": {"a": {"model": "m"}}})
        with self.assertRaises(ValueError):
            resolve_ai_provider({"providers": {"a": {"model": "m"}}}, "missing")

    def test_empty_ai_config_is_not_usable(self):
        normalized = normalize_ai_config({})
        self.assertEqual(normalized["providers"], {})
        with self.assertRaises(ValueError):
            resolve_ai_provider({})

    def test_manual_needs_no_key_but_openai_does(self):
        self.assertTrue(ai_provider_is_usable({"type": "manual"}))
        self.assertFalse(ai_provider_is_usable({"type": "openai"}))
        self.assertFalse(ai_provider_is_usable({"type": "openai", "api_key": "sk-your-api-key"}))
        self.assertTrue(ai_provider_is_usable({"type": "openai", "api_key": "sk-real"}))


class ManualOutreachProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "manual.json"
        self.path.write_text(
            json.dumps(
                {
                    "jobs": {
                        "job-1": {
                            "greeting": "你好，我在做企业级 AI Agent。",
                            "resume_plan": {
                                "project_priority": ["project-2"],
                                "bullet_priority": ["bullet-1"],
                            },
                            "score": 88,
                            "reason": "方向吻合",
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.provider = ManualOutreachProvider({"type": "manual", "file": str(self.path)})

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_authored_greeting_and_plan(self):
        greeting = self.provider.greeting("母版", {"job_id": "job-1"})
        self.assertIn("AI Agent", greeting)
        plan = self.provider.resume_plan("母版", {"job_id": "job-1"}, {"projects": [], "bullets": []})
        self.assertEqual(plan["project_priority"], ["project-2"])

    def test_missing_job_raises(self):
        with self.assertRaises(ValueError):
            self.provider.greeting("母版", {"job_id": "job-unknown"})

    def test_score_falls_back_to_manual_review(self):
        self.assertEqual(self.provider.match_score("母版", {"job_id": "job-1"})[0], 88)
        score, reason = self.provider.match_score("母版", {"job_id": "missing"})
        self.assertEqual(score, 0)
        self.assertIn("需人工复核", reason)

    def test_missing_file_raises(self):
        provider = ManualOutreachProvider({"type": "manual", "file": "/no/such/file.json"})
        with self.assertRaises(ValueError):
            provider.greeting("母版", {"job_id": "job-1"})


class CreateAiClientTests(unittest.TestCase):
    def test_manual_returns_manual_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            path.write_text(json.dumps({"jobs": {}}, ensure_ascii=False), encoding="utf-8")
            client = create_ai_client(
                {"active": "manual", "providers": {"manual": {"type": "manual", "file": str(path)}}}
            )
            self.assertIsInstance(client, ManualOutreachProvider)

    def test_openai_legacy_flat_block(self):
        client = create_ai_client(
            {"api_key": "sk-test", "base_url": "https://example.invalid/v1", "model": "m"}
        )
        self.assertIsInstance(client, OpenAICompatibleClient)

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            create_ai_client({"active": "x", "providers": {"x": {"type": "does-not-exist"}}})

    def test_registered_provider_is_usable(self):
        sentinel = object()
        register_provider("sentinel-test", lambda spec: sentinel)
        client = create_ai_client({"active": "s", "providers": {"s": {"type": "sentinel-test"}}})
        self.assertIs(client, sentinel)


class FlatClientToleratesMultiProviderBlockTests(unittest.TestCase):
    def test_openai_client_resolves_active_openai_provider(self):
        client = OpenAICompatibleClient(
            {
                "active": "relay",
                "providers": {
                    "relay": {
                        "type": "openai",
                        "api_key": "sk-test",
                        "base_url": "https://e/v1",
                        "model": "m",
                    }
                },
            }
        )
        self.assertEqual(client.model, "m")

    def test_openai_client_rejects_manual_active(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleClient(
                {"active": "manual", "providers": {"manual": {"type": "manual"}}}
            )


if __name__ == "__main__":
    unittest.main()

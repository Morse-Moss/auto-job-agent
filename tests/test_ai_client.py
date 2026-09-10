import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jobagent.ai import OpenAICompatibleClient


class OpenAICompatibleClientTests(unittest.TestCase):
    def test_chat_passes_configured_model_high_reasoning_and_completion_budget(self):
        captured = []

        class Completions:
            def create(self, **kwargs):
                captured.append(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
                )

        client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        client.model = "gpt-5.6-terra"
        client.max_output_tokens = 2048
        client.reasoning_effort = "high"

        result = client._chat("system", "user", temperature=0.0)

        self.assertEqual(result, "ok")
        self.assertEqual(captured[0]["model"], "gpt-5.6-terra")
        self.assertEqual(captured[0]["reasoning_effort"], "high")
        self.assertEqual(captured[0]["max_completion_tokens"], 2048)
        self.assertNotIn("max_tokens", captured[0])

    def test_greeting_introduces_self_and_includes_jd_hook_and_portfolio(self):
        client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
        with patch.object(client, "_chat", return_value="你好，我目前做 AI 应用和 Agent 开发，最近独立搭建了运营自动化平台，月处理上万条运营数据。看到你们强调 Agent 工作流落地，我可以补充具体实现细节。") as chat:
            result = client.greeting(
                "# 简历\n- 作品集: aimorse.tech\n- 独立做过运营自动化平台，月处理上万条运营数据。",
                {"title": "AI Agent 工程师", "company": "示例公司", "jd": "负责 Agent 工作流落地"},
            )

        system = chat.call_args.args[0]
        self.assertIn("先完成自我定位", system)
        self.assertIn("作品集", system)
        self.assertIn("只讲一条最相关的经历主线", system)
        self.assertIn("JD 中一个具体要求", system)
        self.assertIn("aimorse.tech", result)
        self.assertFalse(result.endswith(("？", "?")))

    def test_greeting_rewrites_once_when_first_draft_is_template_like(self):
        client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
        drafts = [
            "您好，我对贵司岗位很感兴趣，希望有机会进一步沟通。",
            "你好，我目前做 AI 应用和 Agent 开发。看到你们在做 Agent 工作流落地，我独立搭过运营自动化平台，月处理上万条运营数据。简历里附有作品集网站，里面有相关项目细节。",
        ]
        with patch.object(client, "_chat", side_effect=drafts) as chat:
            result = client.greeting(
                "# 简历\n- 独立搭过运营自动化平台。",
                {"title": "AI Agent 工程师", "company": "示例公司", "jd": "负责 Agent 工作流落地"},
            )

        self.assertEqual(chat.call_count, 2)
        self.assertIn("上一版未通过自然度检查", chat.call_args.args[1])
        self.assertEqual(result, drafts[1])

    def test_greeting_does_not_invent_portfolio_when_resume_has_none(self):
        client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
        with patch.object(
            client,
            "_chat",
            return_value="你好，我目前做 AI 应用开发。看到岗位强调 RAG 落地，我做过知识库问答系统。",
        ):
            result = client.greeting(
                "# 简历\n- 做过知识库问答系统。",
                {"title": "RAG 工程师", "company": "示例公司", "jd": "负责 RAG 落地"},
            )

        self.assertNotIn("作品集", result)
        self.assertNotIn("aimorse.tech", result)


if __name__ == "__main__":
    unittest.main()

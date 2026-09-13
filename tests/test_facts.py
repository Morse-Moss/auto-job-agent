"""facts.py 事实对照检查测试（C 组防线 2026-09-13）。"""

from jobagent import facts

FACTS = [
    {"id": "llm-gateway", "text": "在公司搭过大模型统一接入服务：统一管理多渠道鉴权。"},
    {"id": "job-agent", "text": "独立做求职智能体：浏览器自动化+任务队列。"},
]


def test_extract_claims_flags_achievement_phrases():
    hits = facts.extract_claims("我搭过统一接入服务，周均处理 1,000 条数据。")
    phrases = [hit["phrase"] for hit in hits]
    assert any("搭过" in phrase for phrase in phrases)
    assert any("周均" in phrase or "1,000" in phrase for phrase in phrases)


def test_check_greeting_with_declared_claims(tmp_path):
    greeting = "你好，我搭过公司首个大模型统一接入，独立做过求职智能体。"
    claims = [
        {"fact_id": "llm-gateway", "text": "搭过公司首个大模型统一接入"},
        {"fact_id": "job-agent", "text": "独立做过求职智能体"},
    ]
    result = facts.check_greeting(greeting, claims, FACTS)
    assert result["has_claims"] is True
    assert len(result["declared"]) == 2
    assert all(item["fact_found"] and item["in_greeting"] for item in result["declared"])
    # 已声明的表述不应再出现在待核对里
    assert result["unchecked"] == []


def test_check_greeting_flags_uncovered_claims():
    greeting = "你好，我搭过大模型统一接入，还做过带引用的 RAG 问答。"
    claims = [{"fact_id": "llm-gateway", "text": "搭过大模型统一接入"}]
    result = facts.check_greeting(greeting, claims, FACTS)
    uncovered = [hit["phrase"] for hit in result["unchecked"]]
    # "做过"和 RAG 相关数字/表述没有被任何 claim 覆盖，必须被圈出来
    assert any("做过" in phrase for phrase in uncovered)


def test_check_greeting_missing_fact_reference(tmp_path):
    greeting = "你好，我主导过企业级中台。"
    claims = [{"fact_id": "ghost-fact", "text": "主导过企业级中台"}]
    result = facts.check_greeting(greeting, claims, FACTS)
    assert result["declared"][0]["fact_found"] is False


def test_check_greeting_without_claims_marks_everything_unchecked():
    result = facts.check_greeting("你好，我搭过统一接入服务。", [], FACTS)
    assert result["has_claims"] is False
    assert len(result["unchecked"]) >= 1


def test_check_job_untracked(tmp_path, monkeypatch):
    monkeypatch.setattr(
        facts, "load_manual_entries", lambda: {"other-job": {"greeting": "x"}}
    )
    result = facts.check_job("job-404")
    assert result["tracked"] is False


def test_check_job_tracked(tmp_path, monkeypatch):
    monkeypatch.setattr(
        facts,
        "load_manual_entries",
        lambda: {
            "job-1": {
                "greeting": "你好，我搭过公司首个大模型统一接入。",
                "claims": [{"fact_id": "llm-gateway", "text": "搭过公司首个大模型统一接入"}],
            }
        },
    )
    monkeypatch.setattr(facts, "load_facts", lambda: FACTS)
    result = facts.check_job("job-1")
    assert result["tracked"] is True
    assert result["declared"][0]["fact_found"] is True
    assert result["unchecked"] == []

"""Offline learning signals derived from reviewed notes and collected JDs."""

from __future__ import annotations

import re
import sqlite3


TOPICS = (
    {
        "id": "agent_workflows",
        "name": "Agent 与工作流",
        "patterns": (r"(?<![A-Za-z0-9_])agents?(?![A-Za-z0-9_])", r"智能体", r"dify", r"langchain", r"langgraph", r"扣子", r"coze", r"(?<![A-Za-z0-9_])n8n(?![A-Za-z0-9_])", r"工作流"),
        "recommendation": "选一个真实业务流程，完成工具调用、状态管理、失败重试和效果评测。",
        "deliverable": "一个可演示的端到端 Agent 工作流和评测记录",
    },
    {
        "id": "rag_knowledge",
        "name": "RAG 与知识库",
        "patterns": (r"(?<![A-Za-z0-9_])rag(?![A-Za-z0-9_])", r"检索增强", r"知识库", r"向量检索", r"语义检索", r"milvus", r"faiss", r"elasticsearch", r"embedding"),
        "recommendation": "用一批真实文档走完清洗、切分、检索、重排、生成和评测，记录每一步的取舍。",
        "deliverable": "一套带检索指标和失败案例的 RAG 小项目",
    },
    {
        "id": "python_api",
        "name": "Python 与 API 工程",
        "patterns": (r"(?<![A-Za-z0-9_])python(?![A-Za-z0-9_])", r"fastapi", r"flask", r"django"),
        "recommendation": "把现有 AI 能力封装成 FastAPI 服务，补齐鉴权、异常、测试、日志和健康检查。",
        "deliverable": "一个可部署、可测试的 Python API 服务",
    },
    {
        "id": "java_enterprise",
        "name": "Java 企业后端",
        "patterns": (r"(?<![A-Za-z0-9_])java(?![A-Za-z0-9_])", r"spring\s*boot", r"spring\s*cloud", r"springcloud", r"nacos", r"微服务"),
        "recommendation": "用 Spring Boot 做企业侧网关，调用 Python AI 服务并处理超时、重试、鉴权和链路追踪。",
        "deliverable": "一个 Java + Python 双栈集成案例",
    },
    {
        "id": "platform_engineering",
        "name": "Linux 与容器化",
        "patterns": (r"(?<![A-Za-z0-9_])linux(?![A-Za-z0-9_])", r"docker", r"kubernetes", r"(?<![A-Za-z0-9_])k8s(?![A-Za-z0-9_])", r"nginx", r"ci/cd", r"容器化", r"运维"),
        "recommendation": "把一个 AI 服务容器化并部署到 Linux，补健康检查、日志、配置和回滚步骤。",
        "deliverable": "一份能重复执行的部署与故障恢复手册",
    },
    {
        "id": "data_messaging",
        "name": "数据库与消息队列",
        "patterns": (r"mysql", r"redis", r"mongodb", r"postgresql", r"kafka", r"rabbitmq", r"消息队列"),
        "recommendation": "做一个异步任务链路，明确 MySQL 持久化、Redis 缓存和消息队列各自承担的职责。",
        "deliverable": "一个可观察、可重放的异步处理案例",
    },
    {
        "id": "ecommerce_integration",
        "name": "跨境电商与系统集成",
        "patterns": (r"跨境电商", r"亚马逊", r"amazon", r"shopify", r"temu", r"进销存", r"(?<![A-Za-z0-9_])erp(?![A-Za-z0-9_])", r"供应链", r"订单系统"),
        "recommendation": "模拟订单、库存和商品同步，练习第三方 API、Webhook、幂等、限流与失败补偿。",
        "deliverable": "一个电商平台接口对接与进销存同步 Demo",
    },
    {
        "id": "delivery_fde",
        "name": "FDE 与方案交付",
        "patterns": (r"(?<![A-Za-z0-9_])fde(?![A-Za-z0-9_])", r"前线部署", r"客户需求", r"需求调研", r"项目交付", r"实施交付", r"解决方案"),
        "recommendation": "选一个客户场景，从需求访谈、方案边界、原型到验收指标完整走一遍。",
        "deliverable": "一份需求到验收的交付案例和复盘",
    },
    {
        "id": "fullstack_web",
        "name": "前端与全栈",
        "patterns": (r"(?<![A-Za-z0-9_])trpc(?![A-Za-z0-9_])", r"typescript", r"javascript", r"react", r"vue", r"全栈", r"前端"),
        "recommendation": "为一个 AI 服务补管理界面，重点练习类型契约、流式状态、错误态和权限边界。",
        "deliverable": "一个前后端契约完整的 AI 工具界面",
    },
    {
        "id": "model_serving",
        "name": "模型部署与推理",
        "patterns": (r"vllm", r"sglang", r"cuda", r"ai\s*infra", r"模型部署", r"推理优化", r"显存", r"量化"),
        "recommendation": "比较 vLLM 与 SGLang 的部署路径，理解批处理、吞吐、量化和显存指标。",
        "deliverable": "一份带容量估算的模型服务选型实验",
    },
    {
        "id": "ai_security",
        "name": "AI 安全",
        "patterns": (r"ai\s*安全", r"大模型安全", r"智能体安全", r"对抗鲁棒", r"prompt\s*injection", r"提示词注入", r"模型攻击", r"伪造检测", r"安全测评"),
        "recommendation": "从提示词注入、越权工具调用和 RAG 数据泄露开始，建立威胁模型与回归用例。",
        "deliverable": "一套 AI 应用安全测试清单和自动化用例",
    },
    {
        "id": "prompt_evaluation",
        "name": "Prompt、评测与微调",
        "patterns": (r"(?<![A-Za-z0-9_])prompt(?![A-Za-z0-9_])", r"提示词", r"模型微调", r"效果评估", r"评测", r"上下文融合"),
        "recommendation": "建立小型黄金数据集，对提示词、上下文和模型版本做可重复的回归评测。",
        "deliverable": "一份有基线、有指标的 AI 效果评测报告",
    },
)

LEARNING_MARKERS = ("要学", "学习", "欠缺", "比较少", "较少", "不熟", "需要补", "补齐", "补足", "加强")
RISK_MARKERS = ("风险", "有坑", "担心", "怀疑", "JD太少", "JD 太少", "描述过少", "规模小", "外包", "驻场", "福利")
STRENGTH_RE = re.compile(r"(?:我有|我熟悉|我擅长).{0,50}(?:经验|优势|能力)?", re.I)


def note_requests_learning(note: str) -> bool:
    compact = re.sub(r"\s+", "", note or "")
    return any(marker in compact for marker in LEARNING_MARKERS)


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _note_matches_topic(note: str, topic: dict) -> bool:
    if not _matches(note, topic["patterns"]):
        return False
    if topic["id"] == "python_api" and re.search(
        r"python.{0,24}(?:(?:用得)?多|较多|熟悉|擅长).{0,36}java.{0,30}(?:少|弱|欠缺|补)",
        note,
        re.I,
    ):
        return False
    if topic["id"] == "java_enterprise" and re.search(
        r"java.{0,24}(?:(?:用得)?多|较多|熟悉|擅长).{0,36}python.{0,30}(?:少|弱|欠缺|补)",
        note,
        re.I,
    ):
        return False
    return True


def _note_category(note: str, learning_intent: int | None) -> str:
    if learning_intent == 1 or (learning_intent is None and note_requests_learning(note)):
        return "learning"
    compact = re.sub(r"\s+", "", note)
    if any(marker.lower() in compact.lower() for marker in RISK_MARKERS):
        return "risk"
    if STRENGTH_RE.search(note):
        return "strength"
    return "general"


def _excerpt(note: str, limit: int = 150) -> str:
    compact = re.sub(r"\s+", " ", note).strip()
    return compact if len(compact) <= limit else f"{compact[: limit - 1]}…"


def build_learning_dashboard(connection: sqlite3.Connection) -> dict:
    jobs = connection.execute(
        """
        SELECT d.job_id, d.title, d.company, d.jd_text, d.skills_json,
               d.collected_at, a.fit_tier, a.relevance_score
          FROM job_details d
          JOIN job_positioning_analysis a
            ON a.platform = d.platform AND a.job_id = d.job_id
         WHERE d.detail_status = 'collected'
           AND COALESCE(d.jd_text, '') <> ''
         ORDER BY a.relevance_score DESC, d.job_id
        """
    ).fetchall()
    target_jobs = [row for row in jobs if row["fit_tier"] != "exclude"]

    market_by_topic = {}
    market_demands = []
    for topic in TOPICS:
        matched = [
            row
            for row in target_jobs
            if _matches(
                " ".join((row["title"] or "", row["jd_text"] or "", row["skills_json"] or "")),
                topic["patterns"],
            )
        ]
        demand = {
            "id": topic["id"],
            "name": topic["name"],
            "job_count": len(matched),
            "share_percent": round(len(matched) * 100 / len(target_jobs), 1) if target_jobs else 0.0,
            "examples": [
                {"job_id": row["job_id"], "title": row["title"], "company": row["company"]}
                for row in matched[:3]
            ],
        }
        market_by_topic[topic["id"]] = demand
        if matched:
            market_demands.append(demand)
    market_demands.sort(key=lambda item: (-item["job_count"], item["name"]))

    note_rows = connection.execute(
        """
        SELECT d.platform, d.job_id, d.title, d.company, d.jd_text,
               decision.note, decision.learning_intent,
               COALESCE(
                 (
                   SELECT sample.sample_name
                     FROM job_review_samples sample
                    WHERE sample.platform = decision.platform
                      AND sample.job_id = decision.job_id
                    ORDER BY CASE WHEN sample.sample_name = 'positioning-v1' THEN 0 ELSE 1 END,
                             sample.sample_name
                    LIMIT 1
                 ),
                 CASE WHEN EXISTS (
                   SELECT 1
                     FROM job_match_analyses latest_match
                    WHERE latest_match.platform = decision.platform
                      AND latest_match.job_id = decision.job_id
                      AND (
                        latest_match.analysis_version,
                        latest_match.profile_id,
                        latest_match.profile_version
                      ) = (
                        SELECT analysis_version, profile_id, profile_version
                          FROM job_match_analyses
                         ORDER BY analyzed_at DESC, rowid DESC
                         LIMIT 1
                      )
                 ) THEN '__reviewed__' END
               ) AS sample_name
          FROM job_decisions decision
          JOIN job_details d
            ON d.platform = decision.platform AND d.job_id = decision.job_id
         WHERE TRIM(COALESCE(decision.note, '')) <> ''
         ORDER BY decision.decided_at, d.job_id
        """
    ).fetchall()
    note_counts = {"total": len(note_rows), "learning": 0, "risk": 0, "strength": 0, "general": 0}
    note_evidence: dict[str, list[dict]] = {topic["id"]: [] for topic in TOPICS}
    signals = []
    for row in note_rows:
        category = _note_category(row["note"], row["learning_intent"])
        note_counts[category] += 1
        topic_ids = []
        if category == "learning":
            for topic in TOPICS:
                if _note_matches_topic(row["note"], topic):
                    topic_ids.append(topic["id"])
                    note_evidence[topic["id"]].append(
                        {
                            "platform": row["platform"],
                            "job_id": row["job_id"],
                            "sample_name": row["sample_name"],
                            "title": row["title"],
                            "company": row["company"],
                            "excerpt": _excerpt(row["note"]),
                        }
                    )
        signals.append(
            {
                "platform": row["platform"],
                "job_id": row["job_id"],
                "sample_name": row["sample_name"],
                "title": row["title"],
                "company": row["company"],
                "category": category,
                "topic_ids": topic_ids,
                "excerpt": _excerpt(row["note"]),
            }
        )

    priorities = []
    for topic in TOPICS:
        evidence = note_evidence[topic["id"]]
        if not evidence:
            continue
        demand = market_by_topic[topic["id"]]
        priorities.append(
            {
                "id": topic["id"],
                "name": topic["name"],
                "note_count": len(evidence),
                "market_job_count": demand["job_count"],
                "share_percent": demand["share_percent"],
                "recommendation": topic["recommendation"],
                "deliverable": topic["deliverable"],
                "evidence": evidence[:3],
            }
        )
    priorities.sort(key=lambda item: (-item["note_count"], -item["market_job_count"], item["name"]))

    if not priorities:
        topic_by_id = {topic["id"]: topic for topic in TOPICS}
        for demand in market_demands[:5]:
            topic = topic_by_id[demand["id"]]
            priorities.append(
                {
                    "id": topic["id"],
                    "name": topic["name"],
                    "note_count": 0,
                    "market_job_count": demand["job_count"],
                    "share_percent": demand["share_percent"],
                    "recommendation": topic["recommendation"],
                    "deliverable": topic["deliverable"],
                    "evidence": [],
                }
            )

    reviewed_jobs = connection.execute("SELECT COUNT(*) FROM job_decisions").fetchone()[0]
    latest_collected_at = max((row["collected_at"] or "" for row in jobs), default="")
    return {
        "snapshot": {
            "collected_jobs": len(jobs),
            "target_jobs": len(target_jobs),
            "reviewed_jobs": int(reviewed_jobs),
            "latest_collected_at": latest_collected_at,
        },
        "notes": {**note_counts, "signals": signals},
        "priorities": priorities,
        "market_demands": market_demands,
    }

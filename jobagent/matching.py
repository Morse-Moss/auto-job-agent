"""Versioned, evidence-backed matching between one candidate profile and collected jobs."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field as dataclass_field
from datetime import datetime
from pathlib import Path

from jobagent.freshness import (
    jd_content_hash,
    live_job_condition,
    migrate_freshness_schema,
    sync_legacy_freshness,
)


MATCH_SCHEMA_VERSION = 5
RECOMMENDATIONS = {"priority", "challenge", "not_fit"}
EDUCATION_RANKS = {
    "unknown": 0,
    "high_school": 1,
    "associate": 2,
    "bachelor": 3,
    "master": 4,
    "doctor": 5,
}
EDUCATION_LABELS = {
    "unknown": "未提供",
    "high_school": "高中",
    "associate": "大专",
    "bachelor": "本科",
    "master": "硕士",
    "doctor": "博士",
}


DEMAND_TOPICS = (
    {
        "id": "low_code_agent",
        "name": "低代码 Agent 平台",
        "patterns": (r"(?<![A-Za-z0-9_])n8n(?![A-Za-z0-9_])", r"coze", r"扣子", r"dify"),
    },
    {
        "id": "java_enterprise",
        "name": "Java 企业后端",
        "patterns": (r"(?<![A-Za-z0-9_])java(?![A-Za-z0-9_])", r"spring\s*boot", r"spring\s*cloud", r"nacos"),
    },
    {
        "id": "platform_engineering",
        "name": "Linux 与容器化",
        "patterns": (r"(?<![A-Za-z0-9_])linux(?![A-Za-z0-9_])", r"docker", r"kubernetes", r"(?<![A-Za-z0-9_])k8s(?![A-Za-z0-9_])", r"nginx"),
    },
    {
        "id": "data_messaging",
        "name": "数据库与消息队列",
        "patterns": (r"mysql", r"redis", r"kafka", r"rabbitmq", r"消息队列"),
    },
    {
        "id": "model_serving",
        "name": "模型部署与推理",
        "patterns": (r"vllm", r"sglang", r"模型部署", r"推理优化", r"显存优化"),
    },
    {
        "id": "speech_ai",
        "name": "语音 AI",
        "patterns": (r"(?<![A-Za-z0-9_])asr(?![A-Za-z0-9_])", r"vad", r"speaker\s+diarization", r"语音识别", r"说话人分离"),
    },
    {
        "id": "go_rust_core",
        "name": "Go / Rust 核心研发",
        "patterns": (
            r"golang",
            r"go\s*(?:语言|开发|工程师|技术栈|服务端|后端|中的|等)",
            r"(?:[/、，,(（])\s*go\s*(?=[/、，,。)）])",
            r"rust",
        ),
    },
    {
        "id": "mcu_hardware",
        "name": "MCU 与嵌入式硬件",
        "patterns": (
            r"(?<![A-Za-z0-9_])mcu(?![A-Za-z0-9_])",
            r"电路原理图",
            r"(?:mcu|嵌入式|芯片|单片机)[^。\n]{0,20}硬件架构",
            r"硬件架构[^。\n]{0,20}(?:mcu|嵌入式|芯片|单片机)",
            r"uart",
            r"spi",
            r"i2c",
            r"can(?:总线|接口)",
        ),
    },
)


@dataclass(frozen=True)
class JobMatchAnalysis:
    track: str
    recommendation: str
    score: int
    confidence: float
    information_status: str
    hard_requirements: tuple[dict, ...]
    strengths: tuple[dict, ...]
    gaps: tuple[dict, ...]
    uncertainties: tuple[str, ...]
    preference_signals: tuple[dict, ...] = ()
    salary_match: dict = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class MatchPreviewResult:
    total: int
    analysis_version: str
    profile_id: str
    profile_version: str
    distribution: dict[str, int]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_text(value: object) -> str:
    """Normalize compatibility glyphs before applying deterministic rules."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t\v\f\u00a0\u3000]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _sentences(text: str) -> list[str]:
    return [
        part.strip(" -:：;；")
        for part in re.split(r"[。！？!?；;\n]", normalize_text(text))
        if part.strip(" -:：;；")
    ]


def _first_evidence(text: str, patterns: tuple[str, ...]) -> str:
    for sentence in _sentences(text):
        if _matches(sentence, patterns):
            return sentence[:220]
    return ""


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _track(title: str, jd: str) -> str:
    title_text = normalize_text(title)
    text = f"{title_text}\n{normalize_text(jd)}"
    product_title = _matches(title_text, (r"产品经理", r"产品策划", r"产品负责人"))
    fde_signal = _matches(text, (r"(?<![A-Za-z])FDE(?![A-Za-z])", r"前线部署", r"Forward Deployed"))
    delivery_signal = _matches(
        text,
        (r"客户需求", r"需求调研", r"解决方案", r"部署交付", r"项目交付", r"客户共创", r"效果复盘"),
    )
    hands_on = _matches(text, (r"Python", r"开发", r"编码", r"亲自", r"搭建", r"原型", r"部署"))
    ai_signal = _matches(text, (r"(?<![A-Za-z])AI(?![A-Za-z])", r"Agent", r"智能体", r"RAG", r"大模型", r"LLM"))

    if product_title and ai_signal and (fde_signal or (delivery_signal and hands_on)):
        return "ai_product_delivery"
    if _matches(title_text, (r"(?<![A-Za-z])FDE(?![A-Za-z])", r"前线部署", r"Forward Deployed")):
        return "fde"
    if _matches(title_text, (r"解决方案", r"交付", r"实施", r"售前")):
        return "solution_delivery"
    if product_title:
        return "product"
    if _matches(title_text, (r"(?<![A-Za-z])BD(?![A-Za-z])", r"销售", r"商务拓展", r"客户经理")):
        return "sales"
    if _matches(title_text, (r"Agent", r"智能体")):
        return "agent_engineering"
    if _matches(title_text, (r"(?<![A-Za-z])RAG(?![A-Za-z])", r"检索增强", r"知识库")):
        return "rag_engineering"
    if _matches(title_text, (r"Java", r"后端开发")) and not ai_signal:
        return "pure_java"
    if ai_signal:
        return "ai_application"
    return "other"


def _education_level(text: str) -> str:
    normalized = normalize_text(text)
    for level, pattern in (
        ("doctor", r"博士"),
        ("master", r"硕士|研究生"),
        ("bachelor", r"本科|学士"),
        ("associate", r"大专|专科"),
        ("high_school", r"高中|中专"),
    ):
        if re.search(pattern, normalized):
            return level
    return "unknown"


def _minimum_experience(value: str, jd: str) -> tuple[int | None, str]:
    source = normalize_text(value)
    normalized_jd = normalize_text(jd)
    source_patterns = (
        r"(?:至少|不少于)\s*(\d+)\s*年",
        r"(\d+)\s*年(?:以上|及以上|\+)",
        r"(\d+)\s*[-–—至]\s*\d+\s*年",
    )
    # A structured range is the strongest source. Do not let unrelated years
    # elsewhere in a long JD override the platform's experience field.
    for pattern in source_patterns:
        match = re.search(pattern, source, re.I)
        if match:
            return int(match.group(1)), source

    for pattern in source_patterns:
        match = re.search(pattern, normalized_jd, re.I)
        if match:
            return int(match.group(1)), _first_evidence(normalized_jd, (pattern,))
    return None, ""


def _candidate_skill_index(profile: dict) -> tuple[dict[str, dict], set[str]]:
    skills: dict[str, dict] = {}
    aliases: set[str] = set()
    for raw in profile.get("skills") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        item = dict(raw)
        item_aliases = [normalize_text(value) for value in item.get("aliases") or [] if value]
        item["aliases"] = item_aliases
        skills[str(item["id"])] = item
        aliases.update(alias.casefold() for alias in item_aliases)
    adjacent = {
        normalize_text(value).casefold()
        for value in profile.get("adjacent_skills") or []
        if value
    }
    return skills, aliases | adjacent


def _requirement(
    identifier: str,
    label: str,
    jd_evidence: str,
    candidate_evidence: str,
) -> dict:
    return {
        "id": identifier,
        "label": label,
        "jd_evidence": normalize_text(jd_evidence),
        "candidate_evidence": normalize_text(candidate_evidence),
        "severity": "hard",
    }


def _is_negated_requirement(evidence: str) -> bool:
    return bool(
        re.search(
            r"(?:无需|不需要|不要求|不涉及|不必|非必须|无须)[^。！？!?;；\n]{0,40}",
            evidence,
            re.I,
        )
    )


def _information_state(job: dict, jd: str) -> tuple[str, tuple[str, ...], float]:
    uncertainties: list[str] = []
    if len(jd) < 100:
        uncertainties.append("JD 正文过短，职责和要求可能不完整")
    fields = (
        ("experience_raw", "经验要求未提供"),
        ("education_raw", "学历要求未提供"),
        ("location", "工作地点未提供"),
        ("salary_raw", "薪资范围未提供"),
        ("company", "公司名称未提供"),
    )
    present = 0
    for field, message in fields:
        if normalize_text(job.get(field)):
            present += 1
        else:
            uncertainties.append(message)
    confidence = min(0.95, 0.35 + (0.15 if len(jd) >= 100 else 0) + present * 0.09)
    if len(jd) < 60 or len(uncertainties) >= 3:
        status = "insufficient"
        confidence = min(confidence, 0.65)
    elif uncertainties:
        status = "partial"
    else:
        status = "sufficient"
    return status, tuple(uncertainties), round(confidence, 2)


def salary_match(value: object, target: dict | None = None) -> dict:
    """Compare a monthly JD range with the candidate's target range."""
    text = normalize_text(value)
    target = target or {}
    target_min = float(target.get("target_min_k") or 10)
    target_max = float(target.get("target_max_k") or target_min)
    matched = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*K", text, re.I)
    if not matched:
        return {"level": "unknown", "score": 50, "raw": text}
    minimum, maximum = map(float, matched.groups())
    if maximum < target_min:
        level, score = "low", 35
    elif minimum >= target_min and minimum <= target_max:
        level, score = "high", 95
    elif maximum >= target_min and minimum < target_min:
        level, score = "acceptable", 78
    elif minimum > target_max:
        level, score = "stretch", 65
    else:
        level, score = "acceptable", 70
    return {"level": level, "score": score, "raw": text, "minimum_k": minimum, "maximum_k": maximum}


def analyze_job_match(job: dict, profile: dict) -> JobMatchAnalysis:
    title = normalize_text(job.get("title"))
    jd = normalize_text(job.get("jd_text") or job.get("jd"))
    text = f"{title}\n{jd}"
    track = _track(title, jd)
    target_tracks = {str(value) for value in profile.get("target_tracks") or []}
    constraints = profile.get("hard_constraints") or {}
    education = profile.get("education") or {}
    candidate_education = str(education.get("level") or "unknown")
    hard_requirements: list[dict] = []
    gaps: list[dict] = []
    strengths: list[dict] = []
    preference_signals: list[dict] = []

    job_education_text = normalize_text(job.get("education_raw"))
    required_education = _education_level(job_education_text or jd)
    if (
        required_education != "unknown"
        and EDUCATION_RANKS.get(required_education, 0) > EDUCATION_RANKS.get(candidate_education, 0)
        and (job_education_text or _matches(jd, (r"硕士(?:及|或)?以上", r"博士(?:及|或)?以上", r"学历要求[^。\n]{0,20}(?:硕士|博士)")))
    ):
        evidence = (
            f"学历：{job_education_text}"
            if job_education_text
            else _first_evidence(jd, (r"硕士", r"博士", r"研究生"))
        )
        hard_requirements.append(
            _requirement(
                "education_mismatch",
                "学历门槛",
                evidence,
                f"当前档案：{EDUCATION_LABELS.get(candidate_education, candidate_education)}",
            )
        )

    school_tiers = " ".join(str(value) for value in education.get("school_tiers") or [])
    school_requirement = _first_evidence(jd, (r"985", r"211", r"双一流"))
    school_tier_is_required = (
        school_requirement
        and not _matches(school_requirement, (r"优先", r"加分", r"可放宽"))
        and _matches(
            school_requirement,
            (
                r"必须",
                r"仅限",
                r"均为",
                r"(?:要求|须)[^，。；;\n]{0,12}(?:985|211|双一流)",
                r"(?:985|211|双一流)[^，。；;\n]{0,12}(?:要求|必须)",
            ),
        )
    )
    if school_tier_is_required:
        if not _matches(school_tiers, (r"985", r"211", r"双一流")):
            hard_requirements.append(
                _requirement("school_tier_mismatch", "院校门槛", school_requirement, f"当前档案：{school_tiers or '未提供院校层级'}")
            )

    required_years, experience_evidence = _minimum_experience(
        normalize_text(job.get("experience_raw")), jd
    )
    candidate_years = float(profile.get("experience_years") or 0)
    maximum_required = int(constraints.get("maximum_required_experience_years") or 99)
    if required_years is not None and candidate_years < required_years:
        item = {
            "id": "experience_depth",
            "label": "经验年限",
            "jd_evidence": experience_evidence or f"至少 {required_years} 年经验",
            "candidate_evidence": f"当前档案：约 {candidate_years:g} 年",
            "severity": "major" if required_years >= maximum_required else "moderate",
        }
        if required_years > maximum_required:
            hard_requirements.append(
                _requirement(
                    "experience_requirement",
                    "经验门槛",
                    item["jd_evidence"],
                    item["candidate_evidence"],
                )
            )
        else:
            gaps.append(item)

    labels = {normalize_text(value).casefold() for value in _json_list(job.get("labels_json"))}
    recruiter = normalize_text(f"{job.get('recruiter_name', '')} {job.get('recruiter_role', '')}")
    if constraints.get("reject_third_party") and (
        "third_party" in labels or _matches(recruiter, (r"猎头", r"招聘顾问", r"人力资源顾问", r"RPO"))
    ):
        hard_requirements.append(
            _requirement("third_party", "第三方招聘", recruiter or "第三方招聘标签", "个人规则：不接受第三方代招")
        )
    dispatch_evidence = _first_evidence(jd, (r"劳务派遣", r"第三方签约", r"人力外包", r"外包员工"))
    if constraints.get("reject_dispatch") and dispatch_evidence:
        hard_requirements.append(
            _requirement("dispatch", "外包或劳务派遣", dispatch_evidence, "个人规则：不接受外包或劳务派遣")
        )
    overseas_evidence = _first_evidence(
        f"{title}\n{jd}",
        (
            r"长期[^。\n]{0,20}(?:海外|国外|英国|美国|伦敦|新加坡|日本|香港|阿联酋)",
            r"常驻[^。\n]{0,20}(?:海外|国外|英国|美国|伦敦|新加坡|日本|香港|阿联酋)",
            r"驻[^。\n]{0,10}(?:海外|国外|英国|美国|伦敦|新加坡|日本|香港|阿联酋)",
            r"\bbase\s*[^，,。\n]{0,12}(?:海外|国外|英国|美国|伦敦|新加坡|日本|香港|阿联酋)",
        ),
    )
    if constraints.get("reject_long_term_overseas") and overseas_evidence:
        hard_requirements.append(
            _requirement("long_term_overseas", "长期海外派驻", overseas_evidence, "个人规则：不接受长期海外派驻")
        )

    blocked_domains = {str(value) for value in constraints.get("blocked_core_domains") or []}
    for topic in DEMAND_TOPICS:
        if topic["id"] not in blocked_domains or not _matches(text, topic["patterns"]):
            continue
        evidence = _first_evidence(text, topic["patterns"])
        if topic["id"] == "go_rust_core":
            core_language_signal = _matches(
                evidence,
                (
                    r"(?:以|基于)[^。\n]{0,20}(?:golang|go\s*语言|rust)[^。\n]{0,12}(?:为核心|核心技术栈|技术栈)",
                    r"(?:golang|go\s*语言|rust)[^。\n]{0,12}(?:为核心|核心研发|开发经验|服务端开发经验)",
                    r"(?:精通|深入掌握)[^。\n]{0,12}(?:golang|go\s*语言|rust)",
                ),
            )
            if not core_language_signal and not _matches(title, topic["patterns"]):
                continue
        required_signal = _matches(
            evidence,
            (r"必须", r"精通", r"熟练", r"深入", r"核心", r"要求", r"具备", r"负责"),
        )
        match_count = sum(bool(re.search(pattern, text, re.I)) for pattern in topic["patterns"])
        if required_signal or match_count >= 2 or _matches(title, topic["patterns"]):
            hard_requirements.append(
                _requirement(topic["id"], topic["name"], evidence, f"当前档案没有 {topic['name']} 经验")
            )

    candidate_skills, candidate_aliases = _candidate_skill_index(profile)
    matched_skill_ids: set[str] = set()
    for skill_id, skill in candidate_skills.items():
        patterns = tuple(re.escape(alias) for alias in skill.get("aliases") or [] if alias)
        if not patterns or not _matches(text, patterns):
            continue
        matched_skill_ids.add(skill_id)
        strengths.append(
            {
                "id": skill_id,
                "label": str(skill.get("name") or skill_id),
                "jd_evidence": _first_evidence(text, patterns),
                "candidate_evidence": normalize_text((skill.get("evidence") or [""])[0]),
            }
        )

    gap_ids = {str(item.get("id")) for item in gaps}
    for topic in DEMAND_TOPICS:
        if topic["id"] in matched_skill_ids or topic["id"] in blocked_domains:
            continue
        if not _matches(text, topic["patterns"]):
            continue
        topic_aliases = {
            normalize_text(re.sub(r"\\[bBsS]|\(\?:|\(\?!|\(\?<!|\[[^]]+\]|[?*+(){}^$]", "", pattern)).casefold()
            for pattern in topic["patterns"]
        }
        if candidate_aliases.intersection(topic_aliases):
            continue
        evidence = _first_evidence(text, topic["patterns"])
        if _is_negated_requirement(evidence):
            continue
        required = _matches(evidence, (r"必须", r"精通", r"熟练", r"要求", r"具备", r"至少", r"核心", r"需"))
        if topic["id"] not in gap_ids:
            gaps.append(
                {
                    "id": topic["id"],
                    "label": topic["name"],
                    "jd_evidence": evidence,
                    "candidate_evidence": f"当前档案没有 {topic['name']} 的直接项目证据",
                    "severity": "major" if required else "moderate",
                }
            )
            gap_ids.add(topic["id"])

    information_status, uncertainties, confidence = _information_state(job, jd)
    salary = salary_match(job.get("salary_raw"), (profile.get("preferences") or {}).get("salary_target"))
    if hard_requirements:
        recommendation = "not_fit"
        score = max(10, 38 - len(hard_requirements) * 8 + len(strengths) * 2)
    else:
        if track in target_tracks:
            score = 55
        elif track == "product" and _matches(text, (r"AI", r"Agent", r"大模型")):
            score = 45
        elif track in {"sales", "pure_java", "other"}:
            score = 25
        else:
            score = 35
        score += min(35, len(strengths) * 10)
        score -= sum(15 if item.get("severity") == "major" else 8 for item in gaps)
        positive_domains = tuple(re.escape(str(value)) for value in (profile.get("preferences") or {}).get("positive_domains") or [])
        if positive_domains and _matches(text, positive_domains):
            score += 5
        calibration = (profile.get("preferences") or {}).get("calibration") or {}
        for signal in calibration.get("signals") or []:
            if signal.get("effect") != "soft":
                continue
            applies = False
            dimension = str(signal.get("dimension") or "")
            if dimension == "track":
                applies = track == signal.get("value")
            elif dimension == "company_transparency":
                company_context = normalize_text(
                    " ".join(
                        str(job.get(field) or "")
                        for field in (
                            "detail_text",
                            "company_size",
                            "industry",
                            "financing",
                        )
                    )
                )
                applies = len(jd) < 100 or not company_context
            elif dimension == "employment":
                applies = bool(
                    {"third_party", "outsourcing_risk"}.intersection(labels)
                    or dispatch_evidence
                )
            if not applies:
                continue
            adjustment = int(signal.get("score_adjustment") or 0)
            score += adjustment
            preference_signals.append(
                {
                    "id": str(signal.get("id") or dimension),
                    "label": str(signal.get("label") or "行为偏好"),
                    "adjustment": adjustment,
                    "confidence": float(signal.get("confidence") or 0),
                    "effect": "soft",
                    "evidence": list(signal.get("evidence") or [])[:3],
                }
            )
        score = max(0, min(100, score))
        # Salary is a soft preference: it influences ranking without becoming a hard rejection.
        score = round(score * 0.85 + salary["score"] * 0.15)
        major_gaps = sum(item.get("severity") == "major" for item in gaps)
        if track in target_tracks and score >= 75 and major_gaps == 0:
            recommendation = "priority"
        elif track == "product" and _matches(text, (r"AI", r"Agent", r"大模型")):
            recommendation = "challenge"
        elif track in target_tracks or score >= 45:
            recommendation = "challenge"
        else:
            recommendation = "not_fit"

        if information_status == "insufficient" and recommendation == "priority":
            recommendation = "challenge"
            score = min(score, 74)

    return JobMatchAnalysis(
        track=track,
        recommendation=recommendation,
        score=int(score),
        confidence=confidence,
        information_status=information_status,
        hard_requirements=tuple(hard_requirements),
        strengths=tuple(strengths),
        gaps=tuple(gaps),
        uncertainties=uncertainties,
        preference_signals=tuple(preference_signals),
        salary_match=salary,
    )


def migrate_matching_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidate_profiles (
          profile_id TEXT NOT NULL,
          profile_version TEXT NOT NULL,
          display_name TEXT NOT NULL,
          profile_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(profile_id, profile_version)
        );
        CREATE TABLE IF NOT EXISTS job_match_analyses (
          analysis_version TEXT NOT NULL,
          profile_id TEXT NOT NULL,
          profile_version TEXT NOT NULL,
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          analyzed_at TEXT NOT NULL,
          jd_content_hash TEXT,
          recommendation TEXT NOT NULL CHECK(recommendation IN ('priority', 'challenge', 'not_fit')),
          track TEXT NOT NULL,
          score INTEGER NOT NULL,
          confidence REAL NOT NULL,
          information_status TEXT NOT NULL,
          hard_requirements_json TEXT NOT NULL,
          strengths_json TEXT NOT NULL,
          gaps_json TEXT NOT NULL,
          uncertainties_json TEXT NOT NULL,
          previous_fit_tier TEXT NOT NULL DEFAULT '',
          previous_relevance_score INTEGER,
          salary_match_json TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY(analysis_version, profile_id, profile_version, platform, job_id)
        );
        CREATE INDEX IF NOT EXISTS idx_job_match_latest
          ON job_match_analyses(profile_id, profile_version, analysis_version, recommendation, score DESC);
        CREATE TABLE IF NOT EXISTS job_match_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          analysis_version TEXT NOT NULL,
          profile_id TEXT NOT NULL,
          profile_version TEXT NOT NULL,
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          analyzed_at TEXT NOT NULL,
          jd_content_hash TEXT,
          recommendation TEXT NOT NULL,
          track TEXT NOT NULL,
          score INTEGER NOT NULL,
          confidence REAL NOT NULL,
          information_status TEXT NOT NULL,
          hard_requirements_json TEXT NOT NULL,
          strengths_json TEXT NOT NULL,
          gaps_json TEXT NOT NULL,
          uncertainties_json TEXT NOT NULL,
          preference_signals_json TEXT NOT NULL DEFAULT '[]',
          source TEXT NOT NULL DEFAULT 'incremental_refresh'
        );
        CREATE INDEX IF NOT EXISTS idx_job_match_history_latest
          ON job_match_history(analysis_version, profile_id, profile_version, platform, job_id, id DESC);
        """
    )
    match_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(job_match_analyses)").fetchall()
    }
    if "preference_signals_json" not in match_columns:
        connection.execute(
            """
            ALTER TABLE job_match_analyses
            ADD COLUMN preference_signals_json TEXT NOT NULL DEFAULT '[]'
            """
        )
    if "jd_content_hash" not in match_columns:
        connection.execute(
            "ALTER TABLE job_match_analyses ADD COLUMN jd_content_hash TEXT"
        )
    if "salary_match_json" not in match_columns:
        connection.execute("ALTER TABLE job_match_analyses ADD COLUMN salary_match_json TEXT NOT NULL DEFAULT '{}' ")
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if current_version < MATCH_SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version = {MATCH_SCHEMA_VERSION}")
    connection.commit()


def load_candidate_profile(path: str | Path) -> dict:
    profile_path = Path(path).resolve()
    if not profile_path.is_file():
        raise ValueError(f"候选人档案不存在：{profile_path}")
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"候选人档案不是有效 JSON：{exc}") from exc
    if not isinstance(profile, dict):
        raise ValueError("候选人档案必须是 JSON 对象")
    return profile


def _validate_profile(profile: dict) -> tuple[str, str]:
    profile_id = str(profile.get("profile_id") or "").strip()
    profile_version = str(profile.get("version") or "").strip()
    if not profile_id or not profile_version:
        raise ValueError("候选人档案必须包含 profile_id 和 version")
    if not isinstance(profile.get("skills") or [], list):
        raise ValueError("候选人档案 skills 必须是列表")
    return profile_id, profile_version


def _save_profile(connection: sqlite3.Connection, profile: dict) -> tuple[str, str]:
    profile_id, profile_version = _validate_profile(profile)
    serialized = json.dumps(profile, ensure_ascii=False, sort_keys=True)
    existing = connection.execute(
        "SELECT profile_json FROM candidate_profiles WHERE profile_id = ? AND profile_version = ?",
        (profile_id, profile_version),
    ).fetchone()
    if existing and existing[0] != serialized:
        raise ValueError("同一候选人档案版本已存在且内容不同，请创建新版本")
    connection.execute(
        """
        INSERT INTO candidate_profiles (
          profile_id, profile_version, display_name, profile_json, created_at
        ) VALUES (?,?,?,?,?)
        ON CONFLICT(profile_id, profile_version) DO NOTHING
        """,
        (
            profile_id,
            profile_version,
            str(profile.get("display_name") or profile_id),
            serialized,
            _now(),
        ),
    )
    return profile_id, profile_version


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def run_match_preview(
    db_path: str | Path,
    profile: dict,
    analysis_version: str = "match-v2.0",
) -> MatchPreviewResult:
    database = Path(db_path).resolve()
    if not database.is_file():
        raise ValueError(f"岗位数据库不存在：{database}")
    version = str(analysis_version or "").strip()
    if not version:
        raise ValueError("分析版本不能为空")

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        migrate_matching_schema(connection)
        migrate_freshness_schema(connection)
        sync_legacy_freshness(connection)
        profile_id, profile_version = _save_profile(connection, profile)
        previous: dict[tuple[str, str], sqlite3.Row] = {}
        if _table_exists(connection, "job_positioning_analysis"):
            previous = {
                (row["platform"], row["job_id"]): row
                for row in connection.execute(
                    "SELECT platform, job_id, fit_tier, relevance_score FROM job_positioning_analysis"
                )
            }
        rows = connection.execute(
            f"""
            SELECT * FROM job_details
            WHERE detail_status = 'collected' AND COALESCE(jd_text, '') <> ''
              AND {live_job_condition('job_details')}
            ORDER BY platform, job_id
            """
        ).fetchall()
        analyzed_at = _now()
        distribution: Counter[str] = Counter()
        with connection:
            connection.execute(
                """
                DELETE FROM job_match_analyses
                WHERE analysis_version = ? AND profile_id = ? AND profile_version = ?
                """,
                (version, profile_id, profile_version),
            )
            for row in rows:
                job = dict(row)
                result = analyze_job_match(job, profile)
                if result.recommendation not in RECOMMENDATIONS:
                    raise ValueError(f"未知匹配结论：{result.recommendation}")
                old = previous.get((row["platform"], row["job_id"]))
                connection.execute(
                    """
                    INSERT INTO job_match_analyses (
                      analysis_version, profile_id, profile_version, platform, job_id,
                      analyzed_at, jd_content_hash, recommendation, track, score, confidence,
                      information_status, hard_requirements_json, strengths_json,
                      gaps_json, uncertainties_json, preference_signals_json, previous_fit_tier,
                      previous_relevance_score, salary_match_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(
                      analysis_version, profile_id, profile_version, platform, job_id
                    ) DO UPDATE SET
                      analyzed_at = excluded.analyzed_at,
                      jd_content_hash = excluded.jd_content_hash,
                      recommendation = excluded.recommendation,
                      track = excluded.track,
                      score = excluded.score,
                      confidence = excluded.confidence,
                      information_status = excluded.information_status,
                      hard_requirements_json = excluded.hard_requirements_json,
                      strengths_json = excluded.strengths_json,
                      gaps_json = excluded.gaps_json,
                      uncertainties_json = excluded.uncertainties_json,
                      preference_signals_json = excluded.preference_signals_json,
                      previous_fit_tier = excluded.previous_fit_tier,
                      previous_relevance_score = excluded.previous_relevance_score,
                      salary_match_json = excluded.salary_match_json
                    """,
                    (
                        version,
                        profile_id,
                        profile_version,
                        row["platform"],
                        row["job_id"],
                        analyzed_at,
                        jd_content_hash(row["jd_text"]),
                        result.recommendation,
                        result.track,
                        result.score,
                        result.confidence,
                        result.information_status,
                        json.dumps(result.hard_requirements, ensure_ascii=False),
                        json.dumps(result.strengths, ensure_ascii=False),
                        json.dumps(result.gaps, ensure_ascii=False),
                        json.dumps(result.uncertainties, ensure_ascii=False),
                        json.dumps(result.preference_signals, ensure_ascii=False),
                        str(old["fit_tier"] or "") if old else "",
                        int(old["relevance_score"]) if old else None,
                        json.dumps(result.salary_match, ensure_ascii=False),
                    ),
                )
                distribution[result.recommendation] += 1
        return MatchPreviewResult(
            total=len(rows),
            analysis_version=version,
            profile_id=profile_id,
            profile_version=profile_version,
            distribution={key: int(distribution.get(key, 0)) for key in sorted(RECOMMENDATIONS)},
        )
    finally:
        connection.close()


def refresh_stale_matches(
    db_path: str | Path,
    *,
    limit: int = 20,
) -> dict:
    """Refresh match rows missing a JD baseline or whose canonical JD changed."""
    database = Path(db_path).resolve()
    if not database.is_file():
        raise ValueError(f"岗位数据库不存在：{database}")
    max_rows = max(0, int(limit))
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        migrate_matching_schema(connection)
        migrate_freshness_schema(connection)
        sync_legacy_freshness(connection)
        snapshot = _latest_snapshot(connection)
        if snapshot is None:
            raise ValueError("尚未生成匹配预览，请先运行匹配预览")
        profile_row = connection.execute(
            "SELECT profile_json FROM candidate_profiles WHERE profile_id = ? AND profile_version = ?",
            (snapshot["profile_id"], snapshot["profile_version"]),
        ).fetchone()
        if profile_row is None:
            raise ValueError("匹配快照对应的候选人档案无法读取，请重新生成匹配预览")
        try:
            profile = json.loads(profile_row["profile_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("候选人档案不是有效 JSON，请重新生成匹配预览") from exc

        decision_join = ""
        decision_select = "NULL AS decision_interest"
        if _table_exists(connection, "job_decisions"):
            decision_join = (
                "LEFT JOIN job_decisions decision ON "
                "decision.platform = match.platform AND decision.job_id = match.job_id"
            )
            decision_select = "decision.interest AS decision_interest"
        rows = connection.execute(
            f"""
            SELECT match.*, d.*, {decision_select}
              FROM job_match_analyses match
              JOIN job_details d
                ON d.platform = match.platform AND d.job_id = match.job_id
              {decision_join}
             WHERE match.analysis_version = ?
               AND match.profile_id = ?
               AND match.profile_version = ?
               AND {live_job_condition('d')}
             ORDER BY match.analyzed_at, match.job_id"""
            , (
                snapshot["analysis_version"],
                snapshot["profile_id"],
                snapshot["profile_version"],
            ),
        ).fetchall()
        stale_rows = [
            (row, _match_refresh_reason(row["jd_content_hash"], row["jd_text"]))
            for row in rows
        ]
        stale_rows = [(row, reason) for row, reason in stale_rows if reason]
        stale_rows.sort(
            key=lambda item: (
                _refresh_priority(item[0]["decision_interest"], item[0]["recommendation"])[0],
                -int(item[0]["score"] or 0),
                str(item[0]["job_id"]),
            )
        )
        selected = stale_rows[:max_rows]
        refreshed_at = _now()
        with connection:
            for row, _reason in selected:
                result = analyze_job_match(dict(row), profile)
                connection.execute(
                    """
                    INSERT INTO job_match_history (
                      analysis_version, profile_id, profile_version, platform, job_id,
                      captured_at, analyzed_at, jd_content_hash, recommendation, track,
                      score, confidence, information_status, hard_requirements_json,
                      strengths_json, gaps_json, uncertainties_json, preference_signals_json,
                      source
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["analysis_version"],
                        row["profile_id"],
                        row["profile_version"],
                        row["platform"],
                        row["job_id"],
                        refreshed_at,
                        row["analyzed_at"],
                        row["jd_content_hash"],
                        row["recommendation"],
                        row["track"],
                        row["score"],
                        row["confidence"],
                        row["information_status"],
                        row["hard_requirements_json"],
                        row["strengths_json"],
                        row["gaps_json"],
                        row["uncertainties_json"],
                        row["preference_signals_json"],
                        "incremental_refresh",
                    ),
                )
                connection.execute(
                    """
                    UPDATE job_match_analyses
                       SET analyzed_at = ?, jd_content_hash = ?, recommendation = ?,
                           track = ?, score = ?, confidence = ?, information_status = ?,
                           hard_requirements_json = ?, strengths_json = ?, gaps_json = ?,
                           uncertainties_json = ?, preference_signals_json = ?
                     WHERE analysis_version = ? AND profile_id = ? AND profile_version = ?
                       AND platform = ? AND job_id = ?
                    """,
                    (
                        refreshed_at,
                        jd_content_hash(row["jd_text"]),
                        result.recommendation,
                        result.track,
                        result.score,
                        result.confidence,
                        result.information_status,
                        json.dumps(result.hard_requirements, ensure_ascii=False),
                        json.dumps(result.strengths, ensure_ascii=False),
                        json.dumps(result.gaps, ensure_ascii=False),
                        json.dumps(result.uncertainties, ensure_ascii=False),
                        json.dumps(result.preference_signals, ensure_ascii=False),
                        snapshot["analysis_version"],
                        snapshot["profile_id"],
                        snapshot["profile_version"],
                        row["platform"],
                        row["job_id"],
                    ),
                )
        return {
            "analysis_version": snapshot["analysis_version"],
            "profile_id": snapshot["profile_id"],
            "profile_version": snapshot["profile_version"],
            "refreshed": len(selected),
            "baseline_initialized": sum(
                reason == "baseline_pending" for _row, reason in selected
            ),
            "jd_changed_refreshed": sum(
                reason == "jd_changed" for _row, reason in selected
            ),
            "refreshed_job_ids": [row["job_id"] for row, _reason in selected],
            "remaining": max(0, len(stale_rows) - len(selected)),
        }
    finally:
        connection.close()


def _latest_snapshot(connection: sqlite3.Connection) -> sqlite3.Row | None:
    if not _table_exists(connection, "job_match_analyses"):
        return None
    return connection.execute(
        """
        SELECT analysis_version, profile_id, profile_version, analyzed_at
        FROM job_match_analyses
        ORDER BY analyzed_at DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()


def _recommendation_changed(previous_fit_tier: str, recommendation: str) -> bool:
    previous_group = {
        "core": "priority",
        "adjacent": "challenge",
        "stretch": "challenge",
        "exclude": "not_fit",
    }.get(str(previous_fit_tier or ""), "")
    return bool(previous_group and previous_group != recommendation)


def _match_refresh_reason(stored_hash: object, jd_text: object) -> str | None:
    """Explain whether a match needs its first JD baseline or a real JD refresh."""
    if not str(stored_hash or "").strip():
        return "baseline_pending"
    if str(stored_hash) != jd_content_hash(str(jd_text or "")):
        return "jd_changed"
    return None


def _refresh_priority(interest: object, recommendation: object) -> tuple[int, str]:
    """Rank stale matches by the user's explicit intent before model output."""
    user_interest = str(interest or "").strip()
    fit = str(recommendation or "").strip()
    if user_interest == "want":
        if fit == "priority":
            return 1, "想投且匹配为优先投"
        if fit == "challenge":
            return 2, "想投但匹配为挑战投"
        return 3, "想投但当前不建议投"
    if user_interest == "maybe":
        if fit == "priority":
            return 4, "再看且匹配为优先投"
        if fit == "challenge":
            return 5, "再看且匹配为挑战投"
        return 6, "再看但当前不建议投"
    if not user_interest and fit in {"priority", "challenge"}:
        return 6, "未审阅的匹配岗位"
    return 7, "已放弃或当前不建议投"


def _latest_match_history(
    connection: sqlite3.Connection,
    *,
    analysis_version: str,
    profile_id: str,
    profile_version: str,
) -> dict[tuple[str, str], dict]:
    rows = connection.execute(
        """
        SELECT history.*
          FROM job_match_history history
          JOIN (
            SELECT platform, job_id, MAX(id) AS latest_id
              FROM job_match_history
             WHERE analysis_version = ? AND profile_id = ? AND profile_version = ?
             GROUP BY platform, job_id
          ) latest ON latest.latest_id = history.id
        """,
        (analysis_version, profile_id, profile_version),
    ).fetchall()
    return {(row["platform"], row["job_id"]): dict(row) for row in rows}


def _comparison_item(value: object) -> dict:
    if not isinstance(value, dict):
        return {"id": str(value)}
    return {
        key: value[key]
        for key in ("id", "label", "severity", "jd_evidence", "candidate_evidence")
        if value.get(key) not in (None, "")
    }


def _evidence_diff(previous: object, current: object) -> tuple[list[dict], list[dict]]:
    previous_items = _json_list(previous)
    current_items = _json_list(current)

    def key(value: object) -> str:
        if isinstance(value, dict):
            return str(value.get("id") or value.get("label") or "").casefold()
        return str(value).casefold()

    previous_by_key = {key(value): value for value in previous_items if key(value)}
    current_by_key = {key(value): value for value in current_items if key(value)}
    added = [
        _comparison_item(current_by_key[item_key])
        for item_key in current_by_key.keys() - previous_by_key.keys()
    ]
    removed = [
        _comparison_item(previous_by_key[item_key])
        for item_key in previous_by_key.keys() - current_by_key.keys()
    ]
    def sort_key(value: dict) -> tuple[str, str]:
        return str(value.get("label") or value.get("id") or ""), str(value.get("id") or "")

    return sorted(added, key=sort_key), sorted(removed, key=sort_key)


def _text_diff(previous: object, current: object) -> tuple[list[str], list[str]]:
    previous_values = {str(value) for value in _json_list(previous) if str(value)}
    current_values = {str(value) for value in _json_list(current) if str(value)}
    return sorted(current_values - previous_values), sorted(previous_values - current_values)


def _attach_refresh_comparison(item: dict, history: dict | None) -> dict:
    if history is None:
        item["previous_match"] = None
        item["refresh_changed"] = False
        return item
    previous = {
        "captured_at": history["captured_at"],
        "analyzed_at": history["analyzed_at"],
        "jd_content_hash": history["jd_content_hash"],
        "recommendation": history["recommendation"],
        "track": history["track"],
        "score": history["score"],
        "confidence": history["confidence"],
        "information_status": history["information_status"],
    }
    item["previous_match"] = previous
    scalar_changed = any(
        item.get(field) != previous[field]
        for field in ("recommendation", "track", "score", "confidence", "information_status")
    )
    comparison = {
        "refresh_kind": (
            "baseline_initialization"
            if not str(history["jd_content_hash"] or "").strip()
            else "jd_changed"
        ),
        "recommendation_changed": item.get("recommendation") != previous["recommendation"],
        "track_changed": item.get("track") != previous["track"],
        "score_delta": int(item.get("score") or 0) - int(previous["score"] or 0),
        "confidence_delta": round(
            float(item.get("confidence") or 0) - float(previous["confidence"] or 0),
            2,
        ),
        "information_status_changed": item.get("information_status") != previous["information_status"],
        "hard_requirements_added": [],
        "hard_requirements_removed": [],
        "gaps_added": [],
        "gaps_removed": [],
        "uncertainties_added": [],
        "uncertainties_removed": [],
    }
    evidence_fields = {
        "hard_requirements": "hard_requirements_json",
        "gaps": "gaps_json",
        "uncertainties": "uncertainties_json",
    }
    if any(field in item for field in evidence_fields.values()):
        for name, current_field in evidence_fields.items():
            added, removed = (
                _text_diff(history[f"{name}_json"], item.get(current_field))
                if name == "uncertainties"
                else _evidence_diff(history[f"{name}_json"], item.get(current_field))
            )
            comparison[f"{name}_added"] = added
            comparison[f"{name}_removed"] = removed
    comparison["changed"] = scalar_changed or any(
        comparison[key]
        for key in (
            "hard_requirements_added",
            "hard_requirements_removed",
            "gaps_added",
            "gaps_removed",
            "uncertainties_added",
            "uncertainties_removed",
        )
    )
    item["refresh_comparison"] = comparison
    item["refresh_changed"] = comparison["changed"]
    return item


def build_match_dashboard(connection: sqlite3.Connection) -> dict:
    migrate_matching_schema(connection)
    migrate_freshness_schema(connection)
    sync_legacy_freshness(connection)
    snapshot = _latest_snapshot(connection)
    if snapshot is None:
        return {
            "snapshot": None,
            "jobs": [],
            "empty_message": "尚未生成匹配预览，请先运行 py -3 main.py --match-preview。",
        }

    detail_columns = {row[1] for row in connection.execute("PRAGMA table_info(job_details)")}
    company_size_expr = "d.company_size" if "company_size" in detail_columns else "''"
    industry_expr = "d.industry" if "industry" in detail_columns else "''"
    rows = connection.execute(
        f"""
         SELECT match.platform, match.job_id, match.recommendation, match.track,
                match.score, match.confidence, match.information_status,
                match.jd_content_hash, d.jd_text,
               match.previous_fit_tier, match.previous_relevance_score,
               match.salary_match_json,
               d.title, d.company, d.salary_raw, d.location, d.experience_raw,
               {company_size_expr} AS company_size, {industry_expr} AS industry,
               decision.interest, decision.decided_at
        FROM job_match_analyses match
        JOIN job_details d
          ON d.platform = match.platform AND d.job_id = match.job_id
        LEFT JOIN job_decisions decision
          ON decision.platform = match.platform AND decision.job_id = match.job_id
        WHERE match.analysis_version = ?
          AND match.profile_id = ?
          AND match.profile_version = ?
          AND {live_job_condition('d')}
        ORDER BY
          CASE match.recommendation
            WHEN 'priority' THEN 0
            WHEN 'challenge' THEN 1
            ELSE 2
          END,
          match.score DESC,
          match.confidence DESC,
          d.title
        """,
        (
            snapshot["analysis_version"],
            snapshot["profile_id"],
            snapshot["profile_version"],
        ),
    ).fetchall()
    send_status_rows = connection.execute(
        """
        SELECT platform, job_id, status
        FROM outreach_send_tasks
        WHERE status IN ('queued', 'running', 'partial', 'confirmed', 'failed', 'paused', 'unknown')
        ORDER BY updated_at DESC
        """
    ).fetchall() if _table_exists(connection, "outreach_send_tasks") else []
    send_status = {}
    for sent in send_status_rows:
        send_status.setdefault((sent["platform"], sent["job_id"]), sent["status"])
    jobs: list[dict] = []
    counts: Counter[str] = Counter()
    conflicts = 0
    decision_conflicts = 0
    history = _latest_match_history(
        connection,
        analysis_version=snapshot["analysis_version"],
        profile_id=snapshot["profile_id"],
        profile_version=snapshot["profile_version"],
    )
    for row in rows:
        item = dict(row)
        item["send_status"] = send_status.get((item["platform"], item["job_id"]))
        try:
            item["salary_match"] = json.loads(item.pop("salary_match_json", "{}") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["salary_match"] = {}
        item["refresh_priority"], item["refresh_priority_reason"] = _refresh_priority(
            item.get("interest"), item.get("recommendation")
        )
        item["match_refresh_reason"] = _match_refresh_reason(
            item.get("jd_content_hash"), item.get("jd_text")
        )
        item["match_stale"] = item["match_refresh_reason"] is not None
        item["changed"] = _recommendation_changed(
            item["previous_fit_tier"], item["recommendation"]
        )
        if item["changed"]:
            conflicts += 1
        if item.get("interest") == "reject" and item["recommendation"] == "priority":
            decision_conflicts += 1
        elif item.get("interest") == "want" and item["recommendation"] == "not_fit":
            decision_conflicts += 1
        item["reviewed"] = item.get("decided_at") is not None
        _attach_refresh_comparison(item, history.get((item["platform"], item["job_id"])))
        counts[item["recommendation"]] += 1
        jobs.append(item)

    return {
        "snapshot": {
            "analysis_version": snapshot["analysis_version"],
            "profile_id": snapshot["profile_id"],
            "profile_version": snapshot["profile_version"],
            "analyzed_at": snapshot["analyzed_at"],
            "total": len(jobs),
            "priority": int(counts["priority"]),
            "challenge": int(counts["challenge"]),
            "not_fit": int(counts["not_fit"]),
            "insufficient": sum(
                item["information_status"] == "insufficient" for item in jobs
            ),
            "conflicts": conflicts,
            "decision_conflicts": decision_conflicts,
            "stale": sum(item["match_stale"] for item in jobs),
            "baseline_pending": sum(
                item["match_refresh_reason"] == "baseline_pending"
                for item in jobs
            ),
            "jd_changed": sum(
                item["match_refresh_reason"] == "jd_changed"
                for item in jobs
            ),
            "recently_refreshed": sum(item["previous_match"] is not None for item in jobs),
            "refresh_changed": sum(item["refresh_changed"] for item in jobs),
        },
        "jobs": jobs,
        "empty_message": "",
    }


def get_match_detail(
    connection: sqlite3.Connection,
    job_id: str,
    platform: str | None = None,
) -> dict | None:
    migrate_matching_schema(connection)
    snapshot = _latest_snapshot(connection)
    if snapshot is None:
        return None
    platform_filter = " AND match.platform = ?" if platform else ""
    params = [
        snapshot["analysis_version"],
        snapshot["profile_id"],
        snapshot["profile_version"],
        job_id,
    ]
    if platform:
        params.append(platform)
    rows = connection.execute(
        """
        SELECT match.*, d.*,
               decision.interest AS decision_interest,
               decision.rejection_reason AS decision_rejection_reason,
               decision.note AS decision_note,
               decision.learning_intent AS decision_learning_intent,
               decision.decided_at AS decision_decided_at
        FROM job_match_analyses match
        JOIN job_details d
          ON d.platform = match.platform AND d.job_id = match.job_id
        LEFT JOIN job_decisions decision
          ON decision.platform = match.platform AND decision.job_id = match.job_id
        WHERE match.analysis_version = ?
          AND match.profile_id = ?
          AND match.profile_version = ?
          AND match.job_id = ?
        """
        + platform_filter
        + " ORDER BY match.platform",
        params,
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("同一岗位编号存在于多个平台，请指定平台")
    row = rows[0] if rows else None
    if row is None:
        return None
    result = dict(row)
    result["refresh_priority"], result["refresh_priority_reason"] = _refresh_priority(
        result.get("decision_interest"), result.get("recommendation")
    )
    result["match_refresh_reason"] = _match_refresh_reason(
        result.get("jd_content_hash"), result.get("jd_text")
    )
    result["match_stale"] = result["match_refresh_reason"] is not None
    history = _latest_match_history(
        connection,
        analysis_version=snapshot["analysis_version"],
        profile_id=snapshot["profile_id"],
        profile_version=snapshot["profile_version"],
    )
    _attach_refresh_comparison(
        result,
        history.get((result["platform"], result["job_id"])),
    )
    for field, target in (
        ("hard_requirements_json", "hard_requirements"),
        ("strengths_json", "strengths"),
        ("gaps_json", "gaps"),
        ("uncertainties_json", "uncertainties"),
        ("preference_signals_json", "preference_signals"),
        ("salary_match_json", "salary_match"),
    ):
        raw = result.pop(field, "{}" if target == "salary_match" else "[]")
        if target == "salary_match":
            try:
                result[target] = json.loads(raw or "{}")
            except (TypeError, json.JSONDecodeError):
                result[target] = {}
        else:
            result[target] = _json_list(raw)
    result["changed"] = _recommendation_changed(
        result.get("previous_fit_tier", ""), result["recommendation"]
    )
    decided_at = result.pop("decision_decided_at", None)
    if decided_at is None:
        result["decision"] = None
    else:
        learning_intent = result.pop("decision_learning_intent", None)
        result["decision"] = {
            "interest": result.pop("decision_interest", ""),
            "rejection_reason": result.pop("decision_rejection_reason", ""),
            "note": result.pop("decision_note", ""),
            "learning_intent": (
                None if learning_intent is None else bool(learning_intent)
            ),
            "decided_at": decided_at,
        }
    for field in (
        "decision_interest",
        "decision_rejection_reason",
        "decision_note",
        "decision_learning_intent",
    ):
        result.pop(field, None)
    return result


def analysis_as_dict(result: JobMatchAnalysis) -> dict:
    return asdict(result)

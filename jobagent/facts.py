"""招呼语事实对照检查（C 组防线，2026-09-13）。

背景：云起未来招呼语出现"带引用的 RAG 问答"这类用户没有的经历表述。
用户明确要求：不要乱答，没做过的不写。本模块把"文案说法"与"登记过的
真实经历"对起来，让审核从"看过了"升级为"核对过属实"。

三部分：
- experience_facts.json：真实经历条目库（用户可随时增删，每条一句话）。
- 账本 entry 的 ``claims`` 字段：招呼语里每条成就表述引用一个 fact_id。
- extract_claims：自动圈出招呼语中的成就类表述，未被 claims 覆盖的
  会在审核界面标为"待核对"。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jobagent.config import resolve_project_path

FACTS_FILE = "private/experience_facts.json"
MANUAL_FILE = "private/outreach_manual.json"

# 成就类表述触发词：做过/搭过/主导/首个/数字规模等，命中即要求有事实依据。
CLAIM_PATTERN = re.compile(
    r"(公司首个|首个|第一个|从\s*0\s*(搭|做|到\s*1)|从零|独立(完成|开发|主导|做)"
    r"|主导|主导过|搭过|搭了|建过|做过|写过|跑通|打通|培训过?同事"
    r"|周均处理[^，。；]*|\d[\d,.]*\s*[条万+]\s*(条)?"
    r"|10\+|统一接入[^，。；]*)"
)


def load_facts(path: str | Path = FACTS_FILE) -> list[dict]:
    """读真实经历条目库。文件缺失返回空列表（检查降级为全部待核对）。"""
    p = Path(path)
    if not p.is_absolute():
        p = resolve_project_path(str(path))
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    facts = data.get("facts") if isinstance(data, dict) else None
    return [f for f in facts if isinstance(f, dict) and f.get("id")] if isinstance(facts, list) else []


def load_manual_entries(path: str | Path = MANUAL_FILE) -> dict:
    """读人工账本全部岗位条目。文件缺失返回空 dict。"""
    p = Path(path)
    if not p.is_absolute():
        p = resolve_project_path(str(path))
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return jobs if isinstance(jobs, dict) else {}


def extract_claims(text: str) -> list[dict]:
    """圈出招呼语里的成就类表述。返回 [{phrase, context}]，context 供人工核对定位。"""
    value = str(text or "")
    hits: list[dict] = []
    for match in CLAIM_PATTERN.finditer(value):
        start = max(0, match.start() - 10)
        end = min(len(value), match.end() + 14)
        hits.append(
            {
                "phrase": match.group(0),
                "context": value[start:end].strip(),
            }
        )
    # 去重：同一触发词在同一上下文反复命中只留一条
    seen: set[tuple[str, str]] = set()
    unique = []
    for hit in hits:
        key = (hit["phrase"], hit["context"])
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return unique


def check_greeting(greeting: str, claims: list[dict], facts: list[dict]) -> dict:
    """对照一条招呼语与其声明的 claims。

    返回：
    - declared: 账本声明的说法→事实对照（fact_found/in_greeting 标记异常）
    - unchecked: 招呼语里出现、但没有任何 claim 覆盖的成就表述
    """
    fact_by_id = {str(f["id"]): f for f in facts}
    declared = []
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        fact = fact_by_id.get(str(claim.get("fact_id") or ""))
        claim_text = str(claim.get("text") or "").strip()
        declared.append(
            {
                "fact_id": claim.get("fact_id"),
                "claim_text": claim_text,
                "fact_text": str(fact.get("text") or "") if fact else "",
                "fact_found": fact is not None,
                "in_greeting": bool(claim_text) and claim_text[:10] in str(greeting or ""),
            }
        )
    covered_texts = [str(c.get("text") or "") for c in (claims or []) if isinstance(c, dict)]
    unchecked = [
        hit for hit in extract_claims(greeting) if not _is_covered(hit, covered_texts)
    ]
    return {
        "has_claims": bool(claims),
        "declared": declared,
        "unchecked": unchecked,
    }


def _is_covered(hit: dict, covered_texts: list[str]) -> bool:
    phrase = hit["phrase"]
    context = hit["context"]
    for covered in covered_texts:
        if not covered:
            continue
        if phrase in covered:
            return True
        head = covered[:8]
        if head and head in context:
            return True
    return False


def check_job(job_id: str) -> dict:
    """对一个岗位的账本招呼语做完整事实检查（review_server API 用）。"""
    entry = load_manual_entries().get(str(job_id))
    if not isinstance(entry, dict):
        return {"tracked": False, "reason": "账本中没有该岗位条目"}
    greeting = str(entry.get("greeting") or "")
    claims = entry.get("claims") if isinstance(entry.get("claims"), list) else []
    result = check_greeting(greeting, claims, load_facts())
    result["tracked"] = True
    result["greeting"] = greeting
    return result

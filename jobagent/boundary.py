"""Read-only boundary calibration over reviewed match decisions."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from jobagent.learning import note_requests_learning
from jobagent.matching import normalize_text


RECOMMENDATION_ORDER = ("priority", "challenge", "not_fit")
RECOMMENDATION_LABELS = {
    "priority": "优先投",
    "challenge": "挑战投",
    "not_fit": "当前不建议投",
}
INTEREST_LABELS = {"want": "想投", "maybe": "再看", "reject": "放弃"}
REJECTION_LABELS = {
    "role_mismatch": "方向不匹配",
    "pure_java": "偏纯 Java / 传统后端",
    "seniority": "经验门槛过高",
    "salary": "薪资与阶段不匹配",
    "employment": "用工关系风险",
    "onsite": "驻场 / 出差不接受",
    "stale": "岗位已过时",
    "other": "其他原因",
}
EXPECTED_INTEREST = {"priority": "want", "challenge": "maybe", "not_fit": "reject"}
NEGATED_REQUIREMENT_RE = re.compile(r"(?:无需|不需要|不涉及|不要求|不再需要|已剥离)", re.I)
REVIEWED_DECISIONS_SAMPLE = "__reviewed__"


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _latest_snapshot(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT analysis_version, profile_id, profile_version, analyzed_at
          FROM job_match_analyses
         ORDER BY analyzed_at DESC, rowid DESC
         LIMIT 1
        """
    ).fetchone()


def _conflict_level(recommendation: str, interest: str) -> str:
    if (recommendation, interest) in {
        ("priority", "reject"),
        ("not_fit", "want"),
    }:
        return "strong"
    if EXPECTED_INTEREST.get(recommendation) != interest:
        return "soft"
    return "aligned"


def _boundary_distance(job: dict, target_tracks: set[str]) -> int | None:
    if job["hard_requirements"]:
        return None
    score = int(job["score"])
    if job["track"] in target_tracks:
        if any(item.get("severity") == "major" for item in job["gaps"]):
            return None
        return abs(score - 75)
    if job["track"] == "product" and re.search(
        r"(?:AI|Agent|大模型)",
        normalize_text(f"{job.get('title') or ''}\n{job.get('jd_text') or ''}"),
        re.I,
    ):
        return None
    return abs(score - 45)


def _selection_key(job: dict) -> tuple:
    conflict_rank = {"strong": 0, "soft": 1, "aligned": 2}[job["conflict_level"]]
    score_rank = (
        job["boundary_distance"]
        if job["recommendation"] == "challenge"
        and job["boundary_distance"] is not None
        else 101
        if job["recommendation"] == "challenge"
        else -int(job["score"])
    )
    return (
        conflict_rank,
        0 if job["correction_count"] else 1,
        0 if job["information_status"] != "sufficient" else 1,
        0 if job["learning_intent"] else 1,
        0 if job["near_boundary"] else 1,
        score_rank,
        job["platform"],
        job["job_id"],
    )


def _reason_lines(job: dict) -> list[str]:
    recommendation = RECOMMENDATION_LABELS[job["recommendation"]]
    interest = INTEREST_LABELS.get(job["interest"], job["interest"])
    reasons = []
    if job["conflict_level"] == "strong":
        reasons.append(f"系统判为{recommendation}，你的决定是{interest}")
    elif job["conflict_level"] == "soft":
        reasons.append(f"系统建议{recommendation}，你的决定是{interest}")
    if job["rejection_reason"]:
        reasons.append(REJECTION_LABELS.get(job["rejection_reason"], job["rejection_reason"]))
    if job["hard_requirements"]:
        reasons.append(
            "硬门槛："
            + "、".join(
                str(item.get("label") or item.get("id"))
                for item in job["hard_requirements"][:2]
            )
        )
    if job["gaps"]:
        reasons.append("能力缺口：" + "、".join(str(item.get("label") or item.get("id")) for item in job["gaps"][:2]))
    if job["strengths"]:
        reasons.append(
            "匹配优势："
            + "、".join(
                str(item.get("label") or item.get("id"))
                for item in job["strengths"][:2]
            )
        )
    if job["information_status"] != "sufficient":
        reasons.append("岗位信息不完整，结论需要降置信处理")
    if job["correction_count"]:
        reasons.append(f"已有 {job['correction_count']} 条系统纠错")
    if job["learning_intent"]:
        reasons.append("备注或标记包含能力补齐信号")
    if job["near_boundary"]:
        reasons.append("分数接近当前分类边界")
    return reasons[:5] or ["作为同档对照样本复核"]


def _insight(
    identifier: str,
    title: str,
    detail: str,
    count: int,
    severity: str = "medium",
) -> dict:
    return {
        "id": identifier,
        "title": title,
        "detail": detail,
        "count": count,
        "severity": severity,
    }


def _build_insights(jobs: list[dict]) -> list[dict]:
    priority_rejected = [
        job for job in jobs if job["recommendation"] == "priority" and job["interest"] == "reject"
    ]
    not_fit_wanted = [
        job for job in jobs if job["recommendation"] == "not_fit" and job["interest"] == "want"
    ]
    experience_priority = [
        job
        for job in priority_rejected
        if job["rejection_reason"] == "seniority"
        or any(item.get("id") in {"experience_depth", "experience_requirement"} for item in job["gaps"])
    ]
    incomplete_priority = [
        job
        for job in jobs
        if job["recommendation"] == "priority" and job["information_status"] != "sufficient"
    ]
    negated_gaps = [
        job
        for job in jobs
        if any(NEGATED_REQUIREMENT_RE.search(str(item.get("jd_evidence") or "")) for item in job["gaps"])
    ]
    insights = []
    if priority_rejected:
        insights.append(
            _insight(
                "priority_rejected",
                f"优先投中有 {len(priority_rejected)} 条被你放弃",
                "优先级可能高估了经验、薪资、用工或工作方式的可接受性，先逐条确认原因。",
                len(priority_rejected),
                "high",
            )
        )
    if not_fit_wanted:
        insights.append(
            _insight(
                "not_fit_wanted",
                f"当前不建议投中有 {len(not_fit_wanted)} 条仍想投",
                "系统可能把可协商的工作方式或偏好当成了淘汰条件，需要区分硬门槛与可确认项。",
                len(not_fit_wanted),
                "high",
            )
        )
    if experience_priority:
        insights.append(
            _insight(
                "experience_overrated",
                f"{len(experience_priority)} 条优先投没有挡住经验门槛",
                "经验年限目前更像扣分项，可能不足以阻止明显资深岗位进入优先投。",
                len(experience_priority),
                "high",
            )
        )
    if negated_gaps:
        insights.append(
            _insight(
                "negated_requirement",
                f"{len(negated_gaps)} 条否定描述被提取为能力缺口",
                "“无需、不涉及”等句子应先做否定语义过滤，再进入能力缺口判断。",
                len(negated_gaps),
                "high",
            )
        )
    if incomplete_priority:
        insights.append(
            _insight(
                "incomplete_priority",
                f"{len(incomplete_priority)} 条信息不足岗位仍进入优先投",
                "信息不完整时应限制最高推荐档，避免把缺失信息误当成没有风险。",
                len(incomplete_priority),
            )
        )
    if not insights:
        insights.append(
            _insight(
                "no_strong_pattern",
                "当前样本没有形成稳定误判模式",
                "继续积累决定和纠错，再评估是否需要调整规则。",
                0,
                "low",
            )
        )
    return insights


def build_boundary_dashboard(
    connection: sqlite3.Connection,
    limit_per_group: int = 10,
) -> dict:
    """Select reviewed, high-information examples without changing stored state."""
    if not 1 <= int(limit_per_group) <= 50:
        raise ValueError("每组边界样本数量需在 1 到 50 之间")
    snapshot = _latest_snapshot(connection)
    if snapshot is None:
        return {
            "snapshot": None,
            "groups": [],
            "insights": [],
            "actions": {"threshold_change_recommended": False},
            "empty_message": "尚未生成匹配预览，无法建立边界复核样本。",
        }

    profile_row = connection.execute(
        """
        SELECT profile_json
          FROM candidate_profiles
         WHERE profile_id = ? AND profile_version = ?
         LIMIT 1
        """,
        (snapshot["profile_id"], snapshot["profile_version"]),
    ).fetchone()
    profile = {}
    if profile_row is not None:
        try:
            profile = json.loads(profile_row["profile_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            profile = {}
    target_tracks = {
        str(value) for value in profile.get("target_tracks") or [] if value
    }

    rows = connection.execute(
        """
        SELECT match.platform, match.job_id, match.recommendation, match.track,
               match.score, match.confidence, match.information_status,
               match.previous_fit_tier, match.previous_relevance_score,
               match.hard_requirements_json, match.strengths_json,
               match.gaps_json, match.uncertainties_json, d.title, d.company,
               d.salary_raw, d.jd_text,
               d.location, d.experience_raw, decision.interest,
               decision.rejection_reason, decision.note,
               decision.learning_intent, decision.decided_at,
               COALESCE(
                 (
                   SELECT sample.sample_name
                     FROM job_review_samples sample
                    WHERE sample.platform = match.platform
                      AND sample.job_id = match.job_id
                    ORDER BY CASE WHEN sample.sample_name = 'positioning-v1' THEN 0 ELSE 1 END,
                             sample.sample_name
                    LIMIT 1
                 ),
                 ?
               ) AS sample_name
          FROM job_match_analyses match
          JOIN job_details d
            ON d.platform = match.platform AND d.job_id = match.job_id
          JOIN job_decisions decision
            ON decision.platform = match.platform AND decision.job_id = match.job_id
         WHERE match.analysis_version = ?
           AND match.profile_id = ?
           AND match.profile_version = ?
         ORDER BY decision.decided_at, match.job_id
        """,
        (
            REVIEWED_DECISIONS_SAMPLE,
            snapshot["analysis_version"],
            snapshot["profile_id"],
            snapshot["profile_version"],
        ),
    ).fetchall()
    correction_counts = {
        (row["platform"], row["job_id"]): int(row["count"])
        for row in connection.execute(
            """
            SELECT platform, job_id, COUNT(*) AS count
              FROM job_corrections
             GROUP BY platform, job_id
            """
        ).fetchall()
    }

    jobs = []
    for row in rows:
        job = dict(row)
        job["hard_requirements"] = _json_list(job.pop("hard_requirements_json", "[]"))
        job["strengths"] = _json_list(job.pop("strengths_json", "[]"))
        job["gaps"] = _json_list(job.pop("gaps_json", "[]"))
        job["uncertainties"] = _json_list(job.pop("uncertainties_json", "[]"))
        job["learning_intent"] = bool(
            job.get("learning_intent") == 1
            or (job.get("learning_intent") is None and note_requests_learning(job.get("note") or ""))
        )
        job["correction_count"] = correction_counts.get((job["platform"], job["job_id"]), 0)
        job["conflict_level"] = _conflict_level(job["recommendation"], job["interest"])
        job["boundary_distance"] = _boundary_distance(job, target_tracks)
        job["near_boundary"] = (
            job["boundary_distance"] is not None and job["boundary_distance"] <= 5
        )
        job.pop("jd_text", None)
        signals = []
        if job["conflict_level"] != "aligned":
            signals.append("conflict")
        if job["information_status"] != "sufficient":
            signals.append("information")
        if job["learning_intent"]:
            signals.append("learning")
        if job["correction_count"]:
            signals.append("correction")
        if job["near_boundary"]:
            signals.append("boundary")
        job["signals"] = signals
        job["reasons"] = _reason_lines(job)
        jobs.append(job)

    groups = []
    recommendation_counts = Counter(job["recommendation"] for job in jobs)
    for recommendation in RECOMMENDATION_ORDER:
        candidates = sorted(
            (job for job in jobs if job["recommendation"] == recommendation),
            key=_selection_key,
        )
        groups.append(
            {
                "recommendation": recommendation,
                "label": RECOMMENDATION_LABELS[recommendation],
                "available": len(candidates),
                "jobs": candidates[: int(limit_per_group)],
            }
        )

    conflict_counts = Counter(job["conflict_level"] for job in jobs)
    selected_jobs = [job for group in groups for job in group["jobs"]]
    selected_conflicts = Counter(job["conflict_level"] for job in selected_jobs)
    selected_count = len(selected_jobs)
    return {
        "snapshot": {
            "analysis_version": snapshot["analysis_version"],
            "profile_id": snapshot["profile_id"],
            "profile_version": snapshot["profile_version"],
            "analyzed_at": snapshot["analyzed_at"],
            "reviewed_jobs": len(jobs),
            "selected_jobs": selected_count,
            "strong_conflicts": int(conflict_counts["strong"]),
            "soft_conflicts": int(conflict_counts["soft"]),
            "information_gaps": sum(job["information_status"] != "sufficient" for job in jobs),
            "learning_signals": sum(job["learning_intent"] for job in jobs),
            "corrections": sum(job["correction_count"] for job in jobs),
            "selected_metrics": {
                "jobs": selected_count,
                "strong_conflicts": int(selected_conflicts["strong"]),
                "soft_conflicts": int(selected_conflicts["soft"]),
                "information_gaps": sum(
                    job["information_status"] != "sufficient" for job in selected_jobs
                ),
                "learning_signals": sum(job["learning_intent"] for job in selected_jobs),
            },
            "distribution": {
                recommendation: int(recommendation_counts[recommendation])
                for recommendation in RECOMMENDATION_ORDER
            },
        },
        "groups": groups,
        "insights": _build_insights(jobs),
        "actions": {
            "threshold_change_recommended": False,
            "next_step": "先复核选出的边界样本，再把确认过的模式转成规则测试。",
            "rule_change_gate": "强冲突需要明确原因；单个偏好或孤立备注不直接变成硬规则。",
        },
        "empty_message": "",
    }

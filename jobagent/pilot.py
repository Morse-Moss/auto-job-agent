"""Build a local, manual-confirmation pilot plan for job outreach."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


DEFAULT_RESUME_TTL_DAYS = 21
DEFAULT_MAX_JOBS = 30


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _resume_age_days(path: Path, now: datetime) -> float:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return max(0.0, (now - modified).total_seconds() / 86400)


def _job_block_reasons(match: dict | None, freshness: dict | None) -> list[str]:
    reasons: list[str] = []
    if match is None:
        reasons.append("match_missing")
    else:
        if match.get("match_stale"):
            reasons.append("match_stale")
        if match.get("information_status") != "sufficient":
            reasons.append("match_information_insufficient")
        if not str(match.get("jd_text") or "").strip():
            reasons.append("jd_missing")
    if freshness is None:
        reasons.append("freshness_missing")
    else:
        if freshness.get("evidence_state") != "active":
            reasons.append("jd_not_active")
        if freshness.get("refresh_due"):
            reasons.append("jd_refresh_due")
        if freshness.get("detail_status") != "collected":
            reasons.append("jd_detail_unavailable")
    return reasons


def action_score(match: dict, freshness: dict | None = None) -> int:
    """Rank a job for review; this never grants send permission."""
    freshness = freshness or {}
    fit = int(match.get("score") or 0)
    salary = match.get("salary_match") or {}
    salary_score = int(salary.get("score") or 50)
    completeness = 100 if match.get("information_status") == "sufficient" else 70
    age = float(freshness.get("age_days") or 0)
    freshness_score = max(40, 100 - min(60, int(age * 3)))
    policy_adjustment = 0
    text = f"{match.get('title', '')} {match.get('jd_text', '')}"
    if "双休" in text:
        policy_adjustment += 5
    if any(value in text for value in ("单休", "大小周", "单双休")):
        policy_adjustment -= 8
    if "0-20" in str(match.get("company_size") or "") and "AI" in text:
        policy_adjustment += 3
    if salary_score < 50 and "0-20" in str(match.get("company_size") or "") and "单休" in text:
        policy_adjustment -= 30
    value = fit * 0.60 + salary_score * 0.15 + freshness_score * 0.15 + completeness * 0.10 + policy_adjustment
    return max(0, min(100, round(value)))


def build_pilot_plan(
    match_dashboard: dict,
    freshness_dashboard: dict,
    resume_path: str | Path,
    *,
    now: str | datetime | None = None,
    max_jobs: int = DEFAULT_MAX_JOBS,
    resume_ttl_days: int = DEFAULT_RESUME_TTL_DAYS,
    excluded_companies: set[str] | None = None,
    sent_job_ids: set[str] | None = None,
) -> dict:
    """Return a bounded plan without model calls, browser actions, or sends."""
    as_of = _parse_time(now) if isinstance(now, str) else (now or datetime.now(timezone.utc))
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    path = Path(resume_path)
    if not path.is_file():
        raise FileNotFoundError(f"resume not found: {path}")

    resume_age = _resume_age_days(path, as_of)
    resume_stale = resume_age > max(1, int(resume_ttl_days))
    match_jobs = {
        (str(job.get("platform") or "boss"), str(job.get("job_id") or "")): job
        for job in (match_dashboard.get("jobs") or [])
        if job.get("job_id")
    }
    freshness_jobs = {
        (str(job.get("platform") or "boss"), str(job.get("job_id") or "")): job
        for job in (freshness_dashboard.get("jobs") or [])
        if job.get("job_id")
    }

    job_blocks: list[dict] = []
    candidates: list[dict] = []
    limit = max(1, int(max_jobs))
    excluded_companies = {str(value).strip() for value in (excluded_companies or set()) if str(value).strip()}
    sent_job_ids = {str(value) for value in (sent_job_ids or set())}
    action_keys = sorted(
        (
            key
            for key, match in match_jobs.items()
            if match.get("recommendation") in {"priority", "challenge"}
        ),
        key=lambda key: (
            0 if match_jobs[key].get("interest") == "want" else 1,
            -action_score(match_jobs[key], freshness_jobs.get(key)),
            key[1],
        ),
    )[:limit]
    for key in action_keys:
        match = match_jobs.get(key)
        freshness = freshness_jobs.get(key)
        reasons = _job_block_reasons(match, freshness)
        if match.get("company") in excluded_companies:
            reasons.append("company_excluded")
        if key[1] in sent_job_ids:
            reasons.append("already_sent")
        if reasons:
            job_blocks.append(
                {
                    "platform": key[0],
                    "job_id": key[1],
                    "title": (match or freshness or {}).get("title", ""),
                    "company": (match or freshness or {}).get("company", ""),
                    "reasons": reasons,
                }
            )
            continue
        candidates.append(
            {
                "platform": key[0],
                "job_id": key[1],
                "title": match.get("title", ""),
                "company": match.get("company", ""),
                "score": match.get("score"),
                "action_score": action_score(match, freshness),
                "recommendation": match.get("recommendation"),
                "source_url": match.get("source_url") or freshness.get("source_url", ""),
                "jd_age_days": freshness.get("age_days"),
                "policy_tags": [
                    tag for tag, ok in (
                        ("double_rest", "双休" in f"{match.get('title', '')} {match.get('jd_text', '')}"),
                        ("single_rest", any(v in f"{match.get('title', '')} {match.get('jd_text', '')}" for v in ("单休", "大小周", "单双休"))),
                        ("ai_startup", "0-20" in str(match.get("company_size") or "") and "AI" in f"{match.get('title', '')} {match.get('jd_text', '')}"),
                    ) if ok
                ],
            }
        )

    candidates.sort(key=lambda item: (-(int(item.get("action_score") or 0)), item["job_id"]))
    candidates = candidates[:limit]

    blockers: list[str] = []
    if resume_stale:
        blockers.append("resume_stale")
    if any("jd_refresh_due" in item["reasons"] for item in job_blocks):
        blockers.append("jd_refresh_due")
    if resume_stale:
        candidates = []
    next_actions: list[str] = []
    if resume_stale:
        next_actions.append("refresh_resume")
    if "jd_refresh_due" in blockers:
        next_actions.append("refresh_jd")
    if candidates:
        next_actions.append("confirm_each_job")
    if not next_actions:
        next_actions.append("improve_candidate_pool")

    return {
        "status": (
            "ready_for_manual_confirmation"
            if candidates and not blockers
            else "blocked"
        ),
        "as_of": as_of.isoformat(),
        "resume": {
            "path": str(path),
            "age_days": round(resume_age, 1),
            "ttl_days": int(resume_ttl_days),
            "stale": resume_stale,
        },
        "limits": {"max_jobs": limit, "browser_actions": 0, "messages_sent": 0},
        "action_pool": {
            "explicitly_wanted": sum(
                match.get("interest") == "want" for match in match_jobs.values()
            ),
            "evaluated": len(action_keys),
            "candidate_pool_size": len(candidates),
        },
        "candidates": candidates,
        "job_blocks": job_blocks,
        "blockers": blockers,
        "next_actions": next_actions,
    }

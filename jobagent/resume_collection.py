"""Resume-driven, read-only market collection orchestration."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable

from jobagent.analysis import run_analysis
from jobagent.calibration import calibrate_resume, save_calibration_dashboard
from jobagent.collection import CollectionStore


def _collection_status(result) -> dict:
    status = str(getattr(result, "status", "stopped") or "stopped")
    error = str(getattr(result, "error", "") or "")
    return {
        "external_collection": status,
        "external_collection_error": error,
        "collection_run_id": str(getattr(result, "run_id", "") or ""),
        "collected_new_jobs": int(getattr(result, "collected", 0) or 0),
    }


def collect_resume_market_data(
    db_path: str | Path,
    cfg: dict,
    resume_text: str,
    filename: str = "resume.md",
    candidate_id: str = "local-candidate",
    analysis_version: str = "resume-match-v1",
    analysis_output_dir: str | Path = "output/analysis",
    collector_factory: Callable | None = None,
) -> dict:
    """Collect jobs using resume-derived queries, then refresh local analysis.

    The first calibration is local and supplies the search plan. The external
    collector is invoked only after this function is explicitly called. Any
    collector failure is represented in the returned plan while the prior
    local calibration and review decisions remain available.
    """
    database = Path(db_path).resolve()
    initial = calibrate_resume(
        database,
        resume_text,
        filename=filename,
        candidate_id=candidate_id,
        analysis_version=analysis_version,
    )
    initial_plan = dict(initial.get("collection_plan") or {})
    queries = [str(value) for value in initial_plan.get("queries") or [] if str(value).strip()]
    if not queries:
        queries = ["AI 应用工程师"]

    collection_cfg = copy.deepcopy((cfg or {}).get("collection") or {})
    cities = list(collection_cfg.get("cities") or [])
    if not cities:
        boss_cfg = (cfg or {}).get("boss") or {}
        if boss_cfg.get("city"):
            cities = [str(boss_cfg["city"])]
    collection_cfg["keywords"] = queries
    collection_cfg["cities"] = cities
    increment = max(1, int(collection_cfg.get("resume_collection_increment", 50)))
    starting_jobs = int(initial_plan.get("local_jobs") or 0)
    starting_records = int(initial_plan.get("collected_records") or starting_jobs)
    target = starting_records + increment
    collector_cfg = copy.deepcopy(cfg or {})
    collector_cfg["collection"] = collection_cfg

    status = {
        "external_collection": "not_run",
        "external_collection_error": "",
        "collection_run_id": "",
        "collected_new_jobs": 0,
    }
    try:
        if collector_factory is None:
            from jobagent.platforms.boss_collector import BossCollector

            collector_factory = BossCollector
        store = CollectionStore(database)
        try:
            collector = collector_factory(collector_cfg, store)
            result = collector.run(target=target, source="search")
            status = _collection_status(result)
        finally:
            store.close()
    except Exception as exc:  # preserve the local result and expose the stop reason
        status = {
            "external_collection": "stopped",
            "external_collection_error": f"{type(exc).__name__}: {exc}",
            "collection_run_id": "",
            "collected_new_jobs": 0,
        }

    analysis_result = None
    try:
        analysis_result = run_analysis(database, analysis_output_dir)
    except Exception as exc:
        status["analysis_error"] = f"{type(exc).__name__}: {exc}"

    final = calibrate_resume(
        database,
        resume_text,
        filename=filename,
        candidate_id=candidate_id,
        analysis_version=analysis_version,
    )
    final_plan = dict(final.get("collection_plan") or {})
    final_plan.update(
        {
            "queries": queries,
            "cities": cities,
            "source": "resume_profile",
            "starting_local_jobs": starting_jobs,
            "starting_collected_records": starting_records,
            "requested_target": target,
            "resume_collection_increment": increment,
            **status,
        }
    )
    final["collection_plan"] = final_plan
    final["privacy"] = {
        **(final.get("privacy") or {}),
        "external_calls": True,
        "raw_resume_stored": False,
    }
    if analysis_result is not None:
        final["collection_plan"]["analysis_total"] = analysis_result.total
    save_calibration_dashboard(database, final)
    return final

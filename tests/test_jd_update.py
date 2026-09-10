import hashlib
import os
import subprocess
import sys

import pytest

from jobagent import jd_update
from jobagent.collection import CollectionStore


def job(job_id, interest=None, **extra):
    return {
        "platform": "boss", "job_id": job_id, "interest": interest,
        "refresh_due": True, "refresh_action": "detail_refresh",
        "lifecycle_state": "aging", "age_days": 30, **extra,
    }


def board(*jobs):
    return {"summary": {"total": 301, "as_of": "2026-09-10"}, "refresh_plan": list(jobs)}


CONFIG = {"collection": {"keywords": ["AI 应用工程师"], "cities": ["上海"]}}


def test_old_inventory_does_not_exhaust_independent_round_budgets():
    plan = jd_update.build_update_plan(board(), CONFIG, favorites_budget=25, search_budget=50)
    assert plan["inventory_total"] == 301
    assert plan["stages"][0]["budget"] == 25
    assert plan["stages"][1]["budget"] == 50
    assert plan["external_status"] == "not_run"
    assert plan["coverage"]["external_coverage"] == "not_checked"


def test_refresh_is_prioritized_deduplicated_and_excludes_terminal_and_scans():
    data = board(job("other"), job("maybe", "maybe"), job("want", "want"),
                 job("want", "want"), job("closed", "want", lifecycle_state="closed"),
                 job("scan", "want", refresh_action="coverage_scan"),
                 job("fresh", "want", refresh_due=False), job("foreign", platform="other"))
    plan = jd_update.build_update_plan(data, CONFIG, refresh_budget=2)
    assert plan["stages"][2]["job_ids"] == ["want", "maybe"]
    assert plan["coverage"]["remaining_detail_jobs"] == 1
    assert plan["coverage"]["coverage_scan_jobs"] == 1
    assert plan["coverage"]["detail_selection"] == "partial"


def test_search_sources_follow_config_without_expanding_scope():
    cfg = {"collection": {"keywords": ["Agent", "Agent", " RAG "], "cities": ["上海", "上海"]}}
    plan = jd_update.build_update_plan(board(), cfg)
    assert plan["stages"][1]["sources"] == [
        {"city": "上海", "keyword": "Agent"}, {"city": "上海", "keyword": "RAG"}]
    assert jd_update.build_update_plan(board(), {})["stages"][1]["status"] == "blocked"


@pytest.mark.parametrize("bad", [-1, 1.5, True, "25", 501])
def test_invalid_budget_rejected(bad):
    with pytest.raises(ValueError):
        jd_update.build_update_plan(board(), CONFIG, favorites_budget=bad)


def test_zero_budgets_disable_stages_without_losing_remaining_work():
    plan = jd_update.build_update_plan(board(job("due")), CONFIG,
                                      favorites_budget=0, search_budget=0, refresh_budget=0)
    assert all(stage["status"] == "skipped" for stage in plan["stages"])
    assert plan["coverage"]["remaining_detail_jobs"] == 1


def test_database_snapshot_is_read_only_and_missing_path_is_not_created(tmp_path):
    path = tmp_path / "jobs.db"
    store = CollectionStore(path)
    run = store.start_run(1)
    store.save_detail({"platform": "boss", "job_id": "old", "title": "AI",
                       "company": "Example", "source": "boss-favorites",
                       "jd": "Agent application development"}, run)
    store.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    plan = jd_update.plan_from_database(path, CONFIG)
    assert plan["inventory_total"] == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        jd_update.plan_from_database(missing, CONFIG)
    assert not missing.exists()


def test_cli_outputs_utf8_on_windows_legacy_encoding():
    result = subprocess.run(
        [sys.executable, "-m", "jobagent.jd_update", "--help"],
        capture_output=True, env={**os.environ, "PYTHONIOENCODING": "cp936"},
        check=True,
    )
    assert "本地更新计划" in result.stdout.decode("utf-8")

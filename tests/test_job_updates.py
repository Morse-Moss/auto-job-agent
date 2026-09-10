import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

from jobagent.job_updates import JobUpdateStore


def test_task_is_durable_and_duplicate_start_returns_existing_task():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "jobs.db"
        store = JobUpdateStore(path)
        first = store.start({"favorites_budget": 2, "search_budget": 3, "refresh_budget": 4})
        second = store.start({"favorites_budget": 2, "search_budget": 3, "refresh_budget": 4})
        assert first["run_id"] == second["run_id"]
        assert second["status"] == "planned"
        assert store.get(first["run_id"])["stages"][0]["budget"] == 2
        store.close()
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("select count(*) from job_update_runs").fetchone()[0] == 1


def test_running_tasks_are_interrupted_on_restart_and_stop_is_durable():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "jobs.db"
        store = JobUpdateStore(path)
        task = store.start({})
        store.mark_running(task["run_id"])
        store.close()
        restarted = JobUpdateStore(path)
        assert restarted.get(task["run_id"])["status"] == "interrupted"
        assert restarted.stop(task["run_id"])["status"] == "cancelled"
        assert restarted.stop(task["run_id"])["status"] == "cancelled"
        restarted.close()


def test_plan_is_stored_as_json_without_external_execution():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "jobs.db"
        store = JobUpdateStore(path)
        task = store.start({})
        record = store.get(task["run_id"])
        assert record["external_status"] == "not_run"
        assert json.loads(record["plan_json"])["schema_version"] == "jd-update-plan-v1"
        store.close()

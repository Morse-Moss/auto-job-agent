import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen
import threading
from concurrent.futures import ThreadPoolExecutor

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
        assert restarted.get(task["run_id"])["status"] == "running"
        restarted.recover_interrupted()
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


def test_latest_returns_most_recent_task():
    with tempfile.TemporaryDirectory() as d:
        store = JobUpdateStore(Path(d) / "jobs.db")
        task = store.start({})
        assert store.latest()["run_id"] == task["run_id"]
        store.stop(task["run_id"])
        newer = store.start({})
        assert store.latest()["run_id"] == newer["run_id"]
        store.close()


def test_parallel_start_returns_one_task(tmp_path):
    path = tmp_path / "jobs.db"
    JobUpdateStore(path).close()
    from jobagent.collection import CollectionStore
    CollectionStore(path).close()
    barrier = threading.Barrier(2)

    def start():
        with closing(JobUpdateStore(path)) as store:
            barrier.wait(timeout=5)
            return store.start({})["run_id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert len(set(results)) == 1


def test_http_latest_reload_and_cancel_preserve_exact_task(tmp_path):
    from jobagent.review_server import create_server
    from test_review_workbench import create_fixture_db
    path = tmp_path / "jobs.db"
    create_fixture_db(path)
    server = create_server(path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(route, data=None):
        req = Request(f"http://127.0.0.1:{server.server_port}{route}",
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as response:
            return json.load(response)["job_update"]

    try:
        assert request("/api/job-updates/latest") is None
        request("/api/job-updates/plan")
        assert request("/api/job-updates/latest") is None
        first = request("/api/job-updates", {})
        assert request("/api/job-updates/latest")["run_id"] == first["run_id"]
        assert request("/api/job-updates", {})["run_id"] == first["run_id"]
        with closing(JobUpdateStore(path)) as store:
            store.mark_running(first["run_id"])
        assert request("/api/job-updates/latest")["status"] == "running"
        assert request(f"/api/job-updates/{first['run_id']}")["status"] == "running"
        assert request(f"/api/job-updates/{first['run_id']}/stop", {})["status"] == "cancelled"
        second = request("/api/job-updates", {})
        assert second["run_id"] != first["run_id"]
        assert request("/api/job-updates/latest")["run_id"] == second["run_id"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jobagent.collection import CollectionStore
from jobagent.job_update_executor import BrowserLease, JobUpdateExecutor
from jobagent.job_updates import JobUpdateStore
from jobagent.platforms.boss_collector import BossCollector

CONFIG = {"collection": {"keywords": ["AI 应用工程师"], "cities": ["上海"]}}


def http_json(port, route, data=None):
    req = Request(
        f"http://127.0.0.1:{port}{route}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=10) as response:
        return json.load(response)["job_update"]


class FakeCollector:
    """Injected stand-in. It never opens a CDP session or touches Boss."""

    def __init__(self, results=None, on_stage=None):
        self.calls = []
        self.results = results or {}
        self.on_stage = on_stage

    def _result(self, kind):
        data = self.results.get(kind) or {}
        return SimpleNamespace(
            run_id="fake-run",
            collected=int(data.get("collected", 0) or 0),
            total=int(data.get("total", 0) or 0),
            status=str(data.get("status", "completed")),
            error=data.get("error"),
        )

    def _stopped_result(self):
        return SimpleNamespace(
            run_id="fake-run", collected=0, total=0,
            status="stopped_by_user", error="用户请求停止，已采部分保留")

    def run_incremental(self, favorites_budget=0, search_budget=0,
                        should_stop=None, progress=None):
        self.calls.append(("incremental", favorites_budget, search_budget))
        if self.on_stage:
            self.on_stage("incremental")
        if should_stop and should_stop():
            return self._stopped_result()
        if progress:
            progress(int(self.results.get("incremental", {}).get("collected", 0) or 0),
                     favorites_budget + search_budget)
        return self._result("incremental")

    def refresh(self, job_ids, max_jobs=20, should_stop=None, progress=None):
        self.calls.append(("refresh", list(job_ids), max_jobs))
        if self.on_stage:
            self.on_stage("refresh")
        if should_stop and should_stop():
            return self._stopped_result()
        if progress:
            progress(int(self.results.get("refresh", {}).get("collected", 0) or 0),
                     len(job_ids))
        return self._result("refresh")


def plan_with_budgets(store, run_id, favorites=2, search=3, refresh_ids=("j1", "j2")):
    plan = store.get(run_id)["plan"]
    for stage in plan["stages"]:
        if stage["source"] == "favorites":
            stage["budget"] = favorites
            stage["status"] = "planned"
        elif stage["source"] == "search":
            stage["budget"] = search
            stage["status"] = "planned"
        elif stage["source"] == "detail_refresh":
            stage["job_ids"] = list(refresh_ids)
            stage["budget"] = len(refresh_ids)
            stage["status"] = "planned"
    store.write_plan(run_id, plan)
    return store.get(run_id)


def test_browser_lease_is_exclusive_and_reentrant_for_same_owner():
    lease = BrowserLease()
    with lease.acquire("update"):
        with lease.acquire("update"):
            assert lease.owner == "update"
            blocked = []
            thread = threading.Thread(target=lambda: blocked.append(lease.try_acquire("resume")))
            thread.start()
            thread.join()
            assert blocked == [False]
    assert lease.owner is None


def test_incremental_collector_assigns_separate_targets_without_connecting():
    with tempfile.TemporaryDirectory() as directory:
        store = CollectionStore(Path(directory) / "jobs.db")
        collector = BossCollector({"boss": {}, "collection": {}}, store)
        browser = Mock(contexts=[Mock(pages=[], new_page=Mock(side_effect=[Mock(), Mock()]))])
        manager = Mock()
        manager.__enter__ = Mock(return_value=Mock(chromium=Mock(connect_over_cdp=Mock(return_value=browser))))
        manager.__exit__ = Mock(return_value=False)
        with patch.object(collector, "_collect_favorites") as favorites, patch.object(collector, "_collect_search") as search, patch("jobagent.platforms.boss_collector.sync_playwright", return_value=manager) as playwright:
            result = collector.run_incremental(favorites_budget=4, search_budget=7)
        assert result.status == "completed"
        assert favorites.call_args.kwargs["target"] == 4
        assert search.call_args.kwargs["target"] == 7
        playwright.assert_called_once()
        store.close()


def test_executor_rejects_external_execution_without_explicit_run_method():
    executor = JobUpdateExecutor()
    assert executor.prepare_only({"run_id": "r1", "status": "planned"})["status"] == "planned"


def test_collection_run_metrics_keep_unknown_refresh_results_explicit():
    with tempfile.TemporaryDirectory() as directory:
        store = CollectionStore(Path(directory) / "jobs.db")
        run_id = store.start_run(2, source="test")
        store.save_detail({"platform": "boss", "job_id": "ok", "source_url": "", "title": "ok", "company": "x", "jd": "jd"}, run_id)
        store.save_failure({"platform": "boss", "job_id": "bad", "source_url": "", "title": "bad", "company": "x"}, run_id, "timeout")
        metrics = store.run_metrics(run_id)
        assert metrics["detail_success"] == 1
        assert metrics["detail_failed"] == 1
        assert metrics["refresh_result_unknown"] == 0
        store.close()


def test_executor_runs_stages_in_order_and_writes_real_counts():
    with tempfile.TemporaryDirectory() as directory:
        store = JobUpdateStore(Path(directory) / "jobs.db", CONFIG)
        try:
            task = store.start({"favorites_budget": 2, "search_budget": 3, "refresh_budget": 2})
            task = plan_with_budgets(store, task["run_id"], favorites=2, search=3,
                                     refresh_ids=("j1", "j2"))
            collector = FakeCollector({"incremental": {"collected": 2},
                                       "refresh": {"collected": 2}})
            result = JobUpdateExecutor().run(task, store, collector=collector)
            assert [call[0] for call in collector.calls] == ["incremental", "incremental", "refresh"]
            assert collector.calls[0][1:] == (2, 0)
            assert collector.calls[1][1:] == (0, 3)
            assert collector.calls[2][1:] == (["j1", "j2"], 2)
            assert result["status"] == "completed"
            assert result["stages"][0]["status"] == "completed"
            assert result["stages"][0]["collected"] == 2
            assert result["stages"][2]["collected"] == 2
            assert store.get(task["run_id"])["stages"][0]["collected"] == 2
        finally:
            store.close()


def test_stop_request_cancels_remaining_stages_without_new_source():
    with tempfile.TemporaryDirectory() as directory:
        store = JobUpdateStore(Path(directory) / "jobs.db", CONFIG)
        try:
            task = store.start({"favorites_budget": 2, "search_budget": 3, "refresh_budget": 2})
            run_id = task["run_id"]
            task = plan_with_budgets(store, run_id, favorites=2, search=3,
                                     refresh_ids=("j1", "j2"))

            def stop_during_first_stage(kind):
                if kind == "incremental" and len(collector.calls) == 1:
                    store.stop(run_id)

            collector = FakeCollector(on_stage=stop_during_first_stage)
            result = JobUpdateExecutor().run(task, store, collector=collector)
            assert len(collector.calls) == 1
            assert result["status"] == "cancelled"
            assert result["stages"][1]["status"] == "cancelled"
            assert result["stages"][2]["status"] == "cancelled"
            assert "停止" in (result["error"] or "")
        finally:
            store.close()


def test_security_check_pauses_every_later_stage():
    with tempfile.TemporaryDirectory() as directory:
        store = JobUpdateStore(Path(directory) / "jobs.db", CONFIG)
        try:
            task = store.start({"favorites_budget": 2, "search_budget": 3, "refresh_budget": 2})
            task = plan_with_budgets(store, task["run_id"], favorites=2, search=3,
                                     refresh_ids=("j1", "j2"))
            collector = FakeCollector({"incremental": {"status": "security_check",
                                                       "error": "需要验证码"}})
            result = JobUpdateExecutor().run(task, store, collector=collector)
            assert len(collector.calls) == 1
            assert result["status"] == "paused"
            assert result["stages"][0]["status"] == "paused"
            assert result["stages"][1]["status"] == "cancelled"
            assert "验证码" in (result["error"] or "")
        finally:
            store.close()


def test_executor_refuses_to_run_while_another_owner_holds_the_lease():
    with tempfile.TemporaryDirectory() as directory:
        store = JobUpdateStore(Path(directory) / "jobs.db", CONFIG)
        try:
            task = store.start({})
            lease = BrowserLease()
            executor = JobUpdateExecutor(lease=lease)
            with lease.acquire("resume-collect"):
                with pytest.raises(RuntimeError):
                    executor.run(task, store, collector=FakeCollector())
            assert store.get(task["run_id"])["status"] == "planned"
        finally:
            store.close()


def test_http_execute_runs_task_and_get_never_executes(tmp_path):
    from jobagent.review_server import create_server
    from test_review_workbench import create_fixture_db

    path = tmp_path / "jobs.db"
    create_fixture_db(path)
    collector = FakeCollector({"incremental": {"collected": 1}})
    server = create_server(path, port=0,
                           job_update_executor=JobUpdateExecutor(collector=collector))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(route, data=None):
        return http_json(server.server_port, route, data)

    try:
        task = request("/api/job-updates", {})
        assert collector.calls == []
        assert request(f"/api/job-updates/{task['run_id']}")["status"] == "planned"
        assert collector.calls == []
        executed = request(f"/api/job-updates/{task['run_id']}/execute", {})
        assert collector.calls
        assert executed["status"] in {"completed", "partial", "paused", "cancelled", "failed"}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_collection_state_and_lease_are_released_when_thread_cannot_start(tmp_path):
    from jobagent.review_server import create_server
    from test_review_workbench import create_fixture_db

    path = tmp_path / "jobs.db"
    create_fixture_db(path)
    server = create_server(path, port=0)
    try:
        with patch("jobagent.review_server.threading.Thread", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                server.start_resume_collection({
                    "resume_text": "x", "filename": "r.md",
                    "candidate_id": "c", "analysis_version": "v1",
                })
        assert server.collection_snapshot()["status"] == "idle"
        assert server.browser_lease.owner is None
        assert server.calibration_operation_lock.acquire(blocking=False)
        server.calibration_operation_lock.release()
    finally:
        server.server_close()


def test_execute_endpoint_returns_conflict_while_another_owner_holds_browser(tmp_path):
    from jobagent.review_server import create_server
    from test_review_workbench import create_fixture_db

    path = tmp_path / "jobs.db"
    create_fixture_db(path)
    lease = BrowserLease()
    server = create_server(path, port=0, job_update_executor=JobUpdateExecutor(lease=lease))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        task = http_json(server.server_port, "/api/job-updates", {})
        with lease.acquire("resume-collect"):
            with pytest.raises(HTTPError) as raised:
                http_json(server.server_port, f"/api/job-updates/{task['run_id']}/execute", {})
            assert raised.value.code == 409
        assert http_json(server.server_port, f"/api/job-updates/{task['run_id']}")["status"] == "planned"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_stop_inside_stage_cancels_run_without_new_stages():
    with tempfile.TemporaryDirectory() as directory:
        store = JobUpdateStore(Path(directory) / "jobs.db", CONFIG)
        try:
            task = store.start({"favorites_budget": 2, "search_budget": 3, "refresh_budget": 2})
            task = plan_with_budgets(store, task["run_id"], favorites=2, search=3,
                                     refresh_ids=("j1", "j2"))
            collector = FakeCollector({"incremental": {"collected": 1}})
            original = collector.run_incremental

            # 模拟：收藏阶段进行到一半时用户点停止（should_stop 变为真）
            def incremental_with_mid_stage_stop(*args, **kwargs):
                if len(collector.calls) >= 1:
                    kwargs["should_stop"] = lambda: True
                return original(*args, **kwargs)

            collector.run_incremental = incremental_with_mid_stage_stop
            result = JobUpdateExecutor().run(task, store, collector=collector)
            assert result["status"] == "cancelled"
            assert result["stages"][0]["status"] == "completed"  # 停止前已采的部分保留
            assert result["stages"][1]["status"] == "cancelled"
            assert result["stages"][2]["status"] == "cancelled"
            assert "停止" in (result["error"] or "")
        finally:
            store.close()


def test_stage_progress_is_persisted_for_the_panel():
    with tempfile.TemporaryDirectory() as directory:
        store = JobUpdateStore(Path(directory) / "jobs.db", CONFIG)
        try:
            task = store.start({"favorites_budget": 2, "search_budget": 3, "refresh_budget": 2})
            task = plan_with_budgets(store, task["run_id"], favorites=2, search=3,
                                     refresh_ids=("j1", "j2"))
            collector = FakeCollector({"incremental": {"collected": 2},
                                       "refresh": {"collected": 2}})
            JobUpdateExecutor().run(task, store, collector=collector)
            plan = store.get(task["run_id"])["plan"]
            assert plan["stages"][0]["progress"] == {"collected": 2, "budget": 2}
            assert plan["stages"][2]["progress"] == {"collected": 2, "budget": 2}
        finally:
            store.close()


def test_collector_refresh_stops_between_items_without_cdp_side_effects():
    with tempfile.TemporaryDirectory() as directory:
        store = CollectionStore(Path(directory) / "jobs.db")
        collector = BossCollector({"boss": {}, "collection": {}}, store)
        store.save_detail({"platform": "boss", "job_id": "j1", "source_url": "",
                           "title": "t", "company": "c", "jd": "jd"}, "seed")
        page = Mock()
        manager = Mock()
        manager.__enter__ = Mock(return_value=Mock(chromium=Mock(
            connect_over_cdp=Mock(return_value=Mock(contexts=[Mock(pages=[], new_page=Mock(return_value=page))])))))
        manager.__exit__ = Mock(return_value=False)
        with patch.object(collector, "_navigate"), \
             patch.object(collector, "_require_login"), \
             patch.object(collector, "_load_detail_page", side_effect=AssertionError("must not load")), \
             patch("jobagent.platforms.boss_collector.sync_playwright", return_value=manager):
            result = collector.refresh(["j1"], should_stop=lambda: True)
        assert result.status == "stopped_by_user"
        assert result.collected == 0
        store.close()


def test_detail_settle_is_adaptive_and_capped():
    with tempfile.TemporaryDirectory() as directory:
        store = CollectionStore(Path(directory) / "jobs.db")
        collector = BossCollector({"boss": {}, "collection": {
            "detail_settle_cap_seconds": 0.1, "adaptive_min_pause_seconds": [0.01, 0.01],
        }}, store)
        page = Mock()
        page.locator.return_value.count.return_value = 1
        page.locator.return_value.first.inner_text.return_value = "岗位描述"
        collector._settle_detail_page(page)
        waits = [call.args[0] for call in page.wait_for_timeout.call_args_list]
        assert len(waits) == 1  # 只有人性化小停顿，没有固定沉睡
        assert waits[0] <= 100
        store.close()


def test_detail_settle_falls_back_to_fixed_wait_when_adaptive_disabled():
    with tempfile.TemporaryDirectory() as directory:
        store = CollectionStore(Path(directory) / "jobs.db")
        collector = BossCollector({"boss": {}, "collection": {"adaptive_wait": False}}, store)
        page = Mock()
        with patch.object(collector, "_wait") as wait:
            collector._settle_detail_page(page)
        wait.assert_called_once_with(page, collector._detail_settle_delay())
        store.close()

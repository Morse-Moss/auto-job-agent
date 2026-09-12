"""Execution guards for the JD update pipeline.

The lease is deliberately independent from the HTTP server so future Boss,
resume-collection, and outreach executors share the same process boundary.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

STAGE_STATUS = {
    "completed": "completed",
    "sample_completed": "completed",
    "exhausted": "completed",
    "security_check": "paused",
    "stopped": "failed",
}

HALT_KIND = {"security_check": "security", "stopped": "error"}

FINAL_STATUS = {"user": "cancelled", "security": "paused", "error": "failed"}


class BrowserLeaseBusy(RuntimeError):
    """The browser is already owned by a different executor."""


class BrowserLease:
    def __init__(self):
        self._condition = threading.Condition()
        self._owner = None
        self._depth = 0

    @property
    def owner(self):
        with self._condition:
            return self._owner

    def try_acquire(self, owner: str) -> bool:
        with self._condition:
            if self._owner not in (None, owner):
                return False
            self._owner = owner
            self._depth += 1
            return True

    def release(self, owner: str) -> None:
        """Release one acquisition. Callable from worker threads, unlike acquire()."""
        with self._condition:
            if self._owner != owner:
                return
            self._depth -= 1
            if self._depth <= 0:
                self._depth = 0
                self._owner = None
                self._condition.notify_all()

    @contextmanager
    def acquire(self, owner: str):
        if not self.try_acquire(owner):
            raise BrowserLeaseBusy(f"浏览器正在被 {self.owner} 占用")
        try:
            yield
        finally:
            self.release(owner)


class JobUpdateExecutor:
    def __init__(self, lease: BrowserLease | None = None, config: dict | None = None,
                 collector=None):
        self.lease = lease or BrowserLease()
        self.config = config or {}
        self.collector = collector

    def prepare_only(self, task: dict) -> dict:
        """Return a task unchanged; preparation never implies external execution."""
        if task.get("status") not in {"planned", "interrupted"}:
            raise ValueError("只有 planned 或 interrupted 任务可以准备执行")
        return task

    def run(self, task: dict, store, collector=None, *, owner: str = "boss-update") -> dict:
        """Execute the stored plan stage by stage and write real counts back.

        Requires an explicit caller: nothing here is reachable from a page load.
        A stop request or a security pause prevents every later stage from
        starting, and each stage records what it actually collected.
        """
        run_id = task["run_id"]
        if task.get("status") not in {"planned", "interrupted"}:
            raise ValueError("只有 planned 或 interrupted 任务可以执行")
        if not self.lease.try_acquire(owner):
            raise BrowserLeaseBusy(f"浏览器正在被 {self.lease.owner} 占用")

        collector = collector or self.collector or self._build_collector(store)
        halt_kind = ""
        halt_reason = ""
        try:
            current = store.mark_running(run_id)
            plan = current["plan"]
            stages = list(plan.get("stages") or [])
            for stage in stages:
                if store.get(run_id).get("stop_requested"):
                    halt_kind = halt_kind or "user"
                    halt_reason = halt_reason or "用户请求停止，未开始新来源"
                    stage["status"] = "cancelled"
                    continue
                if halt_kind:
                    stage["status"] = "cancelled"
                    continue
                if not self._stage_enabled(stage):
                    stage.setdefault("status", "skipped")
                    if stage["status"] not in {"skipped", "blocked"}:
                        stage["status"] = "skipped"
                    stage.setdefault("collected", 0)
                    continue

                result = self._run_stage(stage, store, collector)
                external = str(getattr(result, "status", "") or "")
                error = str(getattr(result, "error", "") or "")
                stage["external_status"] = external
                stage["collected"] = max(0, int(getattr(result, "collected", 0) or 0))
                stage["attempted"] = self._stage_attempted(stage)
                stage["metrics"] = self._stage_metrics(result, store)
                if error:
                    stage["error"] = error
                stage["status"] = STAGE_STATUS.get(external, "failed")
                kind = HALT_KIND.get(external, "error" if stage["status"] == "failed" else "")
                if kind:
                    halt_kind = halt_kind or kind
                    halt_reason = halt_reason or f"{stage.get('source')} 阶段中断：{error or external}"
                store.write_plan(run_id, plan)

            if store.get(run_id).get("stop_requested") and not halt_kind:
                halt_kind, halt_reason = "user", "用户请求停止，未开始新来源"
            plan["external_status"] = "run" if not halt_kind else halt_kind
            plan["coverage"]["external_coverage"] = "checked" if not halt_kind else "partial"
            store.write_plan(run_id, plan)
            return store.finish(run_id, FINAL_STATUS.get(halt_kind, "completed"),
                                halt_reason or None)
        finally:
            self.lease.release(owner)

    def _build_collector(self, store):
        from jobagent.collection import CollectionStore
        from jobagent.platforms.boss_collector import BossCollector

        return BossCollector(self.config, CollectionStore(store.db_path))

    @staticmethod
    def _stage_enabled(stage: dict) -> bool:
        source = stage.get("source")
        if stage.get("status") in {"skipped", "blocked"}:
            return False
        if source == "detail_refresh":
            return bool(stage.get("job_ids"))
        return int(stage.get("budget") or 0) > 0

    @staticmethod
    def _stage_attempted(stage: dict) -> int:
        if stage.get("source") == "detail_refresh":
            return len(stage.get("job_ids") or [])
        return int(stage.get("budget") or 0)

    @staticmethod
    def _stage_metrics(result, store) -> dict:
        run_id = str(getattr(result, "run_id", "") or "")
        if run_id and hasattr(store, "collection_metrics"):
            return store.collection_metrics(run_id)
        return {
            "attempted": int(getattr(result, "collected", 0) or 0),
            "detail_success": int(getattr(result, "collected", 0) or 0),
            "detail_failed": 0,
            "detail_closed": 0,
            "changed": 0,
            "unchanged": 0,
            "refresh_result_unknown": 0,
        }

    def _run_stage(self, stage, store, collector):
        source = stage.get("source")
        if source == "favorites":
            return collector.run_incremental(
                favorites_budget=int(stage.get("budget") or 0), search_budget=0
            )
        if source == "search":
            return collector.run_incremental(
                favorites_budget=0, search_budget=int(stage.get("budget") or 0)
            )
        if source == "detail_refresh":
            job_ids = list(stage.get("job_ids") or [])
            result = collector.refresh(job_ids, max_jobs=len(job_ids))
            # 岗位关闭不会重新上架：用候补顶上被死岗消耗的复核名额，
            # 保证每轮净产出。候补数量以实测关闭数为上限。
            closed = self._closed_count(result, store)
            backups = list(stage.get("backup_job_ids") or [])
            if closed > 0 and backups:
                extra = backups[: int(closed)]
                second = collector.refresh(extra, max_jobs=len(extra))
                result = self._merge_results(result, second)
            return result
        raise ValueError(f"未知阶段：{source}")

    @staticmethod
    def _closed_count(result, store) -> int:
        run_id = str(getattr(result, "run_id", "") or "")
        if run_id and hasattr(store, "collection_metrics"):
            return int(store.collection_metrics(run_id).get("detail_closed") or 0)
        return 0

    @staticmethod
    def _merge_results(first, second):
        import types

        first_error = str(getattr(first, "error", "") or "")
        return types.SimpleNamespace(
            run_id=getattr(second, "run_id", "") or getattr(first, "run_id", ""),
            collected=int(getattr(first, "collected", 0) or 0)
            + int(getattr(second, "collected", 0) or 0),
            total=int(getattr(second, "total", 0) or 0),
            status=getattr(second, "status", "") or getattr(first, "status", ""),
            error=getattr(second, "error", "") or first_error,
        )

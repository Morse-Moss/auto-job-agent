"""Durable local lifecycle for user-triggered JD update plans."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from jobagent.collection import CollectionStore
from jobagent.jd_update import build_update_plan


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class JobUpdateStore:
    def __init__(self, db_path: str | Path, config: dict | None = None):
        self.db_path = Path(db_path).resolve()
        self.config = config or {}
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE IF NOT EXISTS job_update_runs (
          run_id TEXT PRIMARY KEY, status TEXT NOT NULL, external_status TEXT NOT NULL,
          created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, stop_requested INTEGER NOT NULL DEFAULT 0,
          plan_json TEXT NOT NULL, error TEXT)""")
        self.conn.commit()

    def recover_interrupted(self) -> None:
        """Startup recovery only, after the executor's exclusive ownership is established."""
        self.conn.execute("UPDATE job_update_runs SET status='interrupted', finished_at=? WHERE status='running'", (_now(),))
        self.conn.commit()

    def _record(self, row):
        if row is None:
            return None
        data = dict(row)
        data["plan"] = json.loads(data["plan_json"])
        data["stages"] = data["plan"].get("stages", [])
        data["coverage"] = data["plan"].get("coverage", {})
        return data

    def start(self, budgets: dict | None = None) -> dict:
        existing = self.conn.execute(
            "SELECT * FROM job_update_runs WHERE status IN ('planned','running') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if existing:
            return self._record(existing)
        budgets = budgets or {}
        collection = CollectionStore(self.db_path)
        try:
            from jobagent.freshness import build_freshness_dashboard
            plan = build_update_plan(build_freshness_dashboard(collection.conn), self.config, **{
                key: budgets[key] for key in ("favorites_budget", "search_budget", "refresh_budget") if key in budgets
            })
        finally:
            collection.close()
        run_id = f"jd-update-{uuid.uuid4().hex}"
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            existing = self.conn.execute(
                "SELECT * FROM job_update_runs WHERE status IN ('planned','running') ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if existing:
                return self._record(existing)
            self.conn.execute(
                "INSERT INTO job_update_runs(run_id,status,external_status,created_at,plan_json) VALUES(?,?,?,?,?)",
                (run_id, "planned", "not_run", _now(), json.dumps(plan, ensure_ascii=False)),
            )
        return self._record(self.conn.execute("SELECT * FROM job_update_runs WHERE run_id=?", (run_id,)).fetchone())

    def get(self, run_id: str) -> dict | None:
        return self._record(self.conn.execute("SELECT * FROM job_update_runs WHERE run_id=?", (run_id,)).fetchone())

    def latest(self) -> dict | None:
        return self._record(self.conn.execute(
            "SELECT * FROM job_update_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone())

    def mark_running(self, run_id: str) -> dict:
        self.conn.execute("UPDATE job_update_runs SET status='running', started_at=? WHERE run_id=? AND status='planned'", (_now(), run_id))
        self.conn.commit()
        return self.get(run_id)

    def stop(self, run_id: str) -> dict:
        self.conn.execute("UPDATE job_update_runs SET stop_requested=1, status='cancelled', finished_at=? WHERE run_id=? AND status IN ('planned','running','interrupted')", (_now(), run_id))
        self.conn.commit()
        return self.get(run_id)

    def close(self):
        self.conn.close()

"""SQLite 本地记录：已分析/已打招呼的岗位，避免重复投递、节省 API 调用。"""

import sqlite3
from datetime import datetime
from pathlib import Path


class Store:
    def __init__(self, db_path: str = "output/jobagent.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id     TEXT PRIMARY KEY,
                platform   TEXT NOT NULL,
                title      TEXT,
                company    TEXT,
                salary     TEXT,
                score      INTEGER,
                reason     TEXT,
                greeted    INTEGER DEFAULT 0,
                created_at TEXT
            )
            """
        )
        self.conn.commit()

    def seen(self, job_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return row is not None

    def record(self, job: dict, score: int, reason: str, greeted: bool) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                job["job_id"],
                job.get("platform", "boss"),
                job.get("title", ""),
                job.get("company", ""),
                job.get("salary", ""),
                score,
                reason,
                1 if greeted else 0,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def greeted_count_today(self) -> int:
        today = datetime.now().date().isoformat()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE greeted = 1 AND created_at >= ?", (today,)
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self.conn.close()

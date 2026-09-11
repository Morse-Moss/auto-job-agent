"""S6 沟通进展同步：时间线事件 + 独立招聘结果，人工修正不被 AI 推断覆盖。

设计要点（对照 docs/specs/v1-controlled-outreach.md S6）：
- 事件只追加，不物理覆盖，保留时间与证据。
- 事件类型决定维度：progress（沟通进展）/ outcome（招聘结果），两者独立。
- source=manual 的人工登记永远有效；source=inferred 的 AI 推断标记
  pending=1 待人工确认，且在同一岗位上同类型事件已有人工登记时拒绝写入，
  保证"人工修正不被下次推断直接覆盖"。
- 本模块只做记录与展示，不发送任何消息（接管提示由前端呈现）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 事件类型 -> 所属维度。沟通进展与招聘结果独立维护。
EVENT_CATALOG: dict[str, str] = {
    "greeting_sent": "progress",
    "boss_viewed": "progress",
    "replied": "progress",
    "note": "progress",
    "interview": "outcome",
    "offer": "outcome",
    "rejected": "outcome",
}

EVENT_LABELS: dict[str, str] = {
    "greeting_sent": "已发送招呼语",
    "boss_viewed": "Boss 已查看",
    "replied": "对方已回复",
    "note": "备注",
    "interview": "约面试",
    "offer": "Offer",
    "rejected": "不合适/被拒",
}

OUTCOME_EVENT_TYPES = frozenset(event for event, kind in EVENT_CATALOG.items() if kind == "outcome")


class ProgressValidationError(ValueError):
    """进展事件写入或确认时的参数/规则错误。"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ProgressStore:
    """沟通进展时间线存储。幂等迁移，可按需每请求创建。"""

    def __init__(self, db_path: str | Path = "output/jobagent.db"):
        path = Path(db_path)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outreach_progress (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              platform TEXT NOT NULL,
              job_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              kind TEXT NOT NULL,
              source TEXT NOT NULL,
              pending INTEGER NOT NULL DEFAULT 0,
              note TEXT NOT NULL DEFAULT '',
              evidence TEXT NOT NULL DEFAULT '',
              occurred_at TEXT NOT NULL,
              confirmed_at TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_progress_job "
            "ON outreach_progress(platform, job_id, occurred_at)"
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["pending"] = bool(result["pending"])
        result["kind_label"] = "招聘结果" if result["kind"] == "outcome" else "沟通进展"
        result["event_label"] = EVENT_LABELS.get(result["event_type"], result["event_type"])
        return result

    def _validate(
        self,
        platform: str,
        job_id: str,
        event_type: str,
        *,
        note: str,
        evidence: str,
    ) -> str:
        platform = str(platform or "").strip()
        job_id = str(job_id or "").strip()
        if not platform or not job_id:
            raise ProgressValidationError("缺少岗位标识，无法登记进展")
        event_type = str(event_type or "").strip()
        if event_type not in EVENT_CATALOG:
            raise ProgressValidationError(
                "未知的事件类型，可选：" + "、".join(sorted(EVENT_CATALOG))
            )
        if event_type == "note" and not str(note or "").strip():
            raise ProgressValidationError("备注事件必须填写内容")
        return event_type

    def add(
        self,
        platform: str,
        job_id: str,
        event_type: str,
        *,
        note: str = "",
        evidence: str = "",
        source: str = "manual",
        occurred_at: str | None = None,
    ) -> dict:
        if source not in {"manual", "inferred"}:
            raise ProgressValidationError("事件来源只能是 manual 或 inferred")
        event_type = self._validate(
            platform, job_id, event_type, note=note, evidence=evidence
        )
        if source == "inferred":
            latest = self.conn.execute(
                """
                SELECT source FROM outreach_progress
                WHERE platform = ? AND job_id = ? AND event_type = ?
                ORDER BY occurred_at DESC, id DESC LIMIT 1
                """,
                (platform, job_id, event_type),
            ).fetchone()
            if latest is not None and latest["source"] == "manual":
                raise ProgressValidationError(
                    "该事件已有人工登记，AI 推断不覆盖人工修正；如需更新请人工登记"
                )
        row = self.conn.execute(
            """
            INSERT INTO outreach_progress
              (platform, job_id, event_type, kind, source, pending,
               note, evidence, occurred_at, confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                platform,
                job_id,
                event_type,
                EVENT_CATALOG[event_type],
                source,
                1 if source == "inferred" else 0,
                str(note or "").strip(),
                str(evidence or "").strip(),
                str(occurred_at or _now()),
                None,
            ),
        )
        self.conn.commit()
        return self.get_event(row.lastrowid)

    def get_event(self, event_id: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM outreach_progress WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise ProgressValidationError("进展事件不存在")
        return self._serialize(row)

    def list_events(self, platform: str, job_id: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM outreach_progress
            WHERE platform = ? AND job_id = ?
            ORDER BY occurred_at DESC, id DESC
            """,
            (platform, job_id),
        ).fetchall()
        return [self._serialize(row) for row in rows]

    def confirm(self, event_id: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM outreach_progress WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise ProgressValidationError("进展事件不存在")
        if row["source"] != "inferred":
            raise ProgressValidationError("人工登记的记录无需确认")
        self.conn.execute(
            "UPDATE outreach_progress SET pending = 0, confirmed_at = ? WHERE id = ?",
            (_now(), event_id),
        )
        self.conn.commit()
        return self.get_event(event_id)

    def summary(self, platform: str, job_id: str) -> dict:
        """当前状态：每个事件类型取"最新人工登记"优先，无人工则取最新推断。"""
        events = self.list_events(platform, job_id)
        by_type: dict[str, list[dict]] = {}
        for event in events:
            by_type.setdefault(event["event_type"], []).append(event)
        effective: list[dict] = []
        for event_type, group in by_type.items():
            manual = [event for event in group if event["source"] == "manual"]
            chosen = manual[0] if manual else group[0]
            effective.append(chosen)
        outcome = next(
            (event for event in effective if event["kind"] == "outcome"), None
        )
        pending_inferred = [event for event in events if event["pending"]]
        return {
            "events": events,
            "effective": effective,
            "outcome": outcome,
            "pending_count": len(pending_inferred),
            "total": len(events),
        }

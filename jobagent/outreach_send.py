"""Controlled first-contact sending (S5).

One task per job authorization. The greeting text and the resume image are
accounted for separately, and a definite failure can be retried at most once.
Nothing here runs from a page load: the caller must name a specific task.

The platform action itself is injected as a ``sender`` so tests never open a
CDP session, and so the real Boss adapter stays a thin, auditable layer.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from jobagent.job_update_executor import BrowserLease

TEXT_KIND = "greeting"
IMAGE_KIND = "image"
ACTION_KINDS = (TEXT_KIND, IMAGE_KIND)

ACTION_STATES = ("pending", "running", "confirmed", "failed", "unknown")
TASK_STATES = ("queued", "running", "partial", "confirmed", "failed", "paused", "unknown")

# One initial attempt plus at most one retry, and only for definite failures.
MAX_ATTEMPTS = 2


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


class SendValidationError(ValueError):
    """The task or its frozen snapshot does not permit sending."""


class SendFailed(RuntimeError):
    """A definite failure. Eligible for at most one retry."""


class SendPaused(RuntimeError):
    """Security or identity anomaly. Stops the whole task and is never retried."""


class JobClosed(SendFailed):
    """The posting is closed/offline on Boss. Definite: nothing was sent."""


class SendBusy(RuntimeError):
    """The browser is currently held by another executor."""


class OutreachSendStore:
    """Persistence for send tasks and the per-action ledger."""

    def __init__(self, db_path: str | Path):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS outreach_send_tasks (
              task_id TEXT PRIMARY KEY,
              platform TEXT NOT NULL,
              job_id TEXT NOT NULL,
              content_version INTEGER NOT NULL,
              greeting_hash TEXT NOT NULL,
              image_hash TEXT NOT NULL,
              jd_hash TEXT NOT NULL,
              resume_hash TEXT NOT NULL,
              status TEXT NOT NULL,
              stop_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              finished_at TEXT,
              UNIQUE(platform, job_id, content_version)
            );
            CREATE TABLE IF NOT EXISTS outreach_send_actions (
              task_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              evidence TEXT NOT NULL DEFAULT '',
              error TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              PRIMARY KEY(task_id, kind)
            );
            """
        )
        self.conn.commit()

    def _record(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        data = dict(row)
        data["actions"] = self.list_actions(data["task_id"])
        return data

    def get(self, task_id: str) -> dict | None:
        return self._record(
            self.conn.execute(
                "SELECT * FROM outreach_send_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        )

    def list_actions(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM outreach_send_actions WHERE task_id=? ORDER BY kind",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_for(self, platform: str, job_id: str) -> dict | None:
        """Most recent send task for a job, regardless of content version."""
        row = self.conn.execute(
            "SELECT * FROM outreach_send_tasks WHERE platform=? AND job_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (platform, job_id),
        ).fetchone()
        return self._record(row)

    def authorize(
        self,
        platform: str,
        job_id: str,
        *,
        content_version: int,
        greeting_hash: str,
        image_hash: str,
        jd_hash: str,
        resume_hash: str,
    ) -> dict:
        """Return the one task for this job at this content version."""
        version = int(content_version)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            existing = self.conn.execute(
                "SELECT task_id FROM outreach_send_tasks "
                "WHERE platform=? AND job_id=? AND content_version=?",
                (platform, job_id, version),
            ).fetchone()
            if existing:
                return self.get(existing["task_id"])
            task_id = f"send-{uuid.uuid4().hex}"
            now = _now()
            self.conn.execute(
                "INSERT INTO outreach_send_tasks("
                "task_id, platform, job_id, content_version, greeting_hash,"
                "image_hash, jd_hash, resume_hash, status, created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id, platform, job_id, version, greeting_hash,
                    image_hash, jd_hash, resume_hash, "queued", now, now,
                ),
            )
            for kind in ACTION_KINDS:
                self.conn.execute(
                    "INSERT INTO outreach_send_actions("
                    "task_id, kind, status, attempts, evidence, error, updated_at"
                    ") VALUES(?,?,?,?,?,?,?)",
                    (task_id, kind, "pending", 0, "", "", now),
                )
        return self.get(task_id)

    def mark_running(self, task_id: str) -> dict | None:
        self.conn.execute(
            "UPDATE outreach_send_tasks SET status='running', updated_at=? "
            "WHERE task_id=? AND status='queued'",
            (_now(), task_id),
        )
        self.conn.commit()
        return self.get(task_id)

    def claim_action(self, task_id: str, kind: str) -> bool:
        """Atomically move an action to running.

        Returns False when the action is already confirmed, its result is
        unknown, another runner already holds it, or the retry budget is spent.
        The single UPDATE is what stops two runners from sending twice.
        """
        cursor = self.conn.execute(
            "UPDATE outreach_send_actions SET status='running', attempts=attempts+1, "
            "error='', updated_at=? "
            "WHERE task_id=? AND kind=? AND ("
            "  status='pending' OR (status='failed' AND attempts < ?)"
            ")",
            (_now(), task_id, kind, MAX_ATTEMPTS),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def finish_action(
        self, task_id: str, kind: str, status: str, *, evidence: str = "", error: str = ""
    ) -> None:
        self.conn.execute(
            "UPDATE outreach_send_actions SET status=?, evidence=?, error=?, updated_at=? "
            "WHERE task_id=? AND kind=?",
            (status, evidence, error, _now(), task_id, kind),
        )
        self.conn.commit()

    def finish(self, task_id: str, status: str, error: str | None = None) -> dict | None:
        now = _now()
        self.conn.execute(
            "UPDATE outreach_send_tasks SET status=?, stop_reason=?, finished_at=?, "
            "updated_at=? WHERE task_id=?",
            (status, error or "", now, now, task_id),
        )
        self.conn.commit()
        return self.get(task_id)

    def resolve_paused_action(
        self, task_id: str, kind: str, *, sent: bool, note: str = ""
    ) -> dict | None:
        """Human verdict on a paused/unknown action, per manual chat inspection.

        ``sent=True``  -> the message really went out: mark confirmed, never resend.
        ``sent=False`` -> nothing was delivered: back to pending so the normal
        execution path can send it exactly once.
        Only a paused task with an unknown (or exhausted) action can be resolved;
        anything else must go through authorize/execute.
        """
        task = self.get(task_id)
        if task is None:
            raise SendValidationError("发送任务不存在")
        if task["status"] != "paused":
            raise SendValidationError("只有已暂停的发送任务才需要人工核对")
        now = _now()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            action = self.conn.execute(
                "SELECT status FROM outreach_send_actions WHERE task_id=? AND kind=?",
                (task_id, kind),
            ).fetchone()
            if action is None:
                raise SendValidationError(f"发送任务缺少 {kind} 动作")
            if action["status"] == "confirmed":
                return self.get(task_id)
            if action["status"] not in {"unknown", "failed"}:
                raise SendValidationError(
                    f"{kind} 动作当前状态为 {action['status']}，无法人工核对"
                )
            if sent:
                self.conn.execute(
                    "UPDATE outreach_send_actions SET status='confirmed', "
                    "evidence=?, error='', updated_at=? WHERE task_id=? AND kind=?",
                    (f"人工核对：已发出。{note}".strip(), now, task_id, kind),
                )
            else:
                self.conn.execute(
                    "UPDATE outreach_send_actions SET status='pending', "
                    "error=?, updated_at=? WHERE task_id=? AND kind=?",
                    (f"人工核对：未发出，待重发。{note}".strip(), now, task_id, kind),
                )
            self.conn.execute(
                "UPDATE outreach_send_tasks SET status='queued', stop_reason='', "
                "finished_at=NULL, updated_at=? WHERE task_id=?",
                (now, task_id),
            )
        return self.get(task_id)

    def close(self) -> None:
        self.conn.close()


class OutreachSendService:
    """Authorize and execute one job's controlled first contact."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        package_provider,
        sender,
        lease: BrowserLease | None = None,
        owner: str = "boss-send",
    ):
        self.store = OutreachSendStore(db_path)
        self.package_provider = package_provider
        self.sender = sender
        self.lease = lease or BrowserLease()
        self.owner = owner

    # -- authorization -------------------------------------------------
    def authorize(self, platform: str, job_id: str) -> dict:
        package = self._load_ready_package(platform, job_id)
        return self.store.authorize(
            str(platform),
            str(job_id),
            content_version=int(package.get("content_version") or 0),
            greeting_hash=_hash(package.get("greeting_text")),
            image_hash=str(package.get("image_hash") or ""),
            jd_hash=str(package.get("jd_hash") or ""),
            resume_hash=str(package.get("resume_hash") or ""),
        )

    def status(self, task_id: str) -> dict:
        task = self.store.get(task_id)
        if task is None:
            raise SendValidationError("发送任务不存在")
        return task

    def status_for(self, platform: str, job_id: str) -> dict | None:
        """Latest send task for a job, or None when never authorized."""
        return self.store.latest_for(str(platform), str(job_id))

    def resolve_and_continue(
        self, platform: str, job_id: str, *, greeting_sent: bool, note: str = ""
    ) -> dict:
        """Apply the human verdict on a paused task, then run what is left.

        This is the ONLY path that may resume a paused task, and it requires
        the user to have manually checked the Boss chat window first: a wrong
        verdict here means a duplicated greeting in a real chat.
        """
        task = self.status_for(platform, job_id)
        if task is None:
            raise SendValidationError("该岗位没有发送任务")
        if task["status"] != "paused":
            raise SendValidationError("该岗位的发送任务不在暂停状态，无需人工核对")
        self.store.resolve_paused_action(
            task["task_id"], TEXT_KIND, sent=bool(greeting_sent), note=str(note or "")
        )
        return self.execute(task["task_id"])

    def _load_ready_package(self, platform: str, job_id: str) -> dict:
        package = self.package_provider(platform, job_id)
        if not package:
            raise SendValidationError("岗位不存在或不在当前匹配结果中")
        if package.get("stale"):
            raise SendValidationError("投递包已失效：JD 或简历基准已变化，请重新生成")
        if not package.get("ready"):
            raise SendValidationError(
                "投递包尚未就绪：招呼语与简历长图必须分别确认，且内容不能失效"
            )
        return package

    def _assert_snapshot(self, task: dict, package: dict) -> None:
        if _hash(package.get("greeting_text")) != task["greeting_hash"]:
            raise SendValidationError("招呼语已变化，旧授权失效，请重新确认后授权")
        if str(package.get("image_hash") or "") != task["image_hash"]:
            raise SendValidationError("简历长图已变化，旧授权失效，请重新确认后授权")
        if str(package.get("jd_hash") or "") != task["jd_hash"]:
            raise SendValidationError("JD 已变化，旧授权失效，请重新生成投递包")
        if str(package.get("resume_hash") or "") != task["resume_hash"]:
            raise SendValidationError("简历基准已变化，旧授权失效，请重新生成投递包")

    # -- execution -----------------------------------------------------
    def execute(self, task_id: str) -> dict:
        """Run the unfinished actions of one authorized task. Idempotent."""
        task = self.store.get(task_id)
        if task is None:
            raise SendValidationError("发送任务不存在")
        if task["status"] == "confirmed":
            return task  # already fully sent: never send again
        if task["status"] == "paused":
            return task  # security or identity anomaly: wait for a human, never auto-resume
        if not self.lease.try_acquire(self.owner):
            raise SendBusy(f"浏览器正在被 {self.lease.owner} 占用")
        try:
            package = self._load_ready_package(task["platform"], task["job_id"])
            self._assert_snapshot(task, package)
            self.store.mark_running(task_id)
            halted = ""
            for kind, perform in (
                (TEXT_KIND, lambda: self.sender.send_text(task, package)),
                (IMAGE_KIND, lambda: self.sender.send_image(task, package)),
            ):
                if halted:
                    continue
                try:
                    closed = self._run_action(task_id, kind, perform)
                    if closed:
                        halted = "岗位已关闭，停止本任务剩余动作"
                except SendPaused as exc:
                    halted = str(exc)
            if halted:
                return self.store.finish(task_id, "failed" if halted.startswith("岗位已关闭") else "paused", halted)
            return self.store.finish(task_id, self._summarize(task_id))
        finally:
            self.lease.release(self.owner)

    def _action(self, task_id: str, kind: str) -> dict:
        for action in self.store.list_actions(task_id):
            if action["kind"] == kind:
                return action
        raise SendValidationError(f"发送任务缺少 {kind} 动作")

    def _run_action(self, task_id: str, kind: str, perform) -> bool:
        """Claim and run one action. Returns True when the job itself is closed."""
        # The atomic claim decides whether this runner may act at all. A False
        # result means: already confirmed, result unknown, taken by another
        # runner, or retry budget spent.
        if not self.store.claim_action(task_id, kind):
            return False
        task = self.store.get(task_id)
        try:
            evidence = perform()
        except JobClosed as exc:
            self.store.finish_action(task_id, kind, "failed", error=str(exc))
            self._mark_job_closed(task["platform"], task["job_id"], str(exc))
            return True
        except SendPaused:
            self.store.finish_action(task_id, kind, "unknown", error="已暂停，结果待人工核对")
            raise
        except SendFailed as exc:
            self.store.finish_action(task_id, kind, "failed", error=str(exc))
            return
        except Exception as exc:
            # An unclassified error means the delivery state is unknown, so it
            # must never be retried automatically.
            self.store.finish_action(
                task_id, kind, "unknown", error=f"{type(exc).__name__}: {exc}"
            )
            return
        if evidence:
            self.store.finish_action(task_id, kind, "confirmed", evidence=str(evidence))
        else:
            self.store.finish_action(task_id, kind, "unknown", error="未取得送达证据")

    def _mark_job_closed(self, platform: str, job_id: str, reason: str) -> None:
        """Write the closed posting back to freshness so the inventory heals.

        The send path is a live liveness probe: when Boss itself says the
        posting is closed, record it with the same convention the refresh
        flow uses. Best-effort — a write-back failure never breaks sending.
        """
        try:
            connection = sqlite3.connect(self.store.path)
            try:
                from jobagent.freshness import set_lifecycle_state

                set_lifecycle_state(
                    connection,
                    str(platform),
                    str(job_id),
                    "closed",
                    reason[:200],
                    run_id=self.owner,
                )
            finally:
                connection.close()
        except Exception:  # noqa: BLE001 — bookkeeping must not break sending
            pass

    def _summarize(self, task_id: str) -> str:
        states = {action["kind"]: action["status"] for action in self.store.list_actions(task_id)}
        values = set(states.values())
        if values == {"confirmed"}:
            return "confirmed"
        if "unknown" in values:
            return "unknown"
        if "confirmed" in values:
            return "partial"
        return "failed"

    def close(self) -> None:
        self.store.close()
        # 必须随服务一起释放 sender 的 Playwright 连接：否则每次执行泄漏一个
        # 持有浏览器 CDP 会话的 node 驱动进程，积累后卡死浏览器导航管线
        #（真实事故 2026-09-12：5 个泄漏驱动导致全部 page.goto 零响应超时）。
        sender_close = getattr(self.sender, "close", None)
        if callable(sender_close):
            try:
                sender_close()
            except Exception:  # noqa: BLE001 — 释放失败不掩盖主流程结果
                pass

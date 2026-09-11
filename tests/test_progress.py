"""S6 沟通进展测试：存储规则 + HTTP 端点往返（临时库，不发消息）。"""

import json
import sqlite3
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jobagent.progress import (
    EVENT_CATALOG,
    ProgressStore,
    ProgressValidationError,
)
from jobagent.review_server import ReviewRequestHandler


def make_store(tmp: TemporaryDirectory) -> ProgressStore:
    return ProgressStore(Path(tmp.name) / "jobs.db")


class ProgressStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = make_store(self.tmp)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_add_manual_event_and_summary(self):
        event = self.store.add("boss", "job-1", "greeting_sent", note="已发招呼语")
        self.assertEqual(event["kind"], "progress")
        self.assertFalse(event["pending"])
        data = self.store.summary("boss", "job-1")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["pending_count"], 0)
        self.assertIsNone(data["outcome"])

    def test_outcome_kind_is_derived_from_event_type(self):
        event = self.store.add("boss", "job-1", "offer", note="口头 offer")
        self.assertEqual(event["kind"], "outcome")
        self.assertIsNotNone(self.store.summary("boss", "job-1")["outcome"])

    def test_inferred_event_is_pending_and_confirmable(self):
        event = self.store.add(
            "boss", "job-1", "replied", note="对方回复了", source="inferred"
        )
        self.assertTrue(event["pending"])
        confirmed = self.store.confirm(event["id"])
        self.assertFalse(confirmed["pending"])
        self.assertIsNotNone(confirmed["confirmed_at"])

    def test_manual_record_survives_later_inference(self):
        """人工修正不被下次推断直接覆盖：同类型最新为人工时，推断写入被拒绝。"""
        self.store.add("boss", "job-1", "replied", note="人工：已回复")
        with self.assertRaises(ProgressValidationError):
            self.store.add(
                "boss", "job-1", "replied", note="推断", source="inferred"
            )

    def test_manual_event_can_update_inferred(self):
        self.store.add("boss", "job-1", "offer", note="推断", source="inferred")
        manual = self.store.add("boss", "job-1", "offer", note="人工核实")
        data = self.store.summary("boss", "job-1")
        self.assertEqual(data["outcome"]["note"], "人工核实")
        self.assertEqual(data["outcome"]["id"], manual["id"])

    def test_unknown_event_type_rejected(self):
        with self.assertRaises(ProgressValidationError):
            self.store.add("boss", "job-1", "coffee_chat")
        self.assertEqual(set(EVENT_CATALOG), {
            "greeting_sent", "boss_viewed", "replied", "note",
            "interview", "offer", "rejected",
        })

    def test_note_event_requires_content(self):
        with self.assertRaises(ProgressValidationError):
            self.store.add("boss", "job-1", "note", note="  ")

    def test_confirm_manual_or_missing_rejected(self):
        manual = self.store.add("boss", "job-1", "replied")
        with self.assertRaises(ProgressValidationError):
            self.store.confirm(manual["id"])
        with self.assertRaises(ProgressValidationError):
            self.store.confirm(9999)

    def test_migration_is_idempotent(self):
        store2 = ProgressStore(self.store.path)
        try:
            store2.add("boss", "job-2", "boss_viewed")
        finally:
            store2.close()


class ProgressEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        database = Path(self.tmp.name) / "jobs.db"
        sqlite3.connect(database).close()  # 复用迁移过的库结构
        self.database = database
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), ReviewRequestHandler
        )
        # 与生产一致的按请求创建模式：ThreadingHTTPServer 的工作线程
        # 不能复用主线程创建的 SQLite 连接。
        self.server.db_path = database
        self.server.config = {}
        self.server.outreach_service_factory = None
        self.server.send_service_factory = None

        def progress_store():
            return ProgressStore(self.database)

        self.server.progress_store = progress_store  # type: ignore[method-assign]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def seed(self, *args, **kwargs):
        store = ProgressStore(self.database)
        try:
            return store.add(*args, **kwargs)
        finally:
            store.close()

    def test_post_and_get_progress_round_trip(self):
        body = json.dumps(
            {"event_type": "greeting_sent", "note": "第一轮发送"}
        ).encode("utf-8")
        request = Request(
            f"{self.base}/api/outreach/job-1/progress?platform=boss",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            created = json.loads(response.read().decode("utf-8"))["progress_event"]
        self.assertEqual(created["event_type"], "greeting_sent")

        with urlopen(
            f"{self.base}/api/outreach/job-1/progress?platform=boss"
        ) as response:
            data = json.loads(response.read().decode("utf-8"))["progress"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["events"][0]["note"], "第一轮发送")

    def test_invalid_event_type_returns_400(self):
        body = json.dumps({"event_type": "coffee_chat"}).encode("utf-8")
        request = Request(
            f"{self.base}/api/outreach/job-1/progress?platform=boss",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request)
        self.assertEqual(raised.exception.code, HTTPStatus.BAD_REQUEST)

    def test_inferred_rejected_when_manual_exists_returns_400(self):
        self.seed("boss", "job-1", "replied", note="人工")
        body = json.dumps(
            {"event_type": "replied", "note": "推断", "source": "inferred"}
        ).encode("utf-8")
        request = Request(
            f"{self.base}/api/outreach/job-1/progress?platform=boss",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request)
        self.assertEqual(raised.exception.code, HTTPStatus.BAD_REQUEST)

    def test_confirm_endpoint(self):
        event = self.seed(
            "boss", "job-1", "interview", note="约周四", source="inferred"
        )
        body = json.dumps({"event_id": event["id"]}).encode("utf-8")
        request = Request(
            f"{self.base}/api/outreach/job-1/progress/confirm",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            confirmed = json.loads(response.read().decode("utf-8"))["progress_event"]
        self.assertFalse(confirmed["pending"])


if __name__ == "__main__":
    unittest.main()

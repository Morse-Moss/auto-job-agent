"""受控发送端点测试：create_server 注入假服务，不连 CDP、不发消息。"""

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jobagent.outreach_send import OutreachSendService
from jobagent.review_server import create_server


def ready_package():
    return {
        "platform": "boss",
        "job_id": "job-1",
        "content_version": 3,
        "ready": True,
        "stale": False,
        "greeting_text": "你好，我在做企业级 AI Agent。",
        "image_path": "",
        "image_hash": "img-hash",
        "jd_hash": "jd-hash",
        "resume_hash": "resume-hash",
    }


class FakePackages:
    def __init__(self, packages):
        self.packages = packages

    def __call__(self, platform, job_id):
        return self.packages.get((platform, job_id))


class FakeSender:
    def __init__(self):
        self.calls = []

    def send_text(self, task, pack):
        self.calls.append("text")
        return "[聊天中可见招呼语]"

    def send_image(self, task, pack):
        self.calls.append("image")
        return "[聊天中可见长图]"


def post(url, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    return urlopen(request)


class SendEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        database = Path(self.tmp.name) / "jobs.db"
        sqlite3.connect(database).close()  # migrate_review_database requires the file
        sender = FakeSender()
        self.sender = sender
        packages = FakePackages({("boss", "job-1"): ready_package()})

        def send_factory(lease):
            return OutreachSendService(
                database,
                package_provider=packages,
                sender=sender,
                lease=lease,
            )

        self.server = create_server(
            database,
            host="127.0.0.1",
            port=0,
            config={},
            outreach_service_factory=lambda: (_ for _ in ()).throw(AssertionError("不需要")),
            send_service_factory=send_factory,
        )
        self.thread = None
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_authorize_status_execute_round_trip(self):
        with post(f"{self.base}/api/outreach/job-1/authorize") as response:
            payload = json.loads(response.read().decode("utf-8"))
        task = payload["send"]
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["content_version"], 3)

        with urlopen(f"{self.base}/api/outreach/job-1/send") as response:
            status = json.loads(response.read().decode("utf-8"))["send"]
        self.assertEqual(status["task_id"], task["task_id"])

        with post(f"{self.base}/api/outreach/send/{task['task_id']}/execute") as response:
            done = json.loads(response.read().decode("utf-8"))["send"]
        self.assertEqual(done["status"], "confirmed")
        self.assertEqual(self.sender.calls, ["text", "image"])

    def test_status_is_null_before_authorize(self):
        with urlopen(f"{self.base}/api/outreach/job-none/send") as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIsNone(payload["send"])

    def test_execute_unknown_task_is_rejected(self):
        with self.assertRaises(HTTPError) as raised:
            post(f"{self.base}/api/outreach/send/send-missing/execute")
        self.assertEqual(raised.exception.code, 400)


if __name__ == "__main__":
    unittest.main()

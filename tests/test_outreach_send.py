"""S5 send-ledger tests. All platform action is faked; no CDP, no Boss."""

import tempfile
import threading
from pathlib import Path

import pytest

from jobagent.job_update_executor import BrowserLease
from jobagent.outreach_send import (
    SendFailed,
    SendPaused,
    SendValidationError,
    OutreachSendService,
    OutreachSendStore,
)


def package(**overrides):
    data = {
        "platform": "boss",
        "job_id": "job-1",
        "content_version": 1,
        "greeting_text": "你好，我做过企业级 AI Agent 落地。",
        "image_hash": "img-hash-1",
        "jd_hash": "jd-hash-1",
        "resume_hash": "resume-hash-1",
        "ready": True,
        "stale": False,
    }
    data.update(overrides)
    return data


class FakePackages:
    def __init__(self, packages):
        self.packages = packages

    def __call__(self, platform, job_id):
        return self.packages.get((platform, job_id))


class FakeSender:
    def __init__(self, text="[送达]", image="附件已上传", text_exc=None, image_exc=None):
        self.calls = []
        self.text = text
        self.image = image
        self.text_exc = text_exc
        self.image_exc = image_exc

    def send_text(self, task, pack):
        self.calls.append(("text", task["job_id"]))
        if self.text_exc is not None:
            raise self.text_exc
        return self.text

    def send_image(self, task, pack):
        self.calls.append(("image", task["job_id"]))
        if self.image_exc is not None:
            raise self.image_exc
        return self.image


def build(tmp_path, packages, sender):
    db = tmp_path / "jobs.db"
    return OutreachSendService(
        db, package_provider=FakePackages(packages), sender=sender, lease=BrowserLease()
    )


def actions_of(task):
    return {action["kind"]: action for action in task["actions"]}


def test_authorize_requires_a_ready_package(tmp_path):
    service = build(tmp_path, {("boss", "job-1"): package(ready=False)}, FakeSender())
    try:
        with pytest.raises(SendValidationError):
            service.authorize("boss", "job-1")
    finally:
        service.close()


def test_authorize_is_idempotent_for_same_content_version(tmp_path):
    service = build(tmp_path, {("boss", "job-1"): package()}, FakeSender())
    try:
        first = service.authorize("boss", "job-1")
        second = service.authorize("boss", "job-1")
        assert first["task_id"] == second["task_id"]
        assert second["status"] == "queued"
        assert set(actions_of(second)) == {"greeting", "image"}
        assert all(a["status"] == "pending" for a in actions_of(second).values())
    finally:
        service.close()


def test_execute_sends_text_and_image_then_confirms(tmp_path):
    sender = FakeSender()
    service = build(tmp_path, {("boss", "job-1"): package()}, sender)
    try:
        task = service.authorize("boss", "job-1")
        done = service.execute(task["task_id"])
        assert sender.calls == [("text", "job-1"), ("image", "job-1")]
        assert done["status"] == "confirmed"
        assert actions_of(done)["greeting"]["status"] == "confirmed"
        assert actions_of(done)["image"]["status"] == "confirmed"
    finally:
        service.close()


def test_second_execute_never_resends(tmp_path):
    sender = FakeSender()
    service = build(tmp_path, {("boss", "job-1"): package()}, sender)
    try:
        task = service.authorize("boss", "job-1")
        service.execute(task["task_id"])
        service.execute(task["task_id"])
        service.execute(task["task_id"])
        assert sender.calls == [("text", "job-1"), ("image", "job-1")]
    finally:
        service.close()


def test_text_confirmed_but_image_failed_is_partial(tmp_path):
    sender = FakeSender(image_exc=SendFailed("上传控件不存在"))
    service = build(tmp_path, {("boss", "job-1"): package()}, sender)
    try:
        task = service.authorize("boss", "job-1")
        done = service.execute(task["task_id"])
        assert done["status"] == "partial"
        assert actions_of(done)["greeting"]["status"] == "confirmed"
        assert actions_of(done)["image"]["status"] == "failed"
    finally:
        service.close()


def test_failed_action_retries_at_most_once(tmp_path):
    sender = FakeSender(image_exc=SendFailed("上传控件不存在"))
    service = build(tmp_path, {("boss", "job-1"): package()}, sender)
    try:
        task = service.authorize("boss", "job-1")
        service.execute(task["task_id"])
        service.execute(task["task_id"])
        service.execute(task["task_id"])
        image_calls = [c for c in sender.calls if c[0] == "image"]
        assert len(image_calls) == 2
    finally:
        service.close()


def test_unconfirmed_delivery_is_unknown_and_never_retried(tmp_path):
    sender = FakeSender(text="", image="")
    service = build(tmp_path, {("boss", "job-1"): package()}, sender)
    try:
        task = service.authorize("boss", "job-1")
        done = service.execute(task["task_id"])
        assert actions_of(done)["greeting"]["status"] == "unknown"
        assert done["status"] == "unknown"
        service.execute(task["task_id"])
        assert len([c for c in sender.calls if c[0] == "text"]) == 1
    finally:
        service.close()


def test_security_pause_stops_the_whole_task(tmp_path):
    sender = FakeSender(text_exc=SendPaused("检测到安全校验"))
    service = build(tmp_path, {("boss", "job-1"): package()}, sender)
    try:
        task = service.authorize("boss", "job-1")
        done = service.execute(task["task_id"])
        assert done["status"] == "paused"
        assert sender.calls == [("text", "job-1")]
        assert actions_of(done)["image"]["status"] == "pending"
    finally:
        service.close()


def test_changed_snapshot_blocks_execution(tmp_path):
    packages = {("boss", "job-1"): package()}
    service = build(tmp_path, packages, FakeSender())
    try:
        task = service.authorize("boss", "job-1")
        packages[("boss", "job-1")] = package(jd_hash="jd-hash-2")
        with pytest.raises(SendValidationError):
            service.execute(task["task_id"])
    finally:
        service.close()


def test_new_content_version_creates_a_new_task(tmp_path):
    packages = {("boss", "job-1"): package()}
    service = build(tmp_path, packages, FakeSender())
    try:
        first = service.authorize("boss", "job-1")
        packages[("boss", "job-1")] = package(content_version=2)
        second = service.authorize("boss", "job-1")
        assert first["task_id"] != second["task_id"]
        assert service.store.get(first["task_id"])["status"] == "queued"
    finally:
        service.close()


def test_paused_task_is_not_resumed_by_execute(tmp_path):
    sender = FakeSender(text_exc=SendPaused("检测到安全校验"))
    service = build(tmp_path, {("boss", "job-1"): package()}, sender)
    try:
        task = service.authorize("boss", "job-1")
        assert service.execute(task["task_id"])["status"] == "paused"
        calls_after_pause = list(sender.calls)
        again = service.execute(task["task_id"])
        assert again["status"] == "paused"
        assert sender.calls == calls_after_pause
    finally:
        service.close()


def test_uncategorized_error_is_unknown_and_never_retried(tmp_path):
    sender = FakeSender(text_exc=RuntimeError("页面状态不明"))
    service = build(tmp_path, {("boss", "job-1"): package()}, sender)
    try:
        task = service.authorize("boss", "job-1")
        done = service.execute(task["task_id"])
        assert actions_of(done)["greeting"]["status"] == "unknown"
        service.execute(task["task_id"])
        assert len([c for c in sender.calls if c[0] == "text"]) == 1
    finally:
        service.close()


def test_execute_holds_the_browser_lease_and_releases_it(tmp_path):
    sender = FakeSender()
    lease = BrowserLease()
    service = OutreachSendService(
        tmp_path / "jobs.db",
        package_provider=FakePackages({("boss", "job-1"): package()}),
        sender=sender,
        lease=lease,
    )
    try:
        task = service.authorize("boss", "job-1")
        with lease.acquire("resume-collect"):
            with pytest.raises(RuntimeError):
                service.execute(task["task_id"])
        assert sender.calls == []
        service.execute(task["task_id"])
        assert lease.owner is None
    finally:
        service.close()

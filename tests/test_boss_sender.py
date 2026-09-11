"""BossSender 单元测试：全部使用假页面，不连 CDP、不发消息。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import TimeoutError as PWTimeout

from jobagent.outreach_send import SendFailed, SendPaused
from jobagent.platforms.boss_sender import BossSender

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
DETAIL_URL = "https://www.zhipin.com/job_detail/job-1.html"
SECURITY_URL = "https://www.zhipin.com/web/user/safe/verify?_security_check=1"

GREETING = "你好，我做过企业级 AI Agent 落地，方便聊聊岗位的客户场景吗？"


class FakeElement:
    def __init__(self, text=""):
        self._text = text
        self.clicked = 0

    def click(self):
        self.clicked += 1

    def inner_text(self):
        return self._text


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def type(self, text, delay=0):
        self.page.typed.append(text)
        if self.page.echo_typing:
            self.page.body += text

    def press(self, key):
        self.page.pressed.append(key)


class FakePage:
    def __init__(
        self,
        *,
        url=CHAT_URL,
        body="",
        has_input=True,
        has_send=True,
        has_image_input=True,
        image_count=0,
        echo_typing=True,
        count_on_upload=True,
    ):
        self.url = url
        self.body = body
        self.has_input = has_input
        self.has_send = has_send
        self.has_image_input = has_image_input
        self.image_count = image_count
        self.echo_typing = echo_typing
        self.count_on_upload = count_on_upload
        self.typed = []
        self.pressed = []
        self.uploads = []
        self.goto_urls = []
        self._closed = False
        self._keyboard = FakeKeyboard(self)

    @property
    def keyboard(self):
        return self._keyboard

    def is_closed(self):
        return self._closed

    def goto(self, url, wait_until=None):
        self.goto_urls.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        return None

    def wait_for_selector(self, selector, timeout=None):
        if "chat-input" in selector:
            if not self.has_input:
                raise PWTimeout("no chat input")
            return FakeElement()
        if "sendimg" in selector:
            if not self.has_image_input:
                raise PWTimeout("no image input")
            return FakeElement()
        if "btn-startchat" in selector or "op-btn-chat" in selector:
            return FakeElement(text="立即沟通")
        return FakeElement()

    def query_selector(self, selector):
        if "send" in selector and "img" not in selector:
            return FakeElement() if self.has_send else None
        return None

    def inner_text(self, selector, timeout=None):
        return self.body

    def evaluate(self, script):
        return self.image_count

    def set_input_files(self, selector, path):
        self.uploads.append(path)
        if self.count_on_upload:
            self.image_count += 1


def make_package(image_path=""):
    return {
        "platform": "boss",
        "job_id": "job-1",
        "greeting_text": GREETING,
        "image_path": image_path,
    }


class BossSenderTests(unittest.TestCase):
    def test_send_text_reports_visible_greeting(self):
        page = FakePage()
        sender = BossSender({}, page=page)
        evidence = sender.send_text({"job_id": "job-1"}, make_package())
        self.assertIn("招呼语", evidence)
        self.assertEqual(page.typed, [GREETING])

    def test_send_text_without_input_is_definite_failure(self):
        page = FakePage(has_input=False)
        sender = BossSender({}, page=page)
        with self.assertRaises(SendFailed):
            sender.send_text({"job_id": "job-1"}, make_package())

    def test_send_text_without_evidence_is_unknown(self):
        page = FakePage(echo_typing=False)
        sender = BossSender({}, page=page)
        with self.assertRaises(RuntimeError) as raised:
            sender.send_text({"job_id": "job-1"}, make_package())
        self.assertNotIsInstance(raised.exception, SendFailed)

    def test_send_image_requires_existing_file(self):
        page = FakePage()
        sender = BossSender({}, page=page)
        with self.assertRaises(SendFailed):
            sender.send_image({"job_id": "job-1"}, make_package("/no/such/file.jpg"))

    def test_send_image_uploads_and_reports_visible(self):
        with TemporaryDirectory() as tmp:
            image = Path(tmp) / "resume.jpg"
            image.write_bytes(b"fake")
            page = FakePage(image_count=0)
            sender = BossSender({}, page=page)
            evidence = sender.send_image({"job_id": "job-1"}, make_package(str(image)))
            self.assertIn("长图", evidence)
            self.assertEqual(page.uploads, [str(image)])

    def test_send_image_without_control_is_definite_failure(self):
        with TemporaryDirectory() as tmp:
            image = Path(tmp) / "resume.jpg"
            image.write_bytes(b"fake")
            page = FakePage(has_image_input=False)
            sender = BossSender({}, page=page)
            with self.assertRaises(SendFailed):
                sender.send_image({"job_id": "job-1"}, make_package(str(image)))

    def test_send_image_without_evidence_is_unknown(self):
        with TemporaryDirectory() as tmp:
            image = Path(tmp) / "resume.jpg"
            image.write_bytes(b"fake")
            page = FakePage(count_on_upload=False)
            sender = BossSender({}, page=page)
            with self.assertRaises(RuntimeError) as raised:
                sender.send_image({"job_id": "job-1"}, make_package(str(image)))
            self.assertNotIsInstance(raised.exception, SendFailed)

    def test_security_url_pauses(self):
        page = FakePage(url=SECURITY_URL)
        sender = BossSender({}, page=page)
        with self.assertRaises(SendPaused):
            sender.send_text({"job_id": "job-1"}, make_package())

    def test_opens_chat_from_job_detail_when_not_on_chat(self):
        page = FakePage(url=DETAIL_URL)
        sender = BossSender({}, page=page)
        sender.send_text({"job_id": "job-1"}, make_package())
        self.assertTrue(page.goto_urls)


if __name__ == "__main__":
    unittest.main()

"""BossSender 单元测试：全部使用假页面，不连 CDP、不发消息。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from playwright.sync_api import TimeoutError as PWTimeout

from jobagent.outreach_send import SendFailed, SendPaused
from jobagent.platforms.boss_sender import BossSender

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
DETAIL_URL = "https://www.zhipin.com/job_detail/job-1.html"
SECURITY_URL = "https://www.zhipin.com/web/user/safe/verify?_security_check=1"

GREETING = "你好，我做过企业级 AI Agent 落地，方便聊聊岗位的客户场景吗？"
COMPANY = "深圳市轮动科技有..."
TITLE = "AI 交付专员（FDE）"


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
        has_chat_btn=True,
        image_count=0,
        echo_typing=True,
        count_on_upload=True,
    ):
        self.url = url
        self.body = body
        self.has_input = has_input
        self.has_send = has_send
        self.has_image_input = has_image_input
        self.has_chat_btn = has_chat_btn
        self.image_count = image_count
        self.echo_typing = echo_typing
        self.count_on_upload = count_on_upload
        self.typed = []
        self.pressed = []
        self.uploads = []
        self.goto_urls = []
        self.selector_calls = []
        self.on_click_chat_btn = None
        self._closed = False
        self._keyboard = FakeKeyboard(self)
        self.context = SimpleNamespace(pages=[self])
        if not self.body:
            self.body = f"{TITLE} {COMPANY}"  # 默认会话头部可被身份校验命中

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

    def wait_for_selector(self, selector, timeout=None, state=None):
        self.selector_calls.append((selector, state))
        if "chat-input" in selector:
            if not self.has_input:
                raise PWTimeout("no chat input")
            return FakeElement()
        if "sendimg" in selector:
            if not self.has_image_input:
                raise PWTimeout("no image input")
            return FakeElement()
        if "btn-startchat" in selector or "op-btn-chat" in selector:
            if not self.has_chat_btn:
                raise PWTimeout("no chat button")
            element = FakeElement(text="立即沟通")
            hook = self.on_click_chat_btn or (lambda: setattr(self, "url", CHAT_URL))

            def click_with_hook():
                hook()

            element.click = click_with_hook
            return element
        return FakeElement()

    def query_selector(self, selector):
        if "send" in selector and "img" not in selector:
            return FakeElement() if self.has_send else None
        return None

    def inner_text(self, selector, timeout=None):
        return self.body

    def evaluate(self, script):
        if "innerText" in script:
            return self.body
        return self.image_count

    def set_input_files(self, selector, path):
        self.uploads.append(path)
        if self.count_on_upload:
            self.image_count += 1


def make_package(image_path=""):
    return {
        "platform": "boss",
        "job_id": "job-1",
        "company": COMPANY,
        "title": TITLE,
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

    def test_security_check_query_param_is_not_a_security_page(self):
        """Boss 普通页面会带 _security_check= 防伪参数，不得误判为校验页。

        真实事故：搜索页 URL 带该参数导致发送瞬间误停，招呼语根本没发。
        """
        normal_url = "https://www.zhipin.com/web/geek/jobs?query=AI&_security_check=1_1788317101695"
        self.assertFalse(BossSender._is_security_url(normal_url))
        self.assertTrue(BossSender._is_security_url(SECURITY_URL))
        page = FakePage(url=normal_url)
        sender = BossSender({}, page=page)
        sender.send_text({"job_id": "job-1"}, make_package())

    def test_opens_chat_from_job_detail_when_not_on_chat(self):
        page = FakePage(url=DETAIL_URL)
        sender = BossSender({}, page=page)
        sender.send_text({"job_id": "job-1"}, make_package())

    def test_closed_job_reports_definite_failure(self):
        """宇宙树真实事故：岗位已关闭时没有沟通按钮，应明确报“岗位已关闭”。"""
        page = FakePage(url=DETAIL_URL, has_chat_btn=False, body="职位已关闭")
        sender = BossSender({}, page=page)
        with self.assertRaises(SendFailed) as raised:
            sender.send_text({"job_id": "job-1"}, make_package())
        self.assertIn("岗位已关闭", str(raised.exception))
        self.assertTrue(page.goto_urls)

    def test_follows_newly_opened_chat_tab(self):
        """深圳轮动真实事故：点「立即沟通」后 Boss 新开聊天标签页，
        输入框在新页里；sender 必须跟进新页，而不是留在详情页等待。"""
        detail = FakePage(url=DETAIL_URL)
        chat = FakePage(url=CHAT_URL)
        detail.on_click_chat_btn = lambda: detail.context.pages.append(chat)
        sender = BossSender({}, page=detail)
        evidence = sender.send_text({"job_id": "job-1"}, make_package())
        self.assertIn("招呼语", evidence)
        # 招呼语打进了新聊天页，而不是详情页
        self.assertEqual(chat.typed, [GREETING])
        self.assertEqual(detail.typed, [])

    def test_leftover_chat_tab_is_never_trusted_for_another_job(self):
        """轮动重复发送真实事故：残留的聊天标签页属于别家公司的会话时，
        绝不能直接复用——必须从详情页重新进入并校验会话对象。"""
        leftover = FakePage(url=CHAT_URL, body="五世科技 AI流程搭建专员")
        detail = FakePage(url=DETAIL_URL)
        shared_context = SimpleNamespace(pages=[leftover, detail])
        leftover.context = shared_context
        detail.context = shared_context
        target_chat = FakePage(url=CHAT_URL)
        # 工作页（残留聊天页）被导航到详情页后，点击沟通 → Boss 开出正确会话
        leftover.on_click_chat_btn = lambda: shared_context.pages.append(target_chat)
        sender = BossSender({}, page=leftover)
        sender.send_text({"job_id": "job-1"}, make_package())
        # 招呼语进了目标会话；残留会话一个字都没被输入
        self.assertEqual(target_chat.typed, [GREETING])
        self.assertEqual(leftover.typed, [])

    def test_unmatched_conversation_is_definite_failure_before_typing(self):
        """打开的会话与目标职位对不上时，必须在输入任何内容前明确失败。"""
        page = FakePage(url=DETAIL_URL, body="完全无关的其他公司会话内容")
        sender = BossSender({}, page=page)
        with self.assertRaises(SendFailed) as raised:
            sender.send_text({"job_id": "job-1"}, make_package())
        self.assertIn("不一致", str(raised.exception))
        self.assertEqual(page.typed, [])

    def test_image_input_waits_attached_not_visible(self):
        """深圳轮动真实事故：图片上传 file input 是隐藏控件，
        默认等 visible 会永远超时；必须以 attached 等待。"""
        with TemporaryDirectory() as tmp:
            image = Path(tmp) / "resume.jpg"
            image.write_bytes(b"fake")
            page = FakePage(url=CHAT_URL)
            sender = BossSender({}, page=page)
            sender.send_image({"job_id": "job-1"}, make_package(str(image)))
            image_calls = [
                call for call in page.selector_calls if "sendimg" in call[0]
            ]
            self.assertTrue(image_calls)
            self.assertEqual(image_calls[0][1], "attached")


if __name__ == "__main__":
    unittest.main()

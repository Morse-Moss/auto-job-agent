"""Boss 真实发送适配器：在聊天窗口发送招呼语与简历长图。

只做「执行动作」，不做授权判断：授权、快照校验、防重复发送由
``jobagent.outreach_send`` 负责，本模块只被该服务在已授权后调用。

安全约束（与采集器一致）：
- 通过 CDP 复用已有登录会话，不新建上下文。
- 每次交互前后检查 Boss 安全校验 URL，命中即抛 ``SendPaused``，不刷新、不重试。
- 找不到入口/控件时抛 ``SendFailed``（确定未发出）；已点击但拿不到送达证据时抛
  普通 ``RuntimeError``，让上层记为 unknown，从而永不自动重试。
"""

import random
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from ..outreach_send import JobClosed, SendFailed, SendPaused

BASE_URL = "https://www.zhipin.com"
SECURITY_URL_MARKERS = (
    "/web/passport/zp/verify",
    "/web/user/safe/verify",
    "/safe/verify",
)

CLOSED_JOB_MARKERS = ("职位已关闭", "已停止招聘", "该职位已下线")

SELECTORS = {
    "logged_in": "[ka='header-username'], .user-nav .nav-figure",
    "chat_btn": "a.btn-startchat, a.op-btn-chat",
    "chat_input": "#chat-input, div[contenteditable='true'].chat-input",
    "send_btn": "button.btn-send, .send-message, button.btn-sure-v2",
    "image_input": ".chat-controls .btn-sendimg input[type=file]",
    "message_item": ".message-item",
    "message_content": ".message-content",
}


class BossSender:
    """Playwright/CDP implementation of the sender contract.

    ``send_text`` / ``send_image`` return a short evidence string on success and
    raise on failure. A ``page`` may be injected for tests; production connects
    over CDP and never closes the shared browser.
    """

    def __init__(self, cfg: dict | None = None, *, page=None):
        self.cfg = dict(cfg or {})
        self._page = page
        self._external_page = page is not None
        self._playwright = None
        self._browser = None

    # -- connection ----------------------------------------------------
    @property
    def cdp_endpoint(self) -> str:
        return str(self.cfg.get("cdp_endpoint") or "http://127.0.0.1:17332")

    def precheck(self) -> dict:
        """发送前预检：CDP 可达 + Boss 登录态（wt2 cookie）。

        轻量探测，不导航、不点击、不动任何页面；始终返回结构化结果，
        失败信息放在 detail，供发送门 UI 在授权前提示。
        """
        result = {
            "ok": False,
            "cdp": False,
            "zhipin_page": False,
            "logged_in": False,
            "detail": "",
        }
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(
                self.cdp_endpoint
            )
        except Exception as exc:  # 预检绝不向上抛，浏览器掉线是正常业务态
            result["detail"] = (
                f"浏览器服务不可达（{exc.__class__.__name__}），"
                "请先启动 MorseTools general-web"
            )
            self.close()
            return result
        result["cdp"] = True
        try:
            contexts = self._browser.contexts if self._browser else []
            context = contexts[0] if contexts else None
            if context is None:
                result["detail"] = "浏览器服务在线，但没有可用的浏览器上下文"
                return result
            page = next(
                (
                    p
                    for p in context.pages
                    if "zhipin.com" in (p.url or "")
                    and not self._is_security_url(p.url)
                ),
                None,
            )
            result["zhipin_page"] = page is not None
            cookies = context.cookies("https://www.zhipin.com")
            result["logged_in"] = any(
                c.get("name") == "wt2" and c.get("value") for c in cookies
            )
            if result["logged_in"] and result["zhipin_page"]:
                result["ok"] = True
                result["detail"] = "浏览器在线，Boss 登录态正常"
            elif result["logged_in"]:
                result["ok"] = True
                result["detail"] = "浏览器在线、已登录，但没打开 zhipin.com 页面（发送时会自动新开）"
            else:
                result["detail"] = "浏览器在线，但未检测到 Boss 登录态，请先在浏览器里登录"
        finally:
            self.close()
        return result

    def _get_page(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._external_page:
            raise SendFailed("注入的页面已关闭")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_endpoint)
        context = self._browser.contexts[0]
        self._page = self._pick_page(context)
        return self._page

    @staticmethod
    def _pick_page(context):
        # 跳过停在安全校验页的标签页（与 precheck 的挑选规则一致），
        # 否则一个后台残留的校验页会让每次发送瞬间误停。
        for page in context.pages:
            url = page.url or ""
            if "zhipin.com" in url and not BossSender._is_security_url(url):
                return page
        return context.new_page()

    def close(self) -> None:
        """Drop our CDP connection. Never close the shared browser context."""
        if self._external_page:
            return
        if self._playwright is not None:
            try:
                self._playwright.stop()
            finally:
                self._playwright = None
                self._browser = None
                self._page = None

    # -- safety --------------------------------------------------------
    @staticmethod
    def _is_security_url(url: str) -> bool:
        value = urllib.parse.unquote(str(url or "")).lower()
        return any(marker in value for marker in SECURITY_URL_MARKERS)

    def _assert_safe(self, page) -> None:
        if self._is_security_url(getattr(page, "url", "") or ""):
            raise SendPaused("检测到 Boss 安全校验，已停止发送，等待人工处理")

    def _wait(self, page, seconds) -> None:
        lo, hi = seconds
        remaining = max(0, round(random.uniform(float(lo), float(hi)) * 1000))
        while remaining > 0:
            self._assert_safe(page)
            step = min(500, remaining)
            page.wait_for_timeout(step)
            remaining -= step
            self._assert_safe(page)

    # -- chat window ---------------------------------------------------
    def _ensure_chat(self, job_id: str, company: str = "", title: str = ""):
        """Return a page with THIS job's chat window open (read nothing else).

        Two real incidents shaped this flow:
        - 2026-09-12 (轮动): clicking 立即沟通 opens the chat as a NEW tab;
          we must follow it instead of waiting on the job-detail page.
        - 2026-09-12 (重复发送): a leftover chat tab must NEVER be trusted as
          "the conversation" — it may belong to a different job. Always enter
          through the job-detail page, then verify the conversation header
          actually mentions this job's title/company before typing anything.
        """
        cached = getattr(self, "_verified_chat", None)
        if (
            cached
            and cached.get("job_id") == job_id
            and not cached["page"].is_closed()
            and "/web/geek/chat" in (cached["page"].url or "")
            and self._conversation_matches(cached["page"], company, title)
        ):
            return cached["page"]

        page = self._get_page()
        # Never navigate away from (or retry past) a security page.
        self._assert_safe(page)
        context = page.context
        chats_before = {
            id(p) for p in context.pages if "/web/geek/chat" in (p.url or "")
        }
        detail = f"{BASE_URL}/job_detail/{job_id}.html"
        page.goto(detail, wait_until="domcontentloaded")
        self._wait(page, self.cfg.get("page_settle_range_seconds", [12, 18]))
        self._assert_safe(page)
        try:
            button = page.wait_for_selector(SELECTORS["chat_btn"], timeout=10_000)
        except PWTimeout as exc:
            body_text = ""
            try:
                body_text = page.inner_text("body", timeout=3_000) or ""
            except Exception:  # noqa: BLE001
                pass
            if any(marker in body_text for marker in CLOSED_JOB_MARKERS):
                raise JobClosed(
                    "岗位已关闭或已下线，无法发起沟通（招呼语未发出）"
                ) from exc
            raise SendFailed("未找到「立即沟通/继续沟通」入口，无法打开会话") from exc
        button.click()
        self._wait(page, self.cfg.get("chat_settle_range_seconds", [3, 6]))
        self._assert_safe(page)
        chat = page
        if "/web/geek/chat" not in (page.url or ""):
            # 点击后新开的聊天标签页：跟进它，后续输入/上传都在新页里。
            new_chats = [
                p
                for p in context.pages
                if "/web/geek/chat" in (p.url or "") and id(p) not in chats_before
            ]
            if new_chats:
                chat = new_chats[-1]
            elif not any("/web/geek/chat" in (p.url or "") for p in context.pages):
                raise SendFailed("点击沟通后聊天窗口未打开，未发送任何内容")
        # 会话身份校验：确认打开的是目标职位的会话，再允许输入。
        deadline = time.monotonic() + 8
        while not self._conversation_matches(chat, company, title):
            if time.monotonic() >= deadline:
                raise SendFailed(
                    "聊天窗口与目标职位不一致（疑似复用了别的会话），"
                    "未发送任何内容"
                )
            chat.wait_for_timeout(500)
        self._verified_chat = {"job_id": job_id, "page": chat}
        return chat

    @staticmethod
    def _conversation_matches(page, company: str, title: str) -> bool:
        """True when the open conversation header mentions this job."""
        try:
            text = page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            ) or ""
        except Exception:  # noqa: BLE001 — a broken probe must not type blindly
            return False
        needle_title = str(title or "").strip()[:12]
        if needle_title and needle_title in text:
            return True
        needle_company = str(company or "").strip().replace("...", "")[:5]
        if len(needle_company) >= 4 and needle_company in text:
            return True
        # 无任何参照信息时无法校验：保持旧行为放行（真实投递包始终带双字段）。
        return not needle_title and not needle_company

    # -- actions -------------------------------------------------------
    def send_text(self, task: dict, package: dict) -> str:
        greeting = str(package.get("greeting_text") or "").strip()
        if not greeting:
            raise SendFailed("投递包缺少招呼语")
        page = self._ensure_chat(
            str(task.get("job_id") or ""),
            company=str(package.get("company") or ""),
            title=str(package.get("title") or ""),
        )
        self._assert_safe(page)
        try:
            box = page.wait_for_selector(SELECTORS["chat_input"], timeout=15_000)
            box.click()
            page.keyboard.type(greeting, delay=random.randint(50, 120))
        except PWTimeout as exc:
            raise SendFailed("未找到聊天输入框，招呼语未发出") from exc
        self._wait(page, [1.0, 2.0])
        send = page.query_selector(SELECTORS["send_btn"])
        if send is not None:
            send.click()
        else:
            page.keyboard.press("Enter")
        self._wait(page, [2.0, 4.0])
        self._assert_safe(page)
        if not self._text_visible(page, greeting):
            # The click may or may not have delivered: treat as unknown upstream.
            raise RuntimeError("已点击发送但未在会话中确认到招呼语")
        return "聊天中可见招呼语"

    def send_image(self, task: dict, package: dict) -> str:
        image_path = str(package.get("image_path") or "")
        if not image_path or not Path(image_path).is_file():
            raise SendFailed("简历长图文件不存在，未发出")
        page = self._ensure_chat(
            str(task.get("job_id") or ""),
            company=str(package.get("company") or ""),
            title=str(package.get("title") or ""),
        )
        self._assert_safe(page)
        before = self._image_message_count(page)
        try:
            # file input 是隐藏控件：等 attached 而不是默认的 visible，
            # 否则永远超时（真实验收 2026-09-12）。
            page.wait_for_selector(
                SELECTORS["image_input"], timeout=15_000, state="attached"
            )
            page.set_input_files(SELECTORS["image_input"], image_path)
        except PWTimeout as exc:
            raise SendFailed("未找到聊天图片上传控件，长图未发出") from exc
        self._wait(page, [3.0, 6.0])
        self._assert_safe(page)
        if self._image_message_count(page) <= before:
            raise RuntimeError("已上传长图但未在会话中确认到图片消息")
        return "聊天中可见简历长图"

    # -- verification --------------------------------------------------
    @staticmethod
    def _text_visible(page, text: str) -> bool:
        snippet = text[:24]
        try:
            return snippet in (page.inner_text("body", timeout=5_000) or "")
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _image_message_count(page) -> int:
        """Count image-bearing chat messages. Heuristic: <img> inside a message."""
        try:
            return int(
                page.evaluate(
                    """() => {
                        const items = document.querySelectorAll('.message-item, .message-content');
                        let n = 0;
                        items.forEach((el) => {
                            el.querySelectorAll('img').forEach((img) => {
                                const src = img.getAttribute('src') || '';
                                if (src && !src.includes('avatar') && !src.includes('icon')) n += 1;
                            });
                        });
                        return n;
                    }"""
                )
                or 0
            )
        except Exception:  # noqa: BLE001
            return -1

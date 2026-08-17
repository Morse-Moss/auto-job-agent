"""Boss直聘平台自动化。

默认通过 CDP 复用已有浏览器会话，在一个页面内串行处理岗位。遇到 Boss 安全校验时
立即停止，不自动刷新、重试或绕过验证。
"""

import random
import re
import urllib.parse
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from .base import BasePlatform

BASE_URL = "https://www.zhipin.com"
STATE_FILE = Path(".state/boss_state.json")

CITY_CODES = {
    "全国": "100010000",
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "武汉": "101200100",
    "南京": "101190100",
    "苏州": "101190400",
    "西安": "101110100",
}

# Boss 前端改版时优先检查这里 --------------------------------------------------
SELECTORS = {
    "logged_in": "[ka='header-username'], .user-nav .nav-figure",
    "job_card": "li.job-card-wrapper, li.job-card-box",
    "job_link": "a.job-card-left, a.job-card-body",
    "job_name": ".job-name",
    "company": ".company-name h3, .company-name a, .boss-name",
    "salary": ".salary",
    "jd_text": ".job-detail-body .desc, .job-sec-text, .job-detail-section .job-sec-text",
    "recruiter_context": ".boss-info-attr, .job-boss-info, .job-detail-box .boss-info, .job-boss-wrapper",
    "detail_title": ".info-primary .name, .job-detail-header .name, .job-detail-section h1",
    "dialog_close": ".dialog-close, .icon-close, button[aria-label='关闭']",
    "chat_btn": "a.btn-startchat, a.op-btn-chat",
    "chat_input": "#chat-input, div[contenteditable='true'].chat-input",
    "send_btn": ".send-message, button.btn-send",
}

SECURITY_URL_MARKERS = (
    "_security_check=",
    "/web/passport/zp/verify",
    "/web/user/safe/verify",
)
DETAIL_RESPONSE_MARKER = "/wapi/zpgeek/job/detail.json"

OUTSOURCING_PATTERNS = (
    r"外包(?:岗位|项目|人员|团队|性质|用工)?",
    r"(?:长期|需要|需)?驻场",
    r"劳务派遣|派遣制|人力外派|外派至",
    r"客户现场(?:办公|工作)|第三方(?:用工|签约|劳动合同)",
)

THIRD_PARTY_PATTERNS = (
    r"猎头",
    r"代招",
    r"\bRPO\b",
)

THIRD_PARTY_RECRUITER_PATTERNS = (
    r"招聘顾问",
    r"人力资源顾问",
)


class BossSecurityCheck(RuntimeError):
    """表示页面进入 Boss 安全校验，调用方必须结束本轮。"""


class BossPlatform(BasePlatform):
    name = "boss"

    # ------------------------------------------------------------------
    def run(self, dry_run: bool = False) -> None:
        cfg = self.cfg["boss"]
        min_score = self.cfg["match"]["min_score"]
        preferences = (self.cfg.get("preferences") or "").strip()
        resume_md = self.resume_mgr.master
        applied = 0
        scored = 0

        with sync_playwright() as p:
            browser, context, page, owns_browser = self._connect_browser_session(
                p.chromium, cfg
            )
            try:
                self._ensure_login(page, context)

                for keyword in cfg["keywords"]:
                    print(f"\n===== 关键词: {keyword} =====")
                    jobs = self._search(page, keyword, cfg)
                    search_url = page.url
                    print(f"共发现 {len(jobs)} 个岗位")

                    for job in jobs:
                        if applied >= cfg.get("max_apply_per_run", 20):
                            print("已达到单次运行投递上限，停止。")
                            return
                        if self.store.seen(job["job_id"]):
                            continue
                        if self._skip_recruitment_risk(job, dry_run):
                            continue
                        if self._blacklisted(job, cfg):
                            if not dry_run:
                                self.store.record(job, -1, "命中黑名单", greeted=False)
                            continue

                        security_stop = False
                        try:
                            job = self._load_detail(page, job)
                            if not job.get("jd"):
                                continue
                            if self._blacklisted(job, cfg):
                                if not dry_run:
                                    self.store.record(job, -1, "JD命中黑名单", greeted=False)
                                continue
                            if self._skip_recruitment_risk(job, dry_run):
                                continue

                            score, reason = self.ai.match_score(resume_md, job, preferences)
                            scored += 1
                            tag = "[投]" if score >= min_score else "[跳过]"
                            print(f"{tag} [{score:3d}] {job['title']} @ {job['company']}")

                            if dry_run:
                                continue
                            if score < min_score:
                                self.store.record(job, score, reason, greeted=False)
                                continue

                            knowledge = self.job_knowledge(job)

                            tailored = self.ai.tailor_resume(resume_md, job, knowledge)
                            path = self.resume_mgr.save_tailored(job, tailored)
                            print(f"   定制简历已保存: {path}")

                            greet = self.ai.greeting(resume_md, job, knowledge)
                            ok = self._send_greeting(page, greet)
                            self.store.record(job, score, reason, greeted=ok)
                            if ok:
                                applied += 1
                                print(f"   已打招呼({applied}): {greet[:40]}...")
                            self._restore_search_context(page, search_url)
                        except BossSecurityCheck as exc:
                            security_stop = True
                            print(f"[停止] {exc}")
                            return
                        finally:
                            if not security_stop:
                                self._sleep(page, cfg)
            except BossSecurityCheck as exc:
                print(f"[停止] {exc}")
            finally:
                # CDP 模式只断开 Playwright，不关闭用户自己的浏览器。
                if owns_browser:
                    browser.close()
        print(f"\n本次运行结束，共完成 AI 评分 {scored} 个岗位，打招呼 {applied} 次。")

    # ------------------------------------------------------------------
    @staticmethod
    def _connect_browser_session(chromium, cfg: dict):
        """连接已有 CDP context；没有 endpoint 时保留旧的本地启动兼容路径。"""
        endpoint = (cfg.get("cdp_endpoint") or "").strip()
        if endpoint:
            browser = chromium.connect_over_cdp(endpoint, timeout=15_000)
            contexts = list(browser.contexts)
            if not contexts:
                raise RuntimeError("CDP 浏览器没有可用的 BrowserContext")
            context = contexts[0]
            pages = list(context.pages)
            page = next(
                (
                    candidate
                    for candidate in pages
                    if "zhipin.com" in (candidate.url or "")
                    and not BossPlatform._is_security_url(candidate.url)
                ),
                None,
            )
            if page is None:
                page = next(
                    (
                        candidate
                        for candidate in pages
                        if "zhipin.com" in (candidate.url or "")
                    ),
                    None,
                )
            if page is None:
                page = context.new_page()
            return browser, context, page, False

        browser = chromium.launch(headless=cfg.get("headless", False))
        ctx_kwargs = {"viewport": {"width": 1440, "height": 900}}
        if STATE_FILE.exists():
            ctx_kwargs["storage_state"] = str(STATE_FILE)
        context = browser.new_context(**ctx_kwargs)
        return browser, context, context.new_page(), True

    # ------------------------------------------------------------------
    def _ensure_login(self, page, context) -> None:
        self._assert_page_safe(page)
        if "zhipin.com" not in (page.url or ""):
            page.goto(BASE_URL, wait_until="domcontentloaded")
            self._wait_with_safety(
                page,
                self.cfg["boss"].get("page_settle_range_seconds", [12, 18]),
            )
            self._assert_page_safe(page)
        try:
            page.wait_for_selector(SELECTORS["logged_in"], timeout=5000)
            print("已登录（复用 Cookie）")
            return
        except PWTimeout:
            pass

        print("当前 CDP 浏览器未检测到登录状态，请在当前 Boss 页面完成登录…")
        page.wait_for_selector(SELECTORS["logged_in"], timeout=180_000)
        self._assert_page_safe(page)
        print("登录成功（CDP 会话继续复用当前浏览器 Cookie）")

    # ------------------------------------------------------------------
    def _search(self, page, keyword: str, cfg: dict) -> list[dict]:
        city_code = CITY_CODES.get(cfg.get("city", "全国"), "100010000")
        url = (
            f"{BASE_URL}/web/geek/jobs?"
            f"query={urllib.parse.quote(keyword)}&city={city_code}"
        )
        self._assert_page_safe(page)
        if not self._is_same_search_url(page.url, url):
            page.goto(url, wait_until="domcontentloaded")
            self._wait_with_safety(page, cfg.get("page_settle_range_seconds", [12, 18]))
        self._assert_page_safe(page)
        try:
            page.wait_for_selector(SELECTORS["job_card"], timeout=15_000)
        except PWTimeout:
            self._assert_page_safe(page)
            print("警告：未找到岗位卡片。可能是选择器过期或触发了验证码，请查看浏览器窗口。")
            return []

        jobs: list[dict] = []
        cards = page.query_selector_all(SELECTORS["job_card"])
        for index, card in enumerate(cards[: cfg.get("max_jobs_per_keyword", 30)]):
            link = card.query_selector(SELECTORS["job_link"]) or card.query_selector("a")
            if not link:
                continue
            href = link.get_attribute("href") or ""
            job_id = href.split("/")[-1].split(".")[0] if "/job_detail/" in href else href
            name_el = card.query_selector(SELECTORS["job_name"])
            company_el = card.query_selector(SELECTORS["company"])
            salary_el = card.query_selector(SELECTORS["salary"])
            badges = [
                (image.get_attribute("alt") or "").strip()
                for image in card.query_selector_all("img[alt]")
            ]
            jobs.append(
                {
                    "platform": "boss",
                    "job_id": job_id,
                    "card_index": index,
                    "href": href if href.startswith("http") else BASE_URL + href,
                    "title": name_el.inner_text().strip() if name_el else "",
                    "company": company_el.inner_text().strip() if company_el else "",
                    "salary": salary_el.inner_text().strip() if salary_el else "",
                    "listing_badges": " ".join(badge for badge in badges if badge),
                }
            )
        return jobs

    # ------------------------------------------------------------------
    def _load_detail(self, page, job: dict) -> dict:
        self._assert_page_safe(page)
        payload = None
        card_index = job.get("card_index")
        if card_index is not None:
            cards = page.locator(SELECTORS["job_card"])
            try:
                with page.expect_response(
                    lambda response: self._is_detail_response(response),
                    timeout=12_000,
                ) as response_info:
                    cards.nth(int(card_index)).click()
                payload = response_info.value.json()
            except PWTimeout:
                # 首卡可能已在详情面板中；不再通过第二次点击或刷新重试。
                self._assert_page_safe(page)
            self._wait_with_safety(
                page,
                self.cfg["boss"].get("detail_settle_range_seconds", [8, 15]),
            )
            self._assert_page_safe(page)

        if isinstance(payload, dict):
            return self._job_from_detail_payload(job, payload)

        try:
            page.wait_for_selector(SELECTORS["jd_text"], timeout=15_000)
            job["jd"] = page.inner_text(SELECTORS["jd_text"]).strip()
            recruiter = page.query_selector(SELECTORS["recruiter_context"])
            job["recruiter_context"] = (
                (recruiter.inner_text() or "").strip()[:500] if recruiter else ""
            )
        except PWTimeout:
            print(f"警告：抓取 JD 失败: {job['title']}（选择器过期或验证码）")
            job["jd"] = ""
        return job

    # ------------------------------------------------------------------
    def _send_greeting(self, page, greeting: str) -> bool:
        """在当前岗位详情面板点击「立即沟通」并发送打招呼语。"""
        try:
            self._assert_page_safe(page)
            btn = page.wait_for_selector(SELECTORS["chat_btn"], timeout=8_000)
            btn_text = (btn.inner_text() or "").strip()
            if "继续沟通" in btn_text or "已沟通" in btn_text:
                return False  # 之前聊过，跳过
            btn.click()
            self._wait_with_safety(
                page,
                self.cfg["boss"].get("chat_settle_range_seconds", [3, 6]),
            )
            self._assert_page_safe(page)
            box = page.wait_for_selector(SELECTORS["chat_input"], timeout=15_000)
            box.click()
            page.keyboard.type(greeting, delay=random.randint(50, 120))
            self._wait_with_safety(page, [1.5, 3.0])
            send = page.query_selector(SELECTORS["send_btn"])
            if send:
                send.click()
            else:
                page.keyboard.press("Enter")
            self._wait_with_safety(page, [2.0, 4.0])
            self._assert_page_safe(page)
            close = page.query_selector(SELECTORS["dialog_close"])
            if close:
                close.click()
            return True
        except PWTimeout:
            self._assert_page_safe(page)
            if "/web/geek/chat" in (page.url or ""):
                try:
                    if greeting in page.inner_text("body", timeout=5000):
                        return True
                except PWTimeout:
                    pass
            print("警告：打招呼失败（聊天入口/输入框选择器可能过期）")
            return False

    def _restore_search_context(self, page, search_url: str) -> None:
        """沟通页发生整页跳转后，回到原搜索列表继续串行处理。"""
        self._assert_page_safe(page)
        if self._is_same_search_url(page.url, search_url):
            return
        if "/web/geek/chat" not in (page.url or ""):
            return

        try:
            page.go_back(wait_until="domcontentloaded", timeout=15_000)
        except PWTimeout:
            self._assert_page_safe(page)
        if not self._is_same_search_url(page.url, search_url):
            print("警告：沟通后未能返回岗位列表，本轮不再强制刷新页面")
            return
        self._wait_with_safety(
            page,
            self.cfg["boss"].get("page_settle_range_seconds", [12, 18]),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _is_detail_response(response) -> bool:
        try:
            return (
                DETAIL_RESPONSE_MARKER in response.url
                and response.request.method.upper() == "GET"
            )
        except Exception:
            return False

    # ------------------------------------------------------------------
    @staticmethod
    def _job_from_detail_payload(job: dict, payload: dict) -> dict:
        """从列表点击触发的 Boss detail.json 响应提取岗位和招聘者字段。"""
        data = payload.get("zpData") or {}
        job_info = data.get("jobInfo") or {}
        brand = data.get("brandComInfo") or {}
        boss = data.get("bossInfo") or {}

        encrypt_id = str(job_info.get("encryptId") or job.get("job_id") or "")
        title = str(job_info.get("jobName") or job.get("title") or "").strip()
        company = str(brand.get("brandName") or job.get("company") or "").strip()
        salary = str(job_info.get("salaryDesc") or job.get("salary") or "").strip()
        jd = str(job_info.get("postDescription") or "").strip()
        recruiter_name = str(boss.get("name") or "").strip()
        recruiter_role = str(boss.get("title") or "").strip()
        active = str(boss.get("activeTimeDesc") or "").strip()
        recruiter_context = "\n".join(
            value for value in (recruiter_name, recruiter_role, active) if value
        )[:500]

        result = {
            **job,
            "job_id": encrypt_id,
            "title": title,
            "company": company,
            "salary": salary,
            "jd": jd,
            "recruiter_name": recruiter_name,
            "recruiter_role": recruiter_role,
            "recruiter_active": active,
            "recruiter_context": recruiter_context,
        }
        if encrypt_id:
            result["href"] = f"{BASE_URL}/job_detail/{encrypt_id}.html"
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _is_security_url(url: str) -> bool:
        value = str(url or "").lower()
        return any(marker in value for marker in SECURITY_URL_MARKERS)

    # ------------------------------------------------------------------
    @classmethod
    def _assert_page_safe(cls, page) -> None:
        url = str(getattr(page, "url", "") or "")
        if cls._is_security_url(url):
            raise BossSecurityCheck("检测到 Boss 安全校验，已停止本轮，不刷新、不重试")

    # ------------------------------------------------------------------
    @staticmethod
    def _is_same_search_url(current_url: str, target_url: str) -> bool:
        """Compare the search route semantically so query ordering does not reload the page."""
        try:
            current = urllib.parse.urlsplit(str(current_url or ""))
            target = urllib.parse.urlsplit(str(target_url or ""))
        except ValueError:
            return False

        if (
            not current.netloc
            or current.netloc.lower() != target.netloc.lower()
            or current.path != target.path
            or current.path != "/web/geek/jobs"
        ):
            return False

        current_query = urllib.parse.parse_qs(current.query, keep_blank_values=True)
        target_query = urllib.parse.parse_qs(target.query, keep_blank_values=True)
        return all(current_query.get(key) == target_query.get(key) for key in ("query", "city"))

    @classmethod
    def _wait_with_safety(cls, page, seconds: list | tuple) -> None:
        """Wait in short browser turns so a security redirect is observed promptly."""
        if not seconds:
            cls._assert_page_safe(page)
            return

        lo, hi = seconds
        remaining_ms = max(0, round(random.uniform(float(lo), float(hi)) * 1000))
        while remaining_ms > 0:
            cls._assert_page_safe(page)
            step_ms = min(500, remaining_ms)
            page.wait_for_timeout(step_ms)
            remaining_ms -= step_ms
            cls._assert_page_safe(page)

    # ------------------------------------------------------------------
    @staticmethod
    def _blacklisted(job: dict, cfg: dict) -> bool:
        text = f"{job.get('title', '')} {job.get('jd', '')}"
        company = job.get("company", "")
        for kw in cfg.get("blacklist_keywords", []):
            if kw and kw in text:
                return True
        for c in cfg.get("blacklist_companies", []):
            if c and c in company:
                return True
        return False

    # ------------------------------------------------------------------
    @staticmethod
    def _recruitment_channel(job: dict) -> str:
        """区分直招、第三方招聘和明确外包风险。"""
        listing_text = " ".join(
            str(job.get(field, "") or "")
            for field in ("title", "company", "jd", "listing_badges", "recruiter_role")
        )
        recruiter_text = str(job.get("recruiter_context", "") or "")
        combined = f"{listing_text} {recruiter_text}"

        if any(re.search(pattern, combined, re.IGNORECASE) for pattern in OUTSOURCING_PATTERNS):
            return "outsourcing_risk"
        if any(re.search(pattern, combined, re.IGNORECASE) for pattern in THIRD_PARTY_PATTERNS):
            return "third_party"
        if any(
            re.search(pattern, recruiter_text, re.IGNORECASE)
            for pattern in THIRD_PARTY_RECRUITER_PATTERNS
        ):
            return "third_party"
        return "direct"

    def _skip_recruitment_risk(self, job: dict, dry_run: bool) -> bool:
        channel = self._recruitment_channel(job)
        if channel == "direct":
            return False

        reason = {
            "third_party": "第三方招聘/猎头（默认跳过）",
            "outsourcing_risk": "明确外包/驻场/派遣风险",
        }[channel]
        print(f"[跳过] {job.get('title', '')} @ {job.get('company', '')}: {reason}")
        if not dry_run:
            self.store.record(job, -1, reason, greeted=False)
        return True

    # ------------------------------------------------------------------
    @classmethod
    def _sleep(cls, page, cfg: dict) -> None:
        lo, hi = cfg.get("delay_range_seconds", [45, 90])
        cls._wait_with_safety(page, [lo, hi])

"""Boss直聘平台自动化。

流程：扫码登录(Cookie持久化) -> 搜索岗位 -> 抓取JD -> AI结合求职要求打分 ->
     高分岗位以知识库为依据生成定制简历+打招呼语 -> 自动点击「立即沟通」发送 -> 本地记录防重复

注意 Boss 前端经常改版。脚本失效时，优先用 F12 检查并更新下方 SELECTORS。
注意 请控制频率：max_apply_per_run 建议不超过 20，delay_range_seconds 不要调太小。
"""

import random
import time
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
    "jd_text": ".job-sec-text, .job-detail-section .job-sec-text",
    "chat_btn": "a.btn-startchat, a.op-btn-chat",
    "chat_input": "#chat-input, div[contenteditable='true'].chat-input",
    "send_btn": ".send-message, button.btn-send",
}


class BossPlatform(BasePlatform):
    name = "boss"

    # ------------------------------------------------------------------
    def run(self, dry_run: bool = False) -> None:
        cfg = self.cfg["boss"]
        min_score = self.cfg["match"]["min_score"]
        preferences = (self.cfg.get("preferences") or "").strip()
        resume_md = self.resume_mgr.master
        applied = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=cfg.get("headless", False))
            ctx_kwargs = {"viewport": {"width": 1440, "height": 900}}
            if STATE_FILE.exists():
                ctx_kwargs["storage_state"] = str(STATE_FILE)
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()

            self._ensure_login(page, context)

            for keyword in cfg["keywords"]:
                print(f"\n===== 关键词: {keyword} =====")
                jobs = self._search(page, keyword, cfg)
                print(f"共发现 {len(jobs)} 个岗位")

                for job in jobs:
                    if applied >= cfg.get("max_apply_per_run", 20):
                        print("已达到单次运行投递上限，停止。")
                        browser.close()
                        return
                    if self.store.seen(job["job_id"]):
                        continue
                    if self._blacklisted(job, cfg):
                        self.store.record(job, -1, "命中黑名单", greeted=False)
                        continue

                    detail = context.new_page()
                    try:
                        job = self._load_detail(detail, job)
                        if not job.get("jd"):
                            continue
                        if self._blacklisted(job, cfg):
                            self.store.record(job, -1, "JD命中黑名单", greeted=False)
                            continue

                        score, reason = self.ai.match_score(resume_md, job, preferences)
                        tag = "[投]" if score >= min_score else "[跳过]"
                        print(f"{tag} [{score:3d}] {job['title']} @ {job['company']} — {reason}")

                        if score < min_score or dry_run:
                            self.store.record(job, score, reason, greeted=False)
                            continue

                        tailored = self.ai.tailor_resume(resume_md, job, self.knowledge)
                        path = self.resume_mgr.save_tailored(job, tailored)
                        print(f"   定制简历已保存: {path}")

                        greet = self.ai.greeting(resume_md, job, self.knowledge)
                        ok = self._send_greeting(detail, greet)
                        self.store.record(job, score, reason, greeted=ok)
                        if ok:
                            applied += 1
                            print(f"   已打招呼({applied}): {greet[:40]}...")
                    finally:
                        detail.close()
                        self._sleep(cfg)

            browser.close()
        print(f"\n本次运行结束，共打招呼 {applied} 次。")

    # ------------------------------------------------------------------
    def _ensure_login(self, page, context) -> None:
        page.goto(BASE_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(SELECTORS["logged_in"], timeout=5000)
            print("已登录（复用 Cookie）")
            return
        except PWTimeout:
            pass

        print("未登录，正在打开登录页，请用 Boss App 扫码…")
        page.goto(f"{BASE_URL}/web/user/?ka=header-login", wait_until="domcontentloaded")
        page.wait_for_selector(SELECTORS["logged_in"], timeout=180_000)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(STATE_FILE))
        print("登录成功，Cookie 已保存到 .state/boss_state.json")

    # ------------------------------------------------------------------
    def _search(self, page, keyword: str, cfg: dict) -> list[dict]:
        city_code = CITY_CODES.get(cfg.get("city", "全国"), "100010000")
        url = (
            f"{BASE_URL}/web/geek/job?"
            f"query={urllib.parse.quote(keyword)}&city={city_code}"
        )
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(SELECTORS["job_card"], timeout=15_000)
        except PWTimeout:
            print("警告：未找到岗位卡片。可能是选择器过期或触发了验证码，请查看浏览器窗口。")
            return []

        jobs: list[dict] = []
        cards = page.query_selector_all(SELECTORS["job_card"])
        for card in cards[: cfg.get("max_jobs_per_keyword", 30)]:
            link = card.query_selector(SELECTORS["job_link"]) or card.query_selector("a")
            if not link:
                continue
            href = link.get_attribute("href") or ""
            job_id = href.split("/")[-1].split(".")[0] if "/job_detail/" in href else href
            name_el = card.query_selector(SELECTORS["job_name"])
            company_el = card.query_selector(SELECTORS["company"])
            salary_el = card.query_selector(SELECTORS["salary"])
            jobs.append(
                {
                    "platform": "boss",
                    "job_id": job_id,
                    "href": href if href.startswith("http") else BASE_URL + href,
                    "title": name_el.inner_text().strip() if name_el else "",
                    "company": company_el.inner_text().strip() if company_el else "",
                    "salary": salary_el.inner_text().strip() if salary_el else "",
                }
            )
        return jobs

    # ------------------------------------------------------------------
    def _load_detail(self, page, job: dict) -> dict:
        page.goto(job["href"], wait_until="domcontentloaded")
        try:
            page.wait_for_selector(SELECTORS["jd_text"], timeout=15_000)
            job["jd"] = page.inner_text(SELECTORS["jd_text"]).strip()
        except PWTimeout:
            print(f"警告：抓取 JD 失败: {job['title']}（选择器过期或验证码）")
            job["jd"] = ""
        return job

    # ------------------------------------------------------------------
    def _send_greeting(self, page, greeting: str) -> bool:
        """在岗位详情页点击「立即沟通」并发送打招呼语。"""
        try:
            btn = page.wait_for_selector(SELECTORS["chat_btn"], timeout=8_000)
            btn_text = (btn.inner_text() or "").strip()
            if "继续沟通" in btn_text:
                return False  # 之前聊过，跳过
            btn.click()
            box = page.wait_for_selector(SELECTORS["chat_input"], timeout=15_000)
            box.click()
            page.keyboard.type(greeting, delay=random.randint(30, 80))
            time.sleep(random.uniform(0.5, 1.5))
            page.keyboard.press("Enter")
            time.sleep(random.uniform(1, 2))
            return True
        except PWTimeout:
            print("警告：打招呼失败（聊天入口/输入框选择器可能过期）")
            return False

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
    def _sleep(cfg: dict) -> None:
        lo, hi = cfg.get("delay_range_seconds", [10, 25])
        time.sleep(random.uniform(lo, hi))

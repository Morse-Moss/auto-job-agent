"""Read-only Boss collector with incremental persistence and hard safety stops."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from jobagent.collection import (
    CollectionStore,
    canonical_job_url,
    job_from_detail_payload,
    normalize_search_card,
)

from .boss import (
    BASE_URL,
    CITY_CODES,
    DETAIL_RESPONSE_MARKER,
    SELECTORS,
    BossPlatform,
    BossSecurityCheck,
)


FAVORITES_URL = f"{BASE_URL}/web/geek/recommend"
FAVORITE_CARD = "li.item-boss"
FAVORITE_TAB = "[ka='personal_top_interest']"


@dataclass(frozen=True)
class CollectionResult:
    run_id: str
    collected: int
    total: int
    status: str
    error: str | None = None


class BossCollector:
    def __init__(self, cfg: dict, store: CollectionStore):
        self.cfg = cfg
        self.boss_cfg = cfg.get("boss") or {}
        self.collection_cfg = cfg.get("collection") or {}
        self.store = store
        self._consecutive_failures = 0

    def run(self, target: int, source: str = "all") -> CollectionResult:
        target = max(1, int(target))
        starting_count = self.store.detail_inventory_count()
        run_id = self.store.start_run(target_count=target)
        status = "stopped"
        error: str | None = None
        own_pages = []
        original_page = None

        if starting_count >= target:
            status = "sample_completed" if target <= 3 else "completed"
            self.store.finish_run(run_id, status)
            return CollectionResult(run_id, 0, starting_count, status)

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    self.boss_cfg.get("cdp_endpoint", "http://127.0.0.1:17332"),
                    timeout=15_000,
                )
                if not browser.contexts:
                    raise RuntimeError("CDP browser has no usable context")
                context = browser.contexts[0]
                try:
                    original_page = next(
                        (
                            page
                            for page in context.pages
                            if "zhipin.com" in (page.url or "")
                            and not BossPlatform._is_security_url(page.url)
                        ),
                        None,
                    )
                    list_page = context.new_page()
                    own_pages.append(list_page)
                    if original_page:
                        original_page.bring_to_front()

                    if source in ("all", "favorites"):
                        detail_page = context.new_page()
                        own_pages.append(detail_page)
                        if original_page:
                            original_page.bring_to_front()
                        self._collect_favorites(
                            list_page, detail_page, run_id=run_id, target=target
                        )

                    if (
                        source in ("all", "search")
                        and self.store.detail_inventory_count() < target
                    ):
                        self._collect_search(list_page, run_id=run_id, target=target)

                    total = self.store.detail_inventory_count()
                    status = (
                        "sample_completed" if total >= target and target <= 3 else
                        "completed" if total >= target else
                        "exhausted"
                    )
                finally:
                    for page in reversed(own_pages):
                        try:
                            page.close()
                        except Exception:
                            pass
                    if original_page:
                        try:
                            original_page.bring_to_front()
                        except Exception:
                            pass
        except BossSecurityCheck as exc:
            status = "security_check"
            error = str(exc)
        except Exception as exc:
            status = "stopped"
            error = f"{type(exc).__name__}: {exc}"

        self.store.finish_run(run_id, status, error)
        total = self.store.detail_inventory_count()
        return CollectionResult(
            run_id=run_id,
            collected=max(0, total - starting_count),
            total=total,
            status=status,
            error=error,
        )

    def _collect_favorites(self, list_page, detail_page, run_id: str, target: int) -> None:
        self._navigate(list_page, FAVORITES_URL)
        self._require_login(list_page)
        favorite_tab = list_page.locator(FAVORITE_TAB)
        favorite_tab.wait_for(state="visible", timeout=15_000)
        if "current" not in (favorite_tab.get_attribute("class") or ""):
            favorite_tab.click()
            self._wait(list_page, self._navigation_delay())

        page_number = 1
        while self.store.detail_inventory_count() < target:
            BossPlatform._assert_page_safe(list_page)
            cards = list_page.locator(FAVORITE_CARD)
            cards.first.wait_for(state="visible", timeout=15_000)
            count = cards.count()
            source_key = "favorites"

            for index in range(count):
                if self.store.detail_inventory_count() >= target:
                    return
                card = cards.nth(index)
                job = self._favorite_card(card, page_number)
                if not job.get("job_id"):
                    continue
                self.store.save_observation(job, run_id)
                self.store.update_cursor(source_key, page_number, index, "running")
                if self.store.has_collected_detail(job["job_id"]):
                    continue
                self._collect_one(
                    job,
                    run_id,
                    lambda: self._load_detail_page(detail_page, job),
                )
                if self.store.detail_inventory_count() < target:
                    self._wait(detail_page, self._detail_delay())

            next_button = list_page.locator(
                ".options-pages a:has(i.ui-icon-arrow-right)"
            ).last
            if next_button.count() == 0 or "disabled" in (
                next_button.get_attribute("class") or ""
            ):
                self.store.update_cursor(source_key, page_number, count, "exhausted")
                return
            previous_first = self._first_job_id(cards)
            next_button.click()
            self._wait(list_page, self._navigation_delay())
            current_cards = list_page.locator(FAVORITE_CARD)
            if self._first_job_id(current_cards) == previous_first:
                raise RuntimeError("Favorites pagination did not advance")
            page_number += 1

    def _collect_search(self, page, run_id: str, target: int) -> None:
        keywords = self.collection_cfg.get("keywords") or self.boss_cfg.get("keywords") or []
        cities = self.collection_cfg.get("cities") or [self.boss_cfg.get("city", "上海")]
        max_jobs = int(self.collection_cfg.get("max_jobs_per_source", 45))

        for city in cities:
            city_code = CITY_CODES.get(city)
            if not city_code:
                continue
            for keyword in keywords:
                if self.store.detail_inventory_count() >= target:
                    return
                source_key = f"search:{city}:{keyword}"
                url = (
                    f"{BASE_URL}/web/geek/jobs?"
                    f"query={urllib.parse.quote(str(keyword))}&city={city_code}"
                )
                self._navigate(page, url)
                self._require_login(page)
                cards = page.locator(SELECTORS["job_card"])
                try:
                    cards.first.wait_for(state="visible", timeout=15_000)
                except PWTimeout:
                    BossPlatform._assert_page_safe(page)
                    self.store.update_cursor(source_key, 1, 0, "empty")
                    continue

                count = min(cards.count(), max_jobs)
                for index in range(count):
                    if self.store.detail_inventory_count() >= target:
                        return
                    card = cards.nth(index)
                    job = self._search_card(card, city, str(keyword), index)
                    if not job.get("job_id"):
                        continue
                    self.store.save_observation(job, run_id)
                    self.store.update_cursor(source_key, 1, index, "running")
                    if self.store.has_collected_detail(job["job_id"]):
                        continue
                    self._collect_one(
                        job,
                        run_id,
                        lambda card=card, job=job: self._load_search_detail(page, card, job),
                    )
                    if self.store.detail_inventory_count() < target:
                        self._wait(page, self._detail_delay())
                self.store.update_cursor(source_key, 1, count, "exhausted")

    def _collect_one(self, job: dict, run_id: str, loader) -> None:
        try:
            detail = loader()
            if not (detail.get("jd") or "").strip():
                raise RuntimeError("JD text is empty")
            self.store.save_observation(detail, run_id)
            self.store.save_detail(detail, run_id)
            self._consecutive_failures = 0
            print(
                f"[采集] {self.store.collected_detail_count():3d} "
                f"{detail.get('title', '')} @ {detail.get('company', '')}"
            )
        except BossSecurityCheck:
            raise
        except Exception as exc:
            self.store.save_failure(job, run_id, f"{type(exc).__name__}: {exc}")
            self._consecutive_failures += 1
            print(f"[失败] {job.get('title', '')}: {type(exc).__name__}: {exc}")
            limit = int(self.collection_cfg.get("max_consecutive_failures", 3))
            if self._consecutive_failures >= limit:
                raise RuntimeError(f"Stopped after {limit} consecutive detail failures")

    def _load_detail_page(self, page, job: dict) -> dict:
        payload = None
        href = job.get("_signed_href") or job.get("source_url")
        try:
            with page.expect_response(self._is_detail_response, timeout=15_000) as response:
                page.goto(href, wait_until="domcontentloaded", timeout=30_000)
            payload = response.value.json()
        except PWTimeout:
            BossPlatform._assert_page_safe(page)
        self._wait(page, self._detail_settle_delay())
        return self._detail_result(page, job, payload)

    def _load_search_detail(self, page, card, job: dict) -> dict:
        payload = None
        try:
            with page.expect_response(self._is_detail_response, timeout=12_000) as response:
                card.click()
            payload = response.value.json()
        except PWTimeout:
            BossPlatform._assert_page_safe(page)
        self._wait(page, self._detail_settle_delay())
        return self._detail_result(page, job, payload)

    @staticmethod
    def _detail_result(page, job: dict, payload: dict | None) -> dict:
        if isinstance(payload, dict):
            result = job_from_detail_payload(job, payload)
        else:
            result = dict(job)
            jd = ""
            for selector in (
                ".job-detail-body .desc",
                ".job-detail-section .job-sec-text",
                ".job-sec-text",
            ):
                locator = page.locator(selector)
                if locator.count():
                    try:
                        jd = locator.first.inner_text(timeout=5_000).strip()
                    except PWTimeout:
                        continue
                    if jd:
                        break
            result["jd"] = jd
        try:
            result["detail_text"] = page.locator("body").inner_text(timeout=5_000)[:30_000]
        except PWTimeout:
            result["detail_text"] = ""
        return result

    @staticmethod
    def _favorite_card(card, page_number: int) -> dict:
        data = card.evaluate(
            """el => {
              const text = selector => (el.querySelector(selector)?.innerText || '').trim();
              const link = el.querySelector('.job-info a.name');
              const jobMeta = el.querySelector('.job-info > p.gray');
              const jobSpans = [...(jobMeta?.querySelectorAll('span') || [])]
                .map(node => (node.innerText || '').trim());
              const companyMeta = [...el.querySelectorAll('.company-info p.gray span')]
                .map(node => (node.innerText || '').trim());
              const recruiter = [...el.querySelectorAll('.info-header h3.name span')]
                .map(node => (node.innerText || '').trim());
              const salary = jobMeta ? (jobMeta.childNodes[0]?.textContent || '').trim() : '';
              return {
                href: link?.href || '',
                title: text('.job-name-text'),
                location: text('.location em'),
                salary_raw: salary,
                experience_raw: jobSpans[0] || '',
                education_raw: jobSpans[1] || '',
                company: text('.company-info b'),
                industry: companyMeta[0] || '',
                financing: companyMeta[1] || '',
                company_size: companyMeta[2] || '',
                recruiter_name: recruiter[0] || '',
                recruiter_role: recruiter[1] || '',
                card_text: (el.innerText || '').trim()
              };
            }"""
        )
        job_id = BossCollector._job_id(data.get("href", ""))
        href = urllib.parse.urljoin(BASE_URL, data.pop("href", ""))
        return {
            "platform": "boss",
            "job_id": job_id,
            "source": "boss-favorites",
            "source_key": "favorites",
            "source_page": page_number,
            "source_url": canonical_job_url(href),
            "_signed_href": href,
            **data,
        }

    @staticmethod
    def _search_card(card, city: str, keyword: str, index: int) -> dict:
        data = card.evaluate(
            """el => {
              const text = selector => (el.querySelector(selector)?.innerText || '').trim();
              const link = el.querySelector('a.job-card-left, a.job-card-body, a.job-name');
              const tags = [...el.querySelectorAll('.job-info .tag-list li, .job-card-footer .tag-list li')]
                .map(node => (node.innerText || '').trim()).filter(Boolean);
              return {
                href: link?.href || '',
                title: text('.job-name'),
                company: text('.boss-name, .company-name'),
                salary_raw: text('.job-salary, .salary'),
                experience_raw: tags[0] || '',
                education_raw: tags[1] || '',
                location: text('.company-location, .job-area'),
                card_text: (el.innerText || '').trim()
              };
            }"""
        )
        data = normalize_search_card(data)
        href = urllib.parse.urljoin(BASE_URL, data.pop("href", ""))
        return {
            "platform": "boss",
            "job_id": BossCollector._job_id(href),
            "source": "boss-search",
            "source_key": f"search:{city}:{keyword}",
            "source_page": 1,
            "source_url": canonical_job_url(href),
            "card_index": index,
            **data,
        }

    def _navigate(self, page, url: str) -> None:
        BossPlatform._assert_page_safe(page)
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self._wait(page, self._navigation_delay())

    @staticmethod
    def _require_login(page) -> None:
        BossPlatform._assert_page_safe(page)
        try:
            page.wait_for_selector(SELECTORS["logged_in"], timeout=8_000)
        except PWTimeout as exc:
            BossPlatform._assert_page_safe(page)
            raise RuntimeError("Boss login was not detected in the CDP session") from exc

    @staticmethod
    def _is_detail_response(response) -> bool:
        try:
            return (
                DETAIL_RESPONSE_MARKER in response.url
                and response.request.method.upper() == "GET"
            )
        except Exception:
            return False

    @staticmethod
    def _job_id(url: str) -> str:
        match = re.search(r"/job_detail/([^/?]+?)\.html", str(url or ""))
        return match.group(1) if match else ""

    @classmethod
    def _first_job_id(cls, cards) -> str:
        if cards.count() == 0:
            return ""
        href = cards.first.locator(".job-info a.name").get_attribute("href") or ""
        return cls._job_id(href)

    def _wait(self, page, delay: list | tuple) -> None:
        BossPlatform._wait_with_safety(page, delay)

    def _navigation_delay(self):
        return self.collection_cfg.get("navigation_delay_range_seconds", [12, 18])

    def _detail_settle_delay(self):
        return self.collection_cfg.get("detail_settle_range_seconds", [8, 15])

    def _detail_delay(self):
        return self.collection_cfg.get("detail_delay_range_seconds", [35, 65])

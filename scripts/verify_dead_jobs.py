# -*- coding: utf-8 -*-
"""逐个验证 JD-empty 岗位：登录会话里打开详情页，判定是否被重定向回首页。

只读验证；结果落 E:/auto-job-agent/.workbuddy/tmp/dead_verify.json
判定规则：wait domcontentloaded + 8s 沉降后，final URL 不含 job_detail 或
.job-sec-text 不存在且 title 为首页标题 => dead(redirect)；
.job-sec-text 有正文 => alive。
"""
import json
import time
from collections import Counter

from playwright.sync_api import sync_playwright

JOBS = json.load(open(r"E:/auto-job-agent/.workbuddy/tmp/dead_jobs.json", encoding="utf-8"))
OUT = r"E:/auto-job-agent/.workbuddy/tmp/dead_verify.json"

results = {}
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:17332")
    ctx = b.contexts[0]
    for i, jid in enumerate(JOBS, 1):
        page = ctx.new_page()
        verdict = "error"
        try:
            page.goto(
                f"https://www.zhipin.com/job_detail/{jid}.html",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            time.sleep(8)
            url = page.url
            jd_text = ""
            for _ in range(3):
                try:
                    el = page.query_selector(".job-sec-text")
                    if el:
                        jd_text = el.inner_text().strip()
                        break
                except Exception:
                    time.sleep(3)
            if "job_detail" not in url:
                verdict = "dead_redirect"
            elif jd_text:
                verdict = "alive"
            else:
                verdict = "empty_no_redirect"
        except Exception as e:
            verdict = f"error:{str(e)[:60]}"
        finally:
            try:
                page.close()
            except Exception:
                pass
        results[jid] = verdict
        print(f"{i}/{len(JOBS)} {jid[:12]} {verdict}", flush=True)
        time.sleep(2)
    b.close()

json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("SUMMARY:", dict(Counter(results.values())))

"""干净探测：搜索列表页的卡片数与分页/加载更多机制。

要点：记录实际 URL、等待渲染、统计卡片数、找下一页/加载更多入口。
"""
from playwright.sync_api import sync_playwright

URL = "https://www.zhipin.com/web/geek/jobs?query=%E5%A4%A7%E6%A8%A1%E5%9E%8B&city=101280800"
CARD = "li.job-card-wrapper, li.job-card-box"


def stat(page):
    return page.evaluate(
        """(sel) => {
            const cards = document.querySelectorAll(sel);
            const hrefs = Array.from(cards).map(c => {
                const a = c.querySelector('a[href*="job_detail"]');
                return a ? a.href : '';
            }).filter(Boolean);
            return {count: cards.length, hrefs: hrefs.length,
                    scrollY: window.scrollY, height: document.body.scrollHeight};
        }""",
        CARD,
    )


with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:17332")
    ctx = browser.contexts[0]
    page = ctx.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        print("URL:", page.url)
        print("标题:", page.title())
        print("初次:", stat(page))

        # 滚动加载：分步滚动，观察卡片数是否增长
        last = -1
        for i in range(6):
            page.mouse.wheel(0, 8000)
            page.wait_for_timeout(2500)
            now = stat(page)
            print(f"  滚动{i+1}: 卡片={now['count']} 滚动位置={now['scrollY']} 页高={now['height']}")
            if now["count"] == last and i >= 2:
                break
            last = now["count"]

        # 分页 DOM 全量特征
        dom = page.evaluate(
            """() => {
                const out = {pageLinks: [], moreBtns: [], footer: ''};
                document.querySelectorAll('a, button, div, span').forEach(el => {
                    if (!el.offsetParent) return;
                    const t = (el.innerText || '').trim();
                    if (/^(\\d+|下一页|加载更多|查看更多)$/.test(t) && t.length <= 6) {
                        out.pageLinks.push(el.tagName + '.' + String(el.className || '').slice(0, 40) + '|' + t);
                    }
                    if (/加载更多|查看更多|load.?more/i.test(t)) out.moreBtns.push(t.slice(0, 30));
                });
                out.pageLinks = [...new Set(out.pageLinks)].slice(0, 15);
                out.moreBtns = [...new Set(out.moreBtns)].slice(0, 5);
                return out;
            }"""
        )
        print("分页链接候选:", dom["pageLinks"])
        print("加载更多:", dom["moreBtns"])
    finally:
        page.close()

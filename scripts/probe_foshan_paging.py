"""验证佛山城市码 + 探搜索页翻页入口（只读探测，不改任何数据）。"""
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:17332"
URL = "https://www.zhipin.com/web/geek/jobs?query=AI%E5%BA%94%E7%94%A8&city=101280800"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(CDP)
    ctx = browser.contexts[0]
    page = ctx.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        # 城市名：页面筛选区会显示当前城市
        city_text = page.evaluate(
            """() => {
                const el = document.querySelector('.city-label, .current-city, .filter-city, .city-name');
                return el ? el.innerText.trim() : '';
            }"""
        )
        title = page.title()
        first_card = page.evaluate(
            """() => {
                const c = document.querySelector('li.job-card-wrapper, li.job-card-box');
                if (!c) return null;
                const name = c.querySelector('.job-name');
                const area = c.querySelector('.job-area, .company-location, .job-card-right .job-area');
                return {job: name ? name.innerText.trim() : '', area: area ? area.innerText.trim() : ''};
            }"""
        )
        # 翻页入口
        pages = page.evaluate(
            """() => {
                const links = Array.from(document.querySelectorAll('.options-pages a, .options-pages span'));
                return links.map(a => ({text: a.innerText.trim(), cls: a.className}));
            }"""
        )
        next_btn = page.locator(".options-pages a:has(i.ui-icon-arrow-right)").last
        print("页面标题:", title)
        print("城市标签:", repr(city_text))
        print("首卡:", first_card)
        print("分页元素:", pages[:12])
        print("下一页按钮存在:", next_btn.count(), "| class:", (next_btn.get_attribute("class") if next_btn.count() else None))
    finally:
        page.close()

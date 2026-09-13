"""筛出「过期嫌疑」岗位：active 但列表扫描久未见到的岗位。

背景：收藏夹会保留已下架岗位，列表在场不等于真在招；反过来扫描多次未见
是很强的过期信号。本脚本把这类岗位单独筛出来，供逐条复核，避免它们混在
可投清单里浪费决策精力。

嫌疑分级（只读，不改库）：
- 高：scan_state=likely_closed（连续未见次数已达阈值）
- 中：scan_state=unseen，或 last_seen 超过 30 天
- 低：last_seen 超过 60 天但列表仍见过

输出：output/过期嫌疑岗位-YYYYMMDD.html
复核落库（确证关闭）由 --refresh-due 或后续 sweep 执行器完成，不在本脚本内。
"""

import html
import sqlite3
from datetime import datetime
from pathlib import Path

DB = r"E:\auto-job-agent\output\jobagent.db"
OUT_DIR = Path(r"E:\auto-job-agent\output")


def suspect_level(scan_state: str, miss: int, last_seen: str) -> str:
    if scan_state == "likely_closed" or miss >= 3:
        return "高"
    if scan_state == "unseen":
        return "中"
    return "低"


def days_since(value: str) -> float:
    if not value:
        return 9999.0
    try:
        seen = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 9999.0
    now = datetime.now(seen.tzinfo) if seen.tzinfo else datetime.now()
    return (now - seen).total_seconds() / 86400


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    version = conn.execute("SELECT MAX(analysis_version) FROM job_match_analyses").fetchone()[0]
    scores: dict[tuple, tuple[int, str]] = {}
    for row in conn.execute(
        "SELECT platform, job_id, score, recommendation, track FROM job_match_analyses "
        "WHERE analysis_version=? ORDER BY score DESC",
        (version,),
    ):
        key = (row["platform"], row["job_id"])
        if key not in scores:
            scores[key] = (int(row["score"]), str(row["recommendation"]))

    source_by_job: dict[tuple, str] = {}
    for row in conn.execute(
        "SELECT platform, job_id, GROUP_CONCAT(DISTINCT source) src FROM job_observations "
        "GROUP BY platform, job_id"
    ):
        src = str(row["src"] or "")
        label = []
        if "favorites" in src:
            label.append("收藏")
        if "search" in src:
            label.append("搜索")
        if "refresh" in src:
            label.append("复核")
        source_by_job[(row["platform"], row["job_id"])] = "/".join(label) or src

    details = {
        (r["platform"], r["job_id"]): dict(r)
        for r in conn.execute("SELECT * FROM job_details")
    }

    rows = []
    for fresh in conn.execute(
        "SELECT * FROM job_freshness WHERE evidence_state='active'"
    ):
        key = (fresh["platform"], fresh["job_id"])
        miss = int(fresh["scan_miss_count"] or 0)
        state = str(fresh["scan_state"] or "")
        last_seen = str(fresh["last_seen_at"] or "")
        gap = days_since(last_seen)
        if state == "listed" and gap <= 30 and miss == 0:
            continue  # 近期在列表出现，不算嫌疑
        level = suspect_level(state, miss, last_seen)
        if level == "低" and gap <= 60:
            continue
        score, rec = scores.get(key, (0, ""))
        detail = details.get(key, {})
        rows.append(
            {
                "level": level,
                "score": score,
                "rec": rec,
                "title": detail.get("title") or "",
                "company": detail.get("company") or "",
                "location": detail.get("location") or "",
                "salary": detail.get("salary_raw") or "",
                "url": detail.get("source_url") or "",
                "source": source_by_job.get(key, ""),
                "miss": miss,
                "scan_state": state,
                "last_seen": last_seen[:10],
                "gap_days": int(gap) if gap < 9999 else -1,
                "closure": fresh["closure_reason"] or "",
            }
        )
    order = {"高": 0, "中": 1, "低": 2}
    rows.sort(key=lambda item: (order[item["level"]], -item["score"]))

    counts = {level: sum(1 for r in rows if r["level"] == level) for level in ("高", "中", "低")}
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>过期嫌疑岗位</title><style>",
        "body{font-family:'Microsoft YaHei',system-ui,sans-serif;margin:24px;background:#fafafa}",
        "table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}",
        "th,td{border:1px solid #ddd;padding:7px 9px;text-align:left}",
        "th{background:#f0f0f0}.lv高{color:#c0392b;font-weight:700}.lv中{color:#e67e22}.lv低{color:#7f8c8d}",
        "a{color:#1a63b8;text-decoration:none}",
        "</style></head><body>",
        "<h1>过期嫌疑岗位</h1>",
        f"<p>生成 {datetime.now():%Y-%m-%d %H:%M}　高 {counts['高']}　中 {counts['中']}　低 {counts['低']}　共 <b>{len(rows)}</b> 个</p>",
        "<p>判定依据：扫描未见次数、最近一次在列表出现的时间。复核确证关闭后才会真正落 closed，本清单只做筛选。</p>",
        "<table><thead><tr><th>嫌疑</th><th>分</th><th>岗位</th><th>公司</th><th>城市</th><th>薪资</th><th>来源</th>"
        "<th>未见次数</th><th>扫描态</th><th>最后出现</th><th>链接</th></tr></thead><tbody>",
    ]
    for r in rows:
        link = f"<a href='{html.escape(r['url'])}' target='_blank'>打开</a>" if r["url"] else "—"
        parts.append(
            "<tr>"
            f"<td class='lv{r['level']}'>{r['level']}</td>"
            f"<td>{r['score']}</td>"
            f"<td>{html.escape(str(r['title']))}</td>"
            f"<td>{html.escape(str(r['company']))}</td>"
            f"<td>{html.escape(str(r['location']))}</td>"
            f"<td>{html.escape(str(r['salary']))}</td>"
            f"<td>{html.escape(str(r['source']))}</td>"
            f"<td>{r['miss']}</td>"
            f"<td>{html.escape(r['scan_state'])}</td>"
            f"<td>{r['last_seen']}{'（'+str(r['gap_days'])+'天前）' if r['gap_days']>=0 else ''}</td>"
            f"<td>{link}</td></tr>"
        )
    parts.append("</tbody></table></body></html>")
    out = OUT_DIR / f"过期嫌疑岗位-{datetime.now():%Y%m%d}.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"过期嫌疑 {len(rows)} 个（高 {counts['高']} / 中 {counts['中']} / 低 {counts['低']}）-> {out}")
    conn.close()


if __name__ == "__main__":
    main()

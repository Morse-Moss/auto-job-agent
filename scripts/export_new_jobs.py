"""导出某一轮采集新增的岗位原始清单（不评分，纯列表）。"""

import html
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

DB = r"E:\auto-job-agent\output\jobagent.db"
OUT_DIR = Path(r"E:\auto-job-agent\output")


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    run = conn.execute(
        "SELECT run_id, started_at, finished_at FROM detail_collection_runs "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    rows = conn.execute(
        "SELECT * FROM job_details WHERE source_run_id=? ORDER BY location, company",
        (run["run_id"],),
    ).fetchall()

    city_counter = Counter(str(r["location"] or "").split("·")[0] or "未知" for r in rows)
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'><title>本轮新增岗位</title><style>",
        "body{font-family:'Microsoft YaHei',system-ui,sans-serif;margin:24px;background:#fafafa}",
        "table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}",
        "th,td{border:1px solid #ddd;padding:7px 9px;text-align:left}",
        "th{background:#f0f0f0;position:sticky;top:0}",
        "a{color:#1a63b8;text-decoration:none}",
        "</style></head><body>",
        "<h1>本轮采集新增岗位（未评分）</h1>",
        f"<p>采集完成 {str(run['finished_at'] or '')[:16]}　共 <b>{len(rows)}</b> 个　"
        + "　".join(f"{k} {v}" for k, v in city_counter.most_common())
        + "</p>",
        "<table><thead><tr><th>岗位</th><th>公司</th><th>城市</th><th>薪资</th><th>经验/学历</th>"
        "<th>JD 字数</th><th>链接</th></tr></thead><tbody>",
    ]
    for r in rows:
        jd_len = len(str(r["jd_text"] or ""))
        link = (
            f"<a href='{html.escape(str(r['source_url'] or ''))}' target='_blank'>打开</a>"
            if r["source_url"]
            else "—"
        )
        parts.append(
            "<tr>"
            f"<td>{html.escape(str(r['title'] or ''))}</td>"
            f"<td>{html.escape(str(r['company'] or ''))}</td>"
            f"<td>{html.escape(str(r['location'] or ''))}</td>"
            f"<td>{html.escape(str(r['salary_raw'] or ''))}</td>"
            f"<td>{html.escape(str(r['experience_raw'] or ''))}/{html.escape(str(r['education_raw'] or ''))}</td>"
            f"<td>{jd_len}</td><td>{link}</td></tr>"
        )
    parts.append("</tbody></table></body></html>")
    out = OUT_DIR / f"本轮新增岗位-{datetime.now():%Y%m%d}.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"{len(rows)} 个新增岗位 -> {out}")
    conn.close()


if __name__ == "__main__":
    main()

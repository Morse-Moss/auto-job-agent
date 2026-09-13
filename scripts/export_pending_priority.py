"""导出「未决策 + 活跃 + 优先档」岗位清单为可过审的 HTML。

用途：存量池里大量高分岗位从未决策，是"能投岗位太少"的直接原因。
输出：output/待决策优先岗位-YYYYMMDD.html（中文表头、分数分色、可点击 Boss 链接）。
只读数据库，不改动任何状态。
"""

import html
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

DB = r"E:\auto-job-agent\output\jobagent.db"
OUT_DIR = Path(r"E:\auto-job-agent\output")

TRACK_LABELS = {
    "ai_application": "AI 应用",
    "agent_engineering": "Agent 工程",
    "fde": "FDE/交付",
    "solution_delivery": "方案交付",
    "rag_engineering": "RAG",
    "product": "AI 产品",
    "pure_java": "Java",
    "sales": "销售",
    "other": "其他",
}


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    version = conn.execute("SELECT MAX(analysis_version) FROM job_match_analyses").fetchone()[0]

    best: dict[tuple, dict] = {}
    for row in conn.execute(
        "SELECT platform, job_id, score, recommendation, track, strengths_json, gaps_json, "
        "uncertainties_json, confidence, information_status FROM job_match_analyses "
        "WHERE analysis_version=? ORDER BY score DESC",
        (version,),
    ):
        key = (row["platform"], row["job_id"])
        if key not in best:
            best[key] = dict(row)

    decided = {
        (r["platform"], r["job_id"])
        for r in conn.execute("SELECT platform, job_id FROM job_decisions")
    }
    # 可信在架：近期在招聘列表出现过、无缺失记录。
    # 过期嫌疑（扫描多次未见、久未出现）一律剔除，避免浪费决策精力。
    active = {
        (r["platform"], r["job_id"])
        for r in conn.execute(
            "SELECT platform, job_id FROM job_freshness "
            "WHERE evidence_state='active' AND scan_state='listed' "
            "AND COALESCE(scan_miss_count, 0) = 0 "
            "AND last_seen_at >= datetime('now', '-30 day')"
        )
    }
    source_by_job: dict[tuple, str] = {}
    for row in conn.execute(
        "SELECT platform, job_id, GROUP_CONCAT(DISTINCT source) src "
        "FROM job_observations GROUP BY platform, job_id"
    ):
        src = str(row["src"] or "")
        marks = []
        if "favorites" in src:
            marks.append("收藏")
        if "search" in src:
            marks.append("搜索")
        if "refresh" in src:
            marks.append("复核")
        source_by_job[(row["platform"], row["job_id"])] = "/".join(marks) or src
    details = {
        (r["platform"], r["job_id"]): dict(r)
        for r in conn.execute("SELECT * FROM job_details")
    }

    rows = []
    for key, match in best.items():
        if key in decided or key not in active:
            continue
        if match["recommendation"] != "priority":
            continue
        detail = details.get(key, {})
        rows.append((match, detail))
    rows.sort(key=lambda item: (-int(item[0]["score"]), item[1].get("company") or ""))

    city_counter = Counter()
    for _, detail in rows:
        loc = str(detail.get("location") or "")
        city_counter[loc.split("·")[0] or "未知"] += 1

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>待决策优先岗位清单</title><style>",
        "body{font-family:'Microsoft YaHei',system-ui,sans-serif;margin:24px;background:#fafafa;color:#222}",
        "h1{font-size:20px} .meta{color:#666;font-size:13px;margin-bottom:12px}",
        "table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}",
        "th,td{border:1px solid #ddd;padding:7px 9px;text-align:left;vertical-align:top}",
        "th{background:#f0f0f0;position:sticky;top:0}",
        "tr:hover{background:#f7fbff}",
        ".s90{color:#c0392b;font-weight:700}.s80{color:#e67e22;font-weight:700}.s70{color:#2980b9}",
        ".track{display:inline-block;padding:1px 6px;border-radius:3px;background:#eef2f7;color:#345;font-size:12px}",
        ".strength{color:#1e7d34}.gap{color:#b9770e}",
        "a{color:#1a63b8;text-decoration:none} a:hover{text-decoration:underline}",
        "</style></head><body>",
        "<h1>待决策优先岗位清单</h1>",
        f"<p class='meta'>生成时间 {datetime.now():%Y-%m-%d %H:%M}　·　条件：未决策 + Boss 在架 + 匹配优先档　·　共 <b>{len(rows)}</b> 个</p>",
        "<p class='meta'>城市分布：" + "　".join(f"{k} {v}" for k, v in city_counter.most_common()) + "</p>",
        "<table><thead><tr>",
        "<th>分</th><th>赛道</th><th>岗位</th><th>公司</th><th>城市</th><th>薪资</th><th>经验/学历</th><th>来源</th><th>匹配亮点</th><th>缺口</th><th>链接</th>",
        "</tr></thead><tbody>",
    ]
    for match, detail in rows:
        score = int(match["score"])
        cls = "s90" if score >= 90 else ("s80" if score >= 80 else "s70")
        strengths = json.loads(match["strengths_json"] or "[]")
        gaps = json.loads(match["gaps_json"] or "[]")
        gap_text = "；".join(str(g.get("label")) for g in gaps[:2]) or "—"
        strength_text = "；".join(str(s.get("label")) for s in strengths[:3]) or "—"
        url = str(detail.get("source_url") or "")
        title = str(detail.get("title") or "（缺标题）")
        link = f"<a href='{html.escape(url)}' target='_blank'>打开</a>" if url else "—"
        parts.append(
            "<tr>"
            f"<td class='{cls}'>{score}</td>"
            f"<td><span class='track'>{TRACK_LABELS.get(str(match['track']), match['track'])}</span></td>"
            f"<td>{html.escape(title)}</td>"
            f"<td>{html.escape(str(detail.get('company') or ''))}</td>"
            f"<td>{html.escape(str(detail.get('location') or ''))}</td>"
            f"<td>{html.escape(str(detail.get('salary_raw') or ''))}</td>"
            f"<td>{html.escape(str(detail.get('experience_raw') or ''))}/{html.escape(str(detail.get('education_raw') or ''))}</td>"
            f"<td>{html.escape(source_by_job.get((match['platform'], match['job_id']), ''))}</td>"
            f"<td class='strength'>{html.escape(strength_text)}</td>"
            f"<td class='gap'>{html.escape(gap_text)}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    parts.append("</tbody></table></body></html>")

    out = OUT_DIR / f"待决策优先岗位-{datetime.now():%Y%m%d}.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"已导出 {len(rows)} 个岗位 -> {out}")
    conn.close()


if __name__ == "__main__":
    main()

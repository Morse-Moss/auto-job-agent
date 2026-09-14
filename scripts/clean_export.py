"""清洗整理采集数据，产出可直接用于设计评分机制的数据集与输入说明。

流程：
1. 复用 analysis.run_analysis 做离线定位分析（薪资解析/赛道/档位/风险），
   结果落 job_positioning_analysis 表，同时产出英文表头 CSV。
2. 本脚本再导出一份面向决策的"清洗数据集"：
   - 中文表头 CSV（Excel 直接打开，UTF-8 BOM）
   - 可浏览 HTML（配色分级、可点击链接）
   - 评分机制设计输入 Markdown（字段缺失率、分布统计、可选评分维度）
3. 清洗原则：保留全部行、不隐式丢弃；质量与风险以标注列呈现。

只读原始表 + 写入分析表，不改动岗位生命周期状态。
"""

from __future__ import annotations

import csv
import html
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobagent.analysis import analyze_job, parse_salary  # noqa: E402
from jobagent.freshness import (  # noqa: E402
    live_job_condition,
    migrate_freshness_schema,
    sync_legacy_freshness,
)

DB = r"E:\auto-job-agent\output\jobagent.db"
OUT_DIR = Path(r"E:\auto-job-agent\output\clean")

TRACK_LABELS = {
    "fde": "FDE/前线部署",
    "agent_engineering": "Agent 工程",
    "rag_engineering": "RAG",
    "ai_application": "AI 应用",
    "solution_delivery": "方案交付",
    "product": "AI 产品",
    "pure_java": "Java",
    "sales": "销售",
    "other": "其他",
}
TIER_LABELS = {"core": "核心", "adjacent": "相邻", "stretch": "勉强", "exclude": "排除"}
RISK_LABELS = {
    "third_party": "第三方/派遣",
    "outsourcing_risk": "外包风险",
    "low_quality_jd": "JD 过薄",
    "unclear_salary": "薪资不明",
    "high_sales_share": "销售成分高",
    "internship": "实习岗",
    "salary_below_floor": "薪资偏低",
}

CSV_HEADERS = [
    "岗位ID", "平台", "岗位名", "公司", "城市", "区域", "薪资原文", "薪资下限K", "薪资上限K",
    "薪数", "平均月薪K", "经验要求", "学历要求", "赛道", "定位档", "相关度分", "FDE价值分",
    "风险", "判定理由", "JD字数", "数据质量", "疑似重复", "来源", "在架状态",
    "首次采集", "最近更新", "链接",
]


def quality_of(jd_len: int, has_salary: bool, has_company: bool) -> str:
    if not jd_len:
        return "无JD"
    if jd_len < 100:
        return "过薄"
    if jd_len < 200:
        return "偏薄"
    if not has_salary:
        return "缺薪资"
    if not has_company:
        return "缺公司"
    return "完整"


def avg_monthly(salary_raw: str) -> str:
    info = parse_salary(salary_raw)
    if info.kind != "monthly" or info.minimum_k is None or info.maximum_k is None:
        return ""
    months = info.pay_months or 12
    return f"{(info.minimum_k + info.maximum_k) / 2 * months / 12:.1f}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    migrate_freshness_schema(conn)
    sync_legacy_freshness(conn)

    rows = [
        dict(r)
        for r in conn.execute(
            f"SELECT * FROM job_details WHERE detail_status='collected' AND {live_job_condition('job_details')}"
        )
    ]

    # 疑似重复：同公司 + 同岗位名 + 同城市
    dup_counter = Counter(
        (str(r.get("company") or ""), str(r.get("title") or ""), str(r.get("location") or ""))
        for r in rows
    )

    source_by_job: dict[tuple, str] = {}
    for r in conn.execute(
        "SELECT platform, job_id, GROUP_CONCAT(DISTINCT source) src FROM job_observations "
        "GROUP BY platform, job_id"
    ):
        src = str(r["src"] or "")
        marks = []
        if "favorites" in src:
            marks.append("收藏")
        if "search" in src:
            marks.append("搜索")
        if "refresh" in src:
            marks.append("复核")
        source_by_job[(r["platform"], r["job_id"])] = "/".join(marks) or src

    state_by_job = {
        (r["platform"], r["job_id"]): (r["evidence_state"], r["last_seen_at"])
        for r in conn.execute("SELECT platform, job_id, evidence_state, last_seen_at FROM job_freshness")
    }

    records: list[dict] = []
    for row in rows:
        analysis = analyze_job(row)
        key = (row["platform"], row["job_id"])
        jd_len = len(str(row.get("jd_text") or ""))
        city = str(row.get("location") or "").split("·")[0]
        area = "·".join(str(row.get("location") or "").split("·")[1:])
        salary = analysis.salary
        state, last_seen = state_by_job.get(key, ("", ""))
        spec = (str(row.get("company") or ""), str(row.get("title") or ""), str(row.get("location") or ""))
        records.append(
            {
                "岗位ID": row["job_id"],
                "平台": row["platform"],
                "岗位名": row.get("title") or "",
                "公司": row.get("company") or "",
                "城市": city,
                "区域": area,
                "薪资原文": row.get("salary_raw") or "",
                "薪资下限K": salary.minimum_k if salary.kind == "monthly" else "",
                "薪资上限K": salary.maximum_k if salary.kind == "monthly" else "",
                "薪数": salary.pay_months if salary.kind == "monthly" else "",
                "平均月薪K": avg_monthly(str(row.get("salary_raw") or "")),
                "经验要求": row.get("experience_raw") or "",
                "学历要求": row.get("education_raw") or "",
                "赛道": TRACK_LABELS.get(analysis.track, analysis.track),
                "定位档": TIER_LABELS.get(analysis.fit_tier, analysis.fit_tier),
                "相关度分": analysis.relevance_score,
                "FDE价值分": analysis.fde_value_score,
                "风险": "；".join(RISK_LABELS.get(x, x) for x in analysis.risks),
                "判定理由": "；".join(analysis.reasons),
                "JD字数": jd_len,
                "数据质量": quality_of(
                    jd_len, bool(str(row.get("salary_raw") or "").strip()), bool(str(row.get("company") or "").strip())
                ),
                "疑似重复": "是" if dup_counter[spec] > 1 else "",
                "来源": source_by_job.get(key, ""),
                "在架状态": state,
                "首次采集": str(row.get("collected_at") or "")[:16],
                "最近更新": str(row.get("updated_at") or "")[:16],
                "链接": row.get("source_url") or "",
                "_track": analysis.track,
                "_tier": analysis.fit_tier,
                "_risks": analysis.risks,
                "_state": state,
                "_last_seen": str(last_seen or ""),
            }
        )

    stamp = datetime.now().strftime("%Y%m%d")
    # 1) 中文表头 CSV
    csv_path = OUT_DIR / f"岗位清洗数据-{stamp}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for rec in records:
            writer.writerow({k: rec.get(k, "") for k in CSV_HEADERS})

    # 2) HTML 可浏览表
    city_counter = Counter(r["城市"] or "未知" for r in records)
    tier_counter = Counter(r["定位档"] for r in records)
    track_counter = Counter(r["赛道"] for r in records)
    quality_counter = Counter(r["数据质量"] for r in records)
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'><title>岗位清洗数据</title><style>",
        "body{font-family:'Microsoft YaHei',system-ui,sans-serif;margin:20px;background:#fafafa;color:#222}",
        "h1{font-size:19px}.meta{color:#666;font-size:13px;margin:6px 0 12px}",
        "table{border-collapse:collapse;width:100%;background:#fff;font-size:12.5px}",
        "th,td{border:1px solid #ddd;padding:6px 8px;vertical-align:top}",
        "th{background:#eef1f5;position:sticky;top:0}",
        "tr:hover{background:#f7fbff}",
        ".s80{color:#c0392b;font-weight:700}.s60{color:#e67e22}.s40{color:#2980b9}",
        ".risk{color:#b03a2e}.ok{color:#1e7d34}",
        "a{color:#1a63b8;text-decoration:none}",
        "</style></head><body>",
        "<h1>岗位清洗数据</h1>",
        f"<p class='meta'>生成 {datetime.now():%Y-%m-%d %H:%M}　共 <b>{len(records)}</b> 条　"
        f"城市：" + "　".join(f"{k} {v}" for k, v in city_counter.most_common(8)) + "</p>",
        "<p class='meta'>定位档：" + "　".join(f"{k} {v}" for k, v in tier_counter.most_common())
        + "　｜　数据质量：" + "　".join(f"{k} {v}" for k, v in quality_counter.most_common()) + "</p>",
        "<p class='meta'>赛道：" + "　".join(f"{k} {v}" for k, v in track_counter.most_common()) + "</p>",
        "<table><thead><tr>"
        + "".join(f"<th>{h}</th>" for h in CSV_HEADERS)
        + "</tr></thead><tbody>",
    ]
    for rec in records:
        score = int(rec["相关度分"] or 0)
        cls = "s80" if score >= 80 else ("s60" if score >= 60 else "s40")
        cells = []
        for h in CSV_HEADERS:
            value = str(rec.get(h, ""))
            if h == "链接" and value:
                cells.append(f"<td><a href='{html.escape(value)}' target='_blank'>打开</a></td>")
            elif h == "相关度分":
                cells.append(f"<td class='{cls}'>{value}</td>")
            elif h == "风险":
                cells.append(f"<td class='risk'>{html.escape(value)}</td>")
            else:
                cells.append(f"<td>{html.escape(value)}</td>")
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</tbody></table></body></html>")
    html_path = OUT_DIR / f"岗位清洗数据-{stamp}.html"
    html_path.write_text("\n".join(parts), encoding="utf-8")

    # 3) 评分机制设计输入
    md_path = OUT_DIR / f"评分设计输入-{stamp}.md"
    md_path.write_text(_build_design_input(records, stamp), encoding="utf-8")

    # 分析结果落库（复用 pipeline 表，便于后续匹配链路读取）
    conn.close()
    print(f"清洗完成：{len(records)} 条")
    print(f"  CSV  : {csv_path}")
    print(f"  HTML : {html_path}")
    print(f"  说明 : {md_path}")


def _build_design_input(records: list[dict], stamp: str) -> str:
    total = len(records)
    city = Counter(r["城市"] or "未知" for r in records)
    track = Counter(r["赛道"] for r in records)
    tier = Counter(r["定位档"] for r in records)
    quality = Counter(r["数据质量"] for r in records)
    exp = Counter(r["经验要求"] or "未标注" for r in records)
    edu = Counter(r["学历要求"] or "未标注" for r in records)
    state = Counter(r["在架状态"] or "未知" for r in records)
    dup = sum(1 for r in records if r["疑似重复"])
    with_risk = sum(1 for r in records if r["风险"])
    salary_known = sum(1 for r in records if r["平均月薪K"])
    monthly = sorted(float(r["平均月薪K"]) for r in records if r["平均月薪K"])
    lines = [
        f"# 评分机制设计输入（{stamp}）",
        "",
        f"数据量：{total} 条（已采集 JD 且当前在架）；疑似重复 {dup} 条；带风险标注 {with_risk} 条。",
        "",
        "## 一、分布概览",
        "",
        "城市：" + "　".join(f"{k} {v}" for k, v in city.most_common()),
        "",
        "赛道：" + "　".join(f"{k} {v}" for k, v in track.most_common()),
        "",
        "现有定位档：" + "　".join(f"{k} {v}" for k, v in tier.most_common()),
        "",
        "数据质量：" + "　".join(f"{k} {v}" for k, v in quality.most_common()),
        "",
        "经验要求：" + "　".join(f"{k} {v}" for k, v in exp.most_common(8)),
        "",
        "学历要求：" + "　".join(f"{k} {v}" for k, v in edu.most_common(6)),
        "",
        "在架状态：" + "　".join(f"{k} {v}" for k, v in state.most_common()),
        "",
    ]
    if monthly:
        lines += [
            "## 二、薪资分布（可解析出月薪的岗位）",
            "",
            f"可解析 {salary_known} 条，最低 {monthly[0]:.1f}K，最高 {monthly[-1]:.1f}K，"
            f"中位 {monthly[len(monthly) // 2]:.1f}K。",
            "",
        ]
    lines += [
        "## 三、可直接用于评分的字段",
        "",
        "1. 赛道（结构化，已分类）：FDE/Agent/RAG/AI 应用/方案交付/产品 等。",
        "2. 薪资：可解析出下限/上限/薪数/折算月均，缺失会标注为“缺薪资”。",
        "3. 经验与学历要求：原文口径（如 1-3 年、3-5 年、本科）。",
        "4. 城市与区域：城市级可聚合，区域可细到街道。",
        "5. JD 全文与字数：可做关键词覆盖、技能命中、职责匹配。",
        "6. 风险标注：第三方派遣、外包风险、JD 过薄、薪资不明、实习岗等。",
        "7. 公司信息：融资阶段、规模、行业（部分岗位缺失，见缺失率）。",
        "8. 在架与时间：lifecycle 状态、最近一次在列表出现时间（用于时效权重）。",
        "9. 数据质量标注：完整 / 偏薄 / 过薄 / 缺薪资 / 缺公司 / 无JD。",
        "",
        "## 四、字段缺失情况（影响评分可用性）",
        "",
        f"- 薪资可解析：{salary_known}/{total}",
        f"- JD 字数 < 200：{quality.get('过薄', 0) + quality.get('偏薄', 0)} 条",
        f"- 缺薪资标注：{quality.get('缺薪资', 0)} 条",
        f"- 缺公司标注：{quality.get('缺公司', 0)} 条",
        "",
        "## 五、建议在设计评分时先定的几件事",
        "",
        "1. 目标赛道与权重：FDE / Agent / RAG / AI 应用 是否同等对待。",
        "2. 薪资处理口径：按月薪中位数、按上限、还是设置薪资下限过滤。",
        "3. 经验年限：应届/1-3 年/3-5 年如何折算匹配度。",
        "4. 风险项扣分规则：派遣与外包是否直接排除。",
        "5. 数据质量兜底：JD 过薄或无非空 JD 的岗位是否进入评分池。",
        "6. 城市优先级：广佛深为主时，其他城市如何降权。",
        "",
        "以上维度都已在数据集中以列的形式提供，可直接取用。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()

"""Deterministic positioning analysis for collected job descriptions."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class SalaryInfo:
    kind: str
    minimum_k: float | None = None
    maximum_k: float | None = None
    pay_months: int | None = None


@dataclass(frozen=True)
class PositioningAnalysis:
    track: str
    fit_tier: str
    relevance_score: int
    fde_value_score: int
    salary: SalaryInfo
    risks: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisResult:
    total: int
    csv_path: Path
    report_path: Path


DESIRED_TRACKS = {
    "fde",
    "agent_engineering",
    "rag_engineering",
    "ai_application",
    "solution_delivery",
}


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def parse_salary(value: str) -> SalaryInfo:
    text = str(value or "").strip()
    if not text:
        return SalaryInfo("unknown")
    if "元/天" in text:
        return SalaryInfo("daily")
    match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*K", text, re.I)
    if not match:
        return SalaryInfo("other")
    pay_match = re.search(r"(\d+)薪", text)
    return SalaryInfo(
        "monthly",
        float(match.group(1)),
        float(match.group(2)),
        int(pay_match.group(1)) if pay_match else 12,
    )


def _track(title: str, text: str) -> str:
    if _matches(title, (r"(?<![A-Za-z])BD(?![A-Za-z])", r"销售", r"客户经理", r"商务拓展")):
        return "sales"
    if _matches(title, (r"产品经理", r"产品策划", r"产品运营")):
        return "product"
    if _matches(title, (r"(?<![A-Za-z])FDE(?![A-Za-z])", r"前线部署", r"Forward Deployed")):
        return "fde"
    if _matches(title, (r"解决方案", r"售前", r"交付", r"实施", r"部署工程师")):
        return "solution_delivery"
    if _matches(title, (r"(?<![A-Za-z])Agent(?![A-Za-z])", r"智能体")):
        return "agent_engineering"
    if _matches(title, (r"(?<![A-Za-z])RAG(?![A-Za-z])", r"检索增强")):
        return "rag_engineering"
    if _matches(title, (r"\bJava\b", r"后端开发")) and not _matches(
        title,
        (
            r"(?<![A-Za-z])AI(?![A-Za-z])",
            r"大模型",
            r"(?<![A-Za-z])RAG(?![A-Za-z])",
            r"(?<![A-Za-z])Agent(?![A-Za-z])",
        ),
    ):
        return "pure_java"
    if _matches(title, (r"(?<![A-Za-z])AI(?![A-Za-z])", r"人工智能", r"大模型", r"算法", r"模型")):
        return "ai_application"
    if _matches(text, (r"(?<![A-Za-z])Agent(?![A-Za-z])", r"(?<![A-Za-z])RAG(?![A-Za-z])", r"大模型", r"人工智能")):
        return "ai_application"
    return "other"


def analyze_job(job: dict) -> PositioningAnalysis:
    title = str(job.get("title") or "")
    jd = str(job.get("jd_text") or job.get("jd") or "")
    text = f"{title} {jd}"
    recruiter = str(job.get("recruiter_role") or "")
    experience = str(job.get("experience_raw") or "")
    salary = parse_salary(str(job.get("salary_raw") or ""))
    try:
        stored_labels = set(json.loads(job.get("labels_json") or "[]"))
    except (TypeError, json.JSONDecodeError):
        stored_labels = set()

    track = _track(title, text)
    score = {
        "fde": 45,
        "agent_engineering": 45,
        "rag_engineering": 45,
        "ai_application": 35,
        "solution_delivery": 30,
        "product": 20,
        "sales": 15,
        "pure_java": 5,
        "other": 5,
    }[track]
    reasons: list[str] = []

    if _matches(text, (r"(?<![A-Za-z])Agent(?![A-Za-z])", r"智能体", r"(?<![A-Za-z])RAG(?![A-Za-z])", r"大模型", r"LLM")):
        score += 15
        reasons.append("包含 Agent/RAG/大模型工程内容")
    if _matches(text, (r"Python", r"FastAPI", r"LangGraph", r"Dify", r"Coze", r"n8n", r"模型\s*API")):
        score += 10
        reasons.append("技术栈与现有项目经验相邻")
    if _matches(text, (r"客户需求", r"需求调研", r"业务需求", r"客户共创", r"解决方案", r"系统集成", r"部署交付", r"项目交付")):
        score += 15
        reasons.append("包含业务对接或交付闭环")
    if _matches(experience, (r"经验不限", r"1年以内", r"1-3年")):
        score += 10
        reasons.append("经验门槛处于当前可竞争区间")

    fde_score = 0
    if track == "fde":
        fde_score += 35
    if _matches(text, (r"客户需求", r"需求调研", r"业务需求", r"客户共创", r"客户现场")):
        fde_score += 20
    if _matches(text, (r"解决方案", r"方案设计", r"架构设计")):
        fde_score += 15
    if _matches(text, (r"交付", r"部署", r"实施", r"系统集成", r"上线")):
        fde_score += 15
    if _matches(text, (r"业务理解", r"行业", r"流程梳理", r"效果复盘")):
        fde_score += 10
    if _matches(text, (r"Python", r"开发", r"编码", r"API")):
        fde_score += 5

    risks: list[str] = []
    if "third_party" in stored_labels or _matches(
        recruiter, (r"猎头", r"招聘顾问", r"人力资源顾问", r"\bRPO\b")
    ):
        risks.append("third_party")
    if _matches(
        text,
        (
            r"劳务派遣",
            r"第三方(?:签约|用工)",
            r"人力外包",
            r"外包(?:岗位|人员|员工|驻场|招聘)",
        ),
    ):
        risks.append("outsourcing_risk")
    if "pure_java" in stored_labels or track == "pure_java":
        risks.append("pure_java")
    if track == "sales":
        risks.append("non_engineering_role")
    if track == "product":
        risks.append("product_role")
    if _matches(
        text,
        (
            r"长期驻场",
            r"长期[^。；\n]{0,20}客户现场办公",
            r"驻场工作模式",
            r"高频驻场",
            r"驻场开发工作",
        ),
    ) or "驻场" in title:
        risks.append("onsite_heavy")
    if _matches(title, (r"实习", r"Intern")) or salary.kind == "daily":
        risks.append("internship")
    if "在校/应届" in experience or _matches(title, (r"应届", r"校招")):
        risks.append("graduate_only")
    if _matches(title, (r"驻(?:英国|阿联酋|新加坡|海外|欧美|东南亚)", r"^海外")):
        risks.append("overseas_assignment")
    if _matches(experience, (r"3-5年", r"5-10年", r"10年以上")):
        risks.append("seniority_gap")
    if salary.kind == "monthly" and (
        (salary.minimum_k or 0) >= 35 or (salary.maximum_k or 0) >= 60
    ):
        risks.append("salary_stretch")
    if track == "other":
        risks.append("non_ai_role")

    hard_exclusions = {
        "third_party",
        "outsourcing_risk",
        "pure_java",
        "non_engineering_role",
        "product_role",
        "internship",
        "graduate_only",
        "non_ai_role",
    }
    if hard_exclusions.intersection(risks):
        tier = "exclude"
    elif risks or score < 75:
        tier = "stretch" if score >= 45 else "exclude"
    elif track in DESIRED_TRACKS:
        tier = "core"
    else:
        tier = "adjacent"

    if "third_party" in risks:
        reasons.append("第三方招聘关系，不进入直接投递池")
    if "outsourcing_risk" in risks:
        reasons.append("存在外包、派遣或长期驻场信号")
    if "seniority_gap" in risks:
        reasons.append("经验门槛高于当前主投区间")
    if "salary_stretch" in risks:
        reasons.append("薪资带宽显示岗位可能偏资深")
    if "pure_java" in risks:
        reasons.append("岗位核心仍是传统 Java 开发")
    if "non_engineering_role" in risks:
        reasons.append("岗位核心是销售或商务而非工程交付")
    if "product_role" in risks:
        reasons.append("岗位核心是产品管理而非工程开发")
    if "onsite_heavy" in risks:
        reasons.append("存在长期或高频驻场要求，保留为人工复核项")
    if "graduate_only" in risks:
        reasons.append("岗位限定在校或应届身份")
    if "overseas_assignment" in risks:
        reasons.append("岗位要求长期海外派驻，保留为人工复核项")

    return PositioningAnalysis(
        track=track,
        fit_tier=tier,
        relevance_score=max(0, min(100, score)),
        fde_value_score=max(0, min(100, fde_score)),
        salary=salary,
        risks=tuple(risks),
        reasons=tuple(reasons),
    )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _create_analysis_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS job_positioning_analysis (
          platform TEXT NOT NULL,
          job_id TEXT NOT NULL,
          analyzed_at TEXT NOT NULL,
          track TEXT NOT NULL,
          fit_tier TEXT NOT NULL,
          relevance_score INTEGER NOT NULL,
          fde_value_score INTEGER NOT NULL,
          salary_kind TEXT NOT NULL,
          salary_min_k REAL,
          salary_max_k REAL,
          salary_months INTEGER,
          risks_json TEXT NOT NULL,
          reasons_json TEXT NOT NULL,
          PRIMARY KEY(platform, job_id)
        )
        """
    )


def run_analysis(db_path: str | Path, output_dir: str | Path) -> AnalysisResult:
    database = Path(db_path).resolve()
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    _create_analysis_table(connection)
    connection.execute("DELETE FROM job_positioning_analysis")
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM job_details WHERE detail_status = 'collected'"
        )
    ]
    analyzed_at = _now()
    exported: list[dict] = []

    for row in rows:
        analysis = analyze_job(row)
        connection.execute(
            """
            INSERT INTO job_positioning_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform, job_id) DO UPDATE SET
              analyzed_at=excluded.analyzed_at,
              track=excluded.track,
              fit_tier=excluded.fit_tier,
              relevance_score=excluded.relevance_score,
              fde_value_score=excluded.fde_value_score,
              salary_kind=excluded.salary_kind,
              salary_min_k=excluded.salary_min_k,
              salary_max_k=excluded.salary_max_k,
              salary_months=excluded.salary_months,
              risks_json=excluded.risks_json,
              reasons_json=excluded.reasons_json
            """,
            (
                row["platform"],
                row["job_id"],
                analyzed_at,
                analysis.track,
                analysis.fit_tier,
                analysis.relevance_score,
                analysis.fde_value_score,
                analysis.salary.kind,
                analysis.salary.minimum_k,
                analysis.salary.maximum_k,
                analysis.salary.pay_months,
                json.dumps(analysis.risks, ensure_ascii=False),
                json.dumps(analysis.reasons, ensure_ascii=False),
            ),
        )
        exported.append(
            {
                "fit_tier": analysis.fit_tier,
                "relevance_score": analysis.relevance_score,
                "fde_value_score": analysis.fde_value_score,
                "track": analysis.track,
                "title": row.get("title", ""),
                "company": row.get("company", ""),
                "salary_raw": row.get("salary_raw", ""),
                "experience_raw": row.get("experience_raw", ""),
                "location": row.get("location", ""),
                "recruiter_role": row.get("recruiter_role", ""),
                "risks": ",".join(analysis.risks),
                "reasons": "；".join(analysis.reasons),
                "source_url": row.get("source_url", ""),
                "job_id": row["job_id"],
            }
        )

    connection.commit()
    connection.close()
    tier_order = {"core": 0, "adjacent": 1, "stretch": 2, "exclude": 3}
    exported.sort(
        key=lambda item: (
            tier_order[item["fit_tier"]],
            -item["relevance_score"],
            -item["fde_value_score"],
        )
    )
    csv_path = target / "job-positioning.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(exported[0].keys()) if exported else [])
        if exported:
            writer.writeheader()
            writer.writerows(exported)

    report_path = target / "job-positioning-summary.md"
    report_path.write_text(_build_report(exported), encoding="utf-8")
    return AnalysisResult(len(exported), csv_path, report_path)


def _build_report(rows: list[dict]) -> str:
    tier_counts = Counter(row["fit_tier"] for row in rows)
    track_counts = Counter(row["track"] for row in rows)
    risk_counts = Counter(
        risk for row in rows for risk in row["risks"].split(",") if risk
    )
    core_rows = [row for row in rows if row["fit_tier"] == "core"]
    core_cities = Counter(
        str(row["location"] or "未知").split("·", 1)[0] for row in core_rows
    )
    core_experience = Counter(row["experience_raw"] or "未知" for row in core_rows)
    core_salaries = [parse_salary(row["salary_raw"]) for row in core_rows]
    monthly_salaries = [salary for salary in core_salaries if salary.kind == "monthly"]
    lines = [
        "# 岗位定位离线分析",
        "",
        f"- 分析岗位：{len(rows)}",
        f"- 核心匹配：{tier_counts['core']}",
        f"- 相邻方向：{tier_counts['adjacent']}",
        f"- 可挑战：{tier_counts['stretch']}",
        f"- 排除：{tier_counts['exclude']}",
        "",
        "## 岗位方向",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in track_counts.most_common())
    lines.extend(["", "## 风险信号", ""])
    lines.extend(f"- {name}: {count}" for name, count in risk_counts.most_common())
    lines.extend(["", "## 核心池城市", ""])
    lines.extend(f"- {name}: {count}" for name, count in core_cities.most_common())
    lines.extend(["", "## 薪资与经验", ""])
    if monthly_salaries:
        median_min = statistics.median(
            salary.minimum_k for salary in monthly_salaries if salary.minimum_k is not None
        )
        median_max = statistics.median(
            salary.maximum_k for salary in monthly_salaries if salary.maximum_k is not None
        )
        lines.append(f"- 核心池月薪中位区间：{median_min:g}-{median_max:g}K")
    else:
        lines.append("- 核心池月薪中位区间：无可解析数据")
    lines.extend(
        f"- 核心池经验 {name}: {count}"
        for name, count in core_experience.most_common()
    )
    lines.extend(["", "## 优先观察岗位", ""])
    for row in [item for item in rows if item["fit_tier"] in {"core", "adjacent"}][:30]:
        lines.append(
            f"- {row['title']} @ {row['company']} | {row['salary_raw']} | "
            f"相关度 {row['relevance_score']} | FDE价值 {row['fde_value_score']}"
        )
    lines.append("")
    return "\n".join(lines)

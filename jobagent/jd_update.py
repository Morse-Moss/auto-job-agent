"""Offline JD update planning. No browser, model, or outreach dependency."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from jobagent.freshness import build_freshness_dashboard


def _budget(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 500:
        raise ValueError("每阶段预算必须为 0–500 的整数")
    return value


def _unique_strings(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def build_update_plan(
    dashboard: dict,
    cfg: dict,
    *,
    favorites_budget: int = 25,
    search_budget: int = 25,
    refresh_budget: int = 20,
) -> dict:
    """Plan independent source budgets and prioritized detail work, never execute it."""
    favorites_budget, search_budget, refresh_budget = map(
        _budget, (favorites_budget, search_budget, refresh_budget)
    )
    collection = cfg.get("collection") or {}
    boss = cfg.get("boss") or {}
    keywords = _unique_strings(collection.get("keywords") or boss.get("keywords") or [])
    cities = _unique_strings(collection.get("cities") or ([boss["city"]] if boss.get("city") else []))
    sources = [{"city": city, "keyword": keyword} for city in cities for keyword in keywords]

    candidates = {}
    scan_ids = set()
    for item in dashboard.get("refresh_plan") or []:
        job_id = str(item.get("job_id") or "").strip()
        if (item.get("platform", "boss") != "boss" or not job_id
                or not item.get("refresh_due")
                or item.get("lifecycle_state") in {"closed", "archived"}):
            continue
        action = item.get("refresh_action", "detail_refresh")
        if action == "coverage_scan":
            scan_ids.add(job_id)
        elif action == "detail_refresh":
            candidates.setdefault(job_id, item)

    intent_priority = {"want": 0, "maybe": 1}
    ordered = sorted(candidates.values(), key=lambda item: (
        intent_priority.get(item.get("interest"), 2),
        item.get("refresh_priority", 99),
        -float(item.get("age_days") or 0),
        str(item["job_id"]),
    ))
    selected = [str(item["job_id"]) for item in ordered[:refresh_budget]]
    backups = [
        str(item["job_id"])
        for item in ordered[refresh_budget : refresh_budget * 2]
    ]
    remaining = len(ordered) - len(selected)
    summary = dashboard.get("summary") or {}
    return {
        "schema_version": "jd-update-plan-v1",
        "as_of": summary.get("as_of"),
        "status": "planned",
        "external_status": "not_run",
        "inventory_total": int(summary.get("total") or 0),
        "stages": [
            {"source": "favorites", "budget": favorites_budget,
             "budget_unit": "new_inventory_records",
             "status": "planned" if favorites_budget else "skipped"},
            {"source": "search", "budget": search_budget,
             "budget_unit": "new_inventory_records", "sources": sources,
             "status": "skipped" if not search_budget else "planned" if sources else "blocked",
             "reason": "" if sources or not search_budget else "缺少已确认的搜索关键词或城市"},
            {"source": "detail_refresh", "budget": refresh_budget,
             "budget_unit": "existing_job_attempts", "job_ids": selected,
             "backup_job_ids": backups,
             "status": "planned" if selected else "skipped"},
        ],
        "coverage": {
            "external_coverage": "not_checked",
            "detail_due_jobs": len(ordered),
            "selected_detail_jobs": len(selected),
            "remaining_detail_jobs": remaining,
            "coverage_scan_jobs": len(scan_ids),
            "detail_selection": "partial" if remaining else "all_due_selected",
        },
        "notes": [
            "这只是本地计划，未访问 Boss、未更新 JD、未调用模型或发送消息。",
            "发现预算包含失败占位记录，不等于成功详情数；收藏和搜索分别计量。",
            "选中全部到期详情也不代表市场数据已更新；外部来源覆盖尚未检查。",
        ],
    }


def plan_from_database(db_path: str | Path, cfg: dict, **budgets) -> dict:
    """Run legacy freshness migrations on an in-memory snapshot, not the user's DB."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"岗位数据库不存在：{path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(":memory:")) as snapshot:
            source.backup(snapshot)
            snapshot.row_factory = sqlite3.Row
            dashboard = build_freshness_dashboard(snapshot)
            return build_update_plan(dashboard, cfg, **budgets)


def main() -> None:
    from jobagent.config import load_config, resolve_project_path

    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="生成 JD 本地更新计划，不联网或修改岗位数据库")
    parser.add_argument("--db", help="岗位数据库路径，默认使用现有配置")
    parser.add_argument("--favorites-budget", type=int, default=25)
    parser.add_argument("--search-budget", type=int, default=25)
    parser.add_argument("--refresh-budget", type=int, default=20)
    args = parser.parse_args()
    cfg = load_config(require_ai=False)
    database = args.db or (cfg.get("collection") or {}).get("db_path", "output/jobagent.db")
    try:
        result = plan_from_database(
            resolve_project_path(database), cfg,
            favorites_budget=args.favorites_budget, search_budget=args.search_budget,
            refresh_budget=args.refresh_budget,
        )
    except (ValueError, FileNotFoundError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

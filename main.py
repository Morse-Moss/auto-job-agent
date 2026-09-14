"""AI 求职助手 — 命令行入口。

用法：
    python main.py --dry-run      # 只打分不打招呼（推荐先跑这个）
    python main.py --collect-only --collect-target 3
    python main.py                # 正式运行
    python main.py --platform boss
"""

import argparse
import json
import sys

from jobagent.config import load_config, resolve_project_path
from jobagent.ai import OpenAICompatibleClient
from jobagent.analysis import run_analysis
from jobagent.collection import CollectionStore
from jobagent.freshness import build_freshness_dashboard
from jobagent.calibration import calibrate_resume
from jobagent.knowledge import create_provider
from jobagent.matching import load_candidate_profile, run_match_preview
from jobagent.resume import ResumeManager
from jobagent.review_server import run_review_server
from jobagent.review import ReviewStore
from jobagent.resume_collection import collect_resume_market_data
from jobagent.store import Store
from jobagent.platforms.boss import BossPlatform
from jobagent.platforms.boss_collector import BossCollector
from jobagent.pilot import build_pilot_plan

PLATFORMS = {
    "boss": BossPlatform,
    # "liepin": LiepinPlatform,   # TODO: 猎聘
    # "zhilian": ZhilianPlatform, # TODO: 智联
}

BROWSER_RISK_WARNING = (
    "温馨提示：部分站点对浏览器自动化操作检测严格，存在账号封禁风险。"
    "已内置防护措施但无法完全避免，Agent 继续操作即视为接受。"
)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="AI 求职助手")
    parser.add_argument("--platform", default="boss", choices=list(PLATFORMS))
    parser.add_argument("--dry-run", action="store_true", help="只做 AI 打分，不实际打招呼")
    parser.add_argument("--collect-only", action="store_true", help="只采集岗位与 JD，不调用 AI 或沟通")
    parser.add_argument(
        "--refresh-due",
        action="store_true",
        help="只读复核 freshness 计划中的到期旧岗位，不调用 AI 或沟通",
    )
    parser.add_argument(
        "--refresh-target",
        type=int,
        default=10,
        help="本轮最多复核的旧岗位数量，默认 10",
    )
    parser.add_argument(
        "--scan-coverage",
        action="store_true",
        help="只扫描招聘列表覆盖范围并记录未见岗位，不打开详情页",
    )
    parser.add_argument(
        "--scan-source",
        choices=("all", "favorites", "search"),
        default="all",
        help="覆盖扫描来源，默认收藏与搜索",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=8,
        help="本轮最多扫描的搜索来源数量，默认 8",
    )
    parser.add_argument(
        "--review-jobs",
        action="store_true",
        help="启动本地岗位定位审阅台，不调用 AI 或浏览器",
    )
    parser.add_argument(
        "--review-port",
        type=int,
        default=8765,
        help="岗位定位审阅台本地端口，默认 8765",
    )
    parser.add_argument(
        "--analyze-collection",
        action="store_true",
        help="离线分析已采集 JD，不调用 AI 或沟通",
    )
    parser.add_argument(
        "--match-preview",
        action="store_true",
        help="依据本地候选人档案生成版本化匹配预览，不调用 AI 或浏览器",
    )
    parser.add_argument(
        "--calibrate-resume",
        action="store_true",
        help="从本地简历生成候选人档案并结合审阅行为校准匹配，不联网或调用 AI",
    )
    parser.add_argument(
        "--collect-from-resume",
        action="store_true",
        help="按简历生成的岗位范围执行只读采集，并在采集后重新校准",
    )
    parser.add_argument(
        "--pilot-plan",
        action="store_true",
        help="基于本地匹配、新鲜度和简历生成受控试点计划，不调用 AI、浏览器或发送消息",
    )
    parser.add_argument(
        "--pilot-max",
        type=int,
        default=30,
        help="候选池最多保留的岗位数，默认 30",
    )
    parser.add_argument(
        "--analysis-output",
        default="output/analysis",
        help="离线分析 CSV 与报告输出目录",
    )
    parser.add_argument("--collect-target", type=int, help="采集库中的目标 JD 总数")
    parser.add_argument(
        "--collect-source",
        choices=("all", "favorites", "search"),
        default="all",
        help="采集来源，默认先收藏后搜索",
    )
    parser.add_argument(
        "--legacy-auto-apply",
        action="store_true",
        help="[已封闭] 运行旧的全自动投递链路，不经过审核、逐岗授权与简历长图",
    )
    args = parser.parse_args()

    offline_only = (
        args.collect_only
        or args.refresh_due
        or args.scan_coverage
        or args.analyze_collection
        or args.review_jobs
        or args.match_preview
        or args.calibrate_resume
        or args.collect_from_resume
        or args.pilot_plan
    )
    cfg = load_config(require_ai=not offline_only)
    if args.review_jobs:
        collection_cfg = cfg.get("collection") or {}
        run_review_server(
            resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db")),
            host="127.0.0.1",
            port=args.review_port,
            config=cfg,
            analysis_output_dir=resolve_project_path(args.analysis_output),
        )
        return
    if args.pilot_plan:
        collection_cfg = cfg.get("collection") or {}
        resume_cfg = cfg.get("resume") or {}
        matching_cfg = cfg.get("matching") or {}
        resume_path = resolve_project_path(
            resume_cfg.get("master_file", "resume/master_resume.md")
        )
        db_path = resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db"))
        collection_store = CollectionStore(db_path)
        review_store = ReviewStore(db_path)
        try:
            match_dashboard = review_store.match_dashboard()
            execute = getattr(collection_store.conn, "execute", None)
            company_rows = (
                execute("SELECT platform, job_id, company_size FROM job_details").fetchall()
                if callable(execute)
                else []
            )
            company_sizes = {(str(row[0]), str(row[1])): str(row[2] or "") for row in company_rows}
            for job in match_dashboard.get("jobs", []):
                job["company_size"] = company_sizes.get(
                    (str(job.get("platform") or "boss"), str(job.get("job_id") or "")), ""
                )
            sent_rows = (
                execute("SELECT DISTINCT job_id FROM outreach_send_tasks WHERE status IN ('confirmed', 'partial')").fetchall()
                if callable(execute)
                else []
            )
            plan = build_pilot_plan(
                match_dashboard,
                build_freshness_dashboard(collection_store.conn),
                resume_path,
                max_jobs=args.pilot_max,
                excluded_companies={"比特鹰", "云起未来"},
                sent_job_ids={str(row[0]) for row in sent_rows},
            )
        finally:
            collection_store.close()
            review_store.close()
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    if args.refresh_due:
        collection_cfg = cfg.get("collection") or {}
        collection_store = CollectionStore(
            resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db"))
        )
        try:
            board = build_freshness_dashboard(collection_store.conn)
            target = max(0, int(args.refresh_target))
            job_ids = [
                str(job["job_id"])
                for job in board["refresh_plan"]
                if job.get("refresh_due")
                and job.get("refresh_action", "detail_refresh") == "detail_refresh"
            ][:target]
            if not job_ids:
                coverage_due = any(
                    job.get("refresh_due")
                    and job.get("refresh_action") == "coverage_scan"
                    for job in board["refresh_plan"]
                )
                if coverage_due:
                    print("当前只有列表复扫任务，请使用 --scan-coverage")
                else:
                    print("没有到期岗位需要复核")
                return
            print(BROWSER_RISK_WARNING)
            collector = BossCollector(cfg, collection_store)
            result = collector.refresh(job_ids, max_jobs=len(job_ids))
            print(
                f"复核结束：成功 {result.collected}，本地岗位库 {result.total}，"
                f"状态 {result.status}"
            )
            if result.error:
                print(f"停止原因：{result.error}")
        finally:
            collection_store.close()
        return
    if args.scan_coverage:
        collection_cfg = cfg.get("collection") or {}
        collection_store = CollectionStore(
            resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db"))
        )
        try:
            print(BROWSER_RISK_WARNING)
            result = BossCollector(cfg, collection_store).scan_coverage(
                source=args.scan_source,
                max_sources=max(1, int(args.scan_limit)),
            )
            print(
                f"覆盖扫描结束：完整来源 {result.scanned_sources}，"
                f"岗位库 {getattr(result, 'total', collection_store.detail_inventory_count())}，状态 {result.status}"
            )
            if result.error:
                print(f"停止原因：{result.error}")
        finally:
            collection_store.close()
        return
    if args.analyze_collection:
        collection_cfg = cfg.get("collection") or {}
        result = run_analysis(
            resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db")),
            resolve_project_path(args.analysis_output),
        )
        print(f"分析完成：{result.total} 个岗位")
        print(f"明细：{result.csv_path}")
        print(f"报告：{result.report_path}")
        return
    if args.match_preview:
        collection_cfg = cfg.get("collection") or {}
        matching_cfg = cfg.get("matching") or {}
        profile = load_candidate_profile(
            resolve_project_path(
                matching_cfg.get("profile_file", "private/candidate-profile.json")
            )
        )
        result = run_match_preview(
            resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db")),
            profile,
            str(matching_cfg.get("analysis_version") or "match-v2.0"),
        )
        print(
            f"匹配预览完成：{result.total} 个岗位 | "
            f"优先投 {result.distribution.get('priority', 0)} | "
            f"挑战投 {result.distribution.get('challenge', 0)} | "
            f"当前不建议投 {result.distribution.get('not_fit', 0)}"
        )
        print(
            f"版本：{result.analysis_version} / "
            f"{result.profile_id}:{result.profile_version}"
        )
        return
    if args.calibrate_resume:
        collection_cfg = cfg.get("collection") or {}
        resume_cfg = cfg.get("resume") or {}
        matching_cfg = cfg.get("matching") or {}
        resume_path = resolve_project_path(
            resume_cfg.get("master_file", "resume/master_resume.md")
        )
        if not resume_path.is_file():
            raise SystemExit(f"本地简历不存在：{resume_path}")
        result = calibrate_resume(
            resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db")),
            resume_path.read_text(encoding="utf-8"),
            filename=resume_path.name,
            analysis_version=str(
                matching_cfg.get("resume_analysis_version") or "resume-match-v1"
            ),
        )
        distribution = result["match"]["distribution"]
        print(
            f"简历校准完成：{result['match']['total']} 个岗位 | "
            f"优先投 {distribution.get('priority', 0)} | "
            f"挑战投 {distribution.get('challenge', 0)} | "
            f"当前不建议投 {distribution.get('not_fit', 0)}"
        )
        return

    if args.collect_from_resume:
        collection_cfg = cfg.get("collection") or {}
        resume_cfg = cfg.get("resume") or {}
        matching_cfg = cfg.get("matching") or {}
        resume_path = resolve_project_path(
            resume_cfg.get("master_file", "resume/master_resume.md")
        )
        if not resume_path.is_file():
            raise SystemExit(f"本地简历不存在：{resume_path}")
        print(BROWSER_RISK_WARNING)
        result = collect_resume_market_data(
            resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db")),
            cfg,
            resume_path.read_text(encoding="utf-8"),
            filename=resume_path.name,
            analysis_version=str(
                matching_cfg.get("resume_analysis_version") or "resume-match-v1"
            ),
            analysis_output_dir=resolve_project_path(args.analysis_output),
        )
        plan = result["collection_plan"]
        print(
            f"简历采集结束：本轮新增 {plan.get('collected_new_jobs', 0)}，"
            f"库内 {plan.get('local_jobs', 0)}，状态 {plan.get('external_collection', 'stopped')}"
        )
        if plan.get("external_collection_error"):
            print(f"停止原因：{plan['external_collection_error']}")
        return

    if args.collect_only:
        collection_cfg = cfg.get("collection") or {}
        target = args.collect_target or int(collection_cfg.get("target", 300))
        collection_store = CollectionStore(
            resolve_project_path(collection_cfg.get("db_path", "output/jobagent.db"))
        )
        collector = BossCollector(cfg, collection_store)
        try:
            print(BROWSER_RISK_WARNING)
            if args.collect_source == "all":
                result = collector.run(target=target)
            else:
                result = collector.run(target=target, source=args.collect_source)
            print(
                f"采集结束：本轮新增 {result.collected}，库内共 {result.total}，"
                f"状态 {result.status}"
            )
            if getattr(result, "error", None):
                print(f"停止原因：{result.error}")
        finally:
            collection_store.close()
        return

    if not args.dry_run and not args.legacy_auto_apply:
        raise SystemExit(
            "旧的全自动投递链路已封闭：它不经过岗位审核、逐岗授权，也不会上传简历长图。\n"
            "请改用受控流程：py main.py --review-jobs，在「投递准备」中逐岗确认后再发送。\n"
            "如确需运行旧链路，请显式加 --legacy-auto-apply，风险自负。"
        )

    ai = OpenAICompatibleClient(cfg["ai"])
    resume_mgr = ResumeManager(cfg["resume"])
    store = Store()

    kb = create_provider(cfg.get("knowledge"))
    print(f"知识库模式: {kb.name}")
    if kb.name == "none":
        print("提示：未启用知识库（可选功能），打招呼与简历定制仅依据母版简历")

    if not (cfg.get("preferences") or "").strip():
        print("提示：config.yaml 未填写 preferences 求职要求，打分将仅按简历匹配度")

    platform = PLATFORMS[args.platform](cfg, ai, resume_mgr, store, kb=kb)
    try:
        print(BROWSER_RISK_WARNING)
        platform.run(dry_run=args.dry_run)
    finally:
        store.close()


if __name__ == "__main__":
    main()

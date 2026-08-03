"""AI 求职助手 — 命令行入口。

用法：
    python main.py --dry-run      # 只打分不打招呼（推荐先跑这个）
    python main.py                # 正式运行
    python main.py --platform boss
"""

import argparse

from jobagent.config import load_config
from jobagent.ai import DeepSeekClient
from jobagent.knowledge import create_provider
from jobagent.resume import ResumeManager
from jobagent.store import Store
from jobagent.platforms.boss import BossPlatform

PLATFORMS = {
    "boss": BossPlatform,
    # "liepin": LiepinPlatform,   # TODO: 猎聘
    # "zhilian": ZhilianPlatform, # TODO: 智联
}


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 求职助手")
    parser.add_argument("--platform", default="boss", choices=list(PLATFORMS))
    parser.add_argument("--dry-run", action="store_true", help="只做 AI 打分，不实际打招呼")
    args = parser.parse_args()

    cfg = load_config()
    ai = DeepSeekClient(cfg["ai"])
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
        platform.run(dry_run=args.dry_run)
    finally:
        store.close()


if __name__ == "__main__":
    main()

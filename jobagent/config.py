from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    cfg_file = ROOT / "config.yaml"
    if not cfg_file.exists():
        raise SystemExit(
            "未找到 config.yaml。请先执行:\n"
            "  cp config.example.yaml config.yaml\n"
            "然后填写 DeepSeek API Key 等配置。"
        )
    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not cfg.get("ai", {}).get("api_key") or "你的" in cfg["ai"]["api_key"]:
        raise SystemExit("请在 config.yaml 中填写有效的 DeepSeek API Key (ai.api_key)")
    return cfg

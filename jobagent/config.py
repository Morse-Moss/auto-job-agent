from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_API_KEYS = {"sk-yourDeepSeekKey", "sk-your-api-key"}


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def load_config(require_ai: bool = True) -> dict:
    cfg_file = ROOT / "config.yaml"
    if not cfg_file.exists():
        raise SystemExit(
            "未找到 config.yaml。请先执行:\n"
            "  cp config.example.yaml config.yaml\n"
            "然后填写 OpenAI-compatible API 配置。"
        )
    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    api_key = cfg.get("ai", {}).get("api_key") if isinstance(cfg, dict) else None
    if require_ai and (
        not isinstance(api_key, str)
        or not api_key.strip()
        or "你的" in api_key
        or api_key in PLACEHOLDER_API_KEYS
    ):
        raise SystemExit(
            "请在 config.yaml 中填写有效的 OpenAI-compatible API key (ai.api_key)"
        )
    return cfg

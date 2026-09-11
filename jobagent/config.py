from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_API_KEYS = {"sk-yourDeepSeekKey", "sk-your-api-key"}

# 每一类 provider 的默认字段（供扩展；缺失字段回落到这里）
AI_PROVIDER_RESERVED_KEYS = ("providers", "active")


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def normalize_ai_config(ai_cfg: dict | None) -> dict:
    """把 ai 配置统一成 ``{"providers": {name: spec}, "active": name}``。

    支持两种写法，互不冲突：
    1. 多 provider（推荐）：``ai.providers.<name>`` 逐个定义，``ai.active`` 选择当前使用。
       便于配置多个 DeepSeek（或任意 OpenAI-compatible）接口并随时切换。
    2. 单 provider（旧格式）：``ai.api_key`` / ``ai.base_url`` / ``ai.model`` 直接平铺。
       会被规范化为名为 ``ai.active``（默认 ``default``）的唯一 provider。
    """
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    providers_cfg = ai_cfg.get("providers")
    if isinstance(providers_cfg, dict) and providers_cfg:
        providers: dict[str, dict] = {}
        for name, spec in providers_cfg.items():
            if not isinstance(spec, dict):
                continue
            providers[str(name)] = {**spec, "name": str(name)}
        if not providers:
            raise ValueError("ai.providers 未定义任何有效配置")
        active = str(ai_cfg.get("active") or next(iter(providers)))
        if active not in providers:
            raise ValueError(f"ai.active = {active} 不在 ai.providers 中")
        return {"providers": providers, "active": active}

    legacy = {
        key: value
        for key, value in ai_cfg.items()
        if key not in AI_PROVIDER_RESERVED_KEYS
    }
    if not legacy:
        return {"providers": {}, "active": ""}
    legacy.setdefault("type", "openai")
    name = str(ai_cfg.get("active") or "default")
    legacy["name"] = name
    return {"providers": {name: legacy}, "active": name}


def resolve_ai_provider(ai_cfg: dict | None, provider: str | None = None) -> dict:
    """返回要使用的 provider 规格；``provider`` 为空时取 ``ai.active``。"""
    normalized = normalize_ai_config(ai_cfg)
    providers = normalized["providers"]
    if not providers:
        raise ValueError("未配置任何 AI provider（ai.providers 或 ai.api_key）")
    name = str(provider) if provider else normalized["active"]
    if name not in providers:
        raise ValueError(
            f"AI provider {name!r} 不存在，可用: {', '.join(providers)}"
        )
    return providers[name]


def provider_type(spec: dict | None) -> str:
    return str((spec or {}).get("type") or "openai").strip().lower()


def ai_provider_is_usable(spec: dict | None) -> bool:
    """manual 类型无需密钥；其余要求有效的 api_key。"""
    if provider_type(spec) == "manual":
        return True
    api_key = (spec or {}).get("api_key")
    return (
        isinstance(api_key, str)
        and bool(api_key.strip())
        and "你的" not in api_key
        and api_key not in PLACEHOLDER_API_KEYS
    )


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
    if require_ai:
        try:
            active = resolve_ai_provider(cfg.get("ai") if isinstance(cfg, dict) else None)
        except ValueError as exc:
            raise SystemExit(f"config.yaml 的 ai 配置无效：{exc}") from exc
        if not ai_provider_is_usable(active):
            raise SystemExit(
                "config.yaml 中当前 AI provider 缺少有效的 OpenAI-compatible API key。"
                "请填写 ai.providers.<名称>.api_key，或把 ai.active 指向 manual。"
            )
    return cfg

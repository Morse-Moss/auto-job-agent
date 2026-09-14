"""本机浏览器服务（MorseTools general-web）的探测与后台启动。

只做两件事：判断配置里的 CDP 端点是否真的可用；不可达时以脱离终端的方式拉起服务。
不导航、不点击、不发送任何消息——启动服务本身不会碰 Boss 页面。

放在 jobagent 里，是为了让命令行入口（scripts/start_workbench.py）与网页端
（/api/browser/start）共用同一套启动逻辑，避免两处各写一份、行为漂移。
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

MORSE_DIR = Path(r"E:\MorseTools\browser-service")
MORSE_PROFILE = "general-web"
DEFAULT_ENDPOINT = "http://127.0.0.1:17332"
MORSE_LOG = (
    Path(__file__).resolve().parent.parent
    / "output"
    / "logs"
    / "morse-browser-service.log"
)


def endpoint_from_config(config: dict | None) -> str:
    """从配置读取 CDP 端点；缺失时回落到默认端口。"""
    boss = (config or {}).get("boss") or {}
    return str(boss.get("cdp_endpoint") or DEFAULT_ENDPOINT).rstrip("/")


def _host_port(endpoint: str) -> tuple[str, int]:
    parts = urlsplit(endpoint if "://" in endpoint else f"http://{endpoint}")
    default_port = 443 if parts.scheme == "https" else 80
    return (parts.hostname or "127.0.0.1", parts.port or default_port)


def port_listening(endpoint: str, timeout: float = 0.6) -> bool:
    """端口是否有人监听。注意：监听不等于 CDP 可用。"""
    host, port = _host_port(endpoint)
    try:
        with socket.socket() as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def cdp_version(endpoint: str, timeout: float = 2.0) -> dict | None:
    """真正的 CDP 可用性判定：必须返回 webSocketDebuggerUrl 或 Browser。"""
    try:
        with urllib.request.urlopen(
            f"{endpoint}/json/version", timeout=timeout
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    if payload.get("webSocketDebuggerUrl") or payload.get("Browser"):
        return payload
    return None


def cdp_ready(endpoint: str) -> bool:
    return cdp_version(endpoint) is not None


def _detach_flags() -> int:
    """脱离当前终端生命周期所需的 creationflags（与启动脚本保持一致）。"""
    flags = 0
    for name in (
        "DETACHED_PROCESS",
        "CREATE_NEW_PROCESS_GROUP",
        "CREATE_BREAKAWAY_FROM_JOB",
    ):
        flags |= getattr(subprocess, name, 0)
    return flags


def _spawn(command: list[str], cwd: str, handle) -> subprocess.Popen:
    """按优先级尝试脱离组合；breakaway 被环境拒绝时逐级降级。"""
    plain = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    attempts = [("breakaway", _detach_flags())]
    if plain != _detach_flags():
        attempts.append(("detached", plain))
    attempts.append(("default", 0))

    last_error: Exception | None = None
    for _, flags in attempts:
        try:
            return subprocess.Popen(
                command,
                cwd=cwd,
                shell=True,
                stdout=handle,
                stderr=handle,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
            )
        except Exception as exc:  # noqa: BLE001 — 逐个降级重试
            last_error = exc
            continue
    raise last_error if last_error is not None else RuntimeError("无法启动浏览器服务")


def start_browser_service() -> subprocess.Popen:
    """后台拉起 MorseTools general-web。日志落在 output/logs/morse-browser-service.log。"""
    MORSE_LOG.parent.mkdir(parents=True, exist_ok=True)
    handle = open(MORSE_LOG, "a", encoding="utf-8")  # noqa: SIM115 — 交给子进程持有
    return _spawn(
        ["npm", "run", "dev", "--", "--profile", MORSE_PROFILE],
        cwd=str(MORSE_DIR),
        handle=handle,
    )


def ensure_browser_service(
    config: dict | None = None, wait_seconds: float = 40.0
) -> dict:
    """复用健康实例；不可达时后台拉起并等待 CDP 就绪，返回结构化状态。

    返回字段：endpoint / running / started / detail。
    只负责“让服务在线”，不处理登录态——登录必须由本人在浏览器里完成。
    """
    endpoint = endpoint_from_config(config)
    if cdp_ready(endpoint):
        return {
            "endpoint": endpoint,
            "running": True,
            "started": False,
            "detail": "浏览器服务已在运行（CDP 可用）",
        }
    if not MORSE_DIR.is_dir():
        return {
            "endpoint": endpoint,
            "running": False,
            "started": False,
            "detail": f"未找到 MorseTools 目录：{MORSE_DIR}",
        }
    try:
        start_browser_service()
    except Exception as exc:  # noqa: BLE001
        return {
            "endpoint": endpoint,
            "running": False,
            "started": False,
            "detail": f"启动浏览器服务失败：{type(exc).__name__}: {exc}",
        }

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if cdp_ready(endpoint):
            return {
                "endpoint": endpoint,
                "running": True,
                "started": True,
                "detail": "已启动浏览器服务，CDP 就绪",
            }
        time.sleep(0.8)
    return {
        "endpoint": endpoint,
        "running": False,
        "started": True,
        "detail": (
            f"已发起启动，但 {int(wait_seconds)} 秒内 CDP 仍未就绪，"
            f"请查看日志 {MORSE_LOG}"
        ),
    }

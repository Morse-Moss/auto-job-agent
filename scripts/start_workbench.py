"""一键启动：复用健康实例、后台拉起缺失服务、验证应用接口与 CDP 端点。

设计原则（2026-09-14 批量投递落地）：
- 先看端口所有者，再决定动作。健康实例直接复用；端口被非本应用占用时只报告
  并给出处理建议，绝不盲目杀死占用进程。
- 后台启动脱离当前终端生命周期（Windows 用 DETACHED_PROCESS），关闭本终端
  不会带走服务。
- 普通 Edge 可用不等于 CDP 端点可用：必须真正拿到 /json/version 的 CDP 信息。
- 打开页面、执行本脚本都不会触发采集、模型调用或发送消息。

用法::

    py scripts/start_workbench.py              # 检查并补齐（后台启动缺失服务）
    py scripts/start_workbench.py --check      # 只检查，不启动任何东西
    py scripts/start_workbench.py --no-browser # 不自动启动 MorseTools 浏览器服务
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobagent import browser_service  # noqa: E402 — 需要先注入 ROOT

REVIEW_PORT = 8765
MORSE_DIR = browser_service.MORSE_DIR
MORSE_PROFILE = browser_service.MORSE_PROFILE
LOG_DIR = ROOT / "output" / "logs"


def is_listening(host: str, port: int, timeout: float = 0.6) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def port_owner(port: int) -> int | None:
    """从 netstat 解析监听该端口的进程号，不依赖 psutil。"""
    try:
        completed = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:  # noqa: BLE001 — 拿不到所有者不影响主流程
        return None
    for line in (completed.stdout or "").splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}"):
            try:
                return int(parts[-1])
            except ValueError:
                continue
    return None


def http_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError, json.JSONDecodeError):
        return None


def load_config() -> dict:
    import yaml

    path = ROOT / "config.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _detach_flags() -> int:
    """脱离当前终端生命周期所需的 creationflags。

    DETACHED_PROCESS: 不继承控制台，关掉终端不会带走子进程。
    CREATE_NEW_PROCESS_GROUP: 父进程收到 Ctrl+C 时不波及子进程。
    CREATE_BREAKAWAY_FROM_JOB: 关键——若父进程运行在 Windows Job Object 里
      （IDE、部分启动器、受限 shell 都会用），仅靠 DETACHED_PROCESS 仍会被
      “关掉 Job 就杀掉全部子进程”的语义带走，必须显式 breakaway。
      某些环境禁止 breakaway，此时会报 PermissionError，由调用处回退。
    """
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_BREAKAWAY_FROM_JOB"):
        flags |= getattr(subprocess, name, 0)
    return flags


def _spawn_detached(command: list[str], cwd: str, handle, shell: bool = False) -> subprocess.Popen:
    """按优先级尝试不同脱离组合，返回成功的进程对象。

    顺序：breakaway+detached → 仅 detached+new group → 默认。
    每次失败都真实记录原因，不做静默兜底。
    shell=True 用于 npm.cmd 这类 Windows 批处理入口。
    """
    attempts: list[tuple[str, int]] = [
        ("breakaway 脱离 Job", _detach_flags()),
    ]
    plain = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    if plain != _detach_flags():
        attempts.append(("detached 脱离控制台", plain))
    attempts.append(("默认方式", 0))

    last_error: Exception | None = None
    for label, flags in attempts:
        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                shell=shell,
                stdout=handle,
                stderr=handle,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
            )
            if label != "breakaway 脱离 Job":
                print(f"  注意：以“{label}”启动（breakaway 不可用），服务仍可能随本终端退出。")
            return proc
        except Exception as exc:  # noqa: BLE001 — 逐个降级重试
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("无法启动子进程")


def start_review_service(review_port: int = REVIEW_PORT) -> subprocess.Popen | None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "review-server.log"
    # -u 关闭 stdout/stderr 缓冲：进程若被强杀，日志也不会整段丢失（原先 0 字节的成因）。
    handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — 交给子进程持有
    try:
        return _spawn_detached(
            [sys.executable, "-u", str(ROOT / "main.py"), "--review-jobs",
             "--review-port", str(review_port)],
            cwd=str(ROOT),
            handle=handle,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  启动审阅服务失败：{type(exc).__name__}: {exc}")
        return None


def start_morse_service() -> subprocess.Popen | None:
    """拉起 MorseTools general-web；实现与网页端一键启动共用同一模块。"""
    if not MORSE_DIR.is_dir():
        print(f"  未找到 MorseTools 目录：{MORSE_DIR}（请手动启动）")
        return None
    try:
        return browser_service.start_browser_service()
    except Exception as exc:  # noqa: BLE001
        print(f"  启动 MorseTools general-web 失败：{type(exc).__name__}: {exc}")
        print(f"  请手动执行：cd {MORSE_DIR} && npm run dev -- --profile {MORSE_PROFILE}")
        return None


def wait_for(url: str, description: str, timeout: float = 25.0, interval: float = 0.7) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = http_json(url)
        if payload is not None:
            return payload
        time.sleep(interval)
    print(f"  等待{description}超时：{url}")
    return None


def _unwrap_version(payload: dict | None) -> dict | None:
    if not payload:
        return None
    inner = payload.get("version")
    return inner if isinstance(inner, dict) else payload


def ensure_review_service(
    check_only: bool, review_port: int = REVIEW_PORT
) -> tuple[bool, dict | None]:
    if is_listening("127.0.0.1", review_port):
        health = http_json(f"http://127.0.0.1:{review_port}/health")
        if health and health.get("status") == "ok":
            owner = port_owner(review_port)
            print(f"[审阅服务] 复用健康实例（PID {owner}）端口 {review_port}")
            version = _unwrap_version(
                http_json(f"http://127.0.0.1:{review_port}/api/version")
            )
            return True, version
        owner = port_owner(review_port)
        print(
            f"[审阅服务] 端口 {review_port} 被 PID {owner} 占用，但不是本应用的健康实例。\n"
            f"  未做任何处理（绝不盲目杀进程）。请人工确认该进程后再决定是否释放端口。"
        )
        return False, None
    print(f"[审阅服务] 端口 {review_port} 空闲，准备后台启动…")
    if check_only:
        print("  --check 模式：跳过启动。")
        return False, None
    process = start_review_service(review_port)
    if process is None:
        return False, None
    version = wait_for(
        f"http://127.0.0.1:{review_port}/api/version", "审阅服务"
    )
    if version is None:
        return False, None
    print(f"[审阅服务] 已启动（PID {process.pid}）http://127.0.0.1:{review_port}")
    return True, _unwrap_version(version)


def ensure_cdp(config: dict, check_only: bool, with_browser: bool) -> bool:
    if not (config.get("boss") or {}).get("cdp_endpoint"):
        print("[浏览器] 配置里没有 boss.cdp_endpoint，跳过检查。")
        return False
    endpoint = browser_service.endpoint_from_config(config)
    version = browser_service.cdp_version(endpoint)
    if version:
        print(
            f"[浏览器] CDP 可用：{endpoint}（{version.get('Browser', 'unknown')}）"
        )
        return True
    print(f"[浏览器] CDP 端点不可达：{endpoint}（注意：普通 Edge 可用 ≠ 该 CDP 可用）")
    if check_only or not with_browser:
        print(
            "  未自动启动。手动启动："
            f"cd {MORSE_DIR} && npm run dev -- --profile {MORSE_PROFILE}"
        )
        return False
    print("[浏览器] 准备后台启动 MorseTools general-web…")
    process = start_morse_service()
    if process is None:
        return False
    version = wait_for(f"{endpoint}/json/version", "MorseTools general-web")
    if version is None:
        return False
    print(f"[浏览器] 已启动 general-web（PID {process.pid}）{endpoint}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="一键启动投递准备工作台")
    parser.add_argument("--check", action="store_true", help="只检查，不启动任何服务")
    parser.add_argument(
        "--no-browser", action="store_true", help="不自动启动 MorseTools 浏览器服务"
    )
    parser.add_argument(
        "--review-port", type=int, default=REVIEW_PORT,
        help=f"审阅服务端口（默认 {REVIEW_PORT}）",
    )
    args = parser.parse_args()

    print("=" * 56)
    print("投递准备工作台 · 启动自检")
    print("=" * 56)
    config = load_config()
    review_ok, version = ensure_review_service(args.check, args.review_port)
    cdp_ok = ensure_cdp(config, args.check, with_browser=not args.no_browser)

    print("-" * 56)
    if version:
        print(
            f"运行版本：{version.get('revision') or 'unknown'}"
            f" | 加载于 {version.get('loaded_at')}"
        )
        if version.get("code_changed_since_start"):
            print(
                "⚠ 检测到代码已更新但服务未重启，改动："
                + "、".join(version.get("changed_files") or [])
            )
    if review_ok:
        print(f"操作入口：http://127.0.0.1:{args.review_port}（投递准备 / 岗位审阅）")
    print(f"浏览器发送前置：{'就绪' if cdp_ok else '未就绪（只影响真实发送）'}")
    if review_ok:
        print("提示：打开页面不会采集、不会调用模型、不会发送消息。")
        return 0
    print("审阅服务未就绪，请查看上面的原因。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

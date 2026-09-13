# -*- coding: utf-8 -*-
"""批量复核循环：反复调用 main.py --refresh-due 直到无到期岗位。

用法：E:/AI/Python/python.exe scripts/batch_refresh.py
每轮 --refresh-target 20，轮间停 30 秒。日志落 output/batch_refresh.log
"""
import subprocess
import sys
import time
from datetime import datetime

PY = sys.executable
MAX_ROUNDS = 12


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)


def main() -> None:
    for rnd in range(1, MAX_ROUNDS + 1):
        log(f"=== 第 {rnd}/{MAX_ROUNDS} 轮复核开始 ===")
        proc = subprocess.run(
            [PY, "main.py", "--refresh-due", "--refresh-target", "20"],
            capture_output=True, text=True, encoding="utf-8", errors="backslashreplace",
            timeout=3600,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = out.strip().splitlines()[-8:]
        for line in tail:
            log("  " + line)
        if "没有到期岗位需要复核" in out:
            log("所有到期岗位复核完成，退出")
            break
        if "login was not detected" in out:
            log("登录态丢失，停止循环等待人工处理")
            sys.exit(2)
        log(f"第 {rnd} 轮结束，30 秒后继续")
        time.sleep(30)
    log("循环结束")


if __name__ == "__main__":
    main()

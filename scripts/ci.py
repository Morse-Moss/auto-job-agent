"""本地 CI 入口：lint + 构建（编译与语法检查）+ 全量测试，一条命令跑完。

用法：python scripts/ci.py
所有步骤都会执行并在结尾汇总；任何一步失败以非零码退出。
GitHub Actions（.github/workflows/ci.yml）与本仓库的 pre-push 钩子都调用本脚本。
"""

import compileall
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(name: str, command: list[str]) -> bool:
    print(f"\n{'=' * 60}\n[CI] {name}\n{'=' * 60}")
    result = subprocess.run(command, cwd=ROOT)
    return result.returncode == 0


def main() -> int:
    failures: list[str] = []

    if run("lint（ruff）", [sys.executable, "-m", "ruff", "check", "."]):
        print("[CI] lint 通过")
    else:
        failures.append("lint：ruff 检查未通过（按上方 文件:行号 与规则码修复）")

    print(f"\n{'=' * 60}\n[CI] build（Python 字节码编译）\n{'=' * 60}")
    build_ok = compileall.compile_file(str(ROOT / "main.py"), quiet=2)
    build_ok = compileall.compile_dir(str(ROOT / "jobagent"), quiet=2) and build_ok
    if build_ok:
        print("[CI] build 通过")
    else:
        failures.append("build：存在无法编译的 Python 文件（见上方 SyntaxError）")

    node = shutil.which("node")
    if node is None:
        failures.append("build：未找到 node，无法校验 review_ui/app.js")
    elif run("build（review_ui/app.js 语法检查）", [node, "--check", str(ROOT / "review_ui" / "app.js")]):
        print("[CI] 前端语法检查通过")
    else:
        failures.append("build：review_ui/app.js 存在语法错误")

    if run("test（pytest 全量）", [sys.executable, "-m", "pytest", "-q"]):
        print("[CI] 测试通过")
    else:
        failures.append("test：pytest 未全部通过（见上方 FAILED 用例）")

    print(f"\n{'=' * 60}")
    if failures:
        print(f"[CI] 失败，共 {len(failures)} 项：")
        for item in failures:
            print(f"  ✗ {item}")
        return 1
    print("[CI] 全部通过：lint、build、test。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

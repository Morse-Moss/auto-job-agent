"""个人知识库加载。

knowledge/ 目录下的 .md / .txt 文件会被读取并注入 AI 提示词，
作为生成打招呼语与定制简历的事实依据。
按文件名排序加载，超过 max_chars 截断，重要内容请放前面（如 01-xxx.md）。
"""

from pathlib import Path


class KnowledgeBase:
    def __init__(self, cfg: dict | None):
        cfg = cfg or {}
        self.dir = Path(cfg.get("dir", "knowledge"))
        self.max_chars = int(cfg.get("max_chars", 12000))

    def load(self) -> str:
        if not self.dir.exists():
            return ""
        parts: list[str] = []
        total = 0
        for f in sorted(self.dir.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in {".md", ".txt"}:
                continue
            if f.name == "README.md" or f.name.startswith("."):
                continue
            text = f.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            chunk = f"### 来源: {f.name}\n{text}"
            remain = self.max_chars - total
            if remain <= 0:
                break
            if len(chunk) > remain:
                chunk = chunk[:remain]
            parts.append(chunk)
            total += len(chunk)
        return "\n\n".join(parts)

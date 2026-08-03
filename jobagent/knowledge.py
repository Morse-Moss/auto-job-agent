"""知识库提供者：为打招呼语与简历定制提供事实依据。

知识库是可选能力，config.yaml 的 knowledge.provider 支持三种模式：
- none:  不使用知识库（没有知识库也能正常运行，仅依据母版简历生成）
- local: 读取本地 knowledge/ 目录下的 .md/.txt，启动时全量加载
- rag:   按岗位实时调用外部 RAG 检索接口（如 Morse 数字分身系统），
         每个岗位只注入与该 JD 最相关的知识片段。接口契约见 docs/rag-api.md
"""

from pathlib import Path

import requests


class BaseKnowledgeProvider:
    """知识提供者基类。for_job 返回注入提示词的知识文本，无知识时返回空串。"""

    name = "none"

    def for_job(self, job: dict) -> str:
        return ""


class NullKnowledgeProvider(BaseKnowledgeProvider):
    """无知识库模式：打招呼与简历定制仅依据母版简历。"""

    name = "none"


class LocalKnowledgeProvider(BaseKnowledgeProvider):
    """本地目录模式：启动时全量加载，所有岗位共用同一份知识文本。

    按文件名排序加载，超过 max_chars 截断，重要内容命名靠前（如 01-xxx.md）。
    """

    name = "local"

    def __init__(self, cfg: dict):
        self.dir = Path(cfg.get("dir", "knowledge"))
        self.max_chars = int(cfg.get("max_chars", 12000))
        self._cache: str | None = None

    def for_job(self, job: dict) -> str:
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def _load(self) -> str:
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


class RagKnowledgeProvider(BaseKnowledgeProvider):
    """RAG 模式：按岗位 JD 调用外部检索接口，返回 top-k 相关知识片段。

    检索失败或超时会打印警告，并对该岗位降级为无知识模式，不中断投递流程。
    """

    name = "rag"

    def __init__(self, cfg: dict):
        rag = cfg.get("rag") or {}
        self.endpoint = (rag.get("endpoint") or "").strip()
        self.api_key = (rag.get("api_key") or "").strip()
        self.top_k = int(rag.get("top_k", 6))
        self.timeout = float(rag.get("timeout_seconds", 10))
        self.max_chars = int(cfg.get("max_chars", 12000))
        if not self.endpoint:
            raise ValueError("knowledge.provider 为 rag 时必须配置 knowledge.rag.endpoint")

    def for_job(self, job: dict) -> str:
        query = (
            f"{job.get('title', '')} {job.get('company', '')}\n{job.get('jd', '')[:1500]}"
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = requests.post(
                self.endpoint,
                json={"query": query, "top_k": self.top_k},
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except Exception as e:  # noqa: BLE001
            print(f"警告：RAG 检索失败，本岗位降级为无知识模式: {e}")
            return ""

        parts: list[str] = []
        total = 0
        for r in results:
            text = (r.get("text") or "").strip()
            if not text:
                continue
            source = (r.get("source") or "").strip()
            chunk = f"### 来源: {source}\n{text}" if source else text
            remain = self.max_chars - total
            if remain <= 0:
                break
            if len(chunk) > remain:
                chunk = chunk[:remain]
            parts.append(chunk)
            total += len(chunk)
        return "\n\n".join(parts)


def create_provider(cfg: dict | None) -> BaseKnowledgeProvider:
    """根据 config.yaml 的 knowledge 配置创建知识提供者。缺省为 none。"""
    cfg = cfg or {}
    provider = (cfg.get("provider") or "none").strip().lower()
    if provider == "local":
        return LocalKnowledgeProvider(cfg)
    if provider == "rag":
        return RagKnowledgeProvider(cfg)
    return NullKnowledgeProvider()

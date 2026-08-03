"""平台抽象基类。新增平台（猎聘/智联）时继承 BasePlatform 并实现 run()。"""

from abc import ABC, abstractmethod


class BasePlatform(ABC):
    name = "base"

    def __init__(self, cfg: dict, ai, resume_mgr, store, kb=None):
        self.cfg = cfg
        self.ai = ai
        self.resume_mgr = resume_mgr
        self.store = store
        self.kb = kb  # 知识提供者（可选），jobagent.knowledge.BaseKnowledgeProvider 实例

    def job_knowledge(self, job: dict) -> str:
        """获取该岗位可用的知识文本；未配置知识库时返回空串。"""
        return self.kb.for_job(job) if self.kb else ""

    @abstractmethod
    def run(self, dry_run: bool = False) -> None:
        """执行一轮：搜索 -> 打分 -> (非 dry_run 时)打招呼。"""
        raise NotImplementedError

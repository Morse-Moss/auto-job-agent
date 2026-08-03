"""平台抽象基类。新增平台（猎聘/智联）时继承 BasePlatform 并实现 run()。"""

from abc import ABC, abstractmethod


class BasePlatform(ABC):
    name = "base"

    def __init__(self, cfg: dict, ai, resume_mgr, store):
        self.cfg = cfg
        self.ai = ai
        self.resume_mgr = resume_mgr
        self.store = store

    @abstractmethod
    def run(self, dry_run: bool = False) -> None:
        """执行一轮：搜索 -> 打分 -> (非 dry_run 时)打招呼。"""
        raise NotImplementedError

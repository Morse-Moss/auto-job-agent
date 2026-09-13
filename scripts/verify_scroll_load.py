"""小规模验证：佛山单关键词，确认滚动加载能把每源产出从 15 条提到上限。

只跑一个来源（佛山 × AI 应用工程师），目标 +45 条。用于在启动全量采集前
验证滚动加载生效、且不触发安全校验。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from jobagent.collection import CollectionStore  # noqa: E402
from jobagent.config import resolve_project_path  # noqa: E402
from jobagent.platforms.boss_collector import BossCollector  # noqa: E402

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
cfg["collection"]["cities"] = ["佛山"]
cfg["collection"]["keywords"] = ["AI 应用工程师"]

store = CollectionStore(resolve_project_path("output/jobagent.db"))
before = store.detail_inventory_count()
collector = BossCollector(cfg, store)
try:
    result = collector.run(target=before + 45, source="search")
    print("验证结果:", result)
    print("验证前:", before, "验证后:", store.detail_inventory_count())
finally:
    store.close()

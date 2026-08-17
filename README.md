# auto-job-agent

Boss直聘 AI 求职助手：自动搜索岗位 → GPT 等 OpenAI-compatible 模型结合你的求职要求打分筛选 → 按 JD 定制简历 → 自动发送个性化打招呼语。

## 流程

```
搜索关键词岗位 → 黑名单硬过滤（外包/驻场等，不花 API 钱）
      ↓
抓取 JD 全文 → AI 打分【依据 = 求职要求 preferences + 母版简历 + JD】
  · 命中「坚决排除」→ 直接 0-20 分
  · 不满足「硬性要求」→ 最高 40 分
      ↓
低于阈值 → 跳过并记录 ｜ 高分岗位 ↓
知识库检索（可选：none / local / rag）
      ↓
AI 生成定制简历（存 output/tailored/）+ 打招呼语【依据 = 简历 + 知识片段 + JD】
      ↓
自动点击「立即沟通」发送 → SQLite 记录防重复
```

## 快速开始

```bash
# 1. Python 3.10+，安装依赖
pip install -r requirements.txt
playwright install chromium

# 2. 配置
cp config.example.yaml config.yaml          # 填 API 地址、key、模型、求职要求和关键词
cp resume/master_resume.example.md resume/master_resume.md   # 写你的简历

# 3. 试运行（只打分不投递，务必先跑这个）
python main.py --dry-run

# 4. 正式运行
python main.py
```

## 只读岗位采集

准备阶段可以只收集列表与 JD，不调用 GPT、不生成简历、不投递、不发送沟通消息：

```bash
python main.py --collect-only --collect-target 3 --collect-source favorites
python main.py --collect-only --collect-target 300
```

采集优先读取“感兴趣”收藏，再按 FDE、AI Agent、AI 应用、RAG、解决方案与交付方向补充搜索结果。记录保存在 `output/jobagent.db`，每条 JD 增量去重；检测到 Boss 安全校验会立即停止，下一次运行从已保存记录继续。

采集完成后可以运行可解释的离线定位分析，不调用 GPT：

```bash
python main.py --analyze-collection
```

分析结果回写到同一数据库的 `job_positioning_analysis` 表，并导出到 `output/analysis/job-positioning.csv` 与 `job-positioning-summary.md`。分层会区分技术相关度与实际可投性，单独标记经验、薪资、第三方招聘、外包/派遣、长期驻场、产品/销售错位、应届资格和海外派驻风险。

## 岗位定位审阅台

在恢复 AI 评分或自动投递前，先用本地审阅台校准一批岗位：

```bash
python main.py --review-jobs
```

打开 `http://127.0.0.1:8765`。默认样本固定为 60 条：收藏 20、核心 15、挑战 15、排除 10。系统自动展示岗位方向、技术匹配、业务/FDE 价值、当前可投性、薪资可达性和用工关系，并附上对应的 JD 证据；没有足够依据时显示“信息不足”。公司画像会从已采集详情中整理规模、行业、融资、成立时间、工商主体、经营状态和注册资本，不额外访问招聘网站。

你只需要选择“想投 / 再看 / 放弃”，放弃可以直接保存，原因和备注都可选。备注中提到需要补齐的能力时，可以勾选“加入学习雷达”；学习雷达会把个人备注与本地目标岗位 JD 的提及率结合，给出学习优先级、练习产出建议、市场需求快照和原备注回看。所有分析均在本地完成，不调用模型，也不会改写已有备注。

用户决策保存在 `job_decisions`，对系统判断的纠错单独保存在 `job_corrections`；旧 `job_reviews` 表保留兼容且不会被删除。审阅台不会访问 Boss、调用模型或发送沟通消息。

涉及 Boss 的采集或投递前，请先启动并登录 `config.yaml` 中 `boss.cdp_endpoint` 指向的浏览器。程序复用该 CDP 会话，不复制或另存 Cookie；本地岗位审阅台不需要浏览器登录。

## 简历驱动的个人校准

新用户不需要先手工填写一套偏好。系统可以从本地 Markdown 简历提取教育、经验、能力与项目证据，再结合已有岗位数据和审阅行为生成个性化校准：

```bash
py -3 main.py --calibrate-resume
py -3 main.py --review-jobs
```

审阅台默认进入“简历校准”。简历原文只在本次请求中读取，不写入岗位数据库；数据库只保存简历哈希、提取后的事实证据、行为软偏好和匹配结果。城市、薪资、远程/出差等无法由简历可靠判断的项目会进入“待确认”，不会自动成为硬性淘汰条件。

这是一个明确的两阶段流程：

1. **本地校准**：上传简历后只使用本地岗位，提取有原文证据的能力并重算匹配。
2. **按画像采集**：用户确认搜索范围后，再主动点击审阅台的“2. 按画像采集”，或运行 `py -3 main.py --collect-from-resume`。

第二阶段会使用已登录的 Boss CDP 会话，只读采集岗位与公司信息，然后自动重跑离线分析和匹配；不会调用模型、投递或沟通。每轮默认在现有采集库存上追加 50 条目标，可通过 `collection.resume_collection_increment` 调整。登录失效或触发安全校验时会停止外部采集，并保留第一阶段的本地结果。采集运行期间不能上传或校准另一份简历，避免两个画像同时改写匹配快照。

## 个人匹配预览

如需人工维护高级档案，也可以用私密候选人档案对采集到的全部岗位做一次可解释的离线匹配。档案只放在本地 `private/candidate-profile.json`，不要提交到 Git：

```bash
py -3 main.py --match-preview
py -3 main.py --review-jobs
```

进入审阅台的“匹配校准”后，结果会分为“优先投 / 挑战投 / 当前不建议投”，并展示个人档案、v1/v2 判断、JD 原文证据、能力优势、能力缺口、置信度和信息不足。同一版本重跑会刷新该快照；更换分析版本或候选人版本后，新旧版本会并存。已有的 60 个决定、备注、学习标记都会保留；不会访问 Boss、调用模型或自动投递。

修改人工档案时，请复制为新的 `version`，不要覆盖已经使用过的版本。默认产品路径应优先使用简历校准，人工 JSON 作为高级入口保留。

## 个人知识库（可选）

打招呼语与定制简历可以基于你的个人知识库生成，`config.yaml` 的 `knowledge.provider` 支持三种模式：

| 模式 | 说明 | 适合 |
|---|---|---|
| `none` | 不使用，仅依据母版简历（默认） | 没有知识库，开箱即用 |
| `local` | 读取本地 `knowledge/` 目录的 .md/.txt | 想快速补充项目复盘、量化成果 |
| `rag` | 按岗位 JD 实时调用外部检索接口，每个岗位只注入最相关的知识片段 | 已有知识库系统（如个人数字分身、向量库） |

`rag` 模式的 HTTP 接口契约见 [docs/rag-api.md](docs/rag-api.md)，实现该契约的任何系统都能接入。

## 目录结构

```
main.py                      # 入口（--platform boss / --dry-run）
config.example.yaml          # 配置模板（求职要求、知识库、平台参数）
jobagent/
  config.py                  # 配置加载
  ai.py                      # OpenAI-compatible：打分 / 打招呼语 / 定制简历
  knowledge.py               # 知识提供者：none / local / rag
  resume.py                  # 母版简历与定制简历落盘
  store.py                   # SQLite 投递记录（防重复）
  platforms/
    base.py                  # 平台抽象基类
    boss.py                  # Boss直聘（SELECTORS 在文件头部，改版时先查这里）
knowledge/                   # local 模式知识库目录（内容不提交仓库）
docs/rag-api.md              # rag 模式接口契约
```

## 常见问题

- **脚本跑着跑着不动了 / 抓不到岗位**：大概率是 Boss 前端改版或触发验证码。改版时用 F12 检查页面元素并更新 `jobagent/platforms/boss.py` 头部的 `SELECTORS`；验证码需手动在浏览器窗口完成。
- **打分不准**：优先完善 `preferences` 的四个分类（期望/硬性/加分/排除），再调 `match.min_score`。

## 风险提示

浏览器自动化可能违反招聘平台用户协议，存在账号限流、封禁风险。请务必先 `--dry-run` 验证效果，控制 `max_apply_per_run` 和投递频率，风险自负。

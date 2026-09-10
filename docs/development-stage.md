# 开发阶段记录

当前阶段：S1b 持久化更新任务骨架，LOCAL_READY；真实采集执行与 UI 尚未开始。
最近完成：框架/spec/规范落库；只读计划器与可恢复任务状态、HTTP 查询/启动/停止入口实现，测试通过。
下一步：把任务状态接入更新监管 UI，再在单一浏览器租约下接收藏、搜索、旧详情的统一执行。

```yaml
stage: S1a-jd-update-plan
outcome: 提供独立来源预算、到期复核顺序和明确覆盖缺口的只读本地更新计划
controls:
  execution: STAGED
  risk: STANDARD
  delivery: LOCAL
state: CLOSE
preset: null
scope:
  owned: [AGENTS.md, docs/product-contract.md, docs/architecture.md, docs/specs/v1-controlled-outreach.md, docs/development-stage.md, jobagent/jd_update.py, jobagent/job_updates.py, jobagent/review_server.py, tests/test_jd_update.py, tests/test_job_updates.py]
  forbidden: [真实简历, config.yaml, 个人数据库内容, 外部发送]
  unrelated_or_unknown: [本轮开始前的 README/config.example/main/review_server/ai/UI/runtime_safety 修改, 原有未跟踪 outreach/pilot 与对应测试]
dod:
  - 框架、产品边界、阶段 spec 与项目规范落库
  - 旧库已达目标时仍计划增量轮，收藏搜索预算独立
  - 复核按人工意向排序且去重，终态和复扫不混入详情队列
  - HTTP 任务可持久化查询、重复启动复用、重启标记中断、停止可追溯
  - 本阶段仍不调用外部服务，明确计划不是执行结果
approvals:
  - 用户授权先落库框架与 spec 再开始开发；产品更新要求由用户主动触发
verification:
  focused: [py -m pytest tests/test_job_updates.py tests/test_review_workbench.py -q]
  stage_exit: [py -m pytest tests/test_collection.py tests/test_freshness.py tests/test_jd_update.py tests/test_job_updates.py tests/test_review_workbench.py -q, git diff --check]
  real_observation: [本阶段不执行真实 Boss 更新，不发送消息，不调用付费模型]
review:
  shape: combined
  correction_budget: 2
knowledge_impact: [架构与产品契约, 项目规范, 阶段接续记录]
  non_goals: [本阶段不接真实采集执行和浏览器租约, 不接 UI, 不生成三套简历, 不实现发送, 不声称全库 JD 已更新]
```

独立挑战结论：直接拼接现有采集会混淆总库存与本轮额度，且缺少统一租约与恢复状态。采纳收敛建议，先交付只读计划器；S1b 再统一采集、持久化任务、浏览器互斥、停止恢复和 UI。S1a 不是“更新岗位”端到端完成。

## 验证记录（2026-09-10）

- 范围：本阶段列出的 7 个新文件；原有脏修改未改动，不纳入本阶段提交。
- 首轮新模块测试因模块尚不存在失败；实现后 10 项通过。Windows cp936 输出回归先复现乱码，再修复，计划器 11 项通过。
- 受影响边界：`py -m pytest tests/test_collection.py tests/test_freshness.py tests/test_jd_update.py -q`，最终 47 项通过，5.95 秒。
- 独立审查：PASS，无 blocker；审查后仅增加 stdout/stderr UTF-8 配置与针对性回归。
- 实际只读计划：当前看板 379 条记录，163 条待详情复核；默认选 20，剩余 143；本轮收藏/搜索各 25，搜索范围来自现有配置。这里是时间点快照，不是全库永久事实。
- `git diff --check` 通过；已有文件出现 LF/CRLF 提示，不影响本轮文件。
- 外部证据：没有执行 Boss 更新、没有模型调用或消息发送；没有修改个人数据库、简历或重启服务。
- 知识对账：neat-freak 检查产品契约、架构、spec、AGENTS 和本记录，已更新；未写跨项目记忆。
- 失效条件：修改计划器或 freshness 输入结构后需重验；实时数据变化需重新生成计划。

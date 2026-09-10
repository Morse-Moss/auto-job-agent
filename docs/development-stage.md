# 开发阶段记录

当前阶段：S1b 更新任务、监管 UI 与采集执行骨架，执行器已绑定租约，LOCAL_READY；真实 Boss 运行尚未执行。
最近完成：持久化任务执行器绑定浏览器租约，按收藏 → 搜索 → 到期详情串行执行并写回各阶段真实计数；停止请求与安全/登录中断阻断后续阶段；新增 POST /api/job-updates/{run_id}/execute 显式后端动作；简历采集入口接入同一浏览器租约。
下一步：将真实 Boss 小批次验收单列执行，不与模拟验收混报；之后再推进三方向岗位分析。

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
  owned: [AGENTS.md, docs/product-contract.md, docs/architecture.md, docs/specs/v1-controlled-outreach.md, docs/development-stage.md, jobagent/jd_update.py, jobagent/job_updates.py, jobagent/job_update_executor.py, jobagent/platforms/boss_collector.py, jobagent/review_server.py, review_ui/index.html, review_ui/app.js, review_ui/styles.css, tests/test_jd_update.py, tests/test_job_updates.py, tests/test_incremental_executor.py, tests/test_review_workbench.py]
  forbidden: [真实简历, config.yaml, 个人数据库内容, 外部发送]
  unrelated_or_unknown: [本轮开始前的 README/config.example/main/review_server/ai/UI/runtime_safety 修改, 原有未跟踪 outreach/pilot 与对应测试]
dod:
  - 框架、产品边界、阶段 spec 与项目规范落库
  - 旧库已达目标时仍计划增量轮，收藏搜索预算独立
  - 复核按人工意向排序且去重，终态和复扫不混入详情队列
  - HTTP 任务可持久化查询、重复启动复用、重启标记中断、停止可追溯
  - 更新监管 UI 可生成、查看、刷新、取消本地计划；采集器支持独立来源目标与单一浏览器租约
  - 本阶段测试不调用真实外部服务
approvals:
  - 用户授权先落库框架与 spec 再开始开发；产品更新要求由用户主动触发
verification:
  focused: [py -m pytest tests/test_incremental_executor.py tests/test_job_updates.py tests/test_review_workbench.py -q, node --check review_ui/app.js]
  stage_exit: [py -m pytest tests/test_collection.py tests/test_freshness.py tests/test_jd_update.py tests/test_job_updates.py tests/test_incremental_executor.py tests/test_review_workbench.py -q, git diff --check]
  real_observation: [本阶段不执行真实 Boss 更新，不发送消息，不调用付费模型]
review:
  shape: combined
  correction_budget: 2
knowledge_impact: [架构与产品契约, 项目规范, 阶段接续记录]
  non_goals: [本阶段不执行真实 Boss 采集, 不接任务后台执行线程, 不生成三套简历, 不实现发送, 不声称全库 JD 已更新]
```

独立挑战结论：直接拼接现有采集会混淆总库存与本轮额度，且缺少统一租约与恢复状态。采纳收敛建议，先交付只读计划器；S1b 再统一采集、持久化任务、浏览器互斥、停止恢复和 UI。S1a 不是“更新岗位”端到端完成。

## 验证记录（2026-09-10）

- 范围：本阶段列出的 7 个新文件；原有脏修改未改动，不纳入本阶段提交。
- 首轮新模块测试因模块尚不存在失败；实现后 10 项通过。Windows cp936 输出回归先复现乱码，再修复，计划器 11 项通过。
- 受影响边界：`py -m pytest tests/test_collection.py tests/test_freshness.py tests/test_jd_update.py -q`，最终 47 项通过，5.95 秒。
- 独立审查：PASS，无 blocker；审查后仅增加 stdout/stderr UTF-8 配置与针对性回归。
- 实际只读计划：当前看板 379 条记录，163 条待详情复核；默认选 20，剩余 143；本轮收藏/搜索各 25，搜索范围来自现有配置。这里是时间点快照，不是全库永久事实。
- `git diff --check` 通过；已有文件出现 LF/CRLF 提示，不影响本轮文件。
- UI 实测：Edge headless，1440×1000 与 390×844；生成计划、刷新恢复、取消、再次生成均通过；无 page error、无横向溢出。Playwright 内置 Chromium 未安装，未联网下载，使用已安装 Edge 完成实测。
- 外部证据：没有执行 Boss 更新、没有模型调用或消息发送；没有修改个人数据库、简历或重启服务。
- 知识对账：neat-freak 检查产品契约、架构、spec、AGENTS 和本记录，已更新；未写跨项目记忆。
- 失效条件：修改计划器或 freshness 输入结构后需重验；实时数据变化需重新生成计划。

## 验证记录（2026-09-10 执行器接通）

- 范围：`jobagent/job_update_executor.py`、`jobagent/job_updates.py`、`jobagent/review_server.py`、`tests/test_incremental_executor.py`。原有脏修改仍未纳入本阶段提交。
- 先写失败测试再实现：新增 5 项测试首轮全部红灯（缺 `run`、`write_plan`、`finish`、`create_server(job_update_executor=...)`），实现后 9 项通过。
- 新增能力：执行器按收藏 → 搜索 → 到期详情串行执行；每阶段写回 attempted/collected/external_status/error；停止请求后不开启新来源；安全校验与登录失效暂停全部后续阶段；`BrowserLease` 增加可在工作线程调用的 `release`，冲突异常收敛为 `BrowserLeaseBusy` 并在 HTTP 层返回 409。
- 互斥：`start_resume_collection` 现在先取浏览器租约再启动线程，与“boss-update”共用同一租约；采集线程结束或异常均释放租约与校准锁。
- 执行入口：`POST /api/job-updates/{run_id}/execute` 是唯一外部动作入口；GET 状态与生成计划均不触发执行，测试已断言 `collector.calls == []`。
- 受影响边界：`py -m pytest tests/test_collection.py tests/test_freshness.py tests/test_jd_update.py tests/test_job_updates.py tests/test_incremental_executor.py tests/test_review_workbench.py -q`，101 项通过。
- 全量：`py -m pytest -q`，214 项通过；唯一告警来自既有 `tests/test_outreach.py` 的子进程编码，与本轮无关。
- `git diff --check` 通过，仅有既有的 LF/CRLF 提示，无空白错误。`node --check review_ui/app.js` 未改动 UI，无需重跑。
- 外部证据：本轮全部使用注入的模拟采集器与临时数据库，未连接 CDP、未访问 Boss、未调用模型、未发送消息、未修改个人数据库。
- 已知缺口：任务汇总尚未细分“详情成功/失败、旧 JD 已变/未变/确认关闭”，需要从详情版本与新鲜度表取数后补。真实 Boss 验收仍未执行。
- 有意偏离 spec：spec 的 S1b 接口表要求 `POST /api/job-updates`（start）即原子获取浏览器租约并返回 409。实现改为 start 只建任务、由 `POST /api/job-updates/{run_id}/execute` 才获取租约。理由是“生成计划”属于本地只读动作，不应长时间占住浏览器而阻塞简历采集；且这更贴合“不让页面加载或生成计划触发外部动作”的硬约束。互斥能力未削弱：任一时刻仍只有一个持有者。若后续要对齐 spec 原文，需同步修改本条与产品契约。
- 修复：采集线程启动失败时复位 `collection_state`，避免状态卡在 running 并泄漏租约；已加回归测试。

## 验证记录（2026-09-10 细分指标）

- `CollectionStore.run_metrics` 现在分别返回详情尝试、成功、失败、明确关闭、已变、未变；无刷新事件保持 0/未知边界，不猜测内容变化。
- 兼容旧测试数据库缺少 `source_run_id` 的情况，不因历史 schema 让执行任务假失败。
- 受影响测试：`py -m pytest tests/test_collection.py tests/test_freshness.py tests/test_jd_update.py tests/test_job_updates.py tests/test_incremental_executor.py tests/test_review_workbench.py -q`，104 项通过。
- 外部证据仍为空：未连接 CDP、未访问 Boss、未调用模型、未发送消息、未修改个人数据库。

## 真实 Boss 小批次验收（2026-09-10）

- 服务：重启审阅服务加载当前源码；项目配置 `http://127.0.0.1:17332` 正常连接。
- 任务：`jd-update-94ecd4fb06cf41628dec38321ddc3a47`，仅授权小批次：收藏 3、搜索 3、到期详情 2。
- 结果：收藏阶段尝试 3 次、采集 0 条；Boss 返回安全校验，任务状态为 `paused`，搜索与详情阶段均未启动。
- 现场证据：17332 会话页面显示“BOSS 安全提示”，URL 含 `_security_check=1`；未重试、未绕过、未发送消息、未调用模型。
- 结论：真实更新为部分完成/安全中断，不得声称全库 JD 已更新。后续需用户在 Boss 页面完成平台要求的安全处理后，再由用户明确授权新的小批次执行；当前任务不自动续跑。

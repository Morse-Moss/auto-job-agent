# auto-job-agent 项目规范

## 权威与恢复

- 产品约束：[docs/product-contract.md](docs/product-contract.md)。架构：[docs/architecture.md](docs/architecture.md)。阶段 spec：[docs/specs/v1-controlled-outreach.md](docs/specs/v1-controlled-outreach.md)。
- 接续任务先读 [docs/development-stage.md](docs/development-stage.md)，核对当前代码与运行状态；文档的目标能力不等于已实现。
- 非平凡开发沿用用户的 Morse 生命周期与 Karpathy 原则；不另建竞争流程。改 UI 使用 morse-design，先明确交互再实现。

## 结构与命令

- 保持 Python 模块化单体、SQLite 和现有原生前端。采集、分析、资产、审核、执行各有边界，优先复用现有模块。
- 本地测试：`py -m pytest -q`；采集边界：`py -m pytest tests/test_collection.py -q`。若 `python` 是 Store 别名，使用 `py` 或实际 Python 解释器。
- 本地 CI：`py scripts/ci.py`（ruff lint + Python/JS 编译检查 + 全量测试）。提交与推送由 `.githooks/` 钩子自动把关（pre-commit 只查改动文件，pre-push 跑完整 CI）；GitHub Actions 在 push/PR 时跑同一脚本。
- 本地审阅：`py main.py --review-jobs --review-port 8765`。此命令启动本地服务，不等于完成外部验证。
- 不运行裸 `main.py` 验证功能：旧默认路径可能发送消息。`--dry-run` 也不是离线测试，会访问浏览器与模型。

## 不变量

- 页面读取不得触发外部采集或发送；真实发送逐岗由用户点击授权，不用历史试点授权替代当前审核。
- 最新明确确认的 Word 是事实基准；v1 只做三套通用资产，不逐 JD 重写整份简历。
- 列表未见不是关闭证据。旧数据失败保留；人工决定和历史不得被机器重算覆盖。
- 平台登录/验证码/安全警告立即暂停；结果未知不盲重试；文本和长图独立记账。
- 模型是内容建议者，平台适配器执行动作；禁止模型绕过审核调用发送。新发送上线前封闭旧自动入口。
- 秘钥、CDP 会话、私人配置、真实简历、个人数据库和日志禁止入 Git；日志不保存签名 URL 或认证头。
- 不覆盖其他任务的脏文件。提交只纳入本轮可证明归属的文件或补丁，禁止全量暂存。

## 验收

新增行为先有失败测试；使用临时数据库、模拟平台与模型。变更共享边界后运行受影响测试；UI 必须实看。每阶段记录当前源码检查、本地测试、服务加载与真实平台验收，不能互相替代。

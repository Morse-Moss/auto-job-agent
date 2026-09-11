# 差距分析与交付前待办（2026-09-11）

对照 docs/product-contract.md 与 docs/specs/v1-controlled-outreach.md，逐项盘点 v1 受控触达管线的现状。

## 一、结论先行

v1 六步管线中 S1、S2、S3、S4 已完成，S5 代码与界面已完成但缺真实发送试点验收，S6（沟通进展同步）整段未实现。
最大的两个交付阻塞：真实发送试点未执行（P0）、resume-pack 素材包不受版本控制导致新环境启动即报错（P0）。
当前 268 个测试全部通过，ruff 无告警，py scripts/ci.py 退出码 0，CI/lint/构建自动化已就位。

## 二、逐项差距清单

### S1 JD 更新（已完成，无功能缺口）

已实现：py -m jobagent.jd_update 命令；S1b job-updates API 全套（GET /api/job-updates/plan、POST /api/job-updates、GET /api/job-updates/{run_id}、POST /api/job-updates/{run_id}/stop），含 recover_interrupted 中断恢复。
遗留（P3，可选）：仅缺一次真实运行验收。工作量 0.5h，不阻塞交付。

### S2 岗位分析（已完成）

三个方向已经用户确认，无缺口。

### S3 三份简历资产（素材已交付，存在工程缺口）

用户已交付 01-FDE-AI解决方案交付 / 02-AI-Agent应用开发 / 03-AI产品与业务自动化（docx+jpg+pdf），位于 output/resume-pack-20260911/。

缺口1（P0，交付阻塞）：output/ 整目录被 .gitignore，而 config.resume.assets 指向 output/resume-pack-20260911/。任何新 clone 或 CI 环境启动时 normalize_resume_assets 找不到文件会直接报错。
方案A（推荐）：把素材包移入受版本控制的 assets/resume-pack-20260911/，同步改 config.yaml 与 config.example.yaml。
方案B：.gitignore 加例外规则 !output/resume-pack-20260911/。
工作量 0.5h。

缺口2（P2）：长图（1400x2201）在 Boss 真实聊天框的显示尺寸未实测。产品合同明确"未实测不声明发送可用"。随真实发送试点一并完成，单独不阻塞。

### S4 投递包生成（已完成）

13 个岗位的投递包已全部生成（manual 生成器产出招呼语 + 简历资产选择与自动建议）。岗位分布：fde-solution 6、agent-dev 3、ai-product-ops 4。

缺口（P2）：多供应商配置已就位（manual / deepseek-chat / deepseek-reasoner / apizh-terra），但 deepseek 的 key 为占位符，active=manual。可交付状态；待用户决定切换时填 key 即可，工作量 0（填 key 1 分钟）。

### S5 受控首次触达（代码完成，验收未做）

已实现：BossSender（send_text / send_image / _assert_safe 前置检查）、授权+执行路由（台账 per-task 唯一约束、pending/running/confirmed/failed 状态机、确定失败最多重试 2 次）、前端"受控发送"授权门 UI、9+3 个专项测试。

缺口1（P0，交付阻塞）：真实发送试点未执行。产品合同要求最多 3 个岗位、逐岗用户点击授权。这是 v1 验收的核心路径。前置条件：MorseTools 浏览器服务（general-web）在线且 Boss 登录态有效。
工作量 1h（含观察 3 次真实发送）。

缺口2（P1）：浏览器服务依赖 127.0.0.1:17332，非会话持久，掉线需手动重启。建议做发送前一键预检（浏览器可达 + 登录态），至少写入操作手册。工作量 1h。

### S6 沟通进展同步（整段未实现，P0 功能缺口）

v1 范围内唯一整段缺失的模块。需要补齐：
数据层：沟通进展表（job_id / platform / 时间线事件 / 状态 / 备注）。
接口：GET /api/progress、POST /api/progress（v1 允许人工登记，页面抓取可选）。
前端：岗位详情内的时间线视图。
工作量 0.5-1 天。影响：没有它则闭环终点缺失，v1 无法宣告完成。

### 基建与文档

CI/lint/build（已完成）：pyproject.toml（ruff）、requirements-dev.txt、scripts/ci.py（lint+build+test）、.githooks/pre-commit 与 pre-push（core.hooksPath 已配置）、.github/workflows/ci.yml。README 与 AGENTS.md 已补 CI 段。

文档缺口（P1）：development-stage.md 停留在 S5 之前的进度；缺少一份面向使用者的操作手册（启动服务、逐岗授权发送、发送前置检查、故障排查）。工作量 1h。

Git 缺口（P0 操作项）：30 个文件未提交（S5 发送实现、简历资产、CI 三批）。需分批提交。注意 resume/master_resume.md 显示为已修改（M）——按既定规则我未动它，需用户确认这是本人改动还是需要回滚。

## 三、建议实施顺序

1. 分批提交当前工作（S5 发送实现 / 简历资产 / CI 三批）—— 0.5h
2. 修复 resume-pack 版本控制问题（移出 output/ 或 gitignore 例外）—— 0.5h
3. 真实发送试点（不超过 3 岗，逐岗点击授权；前置做浏览器预检）—— 1h
4. 实现 S6 沟通进展同步（表 + API + 时间线 UI）—— 0.5-1 天
5. 文档收尾（development-stage 更新 + 操作手册）—— 1h
6. 终验：py scripts/ci.py 全绿 + 268 测试通过 + 13 岗位投递包逐条核对

## 四、交付前待办清单

[P0] 提交 30 个待提交文件（需用户确认 master_resume.md 处理方式）
[P0] resume-pack 素材包纳入版本控制并更新 config 引用
[P0] 真实发送试点 3 岗（用户逐岗授权点击）
[P0] S6 沟通进展同步模块（表/API/UI）
[P1] 发送前浏览器预检（可达性 + 登录态）
[P1] development-stage.md 更新至 S5 完成点
[P1] 使用者操作手册
[P2] 长图在 Boss 聊天框的显示尺寸实测
[P2] deepseek key 待用户切换时填入
[P3] S1 真实运行验收（可选）

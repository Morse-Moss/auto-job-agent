# Auto Job Agent 🤖

AI 求职助手：**Boss直聘自动打招呼 + 按 JD 定制简历 + 自动投递**，由 DeepSeek 驱动。

## 工作流程

```
搜索岗位 → 抓取 JD → AI 匹配度打分(0-100) → 低分跳过
                                    ↓ 高分
                  按 JD 生成定制简历要点 + 个性化打招呼语
                                    ↓
                    自动点击「立即沟通」发送 → 记录数据库防重复
```

## ⚠️ 免责声明

- 浏览器自动化**可能违反 Boss直聘服务条款**，存在账号被限流/封禁的风险，请自行评估并控制频率。
- 建议先用 `--dry-run` 模式观察 AI 打分效果，再开启真实投递。
- 仅供个人求职使用，请勿用于高频群发或骚扰。

## 快速开始

```bash
# 1. 安装依赖（需要 Python 3.10+）
pip install -r requirements.txt
playwright install chromium

# 2. 配置
cp config.example.yaml config.yaml        # 填 DeepSeek API Key、关键词、城市等
cp resume/master_resume.example.md resume/master_resume.md   # 写你的母版简历

# 3. 试运行（只打分，不打招呼）
python main.py --dry-run

# 4. 正式运行（首次会弹出浏览器，扫码登录 Boss，之后 Cookie 自动复用）
python main.py
```

## 配置说明

见 `config.example.yaml` 内注释。关键项：

| 配置 | 说明 |
| --- | --- |
| `ai.api_key` | DeepSeek API Key（https://platform.deepseek.com 获取） |
| `match.min_score` | 匹配分阈值，低于此分自动跳过（建议 60-75） |
| `boss.keywords` | 搜索关键词，可配多个 |
| `boss.city` | 城市（支持列表见 `jobagent/platforms/boss.py` 的 `CITY_CODES`） |
| `boss.max_apply_per_run` | 单次运行最多打招呼次数（防风控，建议 ≤20） |
| `boss.delay_range_seconds` | 两次操作间随机等待区间 |
| `boss.blacklist_keywords` | 岗位名/JD 命中即跳过（如 外包、驻场） |

## 产出物

- `output/jobagent.db`：SQLite 投递记录（岗位、公司、分数、理由、是否已打招呼）
- `output/tailored/`：每个已投递岗位的**定制简历要点**（Markdown），面试前可直接参考
- `.state/boss_state.json`：登录态 Cookie（勿泄露、勿提交）

## 项目结构

```
main.py                      # 命令行入口
config.example.yaml          # 配置模板
jobagent/
├── config.py                # 配置加载
├── ai.py                    # DeepSeek：匹配打分 / 打招呼语 / 简历定制
├── resume.py                # 母版简历加载 + 定制版输出
├── store.py                 # SQLite 投递记录
└── platforms/
    ├── base.py              # 平台抽象基类
    └── boss.py              # Boss直聘实现
resume/master_resume.example.md
```

## Roadmap

- [x] Boss直聘：搜索 / AI 打分 / 定制简历 / 自动打招呼
- [ ] 猎聘（实现 `jobagent/platforms/liepin.py`，继承 `BasePlatform` 即可）
- [ ] 智联招聘
- [ ] 聊天中自动发送简历附件 / 图片简历
- [ ] 定时任务 + 投递日报通知

## 常见问题

**脚本突然不工作了？** Boss 前端经常改版，九成是 DOM 选择器变了。所有选择器集中在 `jobagent/platforms/boss.py` 顶部的 `SELECTORS` 字典，用浏览器 F12 对照更新即可。

**AI 打分不准？** 调整 `jobagent/ai.py` 中的提示词，或提高/降低 `match.min_score`。

**登录态失效？** 删除 `.state/boss_state.json` 重新扫码。

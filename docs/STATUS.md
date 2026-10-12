# 当前进度

2026-10-12 更新。给接手的人或 agent 看：做到哪了、下一步做什么。开发过程和学到的东西写在 `DEVLOG.md`，这里只留现状。

## 现在的状态

M0–M7 完成（主 agent、记忆、检索、采集、主动推送、网页端含手机版）。现在的重点是**工具调用与任务编排**，按轮迭代，见 `PURPOSE.md` 的"现在的目标"和 `DEVLOG.md`。
截止：北京时间 2026-10-31 01:00。

## 每个模块留下的、HR 能核实的东西

| 模块 | 入口 | 指标 |
|---|---|---|
| M1 骨架 | `uv run polaris metrics m1`；`uv run pytest` | `eval/reports/M1.md`：追踪完整率 100%，合成数据事实核对 60/60 |
| M2 工具层 | `uv run polaris agent ask "问题"`（可录制回放）；MCP 在 `/mcp/` | `eval/reports/M2.md`：工具调用套件 14 题 pass^3 14/14 |
| M3 采集和抽取 | `uv run polaris collect …`、`extract job|post` | `eval/reports/M3.md`：真实岗位 50 条逐字段 F1 0.99 |
| M4 对齐和检索 | `uv run polaris align`、`index`、`search "问题"` | `eval/reports/M4.md`：同岗识别测试集 F1 0.93；检索 NDCG@10 0.73 → 0.89 |
| M5 记忆 | `uv run polaris metrics m5`（单独的库） | `eval/reports/M5.md`：开发 26/28、留出 14/16、测试 13/16 |
| M6 主 agent 和推送 | `uv run polaris say "……"`、`daily`、`metrics injection` | `eval/reports/proactive.md`、`injection.md`：注入 15 次攻击 0 次得逞 |
| M7 网页端 | `.\scripts\start.ps1`（`-Sandbox` 沙盒、`-Phone` 手机） | 浏览器回归 4 组（卡片、跨模块、追踪、手机）；`eval/reports/answers.md`：回答质量开发集 24/24、留出集 13/14 |

怎么用、怎么测：`GUIDE.md`。测试：`uv run pytest`（249 项，独立的 polaris_test 库）；网页回归在 `web/tests/`。

## 下一步

1. **第 1 轮（进行中）**：作者用真实问题问 20 次并标"对 / 有问题"，之后归类、挑一类改。见 `DEVLOG.md`。
2. **之后几轮**：按 `PURPOSE.md` 的完成标准，建从真实使用来的任务题集（开发、留出、测试分开），每个机制做开和关的对比，换一个模型验证。
3. **M8–M10**（`PLAN.md` 第 8 节）：本机连续运行和真实反馈；上云（阿里云香港轻量、HTTPS、合成数据的公开演示）；英文 README、3 分钟视频、提交。

## 用户已经做的决定

- 目的是找到 LLM 和 Agent 方向的实习：HR 能逐项核实的证明材料 + 作者自己每天用的工具（`PURPOSE.md`）。
- 10-12：技术侧重点是工具调用与任务编排；检索、冲突处理、长期记忆保持现有水平。开发方式是老师带着做、每轮只改一处、作者判断。
- 技术栈以 LangGraph 为核心；开发和评测用第三方 OpenAI 兼容服务（deepseek-v4.1-flash）和阿里云（向量、重排）。不用 Tavily，不向用户提 Nebius 和 Tavily。
- 采集按方向找岗位，只看风向标大厂；小红书、脉脉（只采内容不采人）、Boss 用本人一个账号、只读、低频、不伪装，原文不进仓库和公开演示（`DATASET.md`）。
- 先在本机做完，再上云，最后接微信。云主机用阿里云香港轻量，许可证 Apache-2.0。
- 只做本地提交，不推 GitHub；署名 `duoduo-cell`（GitHub 专用邮箱，只设在本仓库）。仓库里不写开发用转接服务的名字，只写"第三方 OpenAI 兼容服务"。

## 等用户办的事

- 第 1 轮的 20 条标注（见上）。
- 重启 API 和常驻采集，用上新代码（停之前先 Ctrl+C 或确认空闲）。
- 手机：电脑和手机装 Tailscale、登录同一账号，再运行 `.\scripts\start.ps1 -Phone`（`GUIDE.md`"在手机上用"）。
- Boss 登录已过期，要重新扫码。
- `.env` 里的 OWNER_TOKEN 建议换一个新值（10-12 曾在开发助手的输出里出现过一次，只在本机）。

## 和用户沟通时注意

- 用户常用语音输入，文字里会有同音错字，按上下文理解。
- 回答简洁、先说结论；讲解用大白话、一步一个概念，细节写进 `DEVLOG.md`；技术判断要有出处。
- 用户提出不同看法时先核实：对的直接改，不对的说明依据。
- 少用术语。比如"标注集"要说成"标准答案集：人读原文把正确答案写下来，用来算准确率"。

# 当前进度

2026-10-11 傍晚更新。这份文件是给接手的人或 agent 看的：做到哪了，下一步做什么。每完成一个模块更新一次。

## 现在的状态

M0、M1、M2 已完成；M3 主体已完成，只差 Boss（令牌二三十分钟过期，要用户在 Boss 窗口里点一下）；M4 主体已完成；M5 记忆已完成；M6 的主动决策规则已完成（排期都提前）。10-11 还做完了技术调研和最终计划：项目介绍（`INTRO.md`）、技术栈（`STACK.md`）、模块顺序（`PLAN.md`）。

| 项 | 状态 |
|---|---|
| 方向、架构、模块顺序 | 已定，见 `PLAN.md`（10-11 重订） |
| 项目介绍和核心技术点 | 已定，见 `INTRO.md`，每条附依据 |
| 技术栈 | 已定，见 `TECH.md`（唯一的技术方案，10-11 重写）：LangGraph 加 LangChain、Postgres 加 pgvector 加 pg_search、Neo4j（可关）、Next.js 加 Vercel AI SDK、自有追踪表 |
| 岗位调研 | 字节 50 个、小红书 59 个，见 `research/`；阿里官网今天没有在招的校招和实习岗位 |
| M0 冒烟 | 开发用的对话服务和阿里云的接口已测完 |
| M1 骨架 | 完成。设计见 `design/M1.md`，指标见 `../eval/reports/M1.md` |
| M2 工具层和 agent 底座 | 完成。设计见 `design/M2.md`，指标见 `../eval/reports/M2.md` |
| M3 采集和抽取 | 主体完成。设计见 `design/M3.md`，指标见 `../eval/reports/M3.md`。库里：腾讯 139、字节 217、小红书官网 20 个岗位，小红书笔记 30 多条；Boss 已接好，等扫码 |
| M4 对齐、图谱、检索 | 主体完成。设计见 `design/M4.md`，指标见 `../eval/reports/M4.md`。数据库换成 Postgres + pgvector + pg_search（BM25，jieba），Neo4j 知识图谱；常驻采集每轮后自动刷新 |
| M5 记忆 | 完成。设计见 `design/M5.md`，指标见 `../eval/reports/M5.md`。双时间线事实表、规则决定的更新、待确认和抑制、记忆工具 |
| M6 主 agent | 主动决策规则完成（`proactive/decide.py`，指标 `../eval/reports/proactive.md`）；主 agent、每日流程、审批未开始 |
| M7 到 M11 | 没开始 |
| 本机环境 | Python 3.12（uv 管理）、git、Node 24、Docker Desktop、WSL2 |

## 每个模块留下的、HR 能核实的东西

| 模块 | 能运行的入口 | 指标 | 提交 |
|---|---|---|---|
| M1 | `docker compose up -d --build` 后打开 http://127.0.0.1:8000/docs；`uv run polaris seed`、`uv run polaris metrics m1`；`uv run pytest` | `eval/reports/M1.md`：追踪完整率 100%，合成数据事实核对 60/60，入库幂等，缓存命中 3/3 | 见 git log |
| M2 | `uv run polaris agent ask "问题"`（可录制、回放）；MCP 服务在 `/mcp/`；`uv run polaris metrics m2`；`uv run pytest`（61 个，含 14 个不花钱的录像回放回归） | `eval/reports/M2.md`：工具调用套件 14 题 pass^3 14/14，参数一次合格率 100%，模型调用追踪 131/131，完整回放 14/14 一致。题目偏简单，验证的是底座 | 见 git log |
| M3 | `uv run polaris collect all`（各来源侦察并行）或单个来源 `collect tencent|bytedance|xhs-careers|xhs|boss`；`uv run polaris extract job|post`；`uv run polaris metrics m3`（不调模型）、`metrics m3-tools`（调模型）；`uv run pytest`（90 多个，含 M3 官网题的录像回放） | `eval/reports/M3.md`：真实岗位 50 条标准答案逐字段 F1 0.99；小红书笔记开发集 v1 0.96 → v2 1.00，留出集 11 条全对（样本小）；真实数据工具调用套件 real-v1 14 题 pass^3 14/14，仍偏简单 | 见 git log |
| M4 | `uv run polaris align`（对齐并评分）、`polaris index`（切块、向量、图谱）、`polaris search "问题"`、`polaris metrics m4`；`uv run pytest`（134 个） | `eval/reports/M4.md`：同岗识别测试集（规则冻结后才标）P=R=F1=0.93；合成冲突发现 10/10；出处可追溯 100%；检索 32 题 NDCG@10 从 BM25 0.73 到完整流水线 0.86 到 0.89，逐步消融 | 见 git log |
| M5 | `uv run polaris metrics m5`（在单独的 polaris_memeval 库里跑，会调模型）；`uv run pytest tests/test_memory.py` | `eval/reports/M5.md`：留出集（规则改完才写）记忆操作 F1 0.92、问答 16/16；开发集第一次 0.90 → v2 0.93 | 见 git log |
| M6（部分） | `uv run polaris metrics proactive`（不调模型）；`uv run polaris metrics m4-bench`（Polaris-Bench v2，会调模型） | `eval/reports/proactive.md`：开发集 23 条、留出集 19 条全对（规则和标签同一作者，待作者本人补标）；`eval/reports/M4.md` 第 5 节：Polaris-Bench v2 knowledge 54/54、search 42/54 | 见 git log |

## 下一步

0. **按 `PLAN.md` 第 0 节的最终计划 v4 做：项目主体是 agent 本身。** 采集常驻后台（小红书、脉脉、Boss，系统计划任务「Polaris 常驻采集」，日志 `data/logs/daemon.log`，看进度 `scripts/status.ps1`），每轮后自动对齐、切块、算向量、重建图谱。
0.5. **M3.5 数据集（10-11 用户插入）：** 按 `DATASET.md` 第 7 节：原始层补字段、常驻采集进程、官网扩到 22 家、牛客、脉脉、小红书和 Boss 全天慢采并回补近半年、面试题表。采集之后在后台一直跑。
1. **M6 主 agent（下一步）：** 一个对话入口把记忆（每句话先过 `memory/extract.remember`，卡片放进系统提示）、检索和图谱工具、主动推送串起来；LangGraph Postgres 检查点存会话；对话长了用摘要中间件压缩；有副作用的工具（忘掉记忆、起草消息）走人工审批；每日流程（刷新知识层 → 主动决策 → 简报）。
2. **评测的下一轮：** Polaris-Bench v2 已满分，要加更难的题（多轮追问、跨工具多跳、要先问清条件的模糊问题）；记忆"该问没问"（模型把换个数当改口）要用代码核对改口词，改完再写新的留出集；同岗识别的两处错（多语言 JD、部门名字面包含）同样改完再标新测试集。
3. **M4 收尾：** 官网每天快照推断下架；Boss 岗位接进对齐后看真实的跨来源冲突。
4. **请作者本人做的：** 按自己的习惯标一批主动推送的场景（推 / 简报 / 不提），检验规则会不会打扰人。

## 用户已经做的决定

- 项目的目的是找到 LLM 和 Agent 方向的实习：既是 HR 能逐项核实的证明材料，也是作者自己每天用的工具。见 `PURPOSE.md`。
- 重点打磨存储检索和冲突处理；技术栈按真实岗位要求调研后再定（已写进 `PURPOSE.md`）。
- 2026-10-11：技术栈由 agent 按"行业通用、方便、大厂认可"自行决定，用户认可以 LangGraph 为核心。
- 2026-10-11：核心链路是"采集 → 知识图谱 → 记忆 → 推荐"，涉及工具调用、记忆压缩、信息采集。采集以小红书为核心（横向和纵向深挖），Boss 大量搜索和筛选，只看风向标大厂，小公司不要。
- 2026-10-11：开发和评测全部在本机用第三方 OpenAI 兼容服务（deepseek-v4.1-flash）和阿里云（向量、重排）完成；不用 Tavily；不要再向用户提 Nebius 和 Tavily。
- 采集按方向找岗位，公司只看风向标大厂（名单作为配置维护，10-11 决定）。
- 2026-10-11：先大量采集建数据集，范围近半年；脉脉也自动采（只采内容不采人）；每个平台只用本人一个账号，不伪装、不绕过限制。见 `DATASET.md`。时间字段记的是岗位自己的时间。
- 小红书和 Boss直聘必须做，在用户本机跑，不进公开演示。
- git 提交署名 `duoduo-cell`，邮箱用 GitHub 专用邮箱，只设在本仓库。提交按大的节点来。
- 仓库里不写开发用转接服务的名字和来历，只写"第三方 OpenAI 兼容服务"。
- 先在本机把整套程序做完，再上云，最后接微信。
- 云主机用阿里云香港轻量，许可证用 Apache-2.0。
- 现阶段不推 GitHub，只做本地提交。

## 等用户办的事

见 `PLAN.md` 第 11 节。小红书已扫码登录（10-11）。现在等的是 Boss：用 Boss直聘 App 扫终端里的二维码；另外请抽查 `eval/gold/jobs_v1.jsonl` 里的 10 条标准答案。

## 和用户沟通时注意

- 用户对云服务、许可证、接口这类概念不熟，提到时用一句话说清它是什么、为什么需要。
- 用户常用语音输入，文字里会有同音错字，按上下文理解。
- 用户提出不同看法时先核实，他对的地方直接改，不对的地方说明依据。
- 用户希望回答简洁、直接，先说结论；技术判断要有出处，不能凭印象。
- 少用术语。比如"标注集"要说成"标准答案集：人读原文把正确答案写下来，用来算准确率"。

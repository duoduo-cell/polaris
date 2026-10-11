# 北极星：技术栈（选型过程记录）

> 2026-10-11 起，技术方案以 `TECH.md` 为准（它吸收了本文的选型表，并按参考答案 WeKnora 做了调整，见 `REFERENCE_WEKNORA.md`）。本文保留选型时的调研和理由。其中"中文关键词检索"一行已改：用 ParadeDB 的 `pg_search`，它有中文分词器 `chinese_lindera`，本文当时写"没找到"是查错了。

2026-10-11 定稿。用户的原则：用行业通用、方便的，优先选字节、小红书、阿里这些公司招人时认可的；每一项都要有依据。项目要做什么见 `INTRO.md`，模块顺序见 `PLAN.md`。

## 选型依据从哪来

1. **大厂在招什么。** 字节跳动官网 50 个 Agent 研发实习岗位（`research/2026-10-11-bytedance-agent-intern-jd.md`）；小红书官网 2027 届校招研发方向 89 个岗位，其中 59 个和 AI、Agent、全栈相关（`research/2026-10-11-xiaohongshu-agent-jd.md`）。阿里集团、阿里云的校招和实习页面今天都显示"暂无在招职位"，所以阿里只看它的开源项目。
2. **大厂自己开源了什么。** 字节：DeerFlow（多 agent 的超级 agent 框架，基于 LangGraph）[A5]、Coze Loop（提示词、评测、追踪）[E6]、Midscene.js（AI 驱动的网页自动化）[B1]。阿里：AgentScope（多 agent 框架，Apache-2.0，约 2.8 万星）[AS]。
3. **工程团队的公开经验和论文。** 见 `INTRO.md` 的依据列表。

## 总览

| 层 | 选什么 | 为什么 | 没选的和原因 |
|---|---|---|---|
| 语言 | 后端 Python 3.12，前端 TypeScript | Python：字节 66% [J1]、小红书 71% [XHS]；TypeScript：字节 24%、小红书 22% | Go 和 Java 在两家都很高（小红书 Java 68%、Go 63%），但 agent、评测、数据处理的生态都在 Python，不为凑关键词换语言；简历上要把数据库、缓存、并发这些工程基本功讲清楚 |
| Agent 编排 | **LangGraph 1.x**，配合 **LangChain 1.x** 的 `create_agent`、工具和中间件 | 字节的 DeerFlow 就构建在 LangGraph 上 [A5]；1.0 版（2025-10）主打持久执行，每一步状态存下来，重启后接着跑 [LG1]；`Send` 能在运行时开出并行子任务 [LG2]；人工审批中间件可以按工具设置"批准、改参数、拒绝"[LG3]。两家岗位都很少点名具体框架（LangChain：字节 20%、小红书 3%），考的是工具调用、多 Agent、记忆、评测这些能力本身，所以选最通用的 | Hermes Agent 做外壳：两套 agent 框架叠在一起，评测和追踪要跨进程，复杂度高，挪到提交后接微信时再评估；AgentScope：好框架，但两家岗位里点名都是 0；自己写循环：不通用 |
| 状态持久化 | LangGraph 的 Postgres 检查点（`langgraph-checkpoint-postgres`） | 和业务数据共用一个 Postgres，不加组件 [LG2] | 内存或 SQLite：重启就丢 |
| 模型接入 | OpenAI 兼容接口。开发和评测全部在本机用第三方 OpenAI 兼容服务上的 `deepseek-v4.1-flash` 加阿里云的向量和重排；提交时只改 `.env` 换成比赛要求的模型。agent 里用 LangChain 的 `ChatOpenAI`，批量流水线用 M1 写的封装 | 已实测（`FINDINGS.md` 第 2 节） | LiteLLM：只有一种协议，用不上 |
| 结构化输出 | M1 的封装：先试 `json_schema`，不支持就换 `json_object`，本地 Pydantic 校验，不合格带着错误重试 | 已有测试，开发期和 Token Factory 上都有模型不支持 `json_schema` | Instructor：功能重叠 |
| 工具协议 | **MCP**：用官方 Python SDK（FastMCP）把 Polaris 的工具开放出去；LangGraph 里用 `langchain-mcp-adapters` 接 MCP 工具 | 字节岗位里 MCP 占 20% [J1]；适配器支持 streamable HTTP 和鉴权头 [MCP1] | — |
| Web 服务 | FastAPI（M1 已有） | — | — |
| 数据库 | **Postgres 17 加 pgvector 0.8**（M1 已有）。关系、向量、图谱边、带时间的记忆、检查点、调用追踪都放在这一个库里 | 一个人开发，少一个组件就少一类故障 | Milvus、Qdrant：多一个服务；Neo4j：多一个服务 |
| 中文关键词检索 | jieba 分词加 Postgres 全文检索。分词放在应用层还是装 pg_jieba 扩展，M3 做小实验二选一 | 混合检索加重排把检索失败率从 5.7% 降到 1.9% [R1]；pg_jieba 在 PolarDB 等环境里有现成文档 [R2] | ParadeDB pg_search：官方文档里没找到中文分词器，许可证是 AGPL [PD]；Elasticsearch：要 JVM，2 核 4G 的主机带不动 |
| 向量和重排 | 开发期：阿里云 `text-embedding-v4`（1024 维）、`qwen3.7-text-rerank`；正式：Token Factory 的 `Qwen3-Embedding-8B`、`Qwen3-Reranker-8B` | 已实测（开发期）；Token Factory 文档（正式）[FINDINGS] | — |
| 知识图谱 | 自己在 Postgres 里建实体表和关系表：实体有别名，关系带原文片段、来源和时间（成立、失效）。抽取方法参照 LightRAG，带时间的边参照 Graphiti | 冲突和出处是重点要打磨的部分，自己实现才说得清；LightRAG 证明了纯 Postgres 表就能存图谱 [K2]；Graphiti 的时间边有论文 [M1] | Neo4j：多一个服务；直接用 LightRAG：它合并实体描述靠模型摘要，没有显式的冲突裁决和失效时间，留作备选 |
| 记忆 | 自己在 Postgres 里建"双时间线"事实表，字段参照 Graphiti，操作参照 Mem0 | 冲突处理是重点要打磨的部分，自己实现才说得清；Graphiti 的做法有论文 [M1] | Graphiti 库：要图数据库；Mem0 库：新版改成只追加不更新 [M2]，和"冲突消解"的展示目标不合 |
| 记忆压缩 | 对话内用 LangChain 内置的对话摘要中间件（超过阈值把旧消息压成摘要，保留最近若干条）；跨会话每天把对话里的事实整合进卡片，原始对话留档 | 官方内置，按 token 数或消息数触发 [LG4]；整合的做法参照 Mem0 和 LangMem [M2] | LangMem 库：还没到 1.0，第三方测得检索很慢，不直接用 |
| 定时任务 | APScheduler 3.11（稳定版） | 4.0 还是预发布，官方明确不建议生产使用 [AP] | Celery：要消息队列；Hermes 定时：随外壳一起移出 |
| 采集 | **小红书**：本机运行 xiaohongshu-mcp，侦察子 agent 通过 `langchain-mcp-adapters` 调它，只放行只读工具（搜索、笔记详情、用户主页、首页推荐）；**Boss**：本机 boss-cli，包成工具，用 JSON 输出，只用搜索和详情；**腾讯**：公开查询接口（httpx）；**字节、小红书招聘官网**：Playwright 加载列表页、读取接口返回 | 小红书工具支持按排序、类型、发布时间筛选，详情带评论和回复 [X1]；Boss 工具支持按公司规模、融资阶段筛选 [B2]；腾讯接口和字节、小红书官网都在 2026-10-11 实测可用 [TC] | Midscene.js：用视觉模型操作网页，每步都要调模型，先留作备用 [B1]；Tavily、外企招聘系统：用户要求只看国内风向标大厂，不做 |
| 代码沙箱 | Docker 容器：不联网、只读挂载、限时，用来跑工程师角色写的代码；查库用只读数据库账号 | DeerFlow 的沙箱也是本地、Docker 或 K8s 三种 [A5] | NVIDIA OpenShell：比赛相关的加分项，上云后有余力再试 [S3] |
| 调用追踪 | 自有的 `llm_calls`、`runs` 表（产品里的评测页和回放都从这里取数）；开发期加 **LangSmith**（LangGraph 原生集成，免费版每月 5,000 条，支持 OpenTelemetry）[LS] | 公开演示的评测页必须用自己的数据；调试和实验对比用业界通用工具；小红书的 AI 可观测岗位点名 OpenTelemetry [XHS] | Langfuse 自己部署要 ClickHouse、Redis、S3，小主机扛不住 [LF]；Coze Loop：Go 服务，部署重，作为参照 [E6] |
| 追踪格式 | 按 OpenTelemetry 生成式 AI 规范命名（invoke_agent、execute_tool） | 业界标准；LangSmith 能直接接收 [E5][LS] | — |
| 评测 | 自建 Polaris-Bench：pytest 风格的跑法、录制回放环境、故障注入；评分参照 τ-bench（比最终状态，算 pass^k）；对比实验也可以推到 LangSmith 的数据集里 | 见 `INTRO.md` 的评测一节 | 直接套现成 benchmark：领域不对 |
| 前端 | **Next.js 加 TypeScript**，Tailwind 加 shadcn/ui；对话用 **Vercel AI SDK** 的 `useChat`，后端 FastAPI 用流式接口返回 | 字节的前端 Agent 岗位点名 React、Next.js、Vercel AI SDK、SSE 流式 [J1]；小红书的 AI 岗位里全栈 37%、React/Vue 29% [XHS]；Vercel 有官方的 Next.js 加 FastAPI 流式模板 [VA] | Vite 加 React：也可以，但字节岗位点名的是 Next.js；LangGraph 自带的 Agent Chat UI：定制余地小 |
| 部署 | Docker Compose 加 Caddy（自动 HTTPS），阿里云香港轻量 | `PLAN.md` 第 5 节 | — |
| 代码质量 | ruff、pytest；GitHub Actions 跑检查、测试和离线 benchmark 冒烟 | 通用做法 | — |

## 几个关键决定的展开

### 为什么 LangGraph 做核心，Hermes 移出主链路

- **评测是这个项目最重的部分。** agent 的每一步都要能追踪、回放、注入故障、算 pass^k。LangGraph 的图在我们自己的进程里，每个节点和工具调用都能拦截和记录；Hermes 是另一个进程里的完整 agent，要把评测伸进去成本高。
- **通用性。** 字节自己最大的开源 agent 项目 DeerFlow 用的就是 LangGraph [A5]。字节岗位里写具体框架名的，LangChain 系最多（20%）[J1]。
- **Hermes 没有丢。** Polaris 的工具通过 MCP 开放，Hermes 或任何 MCP 客户端都能接上。提交之后接微信时，再评估用不用 Hermes 自带的微信渠道。
- **对比赛的影响：** 比赛的硬性要求是运行时调用 Token Factory、用 NVIDIA 开源模型 [HACKATHON.md]，Hermes 和 NemoClaw 只是推荐工具，不影响参赛。

### 单写多读在 LangGraph 里怎么落地

| 角色 | LangGraph 里的形态 |
|---|---|
| 主 agent | 主图里的一个节点，用 `create_agent` 构建，是唯一能写库和起草的 |
| 侦察子 agent | 用 `Send` 按来源并行开出的子图；只拿只读工具，把原文存库，交回编号和摘要 |
| 审查 | 主图里的一个节点：新开一个干净的上下文（只给产出和评分表，不给对话历史），给出通过与否和理由 |
| 守门 | 一组确定性规则，加一个不能调工具的模型调用；所有对外动作经人工审批中间件暂停，等你批准 |
| 工程师 | 一个子图：在 Docker 沙箱里写代码、跑测试，产出要经审查和你批准才合入 |

### 两套追踪为什么不冲突

- `llm_calls` 和 `runs` 是产品的一部分：公开演示的评测页、回放、每日打分都读它们，数据在我们自己的库里。
- LangSmith 是开发工具：调试 LangGraph 的每一步、做提示词和模型的对比实验。只发合成数据或开发期数据；真实的个人数据不发出去，可以按环境变量关掉。

## 依赖清单（新增部分）

| 包 | 用途 |
|---|---|
| `langgraph`、`langgraph-checkpoint-postgres` | 编排和持久化 |
| `langchain`、`langchain-openai` | `create_agent`、中间件、OpenAI 兼容模型 |
| `langchain-mcp-adapters`、`mcp` | 接入和开放 MCP 工具 |
| `langsmith` | 开发期追踪和实验 |
| `apscheduler`（3.11） | 每日定时 |
| `playwright` | 需要浏览器的官网采集 |
| xiaohongshu-mcp（Go 程序，Windows 版，本机运行）、boss-cli（Python 命令行） | 小红书和 Boss 采集，在你电脑上跑 |
| `jieba` | 中文分词 |
| 前端：`next`、`react`、`ai`（Vercel AI SDK）、`tailwindcss`、shadcn/ui | 网页 |

具体版本在实现时锁进 `uv.lock` 和 `package-lock`，用到哪个模块再加，不一次装全。

## 还要实测的

| 项 | 什么时候 | 不行怎么办 |
|---|---|---|
| jieba 放应用层还是装 pg_jieba 扩展，中文检索效果 | M3 | 两种都不行就退到二元分词 |
| xiaohongshu-mcp 在你电脑上扫码登录后的稳定性、合适的抓取节奏 | M3 第一天 | 降低频率；纵向只挖评论数高的笔记 |
| boss-cli 在 Windows 上读取浏览器登录态 | M3 第一天 | 改用它的扫码登录 |
| 腾讯校招站 join.qq.com 用 Playwright 能否正常加载 | M3 | 只用腾讯招聘的公开接口 |
| LangGraph 的 `Send` 加检查点加子图组合在一起是否稳定 | M5 第一天 | 子任务改成顺序执行 |
| LangSmith 在国内网络能否直连 | M5 | 只用自有追踪表 |

## 依据

本文件新增的来源；其他编号见 `INTRO.md` 的依据列表。

- [LG1] LangChain, LangChain and LangGraph 1.0（2025-10-22）— https://www.langchain.com/blog/langchain-langgraph-1dot0 ；LangGraph v1 发布说明：https://docs.langchain.com/oss/python/releases/langgraph-v1
- [LG2] LangGraph 持久化和 Postgres 检查点：https://pypi.org/project/langgraph-checkpoint-postgres/ ；`Send` 并行：https://forum.langchain.com/t/best-practices-for-parallel-nodes-fanouts/1900
- [LG3] LangChain Human-in-the-loop 中间件：https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- [MCP1] langchain-mcp-adapters：https://github.com/langchain-ai/langchain-mcp-adapters
- [LS] LangSmith 的 OpenTelemetry 支持：https://docs.langchain.com/langsmith/trace-with-opentelemetry ；免费版额度来自第三方汇总（https://www.costbench.com/software/ai-observability/langsmith/free-plan/），以官方价格页为准
- [LF] Langfuse 自部署组件要求：https://langfuse.com/docs/deployment/v3/overview
- [PD] ParadeDB 分词器文档：https://www.paradedb.com/docs/documentation/tokenizers/overview ；许可证：https://pgxn.org/dist/pg_search/
- [AP] APScheduler 版本说明：https://apscheduler.readthedocs.io/en/master/migration.html 、https://apscheduler.readthedocs.io/en/3.x/versionhistory.html
- [VA] Vercel AI SDK Python Streaming 模板：https://vercel.com/templates/next.js/ai-sdk-python-streaming
- [AS] 阿里 AgentScope：https://github.com/agentscope-ai/agentscope
- [J1] 字节跳动岗位调研：`research/2026-10-11-bytedance-agent-intern-jd.md`
- [XHS] 小红书岗位调研：`research/2026-10-11-xiaohongshu-agent-jd.md`

# 参考答案：腾讯 WeKnora

2026-10-11 写，**待用户确认**。确认之后再改 `TECH.md`、再动手写代码。

WeKnora（https://github.com/Tencent/WeKnora ）是腾讯开源的生产级知识库和 Agent 平台：GitHub 3.3 万星，最新版 v0.8.2（2026-09-24），许可证 MIT（第三方组件各按原许可证）。
本文对照的是 2026-10-11 克隆的 `17dc7da` 提交，读的是它的源码和 `website-docs/` 下的架构文档。下文文件路径都相对它的仓库根目录。

**用法：** 把它当参考答案来学，沿着它验证过的路走。北极星以自己开发为主：
- 不在它上面二次开发，不把它整体引入当依赖，不大段照搬代码；
- 它用过的同类框架、组件可以采用，前提是适合一个人三周做完，不把技术栈搞重；
- **记忆更新、多来源对齐和冲突处理、主动决策、评测闭环**这四处必须自己实现；
- 业务能力封装成我们自己的 MCP 服务；
- 真要复用某段代码，先看许可证（MIT，可以复用，但要保留版权声明），并在代码里注明出处。目前的计划是只借设计和参数，不复制代码。

## 0. 先说结论

| 方面 | WeKnora 怎么做 | 北极星怎么做 | 和现有计划比 |
|---|---|---|---|
| 关键词检索 | Postgres 里的 ParadeDB `pg_search`，BM25，中文分词器 `chinese_lindera` | 一样：把 Postgres 镜像换成 ParadeDB 的 pg17 版 | **改**：原计划 jieba 加 Postgres 全文检索（我们当时以为 ParadeDB 没有中文分词，查错了） |
| 向量检索 | 同一个 Postgres 里的 pgvector | 一样 | 不变 |
| 图谱 | Neo4j 加 APOC，可选；关掉时图谱召回降级为空、不报错 | Neo4j 5.26 社区版加 APOC，也做成可关；图的结构和写入逻辑自己写 | **改**：不用 Graphiti（它会替我们做合并和冲突，违背"必须自己实现"） |
| 融合和重排 | 加权 RRF（k=60，向量 0.7、关键词 0.3）→ 重排模型 → 综合分 → MMR 去冗余 → 确定性排序 | 照这个流水线，参数作为起点，用我们的标准答案集再调 | **新增**：原计划只写了"混合检索加重排" |
| 分块 | 自适应分块、父子分块、每块加上下文头 | 我们的数据是短网页，不用解析服务；借上下文头、面试题拆成单题、长帖父子分块 | **新增** |
| Agent 循环 | 自写的 ReAct 引擎；只读工具才并行；工具超时；到轮数上限不带工具收尾 | 继续用 LangGraph；照搬这几条规则 | 补充 |
| 人工审批 | 每个 MCP 工具单独设审批；可批准、拒绝、改参数（改后重新校验）；超时 10 分钟；拒绝或超时当作工具结果返回 | 同样的语义，用 LangChain 的人工审批中间件加我们自己的策略表实现 | 补充 |
| 追踪 | OpenTelemetry 写 span，导出到 Langfuse（可选，默认关） | 自有追踪表照旧是唯一来源；命名对齐 OpenTelemetry；LangSmith 可选 | 不变 |
| 评测 | 问答数据集，检索指标 Precision、Recall、NDCG@3/@10、MRR、MAP，生成指标 BLEU、ROUGE | 借它的检索指标和数据格式做检索评测；生成质量不用 BLEU/ROUGE；agent 级评测（pass^k、回放、故障注入）是我们自己的 | 补充 |
| 队列和缓存 | Redis：Asynq 任务队列、流式续传、限流；**它的 Lite 版不用 Redis** | 不引入 Redis：单实例常驻进程加 Postgres 记账 | 不变，并有了依据 |

## 1. 混合检索和重排

### WeKnora 怎么做

主流程在 `internal/application/service/chat_pipeline/` 的插件链里，文档是 `website-docs/02-architecture/04-rag-pipeline.md`：

1. **问题理解**（`query_understand.go`）：模型改写问题、判断意图（知识库检索、联网、寒暄、追问、总结等 9 类）。不需要检索的意图直接跳过后面所有检索步骤。可以给这一步单独配一个小模型。
2. **并行召回**（`search_parallel.go`）：两路同时跑。
   - 片段检索：关键词和向量。默认 Postgres 后端里，关键词是 ParadeDB `pg_search` 的 BM25（`content ||| query`），分词器 `chinese_lindera`（见 `migrations/versioned/000002_embeddings.up.sql`）；向量是 pgvector 的 halfvec 加 HNSW。召回阈值：向量 0.15，关键词 0.3。
   - 图谱实体检索：先用模型从问题里抽实体名，再去 Neo4j 按名字包含匹配，取一跳邻居和关系（`internal/application/repository/retriever/neo4j/repository.go`）。种子实体最多 200 个，返回行最多 2000 条，防止一个常见词拉回整张图。图谱命中的原文片段并入候选集，最多 30 条，没有检索分，重排时用模型分代替。
3. **查询扩展**（`query_expansion.go`）：初次召回不够时触发，不调模型。用 jieba 的 `CutForSearch` 在本地生成几个查询变体，只在关键词这一路多召回；阈值放宽到 0.8 倍。
4. **融合**（`internal/application/service/knowledgebase_search_fusion.go`）：向量和关键词结果用**加权 RRF** 合并，k=60，默认权重向量 0.7、关键词 0.3，再除以最大值归一到 0 到 1。只有一路时：向量分保留原值，BM25 分除以本列表的最高分。
5. **重排**（`rerank.go`、`internal/reranking/`）：
   - 送去重排前先清洗：去掉 Markdown 标记、链接、表格分隔行，代码和公式只去围栏保留内容；最多 200 条候选。
   - 重排阈值 0.2。全部低于阈值时，如果第一名不低于 0.15 就保底留一条；还没有结果就把阈值乘 0.7 再试一次；重排服务挂了就退回检索结果，不中断。
   - **综合分** = 0.6×重排模型分 + 0.3×检索分 + 0.1×来源权重（`internal/reranking/rerank.go` 的 `CompositeScore`）。
   - **MMR 去冗余**（`internal/reranking/mmr.go`）：λ=0.7，冗余度用分词后的 Jaccard 相似度。
6. **合并**（`merge.go`，八步）：去重；子块换回父块内容；同一文档相邻的块拼起来；太短的块（不足 350 字）向前后各扩一点，最长约 850 字；近似重复（词重合 ≥ 0.85）只留高分的。
7. **确定性排序**（`filter_top_k.go`）：分数相同时按文档、类型、块序号、ID 排，结果每次可复现。

### 借鉴什么

- **整条流水线的形状**：问题理解 → 多路并行召回（关键词、向量、图谱）→ 加权 RRF → 重排加保底 → 综合分 → MMR → 确定性截断。我们的数据是岗位和帖子，多一路结构化过滤（公司、城市、类型、时间），放在召回前。
- **它的默认参数作为起点**：RRF 的 k=60、权重 0.7 和 0.3、重排阈值 0.2 加保底、综合分 0.6、0.3、0.1、MMR 的 λ=0.7。再用我们自己的检索标准答案集调，调参过程写进评测报告。
- **关键词检索放在 Postgres 里**：换成 ParadeDB 镜像，用 `pg_search` 的 BM25 和中文分词器。这样不用自己维护 jieba 分词列，和业务数据同库、事务一致。
- **图谱作为一路召回**：问题里的实体 → 图里的邻居 → 关联的岗位和帖子并入候选。种子数和返回行数也设上限。
- **查询扩展只加在关键词那一路、不调模型**：省钱，也可复现。
- **重排前清洗文本、重排服务挂了就退回检索结果**。

### 不借鉴什么，为什么

- **九种向量库后端的抽象层**（ES、Milvus、Qdrant 等）：我们只用 Postgres，抽象层是负担。
- **联网搜索、网页全文抓取、图片召回、Wiki 加权、FAQ 加权**：和求职场景无关，或者第一版用不上。
- **历史引用注入**（把上一轮引用过的片段再塞进来）：我们的追问场景用对话记忆处理，先不做。

## 2. 文档解析和分块

### WeKnora 怎么做

- **解析**：单独一个 Python 服务 `docreader`（gRPC，`docreader/`），支持 25 种以上格式，有 OCR（PaddleOCR-VL）和 PDF 混合解析。
- **分块**（Go 实现，`internal/infrastructure/chunker/`，文档 `website-docs/03-features/04-chunking.md`）：
  - 默认 512 字、重叠 80 字；先给文档"画像"，再按"标题 → 启发式边界 → 递归切分"逐级回退；表格、代码、公式内部不切。
  - **上下文头**：每块前面加面包屑，标明属于哪篇文档、哪一节。
  - **父子分块**：父块 4096 字、子块 384 字，用子块检索、父块回答。
  - **FAQ 分块**：一问一答就是一块，不重叠。

### 借鉴什么

- **上下文头**：岗位每块前面写"公司 ｜ 事业群 ｜ 岗位名 ｜ 职责或要求"，帖子写"来源 ｜ 涉及公司 ｜ 帖子类型 ｜ 日期"。短文本单独拿出来也能看懂。
- **FAQ 式原子块**：面经里每道面试题单独成块，挂在原帖下面（父子关系）。正好对应计划里的"面试题表"。
- **长帖父子分块**：超过约 1,500 字的帖子用子块检索、整帖回答；短的整篇一块。
- **岗位按段切**：职责和要求分开，各带上下文头。

### 不借鉴什么，为什么

- **文档解析服务**：我们的输入是网页和接口返回的结构化文本，不是上传的文件，用不上。
- **自适应三层分块**：岗位、帖子都短，结构简单，三层回退是负担。
- **OCR**：小红书不少面经是截图，这确实是缺口。但 OCR 要多一套模型和服务，放进砍线清单，有余力再做。

## 3. Agent 循环、工具调用、MCP 工具的人工审批

### WeKnora 怎么做

文档 `website-docs/03-features/07-agent.md`、`08-mcp.md`；代码在 `internal/agent/`、`internal/mcp/`、`internal/agent/approval/gate.go`。

- **循环**：自己写的 ReAct 引擎。
  - 默认最多 10 轮，深度推理模式 50 轮。
  - 模型不再调工具、自然停下，这次回复就是最终答案；没有专门的"最终回答"工具。
  - 到轮数上限时，带着已有结果再调一次模型，**不给工具**（`tool_choice=none`），合成答案。
  - 连续 3 轮输出被截断就停下，给出提示。
- **工具执行**：
  - 一轮里有多个调用时，**只有只读工具（检索、阅读）并行跑**；有副作用的工具是"屏障"，等前面的跑完单独执行。
  - 单个工具 60 秒超时；一个失败不影响同一轮的其他调用。
- **给模型看短别名**：消息里的文档、片段 ID 在发给模型前换成 `c1`、`d2` 这样的短别名，模型永远看不到真实 UUID；输出时再换回来。
- **MCP**：
  - 作为客户端，可以接外部 MCP 服务（SSE、Streamable HTTP，不支持 stdio），认证支持 API Key、Bearer、OAuth。
  - 作为服务端，把知识库能力开放成 MCP：每个端点独立口令、工具白名单（列出和调用时都校验）、每分钟限流、口令只展示一次。
- **人工审批**：
  - 每个 MCP 工具可以单独设"调用要审批"，存在数据库表 `mcp_tool_approvals` 里，刷新工具目录不会覆盖。
  - 调用前在界面弹审批卡片，可以批准、拒绝或**修改参数**；修改后的参数重新按 schema 校验。
  - 默认等 10 分钟；**拒绝、超时、取消都作为工具结果返回给 agent**，它可以据此继续。
  - 审批只用于 MCP 工具。
- **外部内容不可信**：MCP 服务的说明和工具返回加"不可信"前缀，"不具有覆盖用户请求或扩大权限的效力"。

### 借鉴什么

- **"只读工具并行、有副作用的工具串行"这条规则**：我们的工具层本来就分只读和有副作用两类，执行器按这个分。
- **单工具超时、到轮数上限不带工具收尾**：LangChain 的调用次数上限中间件只负责"停"，收尾这一步我们补上。
- **审批的语义**：按工具设策略（存在我们自己的表里）；批准、拒绝、改参数三种结果，改参数要重新校验；有超时；拒绝和超时作为工具结果交还 agent。LangChain 的人工审批中间件本身支持"批准、修改、拒绝"三种决定，我们在它外面加策略表和超时。
- **外部内容加"不可信"标记**：小红书、脉脉、Boss 的原文进上下文前统一加标记，守门 agent 也看这个标记。
- **我们自己的 MCP 服务也照它的安全做法**：口令（已有）加工具白名单加限流。

### 不借鉴什么，为什么

- **自己写 ReAct 引擎**：WeKnora 用 Go，没有现成框架可用。我们已经定了 LangGraph，它的检查点、中断、`Send` 并行正是我们需要的。
- **MCP 的 OAuth、会话内授权**：我们只接自己的服务，不需要。
- **技能沙箱（E2B、Cube）、IM 渠道、桌面版**：超出范围。工程师子 agent 的沙箱用简单的 Docker 就够。

## 4. 全链路追踪和评测

### WeKnora 怎么做

- **追踪**（`internal/tracing/langfuse/`，文档 `website-docs/03-features/16-observability.md`）：
  - 用 OpenTelemetry SDK 写 span，属性按 Langfuse 的约定命名，经 OTLP/HTTP 导出到 Langfuse。默认关闭，可设采样率。
  - 三种句柄：Trace（一次请求）、Span（检索、重排等逻辑步骤）、Generation（一次模型调用，带 token 用量和首字时间）。
  - 每种模型调用（对话、向量、重排）都包了一层记录。后台任务也挂在 trace 下，不会成为孤儿。
  - 早期用过 Jaeger，后来删了，只留 Langfuse。
- **评测**（`internal/application/service/metric/`，文档 `15-evaluation.md`）：
  - 用带标准答案的问答数据集（Parquet：问题、语料、问题到相关段落的对应 `qrels`、答案）。
  - 检索指标：Precision、Recall、NDCG@3、NDCG@10、MRR、MAP；生成指标：BLEU-1/2/4、ROUGE-1/2/L。中文先用 jieba 分词。
  - 没有界面入口，通过接口发起。**没有 agent 级评测**（多步工具调用的成功率、回放、故障注入都没有）。

### 借鉴什么

- **检索评测的指标和数据格式**：M4b 的检索评测用"问题 → 相关条目编号"这种 qrels 形式的标准答案，算 Recall@k、NDCG@3/@10、MRR、MAP，中文用 jieba 分词。
- **Trace / Span / Generation 三层结构**：我们的 `runs`、`tool_calls`、`llm_calls` 对上的就是这三层；字段命名按 OpenTelemetry 生成式 AI 规范对齐。
- **后台任务也要挂在一次运行下**：常驻采集已经这样做（每轮一条 `runs`）。

### 不借鉴什么，为什么

- **自建 Langfuse**：它要 ClickHouse、对象存储等一套服务，太重。我们的追踪表是产品里评测页和回放的数据来源，必须自己存；对外导出继续用可选的 LangSmith。
- **用 BLEU、ROUGE 评回答**：开放式回答换个说法分数就掉，对"推荐得对不对"不敏感。我们用逐字段对标准答案、带标准答案的模型打分，以及 pass^k。
- **评测闭环必须自己做**（原则）：标准答案集、逐字段打分、工具调用套件和 pass^k、录制回放、故障注入、提示词改动先开发集再留出集、CI 里的回归。这些已经有一部分，WeKnora 没有对应的东西。

## 5. 基础设施选型，以及它为什么这么选

### WeKnora 怎么做

文档 `website-docs/02-architecture/01-overview.md`、`05-async-tasks.md`、`03-features/05-retrieval-engines.md`：

| 组件 | 选型 | 它给的理由 |
|---|---|---|
| 主服务 | Go、Gin、GORM、dig 依赖注入 | — |
| 文档解析 | 单独的 Python gRPC 服务 | 解析库都在 Python 生态 |
| 主数据库 | **ParadeDB 的 Postgres 17 镜像**（自带 BM25 和 pgvector） | "默认部署无需独立向量库"，和业务数据同库、事务一致；默认就这一个库 |
| 任务队列和缓存 | **Redis 加 Asynq**（6 个独立的 worker 池） | 文档解析、图谱抽取、摘要等耗时任务要异步；多实例部署时流式输出续传、配置变更广播、限流、按模型的并发闸门都要跨进程共享 |
| 图谱 | Neo4j 加 APOC，**可选** | 只有关系多的资料才值得，入库时模型调用会多很多 |
| 对象存储 | 本地、MinIO、COS、S3 等，可选 | 文件和图片 |
| 可观测 | Langfuse，可选 | — |
| **Lite 版** | **SQLite 加进程内执行器，不用 Redis** | 单机、桌面场景；任务逻辑和标准版共用 |

### 借鉴什么

- **默认只依赖一个 Postgres，其他都是可选组件**：北极星的核心路径（采集、抽取、检索、记忆、评测）只依赖 Postgres；Neo4j 做成可关的组件，关掉时图谱这一路召回为空，其他照常（照 WeKnora 的 `NEO4J_ENABLE` 做法）。
- **Postgres 镜像换成 ParadeDB 的 pg17 版**：拿到 BM25 中文关键词检索，同时保留 pgvector，不加新服务。ParadeDB 的 `pg_search` 是 AGPL-3.0，我们原样跑它的镜像、作为独立服务，不改它的代码，不影响北极星的 Apache-2.0。
- **不用 Redis 也能跑**：WeKnora 自己的 Lite 版就是单实例、不用 Redis。我们是单实例：常驻采集进程加 APScheduler，配额和锁记在 Postgres。以后上云要多进程，再按它的用法加 Redis。

### 不借鉴什么，为什么

- **Go 主服务、gRPC 解析服务、对象存储、多种向量库**：一个人三周做不完，也用不上。
- **Asynq 和 6 个 worker 池**：我们的耗时任务（抽取）按轮在常驻进程里跑，量级每天几千条，不需要分布式队列。

## 6. 对 `TECH.md` 的修改建议（确认后再改）

`TECH.md` 是 10-10 的初稿，开头已注明被 `STACK.md` 取代；里面还有"Hermes 做外壳"等过时内容。
**建议：把 `TECH.md` 改成唯一的技术方案文档**（吸收 `STACK.md` 的选型表和下面的调整），`STACK.md` 留作选型过程的记录。这样以后只看一份。

下表按 `TECH.md` 的"总体架构"和"十个环节"逐行列出调整前后；最后一列是同步要改的 `STACK.md` 行。

| 位置 | 调整前（`TECH.md` 现文，或 `STACK.md` 已定） | 调整后 | 依据 | 影响 `STACK.md` |
|---|---|---|---|---|
| 总体架构 · Agent 外壳 | Hermes Agent（`TECH.md`）；`STACK.md` 已改成 LangGraph | LangGraph 1.x 加 LangChain 1.x 中间件，不用外壳（以 `STACK.md` 为准，同步 `TECH.md`） | — | 不变 |
| 总体架构 · 存储 | 一个 Postgres：关系表、pgvector、图谱边表 | **Postgres 17（ParadeDB 镜像）**：业务数据、pgvector、`pg_search` BM25 中文分词；**Neo4j 5.26 社区版加 APOC**：知识图谱，可关 | WeKnora 默认部署 + 可选 Neo4j | "数据库""中文关键词检索""知识图谱"三行 |
| 环节 3 · 去重、对齐、冲突 | 向量初筛，规则加模型裁决，按权威性和时效性取值 | 不变，**自己实现**；候选对用 SimHash（已有）、BM25、向量三路召回，再按岗位名、公司、城市判同岗；字段按来源可信度和时间取值，旧值保留版本 | 原则：必须自己写；WeKnora 没有这一层 | 不变 |
| 环节 4 · 知识存储 | 图谱存公司、岗位、技能、人、时间的关系（在 Postgres） | 图谱放 Neo4j：结构自己定（公司、事业群、团队、岗位、城市、帖子、面试题），用 Cypher 加 APOC 幂等写入（借 WeKnora 的写法）；每条边带来源和时间；**不用 Graphiti** | WeKnora 用 Neo4j；Graphiti 会替我们做冲突处理 | "知识图谱"行 |
| 环节 5 · 卡片和记忆 | 从对话抽事实，新增、更新、冲突三类处理 | 不变，**自己实现**双时间线事实表；借 WeKnora 两点：推断出来的记忆先进"待确认"；被拒绝的留抑制记录、不重复抽取 | WeKnora `23-memory.md` | "记忆"行补一句 |
| 环节 6 · 匹配和检索 | 关键词、向量、图谱混合检索，重排 | 结构化过滤 → 关键词（`pg_search`）、向量（pgvector）、图谱（Neo4j 一跳）三路并行 → 加权 RRF（k=60，0.7、0.3 起步）→ 重排（阿里云）加阈值保底 → 综合分（0.6、0.3、0.1）→ MMR（λ=0.7）→ 确定性排序；召回不足时只在关键词一路做本地查询扩展；参数用标准答案集调 | WeKnora 检索流水线 | "中文关键词检索""向量和重排"两行 |
| 环节 6 · 主动决策 | 推送与否和时机的判断 | 不变，**自己实现**（新颖度、匹配度、紧迫度打分；该克制时克制） | 原则 | 不变 |
| 环节 7 · Agent 编排 | 主 agent 加子 agent，状态机，检查点，降级 | 补充规则：只读工具并行、有副作用的串行；单工具 60 秒超时；到轮数上限不带工具收尾；给模型看短编号（已有） | WeKnora `07-agent.md` | "Agent 编排"行补充 |
| 环节 8 · 行动和工具 | 参数定义、权限、幂等、副作用确认 | 业务能力封装成**自己的 MCP 服务**（已有雏形），加工具白名单和限流；有副作用的工具按策略表审批：批准、拒绝、改参数（改后重新校验），超时 10 分钟，拒绝和超时作为工具结果交还 agent；外部内容加"不可信"标记 | WeKnora `08-mcp.md` | "工具协议"行补充 |
| 环节 9 · 对话和上下文 | 卡片、检索结果、历史对话的组装和压缩 | 补充分块：上下文头；面试题单题成块（挂原帖）；长帖父子分块；不做文件解析服务 | WeKnora `04-chunking.md` | 新增"分块"行 |
| 环节 10 · 评测、追踪、反馈 | 标注集、端到端成功率、全链路追踪、回放、回归 | 不变，**自己实现评测闭环**；补检索指标 Recall@k、NDCG@3/@10、MRR、MAP 和 qrels 形式的标准答案；追踪表命名对齐 OpenTelemetry；不自建 Langfuse | WeKnora `15-evaluation.md`、`16-observability.md` | "评测""调用追踪"两行补充 |
| 贯穿 · 工程化 | 任务队列、缓存、Docker、CI、云端部署 | **不引入 Redis 和任务队列**：单实例常驻进程加 APScheduler，配额和锁记在 Postgres；多实例时再加 Redis | WeKnora Lite 版不用 Redis | "定时任务"行补一句 |
| `FINDINGS.md` 更正 | "ParadeDB pg_search：官方文档里没找到中文分词器" | 改为：有 `chinese_lindera` 分词器，WeKnora 默认部署就在用 | WeKnora 迁移脚本 | — |

**已经做了、要随之撤回的：** 刚才为 M4 加了 `graphiti-core` 依赖和 `src/polaris/graph/graphiti_client.py`（都没提交）。按上表不用 Graphiti，确认后删掉。Neo4j 服务、`NEO4J_PASSWORD` 和配置项保留。

## 7. 三栏清单：自己写、用现成的、仅参考

| 自己写 | 用现成的（框架、组件、服务） | 仅参考（WeKnora 的设计，不引入代码） |
|---|---|---|
| **记忆更新**：双时间线事实表；新增、更新、作废、不变；待确认、抑制记录；矛盾时追问；对话压缩后整合进卡片 | LangGraph 1.x：状态图、检查点（Postgres）、中断、`Send` 并行 | 检索流水线的阶段划分和默认参数（RRF k=60、0.7 和 0.3、阈值、综合分 0.6、0.3、0.1、MMR λ=0.7） |
| **多来源对齐和冲突处理**：同岗识别、实体合并、字段真值裁决、出处和版本 | LangChain 1.x：`create_agent`、人工审批、摘要压缩、调用上限、重试中间件 | 重排保底和失败退回、重排前的文本清洗 |
| **主动决策**：推送打分、时机、克制、主动程度可调 | MCP 官方 Python SDK（FastMCP）、`langchain-mcp-adapters` | 合并阶段：父块替换、相邻块拼接、短块扩展、近似重复删除 |
| **评测闭环**：标准答案集、逐字段打分、工具调用套件和 pass^k、录制回放、故障注入、检索指标、开发集和留出集、CI 回归 | ParadeDB 的 Postgres 17 镜像（`pg_search` BM25 加中文分词、pgvector） | 分块：上下文头、父子分块、FAQ 式原子块 |
| 业务 **MCP 服务**及其工具（岗位、帖子、图谱、记忆、投递），加白名单和限流 | Neo4j 5.26 社区版加 APOC、官方 `neo4j` Python 驱动 | Agent 循环：只读工具并行、单工具超时、到上限不带工具收尾、短别名 |
| 检索编排：三路召回、RRF、MMR、合并（照公式自己写，小函数） | 阿里云 `text-embedding-v4` 和重排接口；开发期 deepseek（OpenAI SDK） | MCP 审批的语义：按工具设策略，批准、拒绝、改参数，超时，结果交还 agent；外部内容不可信前缀 |
| 图谱结构和写入（Cypher） | FastAPI、SQLAlchemy、Alembic、Pydantic | 图谱写入用 APOC 幂等合并；查询时种子和行数上限；关掉图谱时降级为空 |
| 采集器、配额、常驻进程、遇阻即停 | APScheduler、Playwright（接真实 Edge）、boss-cli | 追踪的 Trace / Span / Generation 三层结构 |
| 抽取的提示词、数据结构、校验重试 | jieba（查询扩展、中文指标分词） | 检索评测的指标集和 qrels 数据格式 |
| 守门 agent 和审批策略表；每日计划状态图；推荐和简报 | pytest、ruff、Docker Compose；LangSmith（可选） | 记忆：分类、待确认、抑制记录 |
| 前端页面 | Next.js、TypeScript、shadcn/ui、Vercel AI SDK | Lite 版"单实例不用 Redis"作为不加 Redis 的依据 |

**完全不用的：** WeKnora 的 Go 代码、`docreader` 解析服务、Redis 和 Asynq、多向量库抽象、对象存储、自建 Langfuse、MCP 的 OAuth、IM 渠道、Wiki、技能沙箱、联网搜索，以及 Graphiti。

## 依据

- WeKnora 仓库：https://github.com/Tencent/WeKnora （读的是 `17dc7da`，2026-10-11）；许可证见仓库根目录 `LICENSE`（MIT）和 `THIRD_PARTY_NOTICES.md`
- 架构文档：`website-docs/02-architecture/01-overview.md`、`04-rag-pipeline.md`、`05-async-tasks.md`
- 功能文档：`website-docs/03-features/04-chunking.md`、`05-retrieval-engines.md`、`07-agent.md`、`08-mcp.md`、`09-knowledge-graph.md`、`15-evaluation.md`、`16-observability.md`、`23-memory.md`
- 关键源码：`internal/application/service/knowledgebase_search_fusion.go`（RRF）、`internal/types/retrieval_config.go`（默认参数）、`internal/reranking/rerank.go`（综合分）、`internal/reranking/mmr.go`（MMR）、`internal/application/repository/retriever/neo4j/repository.go`（图谱查询）、`migrations/versioned/000002_embeddings.up.sql`（BM25 中文分词）
- Graphiti（不用的理由见第 6 节）：https://github.com/getzep/graphiti

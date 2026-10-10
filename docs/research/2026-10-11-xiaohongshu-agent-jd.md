# 小红书 Agent 相关研发岗位调研（附阿里情况）

2026-10-11。和字节那份（`2026-10-11-bytedance-agent-intern-jd.md`）用同样的方法，给技术栈选型（`STACK.md`）做依据。

## 方法

- 来源：小红书校园招聘官网 job.xiaohongshu.com，只用公开页面，不登录。
- 筛选：关键词 Agent，项目"2027 校园招聘"，职位方向"研发"，共 **89 个**，全部打开读"工作职责"和"任职资格"。
- 小红书的搜索会匹配正文，89 个里有一部分是网络、存储这类基础设施岗位，只是正文提到了 Agent。下面的统计只取标题和 AI、Agent、大模型、全栈相关的 **59 个**。
- 关键词匹配，一个岗位提到多次只算一次。原文只留在本机，不进仓库。

局限：这批是 2027 届校招的正式岗位，不是实习；关键词匹配会漏掉换了说法的同义表述。

## 技术点出现频率（59 个 AI、Agent、全栈相关岗位）

| 技术点 | 比例 |
|---|---|
| Python | 71% |
| Java | 68% |
| Go | 63% |
| 开源、GitHub | 54% |
| 分布式、高并发 | 46% |
| 评测、Benchmark | 39% |
| 全栈 | 37% |
| AI 编程工具（Claude Code、Codex、Cursor、TRAE） | 32% |
| React / Vue | 29% |
| 工具调用 | 27% |
| 数据库、SQL | 27% |
| Node.js | 24% |
| TypeScript / JavaScript | 22% |
| 上下文管理、工作流编排 | 各 20% |
| RAG、规划和任务拆解 | 各 19% |
| 多 Agent、Harness、可观测和 Trace | 各 17% |
| Prompt、检索 | 各 15% |
| Skills、记忆、Docker/K8s | 各 14% |
| MCP | 10% |
| 泛称"Agent 框架"、向量、SFT、强化学习 | 各 7% |
| LangChain | 3% |
| LangGraph、CrewAI、Dify、Coze、重排、知识图谱 | 各 2% |
| AgentScope、A2A | 0 |

## 看出来的几件事

1. **小红书把 AI 应用开发叫"产品工程师（Product Engineer）"，大量是"AI 全栈"方向。** 全栈 37%，React/Vue 29%，Node.js 24%。前端要做得像样，不能只有接口。
2. **GitHub 上的开源项目是最被看重的加分项**（54%），比字节的 40% 还高。有岗位写"GitHub 有高质量项目贡献者优先"，还有的写"持续关注 Anthropic、OpenAI、Cognition、Cursor 等产品技术动向"（产品工程师 AI 全栈 - 企业智能方向）。
3. **后端语言以 Java 和 Go 为主**，同时要求分布式和高并发（46%）。北极星用 Python 不影响投递 Agent 应用岗位，但简历上的工程基本功（数据库、缓存、并发）要讲清楚。
4. **具体框架名几乎没人写**：LangChain 3%、LangGraph 2%，和字节的结论一致（LangChain 20%、LangGraph 6%）。考察的是概念和工程能力：工具调用、MCP、Agent Runtime、多 Agent、上下文、记忆、评测、可观测。
5. **几个和北极星直接对得上的岗位原话：**
   - Agent Harness 工程师 - 企业智能方向：要求理解"服务治理、任务调度、权限系统、可观测性"，对"Tool Calling、MCP、Agent Runtime、Coding Agent、Multi-Agent"有兴趣或实践。
   - AI 可观测研发工程师：点名 OpenTelemetry 等可观测组件，加分项里有 Langfuse。
   - Agent 平台全栈研发工程师：要求熟悉"Tool Calling、RAG、Workflow 编排、Memory、多轮对话、模型评估"，并能用 Claude Code、Codex、Cursor 辅助开发；加分项包括"MCP Server、工具调用平台、智能体编排系统"。

## 阿里

阿里集团校招站（talent.alibaba.com）和阿里云校招站（careers.aliyun.com）今天搜 Agent，"26 届应届生"和"项目制实习生"两个批次都显示"暂无在招职位"；阿里的招聘按业务分散在淘天、千问、高德等十几个子站上。这次没有拿到阿里的岗位原文。

能看到的是阿里的开源方向：AgentScope（多 agent 框架，Apache-2.0，约 2.8 万星，2026 年 5 月发布 2.0）。没有证据显示它在国内岗位要求里被普遍点名（字节、小红书都是 0）。

## 合起来看（字节 50 个 + 小红书 59 个）

| 共同的高频要求 | 字节 | 小红书 |
|---|---|---|
| Python | 66% | 71% |
| 评测、Benchmark | 56% | 39% |
| 工具调用 | 74% | 27% |
| 开源、个人项目、GitHub | 40% | 54% |
| AI 编程工具 | 30% | 32% |
| 多 Agent | 44% | 17% |
| RAG | 42% | 19% |
| 记忆 | 36% | 14% |
| 可观测、Trace | 24% | 17% |
| MCP | 20% | 10% |
| LangChain / LangGraph | 20% / 6% | 3% / 2% |

结论：
- **框架是手段，用业界最通用的那个就行。** LangGraph 是字节自己的 DeerFlow 选用的底座，用它没有问题，但简历和面试里要讲的是工具调用、评测、多 Agent、记忆、可观测这些能力本身。
- **全栈和 GitHub 质量要当成硬指标。** 前端用 Next.js、TypeScript；仓库的 README、提交历史、测试和指标表都按"给面试官看"的标准做。

## 59 个岗位

官网详情页：`https://job.xiaohongshu.com/campus/position/<编号>`

| # | 岗位 | 编号 |
|---|---|---|
| 1 | Product Engineer-产品工程师（AI全栈）-企业智能方向 | 22002 |
| 2 | Product Engineer-产品工程师（AI Coding 基建方向）-PE Infra | 22024 |
| 3 | Product Engineer-产品工程师（Agent开发方向）-智能客服 | 22481 |
| 4 | Product Engineer-产品工程师（AI 全栈方向）-电商商家/商品/履约 | 22480 |
| 5 | Product Engineer-产品工程师（AI 全栈方向）-电商营销&增长 | 22478 |
| 6 | Product Engineer-产品工程师（AI 全栈方向）-交易主链路 | 22477 |
| 7 | Product Engineer-产品工程师（AI 全栈方向）-直播电商 | 22476 |
| 8 | Product Engineer-开发工程师-rednote国际化 | 21998 |
| 9 | AI商业营销科学工程师（灵犀agent研发） | 22410 |
| 10 | 大模型算力平台研发工程师 | 22058 |
| 11 | Product Engineer-产品工程师（AI与全栈方向）-商业平台技术 | 22026 |
| 12 | Agentic ops 研发工程师 | 22042 |
| 13 | AI 数据库工程师 | 22493 |
| 14 | 智能平台开发 | 22082 |
| 15 | 大模型平台全栈研发工程师 | 22060 |
| 16 | 大模型训练框架研发工程师 | 22055 |
| 17 | 大模型推理加速研发工程师 | 22056 |
| 18 | Agent平台全栈研发工程师 | 22059 |
| 19 | 大模型 Infra 工程师 | 22061 |
| 20 | AI Agent Infra 工程师 | 22062 |
| 21 | AI Infra网络通信工程师 | 22054 |
| 22 | AI可观测研发工程师 | 22043 |
| 23 | Product Engineer-产品工程师（全栈）-小程序酒旅方向 | 22538 |
| 24 | 全栈研发工程师（线索广告方向） | 22539 |
| 25 | AI 应用开发工程师（大模型 AI 质效研发方向） | 22063 |
| 26 | AI Agent工程师（大模型 AI 应用开发方向）-审核研发 | 21969 |
| 27 | AI Agent 工程师（大模型 AI 应用开发方向）-质效研发 | 21966 |
| 28 | AI 应用开发工程师（大模型 AI 产品可靠性方向）-质效研发 | 22064 |
| 29 | 数据引擎AIOps/Agent研发 | 21991 |
| 30 | AI数据infra工程师 | 22491 |
| 31 | 大模型数据工程师 | 22492 |
| 32 | 数据与AI应用研发工程师 | 21985 |
| 33 | AI安全研发工程师 | 22038 |
| 34 | AI全栈研发工程师（AI4Sec方向） | 22041 |
| 35 | 大模型MaaS架构研发工程师 | 22057 |
| 36 | Product Engineer产品工程师（AI与全栈方向）-跨境电商 | 22483 |
| 37 | Product Engineer产品工程师（AI与全栈方向）-快捷售卖 | 22484 |
| 38 | Product Engineer产品工程师（AI与全栈方向）-支付结算 | 22485 |
| 39 | Product Engineer产品工程师（AI与全栈方向）-跨境电商&交易平台 | 22025 |
| 40 | Product Engineer产品工程师（AI与全栈方向）-交易基础 | 22482 |
| 41 | 大模型 Agent 开发工程师（AI Coding方向） | 22079 |
| 42 | Product Engineer-产品工程师（AI与全栈方向）-增长产品 | 22081 |
| 43 | Product Engineer-产品工程师-社区内容方向 | 22019 |
| 44 | AI Agent工程师-社区工程 | 22018 |
| 45 | 点点 Agent研发工程师 | 22020 |
| 46 | 交互技术研究员/Agent 工程师 | 22022 |
| 47 | Product Engineer-产品工程师（AI与全栈方向）-社区工程 | 21486 |
| 48 | AI 应用研发工程师-社区工程 | 22021 |
| 49 | Product Engineer-产品工程师（基建方向） | 22077 |
| 50 | 搜广推 × AI 应用工程师 | 22027 |
| 51 | Product Engineer-产品工程师(AI全栈）-直播业务 | 22076 |
| 52 | 全栈研发工程师（广告投放平台方向） | 22408 |
| 53 | 全栈研发工程师（AI 智能投放方向） | 22407 |
| 54 | 全栈研发工程师（AIGC 营销与创作者工具方向） | 22411 |
| 55 | Product Engineer-产品工程师（AI 全栈方向）-国内电商 | 22023 |
| 56 | AI审核研发工程师-Trust&Safety | 22072 |
| 57 | Agent Harness工程师 - 企业智能方向 | 22074 |
| 58 | 全栈工程师（AI native）-Lab 1327 | 21868 |
| 59 | Dots-大模型高性能计算工程师- AI infra | 21829 |

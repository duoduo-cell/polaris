# 北极星（Polaris）项目介绍 v3

2026-10-11 定稿。v2 写完后，按用户要求把每一个技术判断拿去和公开资料核对：工程团队的公开文章、GitHub 上的开源项目、论文和 benchmark、字节跳动和小红书的岗位、大厂的开源项目。这一版是核对后的结果。技术栈见 `STACK.md`，模块顺序见 `PLAN.md`。

2026-10-13：本文其余部分保留产品设计背景，不代表均已实现或验收；当前进度见 `STATUS.md`，后续路线以 `PURPOSE.md` 为准。

读法：技术判断后面的方括号编号（如 [A3]）对应文末"依据"。v2 哪些说法保留、改了、删了，见"核对表"。

## 一句话

一个常驻云端、替你找实习的个人 agent。你的手机和电脑都不用开：它每天在云端自己去小红书、Boss直聘和大厂招聘官网采集信息，构建自己的知识图谱，对照它对你的记忆筛选和推荐，早上 8 点把简报发给你。白天你随口聊一句近况，它就更新对你的记忆。每次投递它都盯着，到期起草跟进，你点头才发。**它做的每一件事都有记录、有打分。**

## 核心链路：采集 → 知识图谱 → 记忆 → 推荐

| 环节 | 做什么 | 关键技术 |
|---|---|---|
| 信息采集 | 定时、自动地抓。**小红书是核心**：横向按方向、公司、岗位组合出很多关键词去搜笔记；纵向把有价值的笔记挖到底，包括正文、评论和楼中楼回复、作者主页和他发过的其他笔记。**Boss直聘**大量搜索，搜索时就按公司规模、融资阶段筛掉小公司。**风向标大厂的招聘官网**（字节、腾讯、小红书等）整批拉取 | 工具调用：用 MCP 接小红书 [X1]，用命令行工具接 Boss [B2]，用浏览器或公开接口接官网 [TC]；子 agent 并行；增量抓取和去重；限速 |
| 知识图谱 | 从岗位和笔记里抽出实体和关系：公司、部门和团队、岗位、技能、方向、城市、笔记、作者。关系都带出处和时间，比如"某团队在招 Agent 实习""某帖说某团队二面考了 RAG" | 实体和关系抽取 [K2]；同名实体合并（字节、字节跳动、ByteDance、抖音）；带时间的边 [M1]；来源之间的冲突裁决 |
| 记忆 | 持续维护你和 agent 之间的记忆：方向、技能、可实习时间、偏好、投递进度，每条带出处和时间。对话太长时自动压缩；旧的对话定期整合进卡片 | 双时间线事实；记忆压缩（对话摘要 [LG4]，加定期整合 [M2]）；冲突消解 |
| 推荐 | 用对你的记忆去检索图谱和岗位库，给出推荐和理由，决定推不推、什么时候推，每天早上一份简报 | 中文关键词加向量加图谱的混合检索 [R1]；重排；推送决策；你的反馈回流 |

**只看风向标大厂。** 公司名单作为配置维护：字节、腾讯、阿里系、美团、快手、小红书、百度、京东、拼多多、网易、华为、米哈游等。Boss 在搜索时按公司规模和融资阶段筛选；名单外的公司默认不推。

**开发期全部在本机跑。** 模型用第三方 OpenAI 兼容服务上的 `deepseek-v4.1-flash`，向量和重排用阿里云。小红书和 Boss 要用你本人的账号登录，所以这两个采集器在你电脑上跑。

## 对标：Meta Muse 和 OpenAI Dots

2026 年 9 月，Meta 发布了 Muse [P1]，OpenAI 发布了 Dots [P2]，都是常驻云端、替人办事的个人 agent。北极星是同一类东西在求职上的具体应用。

| | Muse（Meta） | Dots（OpenAI） | 北极星 |
|---|---|---|---|
| 运行在哪 | 每个用户独享的云端虚拟机；关掉 App 后继续干活，有变化或需要批准时回来找你 [P1] | 在后台持续追用户设定的目标 [P2] | 云主机常驻，定时任务驱动，你不在线也照常跑 |
| 安全 | 同一台机器上隔离运行一个守门 agent（Sentinel），发往外网的东西都要它批准；发邮件、付款前问用户；有操作审计记录 [P1] | 可以给它分配身份、凭证和工具；在和微软一起接入企业安全管控 [P2] | 守门环节审核所有对外动作和外部内容；对外动作必须你确认；全链路记录可回放 |
| 记忆 | 记住你只提过一次的事，可以让它忘掉 [P1] | 未公开细节 | 每条事实带"什么时候成立、从哪句话来"；新旧冲突有记录；可以删 |
| 多 agent | 主 agent 加守门 agent [P1] | 设想由多个专职 Dot 组队 [P2] | 一个主 agent 做决定，多个只读子 agent 并行采集，另有审查、守门、工程师三个角色（见"架构"） |
| 领域 | 通用：订票、购物、填表 | 通用：开发、科研、办公 | 只做求职，有领域专用的工具、数据和评测 |
| 能否核实 | 闭源 | 闭源 | 开源；每个环节有标准答案集和指标，benchmark 公开 |

## 一天是怎么过的

| 时间 | 发生了什么 | 谁来做 |
|---|---|---|
| 06:00 | 读你的求职卡片，写下今天的计划：去哪些来源、用哪些查询词、花多少预算。计划存进数据库，长任务中途可以回头看 [A1][C2] | 主 agent |
| 06:05 | 每个来源一个只读子 agent 并行采集：小红书（横向搜笔记，纵向挖评论和作者）、Boss（大量搜索，按公司规模筛选）、大厂官网（字节、腾讯、小红书等）。原文直接存库，只把编号和一两千 token 的摘要交回主 agent [A1][C1] | 侦察子 agent |
| 遇到新招聘站 | 在沙箱里写一个采集适配器，用录好的页面跑测试；审查角色检查过、你批准后才启用 [K1][S3] | 工程师 |
| 07:00 | 调用固定流程的工具：从岗位和笔记里抽字段、抽实体和关系，识别同一岗位、合并同名公司，裁决来源之间的矛盾，带着出处写进岗位库和知识图谱 | 主 agent 调工具 |
| 07:30 | 用对你的记忆去检索（中文关键词加向量加图谱，再重排）[R1]，按 JD 正文和图谱里的相关笔记算匹配度、写理由，决定推哪些 | 主 agent 调工具 |
| 07:50 | 主 agent 写简报。审查角色在干净的上下文里检查一遍（链接、出处、截止日、有没有编造）[A3]，守门环节放行后 08:00 发出 | 主 agent、审查、守门 |
| 08:05 | 给今天的工作打分，进评测页（见"评测"） | 评测器 |
| 白天 | 你聊一句"我 11 月就能去"，卡片记下这条事实，旧的那条标为失效但保留 [M1]；你问"上海能转正的 RAG 岗有哪些"，工程师写查询去库里查，回答附出处 | 主 agent、工程师 |
| 投递之后 | 到期没回音，起草跟进消息（带上可提前实习的说明和作品链接）；审查和守门检查后暂停，等你批准 [H1]，由你自己发 | 主 agent、审查、守门 |

## 架构：一个写、多个读

v2 设计了七个各自独立的专职 agent。核对后改了，原因有三条：

- **Cognition 的经验：** 做 Devin 的 Cognition 在 2026 年 4 月总结过哪些多 agent 模式行得通 [A3]。行得通的是"多个 agent 贡献信息，写入只由一个来做"：生成和审查互相把关、只读的检索子 agent、主管拆任务给子 agent。行不通的是多个 agent 并行写，因为每次写入都带着一些隐含的决定，并行写的决定会互相打架 [A2]。
- **Anthropic 的经验：** 多 agent 适合广度优先、可以并行的方向（采集正是这样），不适合需要共享上下文、相互依赖多的任务。而且多 agent 系统的 token 用量大约是普通对话的 15 倍 [A1]，$50 的额度经不起每个环节都用 agent。
- **失败原因的统计：** 伯克利的 MAST 研究分析了 1,642 条多 agent 运行记录，失败主要来自系统设计、agent 之间对不齐、缺少结果核验，而不只是模型能力不够 [A4]。

字节开源的 DeerFlow（MIT 许可，8 万多星）也是"主 agent 加子 agent、沙箱、记忆、技能"的结构，构建在 LangGraph 上 [A5]，可以作为参照。

```
        你 ── 网页对话（后期加微信）──┐
                                    ▼
          主 agent（唯一做决定、唯一写入）：每日计划、调用工具、写简报和草稿
          │                │                  │                 │
   侦察子 agent ×N      审查              工程师             守门
   只读，按来源并行    干净上下文复核     沙箱里写代码        隔离运行，不能调工具
   交回编号和摘要      生成-审查循环     测试和批准后启用     对外动作的最后一道关
          │                │                  │                 │
          └────────────────┴───────┬──────────┴─────────────────┘
                   工具层：Polaris Core（MCP 加 REST），按功能分组、带前缀命名
     xhs_* / boss_* / site_*：采集（只读）   jobs_*：抽取、对齐裁决、入库
     graph_*：知识图谱读写   search_*：混合检索、重排   memory_*：卡片事实读写和压缩
     draft_*：简报和消息草稿   sql_*：只读查库
                                   │
           Postgres：关系、向量、中文关键词、图谱边、带时间的记忆、调用追踪
                                   │
       Token Factory：Nemotron Lightning / Super / Ultra，Qwen 向量和重排
```

固定步骤写成代码工具，按工作流合并成少数几个高价值工具，不是一个接口包一个工具 [T1]。agent 只负责需要判断的环节。

## 核心技术点：围绕工具调用的难点

字节的 Agent 实习岗位里 74% 提到工具调用 [J1]。下面每一条都写了在北极星哪里出现、怎么解决、怎么量化、依据是什么。

### 一、工具调用本身

| # | 难点 | 在北极星里哪里出现 | 怎么解决 | 怎么量化 | 依据 |
|---|---|---|---|---|---|
| 1 | 工具越多越选不准 | 来源多、功能多，工具会有几十个 | 工具按功能分组、加前缀命名；每个子 agent 只拿自己那组；工具多了以后改成"先检索工具再调用" | 工具选择准确率；对比可用工具多少时的表现 | 一个压力测试里，把全部工具说明都塞进提示词，选对率只有 13.62%，先检索再给可提到 43.13% [T2]；Anthropic 的工具检索功能也是为这个问题做的 [T3]；工具集臃肿、功能重叠是常见失败 [C1] |
| 2 | 参数填不对 | 城市、日期（"2027 届""尽快到岗"）、枚举值、上一步结果里的编号 | 严格的参数 schema；出错时返回"该怎么改"加正确示例，而不是一个错误码；用有意义的名字或从 0 开始的序号，不用 UUID | 参数完全正确率；无效参数错误的次数 | Anthropic 的工具设计建议 [T1] |
| 3 | 工具返回太多，撑爆上下文 | 一个来源一次几十条岗位 | 分页、过滤、截断；提供精简和详细两种返回格式；原文存库只传编号；超长时压缩，失败记录保留在上下文里 | 每个任务的 token 数和工具调用次数 | 精简格式约为详细格式的三分之一 [T1]；按需加载、压缩、子 agent 只交回摘要 [C1]；把文件系统当外部记忆、保留出错记录 [C2] |
| 4 | 批量处理时陷入固定套路 | 一次抽取几十条 JD | 每条单独调用；序列化格式加入少量变化 | 抽取准确率是否随批次位置下降 | Manus 的经验：批量看 20 份简历时 agent 会陷入重复节奏 [C2] |
| 5 | 外部工具出错 | 限流、超时、验证码、网站改版 | 退避重试（M1 已做）；每个来源单独熔断；出错后从断点继续，不从头来 | 故障注入下的完成率和恢复率 | 出错后从断点恢复，确定性保护加上让模型自己适应 [A1] |
| 6 | 工具返回"看起来对但其实错"的数据 | 岗位已下架但页面还在；转载帖里的旧薪资 | 记录"最近一次看到"；每条字段带来源和抓取时间；和其他来源交叉核对（见第 10 条） | 过期、错误数据的识别率 | 故障注入研究发现，看似合理的错误输出和过期数据比超时更难处理，重试对它们没用 [E3] |
| 7 | 网页内容里藏着恶意指令 | 岗位页面、搜索结果、帖子 | 外部内容一律当数据、不当指令；读外部内容的环节不能调有副作用的工具；守门环节隔离运行 | 注入攻击成功率；有攻击时的任务完成率 | Muse 的 Sentinel 设计 [P1]；CaMeL 把控制流和数据流分开、读不可信内容的模型不能调工具 [S1]；AgentDojo 的评测方法 [S2] |
| 8 | 有副作用的操作 | 发消息、发邮件、投递 | 执行前暂停，把要做的事和参数给你看，批准后才继续；带幂等键防止重复发送；全部留审计记录 | 未经批准的对外动作必须为 0，用测试保证 | LangGraph 的中断和恢复机制 [H1]；Muse 在发邮件、付款前征得同意并留审计记录 [P1] |

### 二、编码能力

| # | 难点 | 怎么解决 | 怎么量化 | 依据 |
|---|---|---|---|---|
| 9 | 没有现成工具时自己写 | 工程师角色在沙箱里写代码：新站点的采集适配器（先从有公开接口的大厂官网做起，比如腾讯）、修坏掉的适配器、写只读 SQL 查库做统计。写完跑测试，审查通过、你批准后才启用 | 适配器一次写对的比例和三次内写对的比例；查库问答正确率 | 用代码当动作比 JSON 工具调用成功率最高高 20%，越复杂的任务收益越大，还能根据报错自己改 [K1]；DeerFlow 也有编程子 agent 和沙箱 [A5]；NVIDIA OpenShell 提供按策略管控文件、进程、网络出口的沙箱 [S3] |

### 三、已有支撑能力：冲突处理和存储检索

| # | 难点 | 怎么解决 | 怎么量化 | 依据 |
|---|---|---|---|---|
| 10 | 多来源信息互相矛盾 | 同一岗位在官网和转载帖里薪资、截止日不一样：按来源可信度和时效取值，可信度根据历史表现估计、不拍脑袋定；难例交给模型裁决并留理由；每个字段记出处，保留所有版本 | 同岗识别的精确率和召回率；冲突识别率；裁决正确率；出处完整率 | 真值发现（truth discovery）：可信的来源更常给出真实信息，多个可信来源支持的说法更可能为真，来源可信度和真值一起估计 [M4] |
| 11 | 记忆的新旧冲突 | 每条事实记两条时间线：事实什么时候成立和失效，系统什么时候记下和作废。新说法和旧说法矛盾时，把旧的标为失效但不删，所以能回答"它什么时候变的"。每条事实都链接回原话 | 更新正确率；冲突识别率；按时间问答的正确率 | Zep/Graphiti 的双时间线模型：t_valid、t_invalid、t'_created、t'_expired，有矛盾时把旧边作废，事实可追溯到原始消息 [M1]；Mem0 的新增、更新、删除、不变四种操作 [M2] |
| 12 | 记忆要能问 | 问"我 11 月能去实习吗"，要用最新的事实；没记录过的不能编 | 照 LongMemEval 的五项能力出题：信息抽取、跨会话推理、时间推理、知识更新、拒答 | LongMemEval（ICLR 2025）[M3] |
| 13 | 检索要准，冷门岗位也不能漏 | 中文关键词检索（BM25）加向量检索，再重排；按时间扩展查询；agent 自己改写查询、多轮检索；回答附出处 | 前 20 条里漏掉的比例（1 − Recall@20）；nDCG@10；重排前后对比；20 个标题不含方向关键词的冷门岗位召回数 | Anthropic 实测：前 20 条的检索失败率，基线 5.7%，加上下文向量 3.7%，再加 BM25 2.9%，再加重排 1.9% [R1]；Postgres 有现成的中文分词扩展 [R2]；按时间扩展查询来自 LongMemEval [M3] |

### 四、多 agent 协作和成本

| # | 难点 | 怎么解决 | 怎么量化 | 依据 |
|---|---|---|---|---|
| 14 | 交接不清、重复干活、结果没人核验 | 给子 agent 的任务单写清四样：目标、输出格式、可用的工具和来源、边界；共享状态放数据库；审查角色在干净的上下文里核验结果 | 重复调用数；交接失败率；按 MAST 的失败分类给失败归类 | 任务描述不清会导致重复搜索和遗漏 [A1]；MAST 的失败分类 [A4]；生成和审查分开、审查用干净上下文 [A3] |
| 15 | 调用成本要可控（开发期花的是你自己的额度） | 只在并行采集用子 agent；按角色选模型（子 agent 用便宜的，主 agent 用强的）；固定的提示词前缀方便缓存；评测时缓存模型输出 | 每天花费；每份简报的 token 数；同一任务换不同模型的成本和效果对比 | 多 agent 约 15 倍于普通对话的 token [A1]；Manus 统计输入和输出 token 约 100:1，缓存过的输入便宜约 10 倍 [C2] |

### 五、你点名的几块：信息采集、知识图谱、记忆压缩、推荐

| # | 难点 | 怎么解决 | 怎么量化 | 依据 |
|---|---|---|---|---|
| 16 | 小红书横向加纵向深挖 | 横向：查询词按"方向 × 大厂 × 岗位类型"组合生成（如"字节 Agent 实习""腾讯 大模型 面经"），按发布时间和排序方式筛选，结果按笔记编号去重。纵向：按规则决定挖不挖，提到大厂、实习、内推、面经的才展开评论和楼中楼、再看作者主页和他的其他笔记。只开放只读工具（搜索、笔记详情、用户主页、首页推荐），评论、点赞、发布这类写操作在工具白名单里屏蔽。随机间隔、分散在全天、只抓新的。登录态过期时提醒你重新扫码 | 覆盖率：标准答案集里的帖子被采到几成；每条有用信息花几次调用；重复率；被限流次数 | xiaohongshu-mcp 提供 13 个工具，其中搜索可按排序、类型、发布时间、地点筛选，详情可带评论和回复，用户主页带他发过的笔记；文档提醒同一账号在别处网页登录会把会话挤掉 [X1]；工具设计原则 [T1] |
| 17 | Boss 大量搜索和筛选 | 搜索时用公司规模、融资阶段、行业筛选，再用大厂名单过滤一遍；翻页和多城市、多关键词组合；用它自带的随机延迟和限流退避；打招呼和批量打招呼命令一律不用 | 大厂岗位占比；和官网对得上的比例；被限流次数 | boss-cli 的搜索支持城市、薪资、经验、学历、行业、公司规模、融资阶段、职位类型筛选，详情能输出 JSON，自带随机延迟和指数退避 [B2] |
| 18 | 知识图谱怎么建才可信 | 实体合并：别名表加向量相似度加模型裁决，处理"字节、字节跳动、ByteDance、抖音"这类情况。关系必须附原文片段，没有出处的不入图；边带时间和来源，冲突按第 10 条裁决。图谱存在同一个 Postgres 的普通表里，不另装图数据库 | 实体合并的精确率和召回率；关系抽取的精确率；出处完整率；有图谱和没图谱时推荐效果的对比 | LightRAG 从文本抽实体和关系，并支持用纯 Postgres 表存图谱 [K2]；Graphiti 带时间的边、矛盾时作废旧边 [M1] |
| 19 | 记忆压缩会丢信息 | 对话内：用 LangChain 的对话摘要中间件，超过阈值就把旧消息压成摘要、保留最近若干条 [LG4]。跨会话：每天把对话里的事实整合进卡片（新增、更新、作废、不变），原始对话留档，可追溯 [M2]。压缩时先保召回、再保精度 [C1] | 压缩前后按 LongMemEval 方式问答，比较正确率；省下的 token | [LG4][M2][C1][M3] |
| 20 | 推荐：推多了打扰，推少了漏掉 | 检索后重排；按匹配度、截止日紧迫度、新不新决定推不推；每条理由引用岗位原文、图谱里的相关笔记和你的记忆；你的"有用 / 没用"反馈回流 | 推送准确率；漏报率（标准答案集里该推的推了几成）；反馈里"有用"的比例 | 混合检索加重排 [R1] |

## 真实任务验证

后续路线、验收标准与生产实践依据统一见 [PURPOSE.md](../PURPOSE.md)。旧 Polaris-Bench 全面扩建计划撤销；已有模块报告保留为历史证据。

验证同时覆盖任务结果、工具选择与参数、条件遵守、来源事实及实际保存状态，允许合理的不同执行路径。优先把真实失败变成回归案例；固定条件做单机制对照，再用预留新需求、重复运行和另一答题模型检验收益。追踪负责复现与归因，模型评分仅辅助，不能替代关键事实核对和用户体验。

## 对上岗位要求

| 字节 Agent 实习岗位的要求（出现比例）[J1] | 北极星里对应的部分 |
|---|---|
| 工具调用（74%） | 难点 1 到 9；工具调用、故障注入、注入攻击三个评测套件 |
| 评测、Benchmark（56%） | 真实任务对照、失败回归与完整调用证据 |
| 多 Agent、工作流编排（各 44%） | 一个写、多个读的结构；任务单；生成和审查循环 |
| RAG（42%）、知识库（22%） | 混合检索加重排；agent 自己改写查询；带出处的问答；从小红书和岗位构建的知识图谱 |
| 规划、任务拆解（40%） | 主 agent 的每日计划和预算 |
| 个人项目、Hackathon（40%） | 本项目本身 |
| 记忆（36%） | 双时间线的事实记忆、冲突消解、可遗忘；LongMemEval 式评测 |
| Agent Harness（32%） | 录制回放、故障注入、失败归因、上线前回归 |
| 上下文管理（30%） | 原文存库只传编号；子 agent 交回摘要；压缩；固定前缀便于缓存 |
| AI 编程工具（30%） | 工程师角色；本项目用 Claude Code 协作开发，提交记录可查 |
| 可观测、Trace（24%） | OpenTelemetry 规范的全链路追踪 |
| MCP（20%） | Polaris Core 用 MCP 开放工具，Hermes、Claude 等任何 MCP 客户端都能接上 |
| Python（66%）、TypeScript（24%） | Python 后端，TypeScript 前端。不为了凑关键词去用 Go |

另外，字节的一个 Agent Harness 岗位在要求里点名了 HermesAgent [J1]。Hermes 是 MIT 许可，约 25 万星，自带记忆、技能（遵循 agentskills.io 标准）、定时任务、子 agent、MCP 和多渠道网关 [H2]。北极星的工具通过 MCP 开放，Hermes 能直接接上；但核心编排用 LangGraph，理由见 `STACK.md`。

## 范围和取舍

离 10-29 提交还有 18 天。

| | 首版必做 | 有余力再做 |
|---|---|---|
| 运行 | 云端每日定时跑，网页简报 | 微信渠道 |
| 角色 | 主 agent、侦察子 agent、审查、守门 | 工程师为任意官网从零写适配器并自动修复 |
| 编码能力 | 只读 SQL 查库回答问题；为有公开接口的大厂官网自动生成适配配置 | 通用网站适配器生成 |
| 评测 | 真实任务对照与必要回归，见 PURPOSE | 仅在实际故障或任务需求证明必要时扩大覆盖 |
| 数据源 | 小红书（横向加纵向深挖）、Boss（大量搜索加大厂筛选）、字节和腾讯和小红书的招聘官网、手动粘贴；开发期全部在本机跑 | 其他大厂官网、牛客、外企招聘系统 |

不做的不变：不自动群发、不自动投递、不买 star、公开演示只放合成数据。

## 核对表：v2 的说法和核对结果

| v2 的说法 | 结论 | 依据 |
|---|---|---|
| 七个专职 agent 分工 | **改**：一个主 agent 负责所有决定和写入，侦察子 agent 只读并行，另有审查、守门、工程师 | [A1][A2][A3][A4] |
| 多 agent 的好处是工具集小、上下文隔离、并行 | 保留，但补上代价：token 约 15 倍，只在采集这种广度优先的地方用 | [A1] |
| 工具多了先检索再给 | 保留，有实验数据支持；删掉 v2 里"5、15、40 个工具"这种没依据的具体档位 | [T2][T3] |
| 校验失败时返回模型看得懂的错误 | 保留，并补上"给正确示例、用有意义的名字代替 UUID、提供精简返回格式" | [T1] |
| 原文存库，只把编号放进上下文 | 保留 | [C1][C2] |
| 外部内容当数据、守门 agent 审查 | 保留，并改成"读外部内容的环节不能调有副作用的工具" | [S1][P1] |
| 对外动作必须批准、幂等、审计 | 保留 | [H1][P1] |
| 工程师 agent 在沙箱里写适配器 | 保留，补上"审查加批准后才启用"（多个角色可以贡献，写入只经一个口子） | [K1][A3][S3] |
| 按来源可信度和时效取值 | 保留，改为"可信度按历史表现估计" | [M4] |
| 记忆记"什么时候成立、失效、从哪句话来" | 保留，改成双时间线的具体做法 | [M1] |
| 时间问答评测 | 保留已有回归，不作为当前扩建方向 | [M3] |
| 中文关键词加向量加重排 | 保留，补上实测数字 | [R1][R2] |
| 录制回放环境 | 保留，有先例 | [E3][E7] |
| 用 pass@k 评编码 | 保留；另加 pass^k 评稳定性 | [E1] |
| 评审模型要拿人工标注校准 | 保留 | [E4][A1] |
| 追踪"按业界通行格式" | 具体化为 OpenTelemetry 生成式 AI 规范，并注明它还在开发阶段 | [E5] |
| 新增：看似合理的错误数据和过期数据最难处理 | 新增第 6 条难点 | [E3] |
| 新增：批量处理会陷入固定套路 | 新增第 4 条难点 | [C2] |
| 只评最终状态、不评路径 | 撤销：结果、调用过程和保存状态都要检查，允许合理的不同路径 | 当前标准见 PURPOSE |
| Muse 和 Dots 的描述 | 保留。Dots 的"敏感操作要批准"只在二手报道里见到，TechCrunch 原文没写，表里已删掉 | [P1][P2] |

## 选型结果

v3 初稿里留给选型的 8 个问题，已经在 `STACK.md` 里定了，依据也在那里：

| 问题 | 结论 |
|---|---|
| 编排框架 | LangGraph 1.x 加 LangChain 1.x 做核心；Hermes 移出主链路，提交后接微信时再评估 |
| 人工审批 | LangChain 的人工审批中间件，按工具设置"批准、改参数、拒绝" |
| 中文关键词检索 | jieba 分词加 Postgres 全文检索；放应用层还是装 pg_jieba 扩展，M4 做小实验定。ParadeDB 官方文档里没找到中文分词器，不选 |
| 追踪和评测平台 | 自有追踪表（产品里的评测页用）加 LangSmith（开发期调试和实验）；Langfuse 自己部署太重，Coze Loop 作参照 |
| 沙箱 | 先用 Docker 容器；OpenShell 上云后有余力再试 |
| 提示词缓存 | 等 Nebius 账号下来后实测 |
| 网页采集 | Playwright 为主；Midscene.js 留作备用 |
| 前端 | Next.js 加 TypeScript，对话用 Vercel AI SDK |

小红书 2027 届校招的 59 个 AI、Agent、全栈相关岗位也调研了（`research/2026-10-11-xiaohongshu-agent-jd.md`）。结论和字节一致：考的是工具调用、评测、多 Agent、记忆、可观测这些能力，很少点名具体框架；全栈（37%）和 GitHub 开源（54%）很被看重。

## 依据

**多 agent**
- [A1] Anthropic, How we built our multi-agent research system — https://www.anthropic.com/engineering/multi-agent-research-system
- [A2] Cognition, Don't Build Multi-Agents（2025-06）— https://cognition.com/blog/dont-build-multi-agents
- [A3] Cognition, Multi-Agents: What's Actually Working（2026-04-22）— https://cognition.com/blog/multi-agents-working
- [A4] Cemri et al., Why Do Multi-Agent LLM Systems Fail?（MAST，NeurIPS 2025）— https://arxiv.org/abs/2503.13657
- [A5] 字节跳动 DeerFlow — https://github.com/bytedance/deer-flow

**工具和上下文**
- [T1] Anthropic, Writing effective tools for agents — https://www.anthropic.com/engineering/writing-tools-for-agents
- [T2] Gan & Sun, RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection — https://arxiv.org/abs/2505.03275
- [T3] Anthropic, Advanced tool use / Tool search tool — https://www.anthropic.com/engineering/advanced-tool-use 、https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- [C1] Anthropic, Effective context engineering for AI agents — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- [C2] Manus, Context Engineering for AI Agents: Lessons from Building Manus（2025-07-18）— https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus

**安全、审批、编码、沙箱**
- [S1] Debenedetti et al., Defeating Prompt Injections by Design（CaMeL）— https://arxiv.org/abs/2503.18813 ；评论：https://simonwillison.net/2025/Apr/11/camel/
- [S2] AgentDojo（NeurIPS 2024）— https://arxiv.org/abs/2406.13352
- [S3] NVIDIA OpenShell 架构文档 — https://docs.nvidia.com/openshell/latest/about/architecture.html
- [H1] LangGraph Human-in-the-loop — https://docs.langchain.com/oss/python/langgraph/human-in-the-loop
- [K1] Wang et al., Executable Code Actions Elicit Better LLM Agents（CodeAct）— https://arxiv.org/abs/2402.01030

**检索、记忆、冲突**
- [R1] Anthropic, Introducing Contextual Retrieval — https://www.anthropic.com/news/contextual-retrieval
- [R2] pg_jieba：https://help.aliyun.com/en/polardb/polardb-for-postgresql/pg-jieba 、https://ext.pigsty.io/e/pg_jieba/ （zhparser 和 ParadeDB pg_search 在选型时再查官方文档）
- [M1] Rasmussen et al., Zep: A Temporal Knowledge Graph Architecture for Agent Memory — https://arxiv.org/abs/2501.13956 ；Graphiti：https://github.com/getzep/graphiti
- [M2] Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory — https://arxiv.org/abs/2504.19413 （操作定义来自二手转述，引用前要读原文）
- [M3] Wu et al., LongMemEval（ICLR 2025）— https://arxiv.org/abs/2410.10813
- [M4] Li et al., A Survey on Truth Discovery — https://arxiv.org/abs/1505.02463

**评测和追踪**
- [E1] Yao et al., τ-bench — https://arxiv.org/abs/2406.12045
- [E2] Berkeley Function Calling Leaderboard — https://gorilla.cs.berkeley.edu/leaderboard.html ；Epoch AI 的审查（发现最新版 48% 的抽查任务有缺陷）：https://epoch.ai/benchmarks/berkeley-function-calling-leaderboard/review
- [E3] ToolMisuseBench — https://arxiv.org/abs/2604.01508 ；AgentCheck — https://arxiv.org/abs/2607.11098 ；PALADIN — https://arxiv.org/abs/2509.25238
- [E4] Anthropic, Demystifying evals for AI agents — https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- [E5] OpenTelemetry, GenAI agent and framework spans — https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
- [E6] 字节跳动 Coze Loop — https://github.com/coze-dev/coze-loop
- [E7] 录制回放工具示例：langchain-replay — https://github.com/sixty-north/langchain-replay （其他几个库的说法来自它们自己的介绍，选型时再核）

**对标产品、Agent 外壳、字节**
- [P1] Meta Muse：https://www.relevantaudience.com/ai/meta-muse-personal-ai-agent-launch/ 、https://www.techrepublic.com/article/news-meta-muse-ai-agent-us-launch/
- [P2] OpenAI Dots：https://techcrunch.com/2026/09/29/openai-launches-dots-its-bubbly-agentic-avatar/ 、https://www.aljazeera.com/economy/2026/9/30/openai-launches-dots-personal-ai-assistant-built-to-handle-everything
- [H2] Hermes Agent — https://github.com/NousResearch/hermes-agent
- [B1] 字节跳动 Midscene.js — https://github.com/web-infra-dev/midscene
- [J1] 字节跳动 50 个 Agent 实习岗位调研 — `research/2026-10-11-bytedance-agent-intern-jd.md`

**数据源和本次新增**
- [X1] xiaohongshu-mcp（Apache-2.0，约 1.6 万星）— https://github.com/xpzouying/xiaohongshu-mcp
- [B2] boss-cli（Apache-2.0）— https://github.com/jackwener/boss-cli
- [TC] 腾讯招聘公开查询接口（2026-10-11 实测，搜 Agent 有 223 个岗位）— https://careers.tencent.com/tencentcareer/api/post/Query?keyword=Agent&pageIndex=1&pageSize=10
- [K2] LightRAG（港大）：从文本抽实体和关系，支持纯 Postgres 表存图谱（PGTableGraphStorage）— https://github.com/HKUDS/LightRAG ；v1.5.6 发布说明：https://newreleases.io/project/github/HKUDS/LightRAG/release/v1.5.6
- [LG4] LangChain 内置中间件（含对话摘要 SummarizationMiddleware）— https://docs.langchain.com/oss/python/langchain/middleware/built-in

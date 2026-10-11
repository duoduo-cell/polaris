# 验证结论

2026-10-10 联网核对。状态分三种：**已查实**（有官方出处）、**二手**（只有第三方说法）、**待实测**（必须拿到 key 或账号后亲手跑）。

没有任何一项是亲手跑过的，这一版全部来自网页。M0 的任务就是把"待实测"清掉。

## 1. 比赛规则

| 项 | 结论 | 状态 |
|---|---|---|
| 截止时间 | 太平洋时间 10-30 10:00，即北京时间 10-31 01:00 | 已查实 |
| 评审期 | 12-01 到 12-15，期间项目须保持免费可测 | 已查实 |
| 参赛资格 | 排除名单是巴西、魁北克、俄罗斯、克里米亚、古巴、伊朗、朝鲜等，不含中国大陆 | 已查实 |
| "运行在 Nebius 上" | 运行时调用 Token Factory 推理接口即满足，不要求主机也在 Nebius | 已查实 |
| 赛道工具 | NemoClaw、OpenShell、Hermes Agent、Nebius Serverless 是建议，不是硬性要求 | 已查实 |
| 第三方数据 | 原文是"按其条款和许可使用"，比"有权使用"更严 | 已查实 |
| 许可证 | 要在仓库 About 区可见，也就是根目录放 GitHub 能识别的 LICENSE 文件 | 已查实 |
| Tavily 奖 | 运行时真实调用 Tavily API 即可参评，奖金 $3,000，可与主奖叠加一项 | 已查实 |
| 额度 | Token Factory 共 $50（活动表单 $25 + Builder Program $25），Tavily $25，LangSmith $100 | 已查实 |

出处：[规则](https://nebiusglobalaihackathon.devpost.com/rules)、[主页](https://nebiusglobalaihackathon.devpost.com/)、[资源页](https://nebiusglobalaihackathon.devpost.com/resources)、[Builder Program](https://dev.nebius.com/builders)

## 2. Token Factory 和模型

| 项 | 结论 | 状态 |
|---|---|---|
| 接口地址 | `https://api.tokenfactory.nebius.com/v1/`，OpenAI 格式 | 已查实 |
| 模型 ID | 文档里写的四个 ID 都在目录里（见下表） | 已查实 |
| 工具调用 | 平台支持 `tools` 和 `tool_choice`；官方页说 Nemotron 标了工具调用和推理，但建议自行验证 | 已查实，待实测 |
| 结构化输出 | 支持 `response_format` 的 `json_schema` 和 `json_object`，仅限模型卡带"JSON mode"标签的模型 | 已查实，Nemotron 是否带标签待确认 |
| 向量 | `Qwen/Qwen3-Embedding-8B`，$0.01 / 百万 token | 已查实 |
| 重排 | 有专用接口 `POST /v1/rerank`，示例模型 `Qwen/Qwen3-Reranker-8B` | 已查实，目录里是否上架待确认 |
| 限流 | 默认值只在控制台里看得到；按 15 分钟窗口自动伸缩 | 待确认 |
| 微调 | 目录没有给 Nemotron 开自助微调 | 已查实 |

| 模型 ID | 上下文 | 输入 / 输出（每百万 token） |
|---|---|---|
| `nvidia/Nemotron-3_5-Lightning` | 约 100 万 | $0.06 / $0.24 |
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 262K | $0.06 / $0.24 |
| `nvidia/nemotron-3-super-120b-a12b` | 256K | $0.30 / $0.90 |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | 约 100 万（另一处写 256K） | $1.00 / $3.00 |

两个要点：

- **Super 调工具必须开推理。** Daily.co 的实测说关掉推理后工具调用"很差"，开推理并把思考预算限在 512 token 后，30 轮带工具的基准得分 97%。模型卡建议 `temperature=1.0`、`top_p=0.95`，推理开关走 `chat_template_kwargs.enable_thinking`。Token Factory 是否透传这个参数待实测。
- **中文。** Super 的模型卡列了 7 种支持语言，含中文。其余三档没查到明确说明，抽取准确率待实测。

Nano 在 Nebius 官方页有，在第三方目录镜像里没有，优先用 Lightning，用前拿 `/v1/models` 确认。

出处：[Nemotron 目录页](https://nebius.com/services/token-factory/models/nvidia-nemotron-models-inference)、[Nemotron 介绍页](https://nebius.com/services/token-factory/nemotron)、[函数调用](https://docs.tokenfactory.nebius.com/ai-models-inference/function-calling.md)、[结构化输出](https://docs.tokenfactory.nebius.com/ai-models-inference/json)、[重排接口](https://docs.tokenfactory.nebius.com/api-reference/inference/rerank-documents.md)、[限流](https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits.md)、[Super 模型卡](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8)、[Daily.co 实测](https://www.daily.co/blog/nvidia-nemotron-3-super/)、[第三方目录镜像](https://mastra.ai/models/providers/nebius)

### 2.1 开发期的模型服务

用户有一个 OpenAI 兼容的 key，走第三方转接服务，模型是 `gpt-5.5`。决定：开发期用它，提交前换成 Token Factory。地址和 key 只放在本机的 `.env`，不进仓库和文档。

2026-10-10 晚在用户本机跑了 `scripts/smoke_llm.py`，9 项里 6 项通过。原始结果在 `scripts/smoke_result.json`（不进仓库）。

**结论：能用来开发对话、工具调用和结构化抽取，也就是 M1、M2 需要的部分。缺向量模型，M3 之前要另找。**

| 项 | 结论 | 状态 |
|---|---|---|
| 模型列表 | `/v1/models` 正常，共 15 个，全是 gpt 系列对话模型（`gpt-5.4`、`gpt-5.4-mini`、`gpt-5.5`、`gpt-5.6`、`gpt-6-sol` 等），**没有任何向量模型** | 已实测 |
| chat completions | 支持。Hermes 和多数框架走这个接口，所以能拿来做开发 | 已实测 |
| responses 接口 | 支持，但一次要 48 秒 | 已实测 |
| 流式 | 支持，但首包要 23 秒 | 已实测 |
| 工具调用 | 单步 3/3 选对工具、填对参数；往返一次，最终回答用上了工具结果 | 已实测 |
| 结构化输出 | 支持 `json_schema` 加 `strict`。7 个字段对了 6 个，错的是"能否转正"：原文"表现优秀者有转正机会"，模型给了 null。这是字段定义没写清楚，不是服务的问题，M2 写抽取提示词时要明确"写了有转正机会就算 true" | 已实测 |
| 向量 | `text-embedding-3-small` 返回 404，这个服务没有配向量模型 | 已实测 |
| 备选模型 | `gpt-5.4-mini` 返回 429"上游限流"。批量跑评测时要处理限流和重试 | 已实测 |
| 延迟 | 一次普通调用 4 到 18 秒，比直连慢很多。批量抽取要并发，评测要缓存模型输出 | 已实测 |
| 隐藏的系统提示 | 发一句十几个字的话，计费的输入是 4,394 token，其中 4,382 是服务端自己加进去的指令。后果：每次调用多算约 4,400 token 输入；模型行为受这段看不见的指令影响，**开发期在它上面测出的指标不能当正式数字**，正式指标一律以 Nemotron 跑出来的为准 | 已实测 |
| 首次运行的坑 | Windows 下脚本的隐藏输入框不认 Ctrl+V，存进 `.env` 的 key 是 Ctrl+V 这个按键本身（1 个字符），所以第一次全部返回 400。脚本已改成检测这种情况并提示，用鼠标右键粘贴或直接用记事本编辑 `.env` 都可以 | 已修 |
| Tavily | 是搜索服务，不是模型，这个 key 替代不了。免费每月 1,000 额度，M2 才用到 | 已查实 |
| 数据去向 | 开发期发出去的内容会经过这家转接服务。用合成数据开发没问题，真实聊天和个人信息等切到 Token Factory 后再接 | 提醒 |
| DeepSeek | 用户想用这个服务上的 DeepSeek V4.1 Flash。原来的 key 所在分组只有 gpt 系列，调不到。用户换了一个分组的 key 后，见下面的"开发期定稿" | 已实测 |

**开发期定稿（2026-10-10 晚，用户决定）：对话用这个服务上的 `deepseek-v4.1-flash`，向量用阿里云百炼 `text-embedding-v4`（第 2.2 节）。** 换 key 后重跑冒烟脚本，9 项过了 7 项：

| 项 | 结论 | 状态 |
|---|---|---|
| 模型目录 | 换 key 后是 23 个：DeepSeek V4 系列、豆包、GLM、Kimi、Qwen 3.6 到 3.8。原来的 gpt 系列不在这个分组里了 | 已实测 |
| 对话、responses、流式 | 都通过。一次约 2 到 3 秒，流式首包 5 秒。一句话计费输入 35 token，**没有隐藏指令** | 已实测 |
| 工具调用 | 单步 3/3，往返通过 | 已实测 |
| 结构化输出 | 不支持 `json_schema`，报错原文和阿里云一模一样，推测这个服务的 DeepSeek 是从阿里云转过来的。`json_object` 下 7 个字段全对，但这一次用了 18 秒，比直连阿里云的 6.5 秒慢 | 已实测 |
| 限流 | 备选模型 `deepseek-v4-flash` 返回 429"上游限流"，这个服务上已经第二次见到。批量调用必须带重试和退避 | 已实测 |
| 思考关不掉 | `enable_thinking: false` 和 `thinking: {type: disabled}` 都被忽略，每次都先思考；抽取一条岗位平均输出 573 token，其中 324 是思考。开发期的耗时和 token 数偏高，正式数字以提交时的模型为准 | 已实测 |

### 2.2 开发期备选：阿里云百炼

2026-10-10 晚，Nebius 绑卡一步先放下，改用用户已有的阿里云账号。百炼的 OpenAI 兼容地址是 `https://dashscope.aliyuncs.com/compatible-mode/v1`。冒烟脚本用 `deepseek-v4.1-flash` 对话加 `text-embedding-v4` 向量跑了一遍，主要项全部通过。

| 项 | 结论 | 状态 |
|---|---|---|
| 模型目录 | 175 个。有 `deepseek-v4.1-flash`、`deepseek-v4-pro`、`qwen-flash`、`qwen3.5-flash`，向量 `qwen3.7-text-embedding`（及 flash 版），重排 `qwen3.7-text-rerank`。带 `siliconflow/`、`vanchin/` 前缀的是第三方托管，要单独开通，调用返回"产品未开通" | 已实测 |
| 向量 | `text-embedding-v4` 能用，传 `dimensions=1024` 返回 1024 维，约 1.6 秒。它不在模型列表里，但能调通 | 已实测 |
| chat completions | `deepseek-v4.1-flash` 一次约 3 秒。一句十几个字的话计费输入 35 token，**没有隐藏的系统提示**。默认带思考（这次思考了 17 token），批量抽取时要看能不能关 | 已实测 |
| 流式 | 首包 1.6 秒 | 已实测 |
| 工具调用 | 单步 3/3，往返一次通过，每次约 3.5 秒 | 已实测 |
| 结构化输出 | **不支持 `json_schema`**（返回"这种 response_format 现在不可用"），`json_object` 可以，7 个字段全对，"能否转正"也答对了。所以模型调用封装要做成：能用 `json_schema` 就用，不能就用 `json_object` 加本地 schema 校验和失败重试。Token Factory 上也只有带"JSON mode"标签的模型支持 `json_schema`，这个兜底反正要做 | 已实测 |
| 和转接服务的 gpt-5.5 比 | 快 3 到 5 倍，没有隐藏指令，抽取更准；缺点是不支持 `json_schema` | 已实测 |
| 价格 | 没查。在百炼控制台的模型广场看 | 待确认 |
| 数据去向 | 开发期发出去的内容会到阿里云。和 2.1 一样，开发期用合成数据 | 提醒 |

## 3. Agent 底座

| 项 | 结论 | 状态 |
|---|---|---|
| Hermes 接 Token Factory | 内置提供方，设 `NEBIUS_API_KEY` 后 `hermes chat --provider nebius --model <id>` | 已查实 |
| Hermes 能力 | 持久记忆、技能、定时任务、子 agent、MCP 客户端、多渠道网关，MIT 许可 | 已查实 |
| Hermes 接自写 MCP | `config.yaml` 的 `mcp_servers` 支持 stdio 和 HTTP，可按工具名过滤 | 已查实 |
| Hermes 定时任务 | 由网关进程每 60 秒检查，每次是全新会话，可挂技能，结果投递到指定渠道 | 已查实 |
| Hermes 对外接口 | API Server，默认 `127.0.0.1:8642`，OpenAI 格式加会话、运行、任务接口，支持 SSE；跨域默认关 | 已查实 |
| Hermes 渠道 | 飞书功能最全；微信走腾讯 iLink Bot，主动推送依赖对方最近发过消息，不稳；另有邮件、Telegram、钉钉等 | 已查实 |
| Hermes 上下文要求 | 带工具运行至少 64K，四档 Nemotron 都够 | 已查实 |
| Hermes 配 Nemotron 稳不稳 | NVIDIA 官方示例就是 Hermes + Nemotron 3 Super，但没有查到稳定性数据 | 待实测 |
| NemoClaw 阶段 | alpha，早期预览，无 SLA | 已查实 |
| NemoClaw 装 Hermes | 支持，命令是 `nemohermes`；Ubuntu 24.04 + Docker 验证过；最低 4 vCPU、8 GB 内存、20 GB 磁盘，不要求 GPU | 已查实 |
| NemoClaw 接 Token Factory | 走"其他 OpenAI 兼容端点"，官方标注"测试过但有限制" | 已查实，待实测 |
| NemoClaw 里接 MCP | 托管 MCP 只支持带鉴权的 HTTPS Streamable HTTP | 已查实 |
| NemoClaw 在 Nebius 主机上 | 没查到任何人写过，官方只说支持无界面 Linux 服务器 | 待实测 |
| 沙箱访问宿主机服务 | 文档没写，默认拦截未批准的出站请求 | 待实测 |

出处：[Hermes 提供方](https://hermes-agent.nousresearch.com/docs/integrations/providers)、[Hermes 仓库](https://github.com/nousresearch/hermes-agent)、[MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)、[定时任务](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)、[API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)、[渠道](https://hermes-agent.nousresearch.com/docs/user-guide/messaging)、[微信渠道](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/weixin)、[NemoClaw 快速开始](https://docs.nvidia.com/nemoclaw/user-guide/hermes/get-started/quickstart)、[前置条件](https://docs.nvidia.com/nemoclaw/user-guide/hermes/get-started/prerequisites)、[平台支持](https://docs.nvidia.com/nemoclaw/user-guide/hermes/reference/platform-support)、[架构](https://docs.nvidia.com/nemoclaw/user-guide/hermes/about/how-it-works)、[NVIDIA 官方示例](https://developer.nvidia.com/blog/deploy-self-evolving-agents-for-faster-more-secure-research-with-a-hermes-agent-and-nvidia-nemoclaw/)

## 4. 数据源

| 来源 | 能不能接 | 怎么接 | 风险 | 状态 |
|---|---|---|---|---|
| 小红书 | 能 | [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)（Go，Apache-2.0，约 1.5 万星）：无头浏览器加本人账号登录，提供搜索、笔记详情和评论，有 Docker 镜像 | 同一账号在别处网页登录会被挤下线；作者自己提示服务器 IP 有风险；项目自述仅供学习 | 已查实，待实测 |
| 小红书（备选） | 能 | [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)：Playwright 保存登录态 | 免责声明禁止商用，不适合放进参赛仓库 | 已查实 |
| Boss直聘 | 能 | [boss-cli](https://github.com/jackwener/boss-cli)（Python，Apache-2.0）：逆向接口加浏览器 cookie，`search`、`detail` 可输出 JSON，自带随机延迟和退避 | cookie 过期会报"环境异常"需重新登录；设计上是在本机用 | 已查实，待实测 |
| 大厂官网 | 多数能 | 腾讯、快手、美团、阿里、京东、B站可直接调页面背后的 JSON 接口；字节要用浏览器拦截请求；阿里需要会话 cookie 和 csrf | 接口会变，要逐家写适配 | 二手，待实测 |
| Tavily | 能 | 官方 API。免费每月 1,000 额度，基础搜索 1 额度一次，抓取 5 个网页 1 额度 | 对小红书这类站内内容的覆盖没查到 | 已查实 |
| 公开汇总仓库 | 能 | GitHub 上有每日更新的校招和实习汇总，数据来自飞书多维表格 | 多数没写许可证，只适合当线索，不适合转存 | 已查实 |
| GitHub | 能 | 官方 API，读用户自己的仓库 | 无 | 常识 |

三个影响架构的结论：

1. **小红书和 Boss 没有查到面向个人的官方读取接口。** 现有工具都是浏览器自动化或逆向接口。两家用户协议的具体条款没有逐条核对，按"不符合平台条款"处理。比赛规则要求第三方数据按条款使用，所以这两个源不能出现在公开演示里。
2. **这两个源不适合放在云主机上跑。** Nebius 没有中国大陆区域（常识，未在价格页查到区域列表），境外机房 IP 加个人账号登录是风控最敏感的组合。结论是放在用户自己的电脑上跑，把结果推到云端。
3. **规模和用途决定风险等级。** 查到的刑事判例是公司批量抓取用户数据并群发私信牟利，判的是非法获取计算机信息系统数据罪。本人账号、低频、只读、自用远不是这个量级，但仍然违反平台条款，账号可能被限制。

出处除表内链接外：[判例报道](https://www.secrss.com/articles/72097)、[官网抓取博客](https://www.cnblogs.com/iruan/p/18323973)、[同类项目的源适配说明](https://tomevault.io/tome/Soli22de/internship-finder)、[汇总仓库示例](https://github.com/mailyfamily/campus-recruitment)、[Tavily 额度](https://docs.tavily.com/documentation/api-credits.md)

### 4.1 第二轮补查的数据源

| 来源 | 能不能接 | 怎么接 | 风险 | 状态 |
|---|---|---|---|---|
| 脉脉 | 没有可用的读取工具 | 唯一查到的 [maimai-mcp](https://github.com/JACKWang19559/maimai-mcp) 是给招聘方用的（看联系人、给候选人发消息），没有搜人、搜校友、看帖子的能力，1 个星、2 次提交；靠复制 cookie 登录，约 60 天过期 | 作者自己提示发消息有风控风险 | 已查实 |
| LinkedIn 岗位 | 能，量小 | [JobSpy](https://jobspipe.dev/blog/jobspy-review)（MIT）不登录抓公开岗位页，每页 25 条，同一 IP 大约翻到第 10 页被限流 | 最近一次正式发版在 2025 年 7 月，有 47 个未关问题；大陆网络能否直连没查到 | 已查实，待实测 |
| LinkedIn 人脉 | 不建议自动化 | 没有面向个人的官方接口 | 帮助中心有专门的"禁用软件和扩展"页面（正文被 robots 拦截，没读到）；hiQ 案 2022 年和解，hiQ 承认违反条款并接受永久禁令 | 二手 |
| 外企招聘系统 | 能 | Greenhouse 官方文档写明岗位数据公开、GET 不需要鉴权：`GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs`。Lever、Ashby 有同类公开接口 | 没写限流；要先知道每家公司的 board 标识 | Greenhouse 已查实，其余二手 |
| 牛客 | 能 | [newcoder-mcp-server](https://github.com/DevEverything01/newcoder-mcp-server)：Playwright 加反检测插件，能搜岗位、按公司看面经，不需要登录 | 0 个星，许可证声明不一致；作者提示遵守牛客条款、不要高频 | 已查实，待实测 |
| 企业邮箱查找 | 能 | Hunter、Apollo、Snov 这类商业工具有浏览器插件 | 对国内公司的覆盖没查 | 未细查 |

出处除表内链接外：[hiQ 案梳理](https://dataresearchtools.com/hiq-linkedin-ruling-scrapers-2026/)、[Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html)

关于"平台是否允许"：用户认为这些平台和比赛方都允许。能核对到的事实是规则原文要求按第三方条款使用数据，以及这些平台都没有面向个人的官方读取接口。没有查到任何一方的书面许可，也没有逐条读到各平台用户协议的原文。结论见 `PLAN.md` 第 4 节：功能照做，但不进公开演示和提交材料。

### 4.2 2026-10-11 实测和补查

| 来源 | 结论 | 状态 |
|---|---|---|
| 字节招聘官网 | 公开页面能正常浏览。列表接口和详情接口都带页面自己算出的签名，不能直接调；用浏览器加载列表页，接口返回里就有每个岗位完整的职责和要求。搜 Agent 加日常实习加研发共 262 个 | 已实测 |
| 小红书招聘官网 | 公开页面能正常浏览，详情页有完整的"工作职责"和"任职资格"。2027 届校招研发方向搜 Agent 共 89 个。页面先渲染标题、正文后到，抓取时要等正文出现 | 已实测 |
| 腾讯招聘 | 社招站有公开的 JSON 查询接口（`careers.tencent.com/tencentcareer/api/post/Query`），不用签名，搜 Agent 有 223 个，字段含岗位名、事业群、地点、职责、更新时间、链接。校招站 join.qq.com 在内置浏览器里渲染不出内容，M3 用 Playwright 再试 | 已实测 |
| 阿里 | 集团校招站和阿里云校招站搜 Agent，"26 届应届生"和"项目制实习生"都显示"暂无在招职位"；招聘按业务分散在十几个子站 | 已实测 |
| xiaohongshu-mcp | 13 个工具：搜索（可按排序、笔记类型、发布时间、范围、地点筛选）、笔记详情（含评论和回复）、用户主页（含他发过的笔记）、首页推荐，另有评论、点赞、收藏、发布等写操作。有 Windows 版，首次运行会下载约 150MB 的无头浏览器。同一账号在别处网页登录会把会话挤掉。约 1.6 万星，Apache-2.0 | 已查实（README） |
| boss-cli | 搜索可按城市、薪资、经验、学历、行业、公司规模、融资阶段、职位类型筛选，可翻页；详情可输出 JSON；登录态从浏览器读取或扫码；自带随机延迟、限流后指数退避。另有打招呼、批量打招呼命令（我们不用） | 已查实（README） |
| Tavily | 用户决定不用 | 决定 |
| xiaohongshu-mcp 实际运行（10-11） | 用作者的 Docker 镜像 v2.5.5（Docker Hub 很慢，用作者在阿里云的镜像拉下来，解压后 1.61GB），口令保护、只绑本机端口。用户手机扫码登录成功，登录态存在 Docker 卷里。实际有 18 个工具（比 README 多了读通知、读自己主页等），北极星只放行 6 个只读的 | 已实测 |
| 小红书调用速度 | 每次搜索或打开笔记约 48 秒（工具真的在开浏览器），一小时约七八十次调用。"检查登录状态"工具在 30 秒处超时报错，但不影响登录和搜索 | 已实测 |
| 小红书搜索效果 | 搜"字节 Agent 实习"、只看一周内最新：15 条，含面经、实习邀约、内推帖；打开一条内推帖拿到正文、发布时间、IP 属地和前 10 条评论 | 已实测 |
| 小红书内容的写法 | 内推帖为了躲广告审核，品牌名换成谐音或 emoji：抖音写成"🫘音"，头条写成"tou条"，番茄小说写成"🍅小说"，广告写成"犷告"。抽取和同名实体合并要专门处理 | 已实测，M4 处理 |
| 腾讯招聘详情接口 | `ByPostId` 返回任职要求（Requirement）、部门介绍等，不用签名 | 已实测 |
| 浏览器采集（M3） | Playwright 用 `channel="msedge"` 直接驱动系统自带的 Edge，不用另下载浏览器。字节列表页等"页面加载完成"会超时（埋点一直在加载），只等到接口返回就够 | 已实测 |
| 小红书官网详情页 | 先渲染标题和占位的"-"，正文晚到；第一次抓到 20 条里只有 1 条有正文。改成等"任职资格"后面真有内容再取，之后全部正常 | 已实测，已修 |
| 小红书采集（M3 三轮） | 平均每次调用 36 秒；搜索偶尔报"context deadline exceeded"（13 次里 1 次），单次失败不影响整轮。一周内最新的搜索结果里，按"提到大厂或实习、内推、面经"的规则，约三分之一不值得打开 | 已实测 |
| 小红书搬运帖 | 同一条米哈游补招信息一天内被 3 个账号几乎原样发出，正文字符三元组相似度 > 0.6；评论区留"44➕关"这类引流。去重和可信度要处理 | 已实测，M4 处理 |
| boss-cli 实装 | v0.3.5，`uv tool install kabi-boss-cli` 装成独立命令。输出被管道接走时，Windows 默认 GBK 编码会在帮助文字的 emoji 上崩，要设 `PYTHONUTF8=1`。扫码登录在终端里画二维码，过一会儿过期要重来；从 Edge 读登录态在 Chromium 127 以后被加密拦住（README 说明），所以用扫码 | 已实测 |
| 抽取准确率（开发期模型） | 真实岗位 50 条逐字段微平均 F1 0.99，合成岗位 60 条 0.99，每条约 9.5 秒（P95 17 秒）。小红书笔记 24 条：提示词 v1 0.96，错在把写字楼"阿里双子塔"当成阿里、把"bat"展开成三家公司；v2 加了规则后开发集 1.00，留出集见 `eval/reports/M3.md` | 已实测 |

## 5. 主机

| 项 | 结论 | 状态 |
|---|---|---|
| AutoDL 开放端口 | 实例没有独立公网 IP，只把 6006 和 6008 映射到公网；开放端口需要企业认证，个人用户只能用 SSH 隧道代理到自己电脑 | 已查实 |
| AutoDL 结论 | 个人账号给不出公开演示链接，不适合做常驻网站。它是按小时租 GPU 训练用的，这个项目也用不到 GPU | 判断 |
| 阿里云香港轻量 | 免备案。通用型：2 核 2 GB 56 元/月，2 核 4 GB 112 元/月，4 核 8 GB 224 元/月。国际型便宜三成，但线路不针对大陆优化，页面建议服务大陆用户选通用型 | 已查实（开发者社区文章，以购买页为准） |
| 阿里云学生券 | 学生认证后每人每年一张 300 元无门槛券，有效期一年，适用于公共云产品；是否含轻量和香港地域没写 | 已查实，适用范围待确认 |
| GitHub 学生包 | DigitalOcean 的 $200 额度已在 2026-07-31 结束；Azure 学生 $100 还在 | 二手 |
| Nebius CPU 主机 | Intel Ice Lake 起步 $0.06/小时（2 vCPU、8 GB 起），AMD Genoa 起步 $0.13/小时（4 vCPU、16 GB 起）；公网 IP 免费 | 已查实 |
| Nebius 折算 | 按起步价，前者每月约 $43，后者约 $94；NemoClaw 最低要 4 vCPU | 估算 |
| Token Factory 额度能否付主机费 | 没查到 | 待确认 |
| 大陆和香港网络能否直连 Token Factory | 没查到 | 待实测 |

出处：[AutoDL 开放端口文档](https://www.autodl.com/docs/port/)、[香港轻量价格](https://developer.aliyun.com/article/1672279)、[阿里云学生优惠](https://developer.aliyun.com/article/1722327)、[学生包现状](https://capitalandcompute.net/blog/github-student-developer-pack-2026/)、[Nebius AI Cloud 价格](https://nebius.com/prices)

## 6. 微信

| 项 | 结论 | 状态 |
|---|---|---|
| 通道 | Hermes 的微信渠道用腾讯 iLink Bot（微信 ClawBot），不是操控个人号。扫码生成独立的 bot 身份，HTTP 长轮询，不需要公网回调地址 | 已查实 |
| 是否官方 | 多个第三方资料称这是腾讯官方开放的个人号 Bot 通道，以 `@tencent-weixin/openclaw-weixin` 包发布。没有读到腾讯自己的文档 | 二手 |
| 主动推送 | 不能凭空主动发。每条发出的消息要带 `context_token`，它来自用户最近一条来信。Hermes 文档说定时投递依赖这个令牌，令牌过期或刚配对还没来信时会失败 | 已查实 |
| 令牌时效 | 没有任何资料给出时长 | 待实测 |
| 其他限制 | 基本只支持私聊，进不了普通群；登录态会过期，要重新扫码；同一令牌只能一个网关轮询；默认谁都能给 bot 发消息，要设白名单 | 已查实 |
| 备用推送 | WxPusher：自称永久免费，HTTP 接口，有免建应用的极简推送，可推到手机 App 或微信 ClawBot。条数限制没查到 | 已查实 |

出处：[Hermes 微信渠道](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/weixin)、[iLink 协议说明](https://www.wechatbot.dev/zh/protocol)、[协议拆解](https://github.com/x1ah/wechat-ilink-demo)、[WxPusher](https://wxpusher.zjiecode.com/llms.txt)

## 7. 许可证和仓库

| 项 | 结论 | 状态 |
|---|---|---|
| 依赖的许可证 | Hermes Agent 是 MIT；NemoClaw、xiaohongshu-mcp、boss-cli 是 Apache-2.0；JobSpy 是 MIT | 已查实 |
| 不能用的 | MediaCrawler 的声明禁止商用，不要把它的代码放进仓库 | 已查实 |
| 项目许可证 | 选 Apache-2.0，理由见 `PLAN.md` 第 2 节 | 决定 |
| 买 star | GitHub 使用政策禁止"虚假互动"和"刷排名，如自动加星"，处置包括停用账号、终止账号、删除内容。2024 年底有研究统计出约 450 万个疑似假星 | 已查实 |

出处：[NemoClaw 仓库](https://github.com/NVIDIA/NemoClaw)、[GitHub 使用政策](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies)、[假星研究](https://arxiv.org/abs/2412.13459)

## 8. 仍需实测的清单

按顺序做。第 0 条在 M0 当天做，第 1 到 5 条最晚在 M2 结束前做完，决定走主方案还是备选方案。

0. ~~本机运行 `python scripts/smoke_llm.py`，测开发用的 key。~~ 2026-10-10 已做，结论见第 2.1 节。
1. 领两份 $25 额度，把 `.env` 指向 Token Factory 再跑一遍同一个脚本，并调 `/v1/models` 确认四个 Nemotron、向量、重排模型都在。
2. Super 开推理调工具：10 次单工具、10 次多工具，记录成功率和每次 token 数。确认 `chat_template_kwargs` 是否生效。
3. Lightning 和 Super 各抽 10 条中文岗位文本，用 `json_schema` 输出，人工对答案。
4. 本机装 Hermes，用 `nebius` 提供方跑一轮带工具的对话。
5. 本机网络直连 Token Factory 是否通畅，不通的话开发期怎么走。2026-10-10 已确认网络层能连通（本机请求有响应，不用代理），带 key 的真实调用等 Nebius 账号。
6. 逐家试大厂官网接口和一个 Greenhouse 公开接口，从国内和香港 IP 各试一次。
7. ~~本机试 xiaohongshu-mcp 和 boss-cli 各搜一次"实习"。~~ 小红书 10-11 已做（第 4.2 节）；boss-cli 已装好，等用户扫码登录。
8. 微信令牌时效：给 bot 发一条消息，隔 12 小时和 24 小时各试一次定时投递。
9. 香港轻量上限时半天装 NemoClaw，接 Token Factory，接自写 MCP。
10. 控制台查默认限流；领学生券时看是否适用于轻量和香港。

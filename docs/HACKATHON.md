# 比赛要求

Nebius × NVIDIA Global AI Hackathon，规则原文：https://nebiusglobalaihackathon.devpost.com/rules

2026-10-10 对照规则原文核对过，核对记录见 `FINDINGS.md` 第 1 节。

## 时间

- 提交截止：北京时间 2026-10-31 01:00（太平洋时间 10-30 10:00）
- 计划提交：北京时间 10-29
- 评审期：2026-12-01 到 12-15，演示须在此期间保持免费可用
- 公布结果：2027-01-11 前后

## 硬性要求

- 运行在 Nebius 上：运行时调用 Token Factory 推理接口，或用 Nebius 算力部署（Serverless Jobs、Serverless Endpoints、DevPods）。二者满足其一即可，主机不必在 Nebius
- 至少使用一个 NVIDIA 开源模型
- 投递赛道：Personal AI（常驻、私有、持久记忆、可复用技能、数据由用户掌控）
- 赛道建议工具：NVIDIA NemoClaw、OpenShell、Hermes Agent、Nebius Serverless。是建议，不是硬性要求
- 第三方 SDK、接口和数据须按其条款和许可使用
- 项目须在提交期（8-26 之后）内新建，旧项目须有重大更新并书面说明
- 提交材料用英文

## 提交物

- 能运行的项目，功能和视频、描述一致
- 演示链接，需要登录时在测试说明里给账号（账号不要写进公开仓库）
- 英文项目描述
- 所选赛道
- 3 分钟以内的公开 YouTube 视频，展示项目运行以及 Token Factory 和 NVIDIA 模型的用法；不能出现未授权的第三方商标和有版权的音乐
- 公开代码仓库，开源许可证在仓库 About 区可见（根目录放 LICENSE 文件）
- 英文 README：安装运行说明，写明 NVIDIA 模型怎么用、Token Factory 起了什么作用、还用了哪些 Nebius 服务
- 对 Token Factory、AI Cloud、NVIDIA 工具的使用反馈

## 评分

第一轮是通过制：切题，并且真正用了要求的接口。

第二轮四项等权重：技术实现、设计（完整的产品体验，不是概念验证）、潜在影响（为真实人群解决真实问题）、想法质量（对平台有创意、不显而易见的用法）。

## 奖项

- 总奖：$20,000、$10,000、$6,000
- 赛道奖：每个赛道一台 NVIDIA Jetson Orin Nano
- Best Use of Tavily：$3,000，要求运行时真实调用 Tavily API
- 最有价值反馈：$100
- 一个项目可拿一项总奖或赛道奖，外加一项附加奖

## 资源

- Token Factory 额度 $25：在 https://nebius.com/promo-code?utm_promo_event_code=2026-devpost-global-ai-hack&utm_promo_code_type=Token_Factory&utm_promo_activation_code=NEBIUS-DEVPOST-GLOBAL26 填表，激活码 `NEBIUS-DEVPOST-GLOBAL26`
- Token Factory 额度再 $25、Tavily $25、LangSmith $100：加入 Nebius Builder Program，https://dev.nebius.com/builders
- Discord：https://discord.gg/eXYTGhgnhK

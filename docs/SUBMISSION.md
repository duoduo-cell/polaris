# 比赛提交材料（Devpost）

Nebius × NVIDIA Global AI Hackathon，Personal AI 赛道。截止时间：北京时间 2026-10-31 01:00（太平洋时间 10-30 10:00），计划 10-29 提交。要求原文的整理见 [HACKATHON.md](HACKATHON.md)。**提交前，作者本人打开官网规则页再核对一遍。** 规则页有 Cloudflare 人机验证，只能本人在浏览器里过。

提交由作者本人在 Devpost 上操作：登录、勾选同意规则、点提交，都不能代办。下面的英文字段可以直接粘贴。

## 对照要求

| 要求 | 现状 |
|---|---|
| 提交期（8-26 之后）内新建的项目 | 是。第一个提交在 2026-10-10 |
| 运行在 Nebius 上（Token Factory 或 Nebius 算力） | **不满足**。用的是百炼 Qwen，10-25 作者决定，见 [SHOWCASE](SHOWCASE.md) 第 4 节 |
| 至少用一个 NVIDIA 开源模型 | **不满足**，原因同上 |
| 能运行的项目，功能和视频、描述一致 | 本机可以运行；公开演示待部署（[#16](https://github.com/duoduo-cell/polaris/issues/16)） |
| 演示链接，测试账号写在测试说明里 | 待 #16 完成；口令只填进 Devpost，不进仓库 |
| 英文项目描述 | 见下文 |
| 赛道 | Personal AI |
| 3 分钟以内的公开 YouTube 视频 | 待录（[#20](https://github.com/duoduo-cell/polaris/issues/20)） |
| 公开仓库，About 区能看到开源许可证 | 已完成：Apache-2.0，仓库已公开 |
| 英文 README | 已完成（[README.en.md](../README.en.md)），写明没有用 Token Factory 和 NVIDIA 模型 |
| 对 Token Factory、AI Cloud、NVIDIA 工具的使用反馈 | 见下文“Feedback” |

## Devpost 字段

### Project name

Polaris

### Tagline

An evidence-grounded job-search agent for LLM and Agent internship seekers in China: every claim opens to its source.

### Track

Personal AI

### About the project

**Inspiration**

Searching for an LLM or Agent internship in China means reading job posts on career sites, interview write-ups on Xiaohongshu and Maimai, and comparing each one with constraints that rarely match: how many days a week you can come in, how many months you can stay, whether a paper is required. General chat models answer these questions confidently and often invent the job, the requirement or the interview question. I built Polaris to do this for my own search, with one rule: every claim must open to the text it came from.

**What it does**

- Finds jobs under your constraints and marks each hard conflict (education, papers, attendance, length). A constraint the posting does not state is shown as "to confirm", never as a match.
- Pulls real interview questions from real posts, with source and date. If you ask for real questions and none were searched, orchestration sends the agent back to search before it may answer.
- Follow-up cards watch one goal over weeks: a focus job removed or changed, new jobs in scope, new interview posts, outcomes of people like you. Changes are ranked urgent / digest / quiet, and a daily situation report is written from facts computed by rules first.
- An HR-view review: which hard requirements block you, which common skills your card lacks, and which details an interviewer will ask about; compared with peers' public self-descriptions, saying so when the sample is small.
- An application funnel and follow-up drafts that are never sent automatically; rejection reasons feed later gap analysis.
- Bi-temporal memory of your constraints and experience that you can read, correct and delete.

**How it fits Personal AI**

- *Persistent*: a daemon collects and checks follow-up cards on a schedule; long tasks survive page reloads through a persisted task queue.
- *Private and user-owned*: collected text, chats and memory live in the user's own PostgreSQL; logged-in platforms are read only on the user's machine with their own account.
- *Durable memory*: bi-temporal facts (true when / learned when), corrections asked before they overwrite.
- *Reusable skills*: 18 typed tools, the read-only ones also exposed over MCP for other agents.

**How we built it**

Python 3.12, FastAPI and LangGraph 1.2 / LangChain 1.4 for the agent, with middleware for approval of side-effecting tools, call limits, retries and evidence gates. Retrieval runs inside PostgreSQL 17: pgvector plus ParadeDB pg_search (BM25 with Chinese tokenization), with an optional Neo4j graph, fused by weighted RRF and reranked. Next.js 16 and React 19 for the web app. Every model call is recorded with tokens, latency and cost. The models are Qwen through Alibaba Cloud Bailian. CI runs ruff, 794 backend tests against a real Postgres, type checks, lint, unit tests and a production build on every pull request.

**Challenges we ran into**

Running the whole task for real exposed problems no offline test caught: post-filtering lost almost all recall (1 → 32 candidates after filtering first), a 6,000-character tool budget made the model page through results, a local network tunnel stalled model requests (103 s → 2 s with a narrow direct connection), and the model claimed an interview question it never searched for, which a prompt rule did not stop but an orchestration gate did.

**Accomplishments that we're proud of**

The agent says "not found" instead of inventing, and the page marks any citation that is not backed by a tool result. The synthetic demo is fully separate from personal data, with its own database, API and web app and a daily model-call cap.

**What we learned**

Module tests passing is not the product working. The useful failures only appeared when the whole loop ran on real data, and the durable fixes were in orchestration and data flow, not in longer prompts.

**What's next**

Reading a resume to build the evidence chain, moving the public-site collection and alerts to a small server so they run while the laptop is off, and email and calendar connectors that propose application updates for approval.

### Built with

python, fastapi, langgraph, langchain, postgresql, pgvector, paradedb, neo4j, nextjs, react, typescript, mcp, playwright, docker, github-actions, qwen, alibaba-cloud-bailian

### Try it out

- GitHub: https://github.com/duoduo-cell/polaris
- Project page: https://duoduo-cell.github.io/polaris/
- Demo: (#16 部署后填)

### Video

(#20 上传后填 YouTube 链接)

### Testing instructions（只填进 Devpost，不进仓库）

```text
Open <demo URL>. The demo holds handwritten synthetic data only: invented jobs, one interview post and a fictional profile.
Access token: <只在 Devpost 里填>
Try: "找上海 Agent 日常实习，每周最多 4 天，展开第一个岗位和我的情况对比"
Then open 跟进 (follow-up) and press 推进一步 four times, then 现在写报告.
The demo allows a limited number of model calls per day and per visitor.
```

### Feedback on Token Factory, AI Cloud and NVIDIA tools

I planned to run Polaris on Token Factory with a Nemotron model and did not, for one reason: activating billing requires a bank card, and the AI Cloud docs ask for a $25 first payment before promo credits can be applied. As a student in China without an international card, that step blocked me, and I chose not to pay to enter. Suggestion: let hackathon participants redeem event credits without a card, with a hard spending cap at the credit amount. Polaris speaks the OpenAI-compatible API, so switching to Token Factory would be a configuration change.

## 提交后

1. 把 Devpost 项目页链接和提交日期写在本文件开头，并在 README 和项目主页加链接。
2. 截一张提交成功的页面，放进 `docs/img/`。
3. 关闭 [#21](https://github.com/duoduo-cell/polaris/issues/21)。

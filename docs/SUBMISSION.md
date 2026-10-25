# 比赛提交材料（Devpost）

Nebius × NVIDIA Global AI Hackathon，Personal AI 赛道。截止时间：北京时间 2026-10-31 01:00（太平洋时间 10-30 10:00），计划 10-29 提交。要求原文的整理见 [HACKATHON.md](HACKATHON.md)。**提交前，作者本人打开官网规则页再核对一遍。** 规则页有 Cloudflare 人机验证，只能本人在浏览器里过。

## 现在的状态（10-26 凌晨）

- **视频**：已公开发布在作者的 YouTube 频道，https://youtu.be/ogbxFrPQ2Is （2:38）。制作源文件和字幕在 [video/](../video/)。
- **Devpost 草稿**：作者已登录并报名。下面的字段已经全部填进草稿，[公开预览页](https://devpost.com/software/polaris-temldk)显示正常：
  - 项目名、一句话介绍、正文（英文）；
  - 14 个技术标签、GitHub 和项目主页两个链接；
  - 视频链接、5 张图（片头卡和 4 张演示截图，带英文说明）；
  - Additional info 的全部问卷，赛道选了 Personal AI，附件是英文版作品集 PDF。
- **只剩作者本人的三处确认，再点提交**（这几处是以作者名义做的声明和同意条款，按规矩不能代点）：
  1. 打开草稿的 [Additional info](https://devpost.com/submit-to/30790-nebius-x-nvidia-global-ai-hackathon/manage/submissions/1263949-polaris/additional-info/edit)，在页面最下方勾选两项声明（“at least the age of majority”“not employees … of the Promotion Entities”），核对一遍其余答案，点 Save & continue。
  2. 在 [Submit](https://devpost.com/submit-to/30790-nebius-x-nvidia-global-ai-hackathon/manage/submissions/1263949-polaris/finalization) 页勾选同意 Official Rules 和 Devpost Terms of Service。
  3. 点 **Submit project**。提交后到截止前仍可修改。

下面是填进去的英文字段原文，留作记录，也方便以后修改。

## 对照要求

| 要求 | 现状 |
|---|---|
| 提交期（8-26 之后）内新建的项目 | 是。第一个提交在 2026-10-10 |
| 运行在 Nebius 上（Token Factory 或 Nebius 算力） | **不满足**。用的是百炼 Qwen，10-25 作者决定，见 [SHOWCASE](SHOWCASE.md) 第 4 节 |
| 至少用一个 NVIDIA 开源模型 | **不满足**，原因同上 |
| 能运行的项目，功能和视频、描述一致 | 本机可以运行，视频展示的是完整流程；线上演示待部署（[#16](https://github.com/duoduo-cell/polaris/issues/16)，要作者实名购买服务器） |
| 演示链接 | 选填项。暂时填的是本机运行合成演示的说明 [docs/DEMO.md](DEMO.md)，#16 完成后换成线上地址 |
| 英文项目描述 | 见下文 |
| 赛道 | Personal AI |
| 3 分钟以内的公开 YouTube 视频 | 已完成：https://youtu.be/ogbxFrPQ2Is ，2:38 |
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

Devpost 正文是 Markdown，下面是填进去的原文（二级标题在 Devpost 上显示为小节）：

````markdown
## Inspiration

Searching for an LLM or Agent internship in China means reading job posts on career sites and interview write-ups on forums, then comparing each one with constraints that rarely match: how many days a week you can come in, how many months you can stay, whether a paper is required. General chat models answer these questions confidently and often invent the job, the requirement or the interview question. I built Polaris for my own search, with one rule: **every claim must open to the text it came from.**

## What it does

- **Finds jobs under your constraints** and marks each hard conflict (education, papers, attendance, length). A constraint the posting does not state is shown as "to confirm", never as a match.
- **Pulls real interview questions from real posts**, with source and date. If you ask for real questions and none were searched, orchestration sends the agent back to search before it may answer. Any id the answer cites that no tool returned is marked unverified.
- **Follow-up cards** watch one goal over weeks: a focus job removed or changed, new jobs in scope, new interview posts, outcomes of people like you. Changes are ranked urgent / digest / quiet, and a daily situation report is written from facts computed by rules first.
- **An HR-view review**: which hard requirements block you, which common skills your card lacks, and what an interviewer will ask about; compared with peers' public self-descriptions, saying so when the sample is small.
- **An application funnel** and follow-up drafts that are never sent automatically; rejection reasons feed later gap analysis.
- **Bi-temporal memory** of your constraints and experience that you can read, correct and delete.

## How it fits Personal AI

- *Persistent*: a daemon collects and checks follow-up cards on a schedule; long tasks survive page reloads through a persisted task queue.
- *Private and user-owned*: collected text, chats and memory live in the user's own PostgreSQL; logged-in platforms are read only on the user's machine, with their own account, at a low rate.
- *Durable memory*: bi-temporal facts (true when / learned when); corrections are confirmed before they overwrite.
- *Reusable skills*: 18 typed tools; the read-only ones are also exposed over MCP for other agents.

## How we built it

Python 3.12, FastAPI and LangGraph 1.2 / LangChain 1.4, with middleware for approval of side-effecting tools, call limits, retries and evidence gates. Retrieval runs inside PostgreSQL 17: pgvector plus ParadeDB pg_search (BM25 with Chinese tokenization), with an optional Neo4j graph, fused by weighted RRF and reranked. Next.js 16 and React 19 for the web app. Every model call is recorded with tokens, latency and cost. CI runs ruff, 794 backend tests against a real Postgres, type checks, lint, unit tests and a production build on every pull request.

## Challenges we ran into

Running the whole task for real exposed problems no offline test caught:

- Post-filtering lost almost all recall; filtering first took candidates from 1 to 32.
- A 6,000-character tool budget made the model page through results; together with the next two fixes, the full task went from 265 s to 77 s.
- A local network tunnel stalled model requests; a narrow direct route took job search from 103 s to 2 s.
- The model claimed an interview question it never searched for; a prompt rule did not stop it, an orchestration gate did.

## Accomplishments that we're proud of

The agent says "not found" instead of inventing, and the page marks any citation that no tool result backs. The synthetic demo is fully separate from personal data, with its own database, API and web app and a daily model-call cap. Development happens in the open: issues, pull requests, CI and releases.

## What we learned

Module tests passing is not the product working. The useful failures only appeared when the whole loop ran on real data, and the durable fixes were in orchestration and data flow, not in longer prompts.

## What's next

Reading a resume to build the evidence chain, moving public-site collection and alerts to a small server so they run while the laptop is off, and email and calendar connectors that propose application updates for approval.

## Honest note on the requirements

Polaris runs on **Qwen through Alibaba Cloud Bailian**. This entry does **not** use Nebius Token Factory or an NVIDIA model: activating Nebius billing needs a bank card and a first payment, which I could not set up as a student in China. The model layer speaks the OpenAI-compatible API, so switching providers is a configuration change, but that path has not been run. The hosted demo is not deployed yet; the video shows the full flow, and the synthetic demo runs locally from the repository (docs/DEMO.md).
````

### Built with

python, fastapi, langchain, langgraph, postgresql, pgvector, neo4j, react, next.js, typescript, docker, playwright, mcp, qwen

### Try it out

- GitHub: https://github.com/duoduo-cell/polaris
- Project page: https://duoduo-cell.github.io/polaris/en.html
- Local demo guide: https://github.com/duoduo-cell/polaris/blob/main/docs/DEMO.md（#16 部署后换成线上地址）

### Video

https://www.youtube.com/watch?v=ogbxFrPQ2Is

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

# Changelog

Notable changes to Polaris. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until the repository went public on 2026-10-25, work landed as direct commits, and the [dev log](docs/DEVLOG.md) records each round. Since then every change goes through an issue and a pull request.

## [Unreleased]

The hosted synthetic demo ([#16](https://github.com/duoduo-cell/polaris/issues/16)) waits on a server bought under the author's identity.

## [0.2.0] - 2026-10-26

Submitted to the Nebius × NVIDIA Global AI Hackathon, Personal AI track ([Devpost](https://devpost.com/software/polaris-temldk)).

### Added

- A 2:38 demo video with English narration and captions, published on [YouTube](https://youtu.be/ogbxFrPQ2Is), with the scripts that rebuild it in `video/` ([#44](https://github.com/duoduo-cell/polaris/pull/44)).
- Project page on GitHub Pages in Chinese and English ([#36](https://github.com/duoduo-cell/polaris/pull/36)), with an OpenAPI reference ([#43](https://github.com/duoduo-cell/polaris/pull/43)).
- One-page portfolio PDFs in Chinese and English ([#41](https://github.com/duoduo-cell/polaris/pull/41)).
- A daily model-call cap and a per-visitor limit on the public demo ([#35](https://github.com/duoduo-cell/polaris/pull/35)).
- README sections on impact, contributors and the API ([#43](https://github.com/duoduo-cell/polaris/pull/43)); the Devpost submission text in `docs/SUBMISSION.md` ([#39](https://github.com/duoduo-cell/polaris/pull/39), [#44](https://github.com/duoduo-cell/polaris/pull/44)).

### Changed

- The synthetic demo uses a fictional company that only demo mode recognises; chat suggestions and the graph page adapt to demo mode; fixture v3 ([#34](https://github.com/duoduo-cell/polaris/pull/34)). Screenshots retaken on it ([#37](https://github.com/duoduo-cell/polaris/pull/37)).
- `AGENTS.md` describes the issue → branch → PR → CI → squash flow ([#32](https://github.com/duoduo-cell/polaris/pull/32)).

### Fixed

- The demo reset now restores the seeded memory and clears visitors' application marks; a stale "3 days a week" fact had outlived every reset ([#33](https://github.com/duoduo-cell/polaris/pull/33)).

### Known issues

- The entry does not meet the hackathon's "runs on Nebius" and "uses an NVIDIA open model" requirements; it runs on Qwen through Alibaba Cloud Bailian, as stated in the README, video and submission.
- Nobody is told when the database goes down ([#5](https://github.com/duoduo-cell/polaris/issues/5)).

## [0.1.0] - 2026-10-25

First public release. The whole loop runs on the author's machine, from collection to a web conversation, and a synthetic demo runs separately.

### Added

- **Collection** (10-11 to 10-22): collectors for the Tencent, ByteDance and Xiaohongshu career sites, Xiaohongshu, Maimai and Boss. The daemon ramps volume up slowly, halves it on warnings, cools down when blocked, and stops at verification prompts. The raw layer records publish time, first and last seen, and SimHash near-duplicate fingerprints.
- **Extraction and alignment** (M3, M4): LLM extraction of jobs, posts, interview questions and peer samples, and cross-source same-job matching with field-level conflict resolution.
- **Retrieval** (M4): chunking, pgvector embeddings, ParadeDB pg_search BM25 with jieba, and an optional Neo4j graph, combined by three-way recall with weighted RRF, rerank and MMR ([report](eval/reports/M4.md)).
- **Memory** (M5): a bi-temporal fact table, updates decided by rules, pending confirmation and suppression, and memory tools ([report](eval/reports/M5.md)).
- **Agent** (M2, M6): a tool layer with argument validation, tracing, record and replay, and an MCP endpoint ([report](eval/reports/M2.md)). On top of it, a LangGraph agent with Postgres checkpoints, summarisation, approval for side-effecting tools and untrusted marking of outside text, plus a daily proactive brief ([injection report](eval/reports/injection.md)).
- **Web app** (M7, 10-11 to 10-12): today, chat with streamed progress and a stop button, memory, jobs, interviews, graph, traces and evals; result cards that open their sources; a phone layout with pairing.
- **Reliability** (10-13 to 10-24): a persisted task queue that honours cancellation; unknown costs kept unknown instead of reported as zero; one Beijing calendar for dates; matching criteria kept through tools and cards; unverified citations shown to the user; evidence-bound reports whose delivery state is separate from generation ([case](docs/design/REPORT_DELIVERY.md)).
- **Applications** (10-24): follow-up message drafts that are never sent, rejection feedback stored on the card, an application funnel page, and a reminder after 7 days without a reply.
- **Follow-up cards** (10-25): long-running watches over a scope with change detection, urgent / digest / quiet ranking and daily situation reports ([design](docs/design/WATCH.md)).
- **Competitiveness** (10-25): an HR-view review and a three-tier comparison with peers' public self-descriptions.
- **Synthetic demo** (10-23 to 10-25): its own database, API and web app with handwritten data, a guarded import, a dataset check before every proxied request, and a four-step follow-up card script with reset ([DEMO.md](docs/DEMO.md)).
- **Launcher** (10-24): `scripts/up.ps1` starts services detached, so they survive the terminal or editor that launched them.
- **Repository** (10-25): CI for ruff, pytest against Postgres, web type check, lint, unit tests and build ([#2](https://github.com/duoduo-cell/polaris/pull/2)); issue forms, PR template, CONTRIBUTING and SECURITY ([#4](https://github.com/duoduo-cell/polaris/pull/4)); Chinese and English README rewritten for first-time readers ([#14](https://github.com/duoduo-cell/polaris/pull/14)).

### Fixed

Found by running the whole job-search task for real on 10-24 (commit [`1d8cf59`](https://github.com/duoduo-cell/polaris/commit/1d8cf59)):

- Job search filtered after retrieval and lost recall. It now filters first: 1 → 32 candidates. Jobs that do not state days per week are kept and marked "to confirm".
- Tool output held only 1–2 jobs per page. List items are slimmer and the cap went from 6,000 to 16,000 characters.
- Bailian requests hung behind a local TUN accelerator. Only the Bailian host now connects directly: 103 s → 2 s.
- The model claimed an interview question it never searched for. `QuestionEvidenceGate` sends it back to search first.

Also:

- With Postgres down, the suite skipped every database test and still exited 0. CI now fails instead ([#2](https://github.com/duoduo-cell/polaris/pull/2)).
- Memory extraction v7 (10-25) stops storing constraints inside a question, such as "at most 3 days a week", as facts about the user.

### Changed

- The project runs on Qwen models through Alibaba Cloud Bailian and does not use Nebius Token Factory or an NVIDIA model ([#6](https://github.com/duoduo-cell/polaris/pull/6)).
- Superseded planning documents moved to `docs/archive/` ([#11](https://github.com/duoduo-cell/polaris/pull/11)), and the author's interview script left the repository ([#9](https://github.com/duoduo-cell/polaris/pull/9)).

### Known issues

- Nobody is told when the database goes down ([#5](https://github.com/duoduo-cell/polaris/issues/5)).
- The demo profile holds a stale weekly-days fact from before memory extraction v7, and the demo reset does not restore memory ([#12](https://github.com/duoduo-cell/polaris/issues/12)).

[Unreleased]: https://github.com/duoduo-cell/polaris/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/duoduo-cell/polaris/releases/tag/v0.2.0
[0.1.0]: https://github.com/duoduo-cell/polaris/releases/tag/v0.1.0

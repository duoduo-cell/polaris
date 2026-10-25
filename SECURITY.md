# Security policy

## Reporting

Report privately through [GitHub private vulnerability reporting](https://github.com/duoduo-cell/polaris/security/advisories/new). Do not open a public issue. If the problem is leaked data, do not repeat the data in the report: give the file, commit or URL where it appears.

Polaris has one maintainer, so responses are best effort, usually within a few days. Fixes land on `main` and in the next release.

## Supported versions

Only `main` and the latest release get fixes.

## In scope

- Secrets in code or history: model keys, tokens, cookies, login state.
- Collected platform text or personal data appearing anywhere public: the repository, the demo, the project page, release assets.
- The public demo instance: escaping read-only mode, reading data other than the synthetic set, reaching the personal instance, getting around its daily model-call cap or per-visitor limit.
- The local API: requests that skip the owner token or pairing code, the ingest token, or the MCP token.
- Prompt injection through collected content that makes the agent take an action needing approval without it.

## Out of scope

- Rate limits or terms of third-party platforms.
- Attacks that need control of the owner's machine.
- Social engineering.

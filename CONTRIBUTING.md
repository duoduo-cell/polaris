# Contributing to Polaris

Polaris is maintained by one person and used daily for a real internship search. Every change, including the maintainer's own, goes through the same flow: issue, branch, pull request, CI, merge.

## Set up

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 24, and Docker.

```bash
uv sync                      # backend and dev tools
cd web && npm ci && cd ..    # web app
cp .env.example .env         # then fill in your own model endpoint, key and tokens
```

`.env` holds your keys and never goes into a commit. Tests do not read it for model access: `tests/conftest.py` points every model call at fake hosts.

### Database

Polaris needs Postgres 17 with pgvector and ParadeDB pg_search.

- **Full local instance**: `bash scripts/build_db_image.sh` downloads the pg_search package and builds the image, then `docker compose up -d db`.
- **Tests only**: run the same image CI uses.

  ```bash
  docker run -d --name polaris-test-db -e POSTGRES_USER=polaris -e POSTGRES_PASSWORD=polaris -p 5432:5432 paradedb/paradedb:v0.22.6-pg17
  ```

The suite creates and migrates its own `polaris_test` database. Set `TEST_DATABASE_URL` to point elsewhere.

## Checks

These are the commands CI runs (`.github/workflows/ci.yml`). Run them before you push.

```bash
uv run ruff check .
uv run pytest
cd web
npx tsc --noEmit
npm run lint
node --test tests/*.mjs
npm run build
```

Locally, database tests are skipped when Postgres is unreachable. In CI the same situation fails the run.

## Workflow

1. **Issue first.** Describe the task or failure with one concrete case. Small typo fixes can skip this.
2. **Branch** from `main`, named by kind: `feat/…`, `fix/…`, `docs/…`, `ci/…`, `refactor/…`.
3. **One change per PR.** Fill in the template: the linked issue, why, what changes, evidence (before and after), and what is not covered.
4. **CI must pass** before merge. PRs are squash-merged.
5. **Releases** are tagged from `main` with notes grouped by feature, fix and docs.

Commit messages are in English: an imperative subject of at most 72 characters, then a body that explains why. Reference the issue (`Closes #12`).

## Code

- Python style is enforced by ruff (`pyproject.toml`). Comments explain why, not what.
- Every model call goes through `src/polaris/llm/client.py`, which records input, output, tokens, latency and cost. Do not call a provider SDK directly.
- Do not invent job facts or personal details. Fields the extractor cannot find stay empty.
- Agent answers cite their evidence by id: `J-` jobs, `P-` posts, `Q-` interview questions, `R-` freshly fetched raw text, `M-` memory. A change that lets the agent make a new kind of claim must add its evidence path and a test for it.

## Data boundaries

These rules are not negotiable, and reviewers check them on every PR.

- No API keys, cookies, login state or personal data in code, fixtures, screenshots, logs or docs.
- Raw text collected from Xiaohongshu, Maimai, Boss Zhipin, Nowcoder or LinkedIn stays on the machine that collected it. It never enters the repository, the public demo or submission material. Tests and the demo use synthetic data only.
- Collectors use the owner's own account and stay read-only and low-frequency. They do not spoof fingerprints or user agents, rotate accounts or proxies, break signatures or bypass captchas. On a rate limit or a verification prompt they stop and wait for the owner.
- No automated messaging, greeting or applying. People's profiles on Maimai and LinkedIn are not collected.
- No bought stars or fake engagement.

## Security

Report vulnerabilities and leaked data privately, as described in [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contribution is licensed under [Apache-2.0](LICENSE).

# Agent Load Balancer

Agent Load Balancer (ALB) is a local multi-provider routing layer for agent runtimes.

It is derived from the codex-lb architecture, but this workspace is a separate unpublished project. It keeps the Codex proxy path and adds provider-aware foundations for Gemini and Antigravity-compatible runtimes.

## Current Scope

- Codex account routing, quotas, request logs, sticky sessions, drain strategies, API keys, and dashboard flows inherited from codex-lb.
- Gemini provider accounts, encrypted API keys, model catalog entries, native `generateContent` / `streamGenerateContent`, OpenAI-compatible chat, tool/function-call history, thought signatures, streaming decode, cancellation, and usage settlement.
- Antigravity provider accounts, CLI profile execution through `agy --print`, API-key runtime support, model catalog entries, and a chat-compatible preview endpoint.
- Provider registry, provider account APIs, provider routing APIs, provider runtime APIs, separate provider dashboard surfaces, and combined overview data.
- Standalone ALB identity in package metadata, app title, dashboard labels, default data directory, Docker defaults, Helm chart, and release workflows.

## Verified Locally

- Real Gemini runtime smoke with a live `GEMINI_API_KEY` returned `ALB_GEMINI_OK` and settled provider usage.
- Real Antigravity Interactions API smoke with a live key returned `ALB_AGY_API_OK` and settled provider usage.
- Real Antigravity CLI harness smoke through an isolated ALB server returned `ALB_API_OK` from `agy --print`.
- Focused Codex/provider regression suite proves `gpt-*` chat completions remain on the Codex proxy path instead of calling Gemini or Antigravity runtimes.
- OpenAI SDK compatibility e2e tests passed for Codex-compatible public routes.
- Local Python packaging smoke produced ALB wheel/sdist artifacts with `uv build`.
- Local Helm chart rendering tests passed with the repo-local Helm binary.
- Dockerfile release-surface tests passed, including distroless dependency parity with the normal runtime image.
- OpenSpec specs and the active `add-multi-agent-providers` change validated with `@fission-ai/openspec`.

## Not Fully Verified Yet

- Real Codex account end-to-end smoke in this ALB copy.
- Docker image build under the future public ALB repository.

## Quick Start

```powershell
cd E:\Coding\alb
uv sync --all-extras --dev
.\.venv\Scripts\alb-db.exe upgrade head
.\.venv\Scripts\alb.exe --host 127.0.0.1 --port 2455
```

Open the dashboard:

```text
http://127.0.0.1:2455
```

Legacy command aliases are still present:

```powershell
.\.venv\Scripts\agent-load-balancer.exe --host 127.0.0.1 --port 2455
.\.venv\Scripts\agent-load-balancer-db.exe check
.\.venv\Scripts\codex-lb.exe --host 127.0.0.1 --port 2455
.\.venv\Scripts\codex-lb-db.exe check
```

## Configuration

ALB reads `ALB_*` environment variables first. Legacy `CODEX_LB_*` names remain supported for migration and compatibility.

Important defaults:

```env
ALB_DATABASE_URL=sqlite+aiosqlite:///~/.agent-load-balancer/store.db
ALB_DATABASE_MIGRATE_ON_STARTUP=true
ALB_UPSTREAM_BASE_URL=https://chatgpt.com/backend-api
```

Set `ALB_DATA_DIR` if you want a fully explicit local data directory. `CODEX_LB_DATA_DIR` still works as a legacy fallback.

## Development Checks

```powershell
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ty.exe check
.\.venv\Scripts\pytest.exe tests\unit\test_runtime_version.py tests\integration\test_runtime_api.py tests\unit\test_settings_home_dir.py tests\unit\test_settings_connect_address.py
```

Frontend checks:

```powershell
cd frontend
bun test src/components/layout/status-bar.test.tsx
```

## Provider Smoke Plan

Codex:

1. Import or connect a Codex/OpenAI account in the dashboard.
2. Create an API key.
3. Send a minimal `/v1/responses` request through ALB.
4. Confirm request log, account quota settlement, and dashboard overview update.

Scripted smoke:

```powershell
.\.venv\Scripts\python.exe scripts\verify_alb_codex_e2e.py --base-url http://127.0.0.1:2455 --auth-json C:\path\to\auth.json
```

Gemini:

1. Add a Gemini provider account with an API key.
2. Send a minimal chat/generate request to the Gemini runtime path.
3. Confirm streaming, usage settlement, and request logs.

Antigravity:

1. Verify `agy` is installed with `agy --help`.
2. Add an Antigravity CLI profile or API-key account.
3. Run a minimal dashboard harness print request through the CLI profile.
4. Confirm recovered stdout, child-process cleanup, request logs, and one usage settlement.

## Attribution

ALB started from codex-lb's proven Codex load-balancing architecture and is being developed as a separate multi-agent project.

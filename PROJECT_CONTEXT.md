# ALB - Agent Load Balancer

ALB is a universal model gateway for agent clients, starting with Codex Desktop. The goal is to let one agent shell use many model providers, auth modes, accounts, quotas, and routing policies through one stable Codex/OpenAI-compatible surface.

This repository is the fresh home for ALB. It replaces the messy experimental worktree used during early exploration. Do not assume this repo already contains the old implementation until code is explicitly migrated here.

## Current Goal

Build a clean, production-shaped Agent Load Balancer that can:

- Keep Codex Desktop working through the existing Codex plan / codex-lb style path.
- Add provider adapters for OpenAI API, Gemini API, Antigravity OAuth, Mistral API, NVIDIA NIM API, OpenRouter, Groq, and opencode zen.
- Expose provider models in a way that feels like the Codex model picker, for example `openai/gpt-5.5`, `gemini/gemini-2.5-pro`, `mistral/mistral-large-latest`, `groq/...`, `openrouter/...`.
- Support text, streaming, tool calling, image input/output, structured output, reasoning metadata, and provider-specific extras where the provider actually supports them.
- Be honest about capability gaps. If a provider lacks native tool calling or images, ALB must mark that clearly and optionally emulate only when acceptable.
- Provide dashboards for per-provider health, quotas, drains, account status, model capabilities, and combined global usage.
- Preserve the existing quota-drain/reset-drain/account-health ideas from codex-lb while making them provider-agnostic.

## Important Context

There are three related things, and they must not be confused:

1. `codexlb`
   - Existing upstream/community project and live service on the user's machine.
   - The user's live Codex Desktop depends on it.
   - Do not touch, kill, rewrite, or push ALB experiments into the live codexlb checkout.

2. Previous ALB experimental worktree
   - Path used earlier: `C:\Users\ByteMyBootloader\Documents\agent-load-balancer`.
   - It was a fork/experimental area where Codex support was preserved and Gemini/Antigravity work was explored.
   - That worktree may contain useful code, tests, OpenSpec artifacts, dashboard work, and provider-runtime experiments.
   - Migrate from it carefully later, but do not blindly copy junk, generated files, pycache, venvs, or stale test artifacts.

3. This repository
   - New canonical project path: `E:\Coding\alb`.
   - GitHub repository: `https://github.com/Rtx09x/ALB`.
   - This repo should become the clean public ALB project.

## Product Shape

ALB should be an agent model gateway, not just a normal LLM proxy.

High-level flow:

```text
Codex Desktop or agent client
  -> ALB Codex/OpenAI-compatible gateway
    -> Provider registry
      -> Native provider adapters
      -> Generic OpenAI-compatible adapter
      -> Optional LiteLLM bridge adapter
      -> Optional OpenRouter bridge adapter
```

The Codex-facing API must stay stable. Providers behind it can be native, OpenAI-compatible, OAuth-backed, local, or bridge-based.

## Why Not Just LiteLLM

LiteLLM is useful, but it should not be ALB's core.

LiteLLM is good for:

- Many provider APIs behind one interface.
- Basic routing, fallback, retries, budgets, RPM/TPM policies.
- Fast support for OpenAI-compatible and common third-party APIs.

ALB must own things LiteLLM does not fully solve for this project:

- Codex Desktop compatibility.
- Codex plan / codex-lb auth path.
- Account-level quota drain and reset drain behavior.
- Provider dashboards and capability truth.
- Agent/client compatibility shims.
- Tool-call and streaming event normalization for Codex-like clients.
- Provider/account health semantics that match agent workflows.

Best use of LiteLLM:

```text
ALB -> LiteLLM adapter -> broad provider ecosystem
```

Not:

```text
Codex Desktop -> LiteLLM directly
```

## First Provider Set

Implement these first:

- Codex plan / existing codex-lb style provider
- OpenAI API
- Gemini API
- Antigravity OAuth
- Mistral API
- NVIDIA NIM API
- OpenRouter
- Groq
- opencode zen

Provider support should be capability-driven, not hardcoded into random routes.

## Core Architecture

### 1. Codex-Compatible Gateway

This is the stable front door. Codex Desktop should talk to ALB as if it were a compatible OpenAI/Codex backend.

Responsibilities:

- Accept Codex/OpenAI-style requests.
- Preserve streaming behavior expected by Codex Desktop.
- Normalize responses from providers into the expected shape.
- Normalize errors into useful Codex-compatible envelopes.
- Preserve model selection.
- Preserve tool-call behavior where possible.
- Preserve image/multimodal request fields where possible.

### 2. Provider Registry

Every provider adapter registers itself with metadata:

```ts
type ProviderDescriptor = {
  id: string;
  displayName: string;
  authModes: AuthMode[];
  supportsModelListing: boolean;
  defaultModels: ModelDescriptor[];
  healthCheck: HealthCheckDescriptor;
};
```

Provider IDs should be stable:

```text
codex-plan
openai
gemini
antigravity
mistral
nvidia
openrouter
groq
opencode-zen
openai-compatible
litellm
```

### 3. Model Catalog

ALB needs a unified model catalog. Models should have canonical IDs:

```text
codex-plan/gpt-5.5
openai/gpt-5.5
openai/gpt-5.4-mini
gemini/gemini-2.5-pro
gemini/gemini-2.5-flash
antigravity/<model>
mistral/mistral-large-latest
nvidia/<nim-model>
openrouter/<provider>/<model>
groq/<model>
opencode-zen/<model>
```

Each model entry must include capabilities:

```ts
type ModelCapabilities = {
  text: boolean;
  streaming: boolean;
  tools: "native" | "mapped" | "emulated" | "none";
  imageInput: boolean;
  imageOutput: boolean;
  structuredOutput: "native" | "mapped" | "emulated" | "none";
  reasoning: "native" | "mapped" | "none";
  maxContextTokens?: number;
  maxOutputTokens?: number;
};
```

The dashboard and API must show these honestly.

### 4. Provider Adapter Interface

The internal provider interface should be strict and small:

```ts
interface ProviderAdapter {
  id: string;
  listModels(ctx: ProviderContext): Promise<ModelDescriptor[]>;
  healthCheck(ctx: ProviderContext): Promise<ProviderHealth>;
  createResponse(req: NormalizedRequest, ctx: ProviderContext): Promise<NormalizedResponse>;
  streamResponse(req: NormalizedRequest, ctx: ProviderContext): AsyncIterable<NormalizedEvent>;
}
```

Optional extensions:

```ts
interface TokenCountingProvider {
  countTokens(req: NormalizedRequest, ctx: ProviderContext): Promise<TokenCount>;
}

interface OAuthProvider {
  createAuthUrl(ctx: OAuthStartContext): Promise<string>;
  exchangeCode(ctx: OAuthExchangeContext): Promise<AuthTokenSet>;
  refreshToken(ctx: OAuthRefreshContext): Promise<AuthTokenSet>;
}
```

### 5. Normalized Request Model

ALB should translate once at the edge, then pass a normalized request to adapters:

```ts
type NormalizedRequest = {
  model: string;
  messages?: NormalizedMessage[];
  input?: unknown;
  tools?: NormalizedTool[];
  toolChoice?: unknown;
  stream: boolean;
  temperature?: number;
  maxOutputTokens?: number;
  responseFormat?: unknown;
  metadata?: Record<string, unknown>;
};
```

Adapters convert this into provider-specific payloads.

### 6. Tool Calling Strategy

Tool calling must be explicit by capability:

- `native`: provider supports tool/function calling directly.
- `mapped`: provider supports similar tool calls, but schema/events need conversion.
- `emulated`: ALB prompts the model to emit tool-call JSON. This is weaker and must be marked.
- `none`: no tool support.

Codex agent workflows need reliable tool calls. For coding-agent use, prefer models with `native` or strong `mapped` support.

### 7. Auth Manager

Auth must be separate from provider logic.

Supported auth modes:

- Codex plan / codex-lb style auth
- API key
- OAuth
- local/no-auth
- custom headers for OpenAI-compatible endpoints

The auth manager should store:

- provider ID
- account ID
- auth mode
- encrypted secret/token reference
- refresh metadata
- rate-limit metadata if available
- quota/drain state
- enabled/disabled state

Never commit secrets.

### 8. Routing Engine

Routing should eventually support:

- explicit model selection
- fallback chains
- cheapest compatible route
- fastest healthy route
- provider priority
- account-level balancing
- quota-aware routing
- tool-capable-only routing
- image-capable-only routing
- per-provider drains
- reset drain behavior

Routing must respect capabilities. Example: do not route a tool-call-heavy Codex session to a model marked `tools: none`.

### 9. Dashboard

Dashboard should have:

- Global overview
- Provider sections
- Model catalog
- Account/auth status
- Quotas and drains
- Health checks
- Request logs
- Error/fallback logs
- Capability matrix
- Codex compatibility status

There should be separate provider views for Codex/OpenAI, Gemini/Antigravity, Mistral, NVIDIA, OpenRouter, Groq, and opencode zen, plus one combined overview.

## Migration Notes From Previous Work

Previous ALB work reportedly reached a user-approved build state for the original ALB goal:

- Codex path preserved.
- Gemini runtime support added.
- Antigravity CLI/API support explored/added.
- Provider dashboards/settings/quotas split from combined overview.
- Codex regression smoke helper existed as `scripts/verify_alb_codex_e2e.py`.
- Release hardening touched Docker, metrics, tracing, Helm, tests, and OpenSpec artifacts.

Before migrating code here, inspect and cherry-pick only clean pieces:

- provider abstractions
- Codex compatibility tests
- Gemini/Antigravity adapter code
- dashboard components that still fit the new architecture
- OpenSpec requirements/context
- verification scripts

Do not migrate:

- pycache
- venvs
- graphify output
- generated junk
- stale smoke databases
- old PR metadata
- accidental codexlb upstream changes
- anything that assumes the old repo path

## Relationship To Jarvis Provider Work

Jarvis previously moved toward:

- provider-owned packages
- provider registry
- dispatch-only router
- dedicated provider manager/settings UI
- Gemini-native support
- Groq through OpenAI-compatible config

That direction is relevant here. ALB should reuse the architectural lesson, not necessarily copy code blindly.

The useful principle:

```text
Router decides where traffic goes.
Provider adapter owns how provider calls work.
UI/config owns how providers are managed.
```

## Implementation Plan

### Phase 0 - Repo Foundation

- Add clean project skeleton.
- Add strict `.gitignore`.
- Add README/spec context.
- Add basic package/runtime choice.
- Add minimal CI.
- Add secret-handling rules.

### Phase 1 - Codex Gateway Baseline

- Implement Codex/OpenAI-compatible front door.
- Preserve current Codex plan/codex-lb style provider.
- Add health endpoint.
- Add minimal request/response logging.
- Add streaming compatibility smoke test.
- Add model list endpoint with at least Codex/OpenAI entries.

### Phase 2 - Provider Registry And Model Catalog

- Add provider registry.
- Add normalized request/response/event types.
- Add capability matrix.
- Add model catalog API.
- Add provider config loading.
- Add dashboard model picker data source.

### Phase 3 - First Adapters

Implement in this order:

1. OpenAI API
2. Generic OpenAI-compatible endpoint
3. Groq
4. OpenRouter
5. Gemini API
6. Mistral API
7. NVIDIA NIM API
8. Antigravity OAuth
9. opencode zen

Reasoning:

- OpenAI and OpenAI-compatible paths prove the gateway.
- Groq/OpenRouter validate generic provider mapping fast.
- Gemini/Mistral/NVIDIA validate native adapters.
- Antigravity OAuth and opencode zen are more custom and should come after the registry is stable.

### Phase 4 - Auth And Accounts

- API key storage.
- OAuth token storage and refresh.
- Codex plan auth preservation.
- Account enable/disable.
- Per-account health.
- Per-account quota state.
- Secret redaction everywhere.

### Phase 5 - Routing And Drains

- Account balancing.
- Provider balancing.
- Quota-aware route selection.
- Reset drain.
- Quota drain.
- Fallback chains.
- Capability-aware routing.

### Phase 6 - Dashboard

- Combined overview.
- Provider dashboards.
- Model catalog.
- Capability matrix.
- Account status.
- Quota/drain panels.
- Request/error/fallback logs.

### Phase 7 - Hardening

- Codex Desktop smoke tests.
- Provider adapter unit tests.
- Streaming tests.
- Tool-call tests.
- Image request tests where provider supports it.
- OAuth refresh tests.
- Secret redaction tests.
- Docker packaging.
- CI gates.

## Quality Rules

- Do not claim a provider fully works until it has at least one real or mocked adapter test and one gateway-level smoke path.
- Do not route requests to a model that lacks required capabilities unless the route explicitly allows emulation/degradation.
- Do not hide provider limitations.
- Do not commit secrets.
- Do not touch live codexlb.
- Prefer small sequential verification on this machine to avoid CPU/RAM spikes.
- Keep generated/cache directories ignored.

## Suggested Directory Shape

This is a proposed shape, not yet implemented:

```text
alb/
  README.md
  .gitignore
  pyproject.toml or package.json
  src/
    alb/
      gateway/
      providers/
        codex_plan/
        openai/
        openai_compatible/
        gemini/
        antigravity/
        mistral/
        nvidia/
        openrouter/
        groq/
        opencode_zen/
      routing/
      auth/
      catalog/
      dashboard/
      telemetry/
  tests/
    gateway/
    providers/
    routing/
    auth/
  scripts/
    smoke_codex_gateway.*
    verify_provider.*
```

## Public Positioning

ALB is for people who want their agent client to choose from many model providers without rewriting the agent stack each time.

Public pitch:

> ALB is a universal model gateway for agent clients. It gives Codex-like clients one stable API while routing requests across OpenAI, Gemini, Mistral, NVIDIA NIM, Groq, OpenRouter, Antigravity, and other providers with explicit capability tracking, auth management, quotas, drains, and provider dashboards.

## Immediate Next Move

Start implementation in this clean repo only after the user confirms.

Recommended first concrete build task:

1. Add `.gitignore`.
2. Add minimal runtime skeleton.
3. Implement provider descriptor/types.
4. Implement static model catalog.
5. Implement Codex/OpenAI-compatible `/v1/models` or equivalent model-list endpoint.
6. Add one OpenAI-compatible provider adapter.
7. Add one smoke test.

This gives a small V1 without boiling the ocean.

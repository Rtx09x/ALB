# AGENTS

## Environment

- Python: .venv/bin/python (uv, CPython 3.13.3)
- GitHub auth for git/API is available via env vars: `GITHUB_USER`, `GITHUB_TOKEN` (PAT). Do not hardcode or commit tokens.
- For authenticated git over HTTPS in automation, use: `https://x-access-token:${GITHUB_TOKEN}@github.com/<owner>/<repo>.git`

## Code Conventions

The `/project-conventions` skill is auto-activated on code edits (PreToolUse guard).

| Convention | Location | When |
|-----------|----------|------|
| Code Conventions (Full) | `/project-conventions` skill | On code edit (auto-enforced) |
| Git Workflow | `.agents/conventions/git-workflow.md` | Commit / PR |

## Workflow (OpenSpec-first)

This repo uses **OpenSpec as the primary workflow and SSOT** for change-driven development.

### How to work (default)

1) Find the relevant spec(s) in `openspec/specs/**` and treat them as source-of-truth.
2) If the work changes behavior, requirements, contracts, or schema: create an OpenSpec change in `openspec/changes/**` first (proposal -> tasks).
3) Implement the tasks; keep code + specs in sync (update `spec.md` as needed).
4) Validate specs locally: `openspec validate --specs`
5) When done: verify + archive the change (do not archive unverified changes).

### Source of Truth

- **Specs/Design/Tasks (SSOT)**: `openspec/`
  - Active changes: `openspec/changes/<change>/`
  - Main specs: `openspec/specs/<capability>/spec.md`
  - Archived changes: `openspec/changes/archive/YYYY-MM-DD-<change>/`

## Documentation & Release Notes

- **Do not add/update feature or behavior documentation under `docs/`**. Use OpenSpec context docs under `openspec/specs/<capability>/context.md` (or change-level context under `openspec/changes/<change>/context.md`) as the SSOT.
- **Do not edit `CHANGELOG.md` directly.** Leave changelog updates to the release process; record change notes in OpenSpec artifacts instead.

### Documentation Model (Spec + Context)

- `spec.md` is the **normative SSOT** and should contain only testable requirements.
- Use `openspec/specs/<capability>/context.md` for **free-form context** (purpose, rationale, examples, ops notes).
- If context grows, split into `overview.md`, `rationale.md`, `examples.md`, or `ops.md` within the same capability folder.
- Change-level notes live in `openspec/changes/<change>/context.md` or `notes.md`, then **sync stable context** back into the main context docs.

Prompting cue (use when writing docs):
"Keep `spec.md` strictly for requirements. Add/update `context.md` with purpose, decisions, constraints, failure modes, and at least one concrete example."

### Commands (recommended)

- Start a change: `/opsx:new <kebab-case>`
- Create artifacts (step): `/opsx:continue <change>`
- Create artifacts (fast): `/opsx:ff <change>`
- Implement tasks: `/opsx:apply <change>`
- Verify before archive: `/opsx:verify <change>`
- Sync delta specs → main specs: `/opsx:sync <change>`
- Archive: `/opsx:archive <change>`

## Contributing & Merge Gates

When authoring or merging a PR (as a human contributor, a collaborator,
or an AI assistant acting on behalf of either), the binding workflow is
in [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md). The sections
an AI assistant most often needs are:

- [Merge gates](.github/CONTRIBUTING.md#merge-gates) — CI green +
  `@codex review` clean (or findings addressed) + `mergeable=CLEAN` +
  OpenSpec change folder for behavior changes + `Fixes #N` /
  `Closes #N` for issue cover.
- [Collaborator rules](.github/CONTRIBUTING.md#collaborator-rules) —
  no self-merge by default; large PRs get split (≈1-concern per PR,
  ~800 net lines / scoped capability ceiling).
- [Bus factor escape hatch](.github/CONTRIBUTING.md#bus-factor-escape-hatch)
  — self-merge allowed after **14 days** with all gates met and a
  comment invoking the clause.

An assistant preparing a merge MUST verify the gates against the
actual GitHub state (status check rollup, codex review submissions,
`mergeable` field) rather than asserting them from local history.
Local `uv run pytest` / `uv run ruff` / `codex review --base origin/main`
are encouraged but not substitutes for the cloud gates.

## PR Readiness / Review Trapdoors

These rules encode recurring review blockers observed across codex-lb PRs.

- OpenSpec is a hard gate for behavior, API, schema, CLI,
  dashboard-visible, proxy-routing, operator-contract, and compatibility
  changes. Create or update `openspec/changes/<slug>/` before coding, keep
  `spec.md` normative with MUST/SHALL-style requirements, put rationale and
  examples in `context.md` or change notes, and run strict OpenSpec validation
  before calling the PR ready. Code/tests alone are not enough when OpenSpec is
  required.
- Codex review state must come from current-head GitHub evidence. Check labels,
  latest Codex review/comment/reaction, and GraphQL review threads before using
  or claiming `🤖 codex: ok`. Usage-limit, environment, or missing-review
  results mean missing evidence, not approval. Unresolved non-outdated P-level
  Codex threads block readiness even when a top-level review comment looks
  clean.
- Proxy failover and retry patches must prove account ownership and settlement
  invariants. File-pinned requests must not cross accounts; API-key reservations
  must settle before error-health writes; excluded accounts must actually leave
  the selection loop; idle disconnects must not mark otherwise healthy accounts
  unhealthy; security/trusted-access routing must degrade only along the
  documented path.
- Async, fan-out, and session-lifecycle patches must prove task ownership and
  cleanup. Do not share one `AsyncSession` across concurrent tasks; cancel or
  await spawned tasks on failure; preserve finalization/settlement paths after
  partial errors; bound fan-out; and test partial-failure behavior, not only
  the all-success path.
- Database migrations must prove Alembic graph and data hygiene. New revisions
  must sit on the current intended parent with a single-head upgrade path, have
  downgrade/upgrade coverage where the project expects it, and include
  historical-row backfills or compatibility handling when new fields affect
  existing data.
- Issue-resolving PRs must name the exact `Fixes #N` / `Closes #N`, or state
  that they are partial. Keep PRs one concern wide. Revive stale work by making
  a focused branch on current `main`; do not drag an old broad/conflicted branch
  forward unless the maintainer explicitly wants that shape.
- Bug fixes need regression coverage at the externally failing product path:
  route, bridge, websocket, CLI, schema, dashboard UI, or migration path as
  applicable. Helper-only tests are not enough when the failing surface is
  elsewhere.
- Compatibility work must verify canonical and equivalent paths, trailing slash
  behavior, external error envelopes, env-var semantics, and response-schema
  contracts. Update OpenSpec/context and tests together so docs cannot promise
  behavior the code does not implement.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After meaningful code changes, run `graphify update . --no-cluster` manually to keep the graph current. Do not run broad graph/test/type batches in parallel on this machine; keep verification sequential and CPU-safe.

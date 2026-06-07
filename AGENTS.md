# AGENTS.md — Project Conventions for TraceNex / Fy-api

This file provides guidance to coding agents when working in this repository.

## Repository Identity

This repository is **TraceNex**, the user-facing product brand, implemented in the repo directory and GitHub project still named **Fy-api**. It is a downstream fork of `QuantumNous/new-api` with a small overlay of customizations.

- The Go module path intentionally remains `github.com/QuantumNous/new-api`; do not rename it.
- Upstream gateway behavior comes from `QuantumNous/new-api`; TraceNex-specific changes should stay small and merge-friendly.
- Before changing anything, read `OVERLAY.md`. It is the source of truth for TraceNex-specific changes and merge-conflict expectations.
- In the parent workspace, both `Fy-api/` and `TraceNexBiz/` are actively edited. `new-api/` and `old_code/` are read-only references.

### Sibling project: TraceNexBiz (consumer of `/api/internal/*`)

A separate downstream project `~/Projects/apiGateway/TraceNexBiz/` (channel-distribution SaaS, product brand "TraceNex Partner") consumes Fy-api via the `/api/internal/*` routes added in OVERLAY entries B-12..B-18. Contract: HMAC-SHA256 (`X-Auth-KeyId` / `X-Auth-Timestamp` / `X-Auth-Nonce` / `X-Signature`; canonical in `middleware/internal_auth.go::BuildCanonical`) plus `Idempotency-Key`. Any change to that middleware, `controller/tnbiz_internal/*.go`, or the `/api/internal/*` routes is a contract change — `TraceNexBiz/apps/partner-api/internal/infra/fyapi/client_test.go::TestSign_FyApiParity` is the byte-level parity guard. See `OVERLAY-TNBIZ-HANDOFF.md` for current integration state.

### Frontend theme: classic-only

Upstream v1.0 (commit `a42b39760`, 2026-04-28) introduced a parallel `web/default/` frontend (React 19 + TypeScript + Rsbuild + Base UI + Tailwind). TraceNex ships **only** the legacy `web/classic/` frontend (React 18 + Vite + Semi UI). The runtime theme is locked to `"classic"` via `setting/system_setting/theme.go` + `controller/option.go` so the `default` build path is shipped for upstream parity but not selectable. All overlay edits target `web/classic/...` paths.

## Common Commands

### Full-stack dev

```bash
make all                       # build frontend, then start backend dev server
make build-frontend-classic    # bun install + bun run build in web/classic/
make start-backend             # go run main.go
```

### Backend

```bash
go mod tidy
go build -o bin/fy-api
./bin/fy-api                    # default :3000; SQLite unless SQL_DSN is set

go test ./...                   # all backend tests
go test ./... -race             # race detector
go test -cover ./service/...    # coverage for service package tree
go test ./relay/channel/gemini/ -race -run TestBuildUsageFromGeminiMetadata
```

### Frontend (`web/classic/`)

Use Bun for frontend package management and scripts.

```bash
cd web/classic
bun install
bun run dev          # Vite dev server
bun run build        # production build
bun run lint         # Prettier check
bun run lint:fix     # Prettier write
bun run eslint       # ESLint with cache
bun run eslint:fix   # ESLint fix
bun run i18n:extract
bun run i18n:status
bun run i18n:sync
bun run i18n:lint
```

### Server-side deploy via Fabric

The root `fabfile.py` implements the current deployment flow: local git push, server git fetch/checkout, server Podman build, push to ACR, then blue-green deploy using `scripts/prod/06-deploy-blue-green.sh`. Use conda env `fy-api-deploy`.

```bash
conda run -n fy-api-deploy fab info --target=cn
conda run -n fy-api-deploy fab status --target=cn
conda run -n fy-api-deploy fab logs --target=cn --tail=200
conda run -n fy-api-deploy fab release --target=cn --tag=v0.9.8 --ref=origin/main
conda run -n fy-api-deploy fab deploy --target=cn --tag=v0.9.8
conda run -n fy-api-deploy fab rollback --target=cn --tag=v0.9.7

conda run -n fy-api-deploy fab info --target=sg
conda run -n fy-api-deploy fab status --target=sg
conda run -n fy-api-deploy fab logs --target=sg --tail=200
conda run -n fy-api-deploy fab deploy --target=sg --tag=sg-5d733d85

conda run -n fy-api-deploy fab preflight --target=legacy
```

Known targets:

- `cn`: Hangzhou production, `root@8.136.146.211:58422`, key `~/.ssh/tracenex_XN.pem`.
- `sg`: Singapore production, `root@47.236.133.70:58422`, key `~/.ssh/AI_tracenex.pem`, public URL `https://api.aitracenex.com`, ACR namespace `ai_transnext`.
- `legacy`: old source server, `root@8.222.175.17`, default SSH key/agent, contains `/root/TraceNex` and local MySQL source data.

### Upstream sync orientation

```bash
git fetch upstream
git rev-list --count HEAD..upstream/main
git log HEAD..upstream/main --oneline | head
```

Follow `docs/Weekly-upstream-sync-runbook.md` for the weekly merge flow and the criteria that trigger an on-demand release.

### Pull request workflow

When creating a PR for the user, use a separate git worktree for the PR branch instead of switching the main worktree away from its current branch. This keeps the user's active workspace, IDE state, and untracked files undisturbed.

```bash
git worktree add ../fy-api-pr-<name> -b <branch-name> develop
cd ../fy-api-pr-<name>
# make edits, run tests, commit, push, and create the PR here
```

After the PR is created, leave the main worktree on its original branch. Do not merge the PR unless the user explicitly asks.

### Migration context

SG production was migrated from the legacy self-hosted MySQL on 2026-05-07.

- Source: `legacy` / `8.222.175.17`, databases `tracenex` and `tracenex_log.logs`.
- Target: SG RDS `transnext_db` used by `api.aitracenex.com`.
- SG pre-migration backup: `/opt/fy-api/backup/transnext_db-before-legacy-migration-20260507-231343.sql.gz` on the SG server.
- Legacy MySQL operational access is via `/etc/mysql/debian.cnf`; application DSNs are in `/root/TraceNex/.env`. Do not print database passwords.

## High-Level Architecture

The backend uses a layered structure with the relay layer plugged into request handling for provider routing:

```text
router/        HTTP routing. api-router.go registers /api/*; relay-router.go registers /v1/*, /v1beta/*, /v1/messages, etc.
controller/    HTTP handlers: parse requests, call service/model, return responses.
service/       Business logic: billing, quota, channel selection, OAuth flows, subscription tasks, task billing.
model/         GORM models and DB access. main.go handles DB initialization and migrations.
relay/         AI API proxy core.
  relay/channel/   Provider adapters: openai, claude, gemini, aws, ali, volcengine, minimax, task, codex, etc.
  relay/common/    RelayInfo, stream state, billing helpers, request-body storage.
  relay/helper/    Stream scanning/parsing and relay helpers.
  relay/reasonmap/ Reasoning-effort suffix mapping.
middleware/    Auth, distributor/channel selection, rate limits, i18n, request id, performance, body cleanup.
setting/       Runtime configuration split by concern: model, operation, ratio, performance, system.
common/        Shared utilities: JSON wrapper, env, Redis, crypto, URL/SSRF validation, disk cache.
dto/           Request/response DTOs for OpenAI, Claude, Gemini, task APIs, etc.
constant/      Channel types, API types, context keys, env keys.
types/         Typed errors, relay format enum, generic sets, file source abstractions.
i18n/          Backend i18n using go-i18n and embedded YAML locales.
oauth/         OAuth registry and providers.
pkg/           Internal packages such as cachex, ionet, perf_metrics, billingexpr.
web/           Frontend themes container.
  web/classic/   Active TraceNex frontend (React 18 + Vite + Semi UI). All overlay edits live here.
  web/default/   Upstream v1.0 frontend (React 19 + Rsbuild + Base UI + Tailwind). Built but not selectable.
```

Key architecture patterns:

- **Startup flow**: env/config -> DB open + migrate -> option map -> Redis -> cache -> OAuth registry -> router setup -> HTTP server.
- **Channel adapters**: each provider implements `relay/channel/adapter.go` (`Init`, `GetRequestURL`, `SetupRequestHeader`, `ConvertRequest`, `DoRequest`, `DoResponse`).
- **RelayInfo**: `relay/common/relay_info.go` carries per-request channel, model, user, price, stream, retry, and request-id state through the relay path.
- **Billing pipeline**: `middleware/distributor.go` selects a channel, relay adapters call upstream, then `service/text_quota.go` or `service/task_billing.go` records actual consumption. Usage details must flow into `dto.Usage` for billing.
- **Request ID**: middleware generates `X-Oneapi-Request-Id`; consume/error logs store `model.RequestId`; log endpoints support `?request_id=` filtering.
- **Channel affinity**: `service/channel_affinity.go` keeps consecutive requests from the same user/group on the same upstream channel when configured.
- **Theme switching**: `common/embed-file-system.go` chooses between `web/default/dist` and `web/classic/dist` at runtime via `common.GetTheme()`. TraceNex pins this to `"classic"` (see `setting/system_setting/theme.go`).

## TraceNex Overlay Rules

- Prefer new files over editing upstream files.
- If an upstream file must be touched, add a `// Fy-api overlay:` comment in Go or `{/* Fy-api overlay: */}` in JSX.
- Update `OVERLAY.md` in the same change when adding, removing, or changing TraceNex-specific behavior.
- Preserve upstream attribution: `LICENSE`, `NOTICE`, `THIRD-PARTY-LICENSES.md`, copyright headers, and the Go module path.
- TraceNex brand customizations are allowed for user-facing brand text, logo/favicon, README additions, and package names called out in `OVERLAY.md` / `CLAUDE.md`.
- Do not introduce brand words into frontend i18n keys; keys are Chinese source strings (in `web/classic/`) and brand replacement is value-side via gsub.

## Coding Rules

### JSON wrapper

All JSON marshal/unmarshal operations in business code must use `common/json.go` wrappers:

- `common.Marshal`
- `common.Unmarshal`
- `common.UnmarshalJsonStr`
- `common.DecodeJson`
- `common.GetJsonType`

`encoding/json` types such as `json.RawMessage` and `json.Number` may still be referenced as types.

### Database compatibility

SQLite, MySQL >= 5.7.8, and PostgreSQL >= 9.6 must all remain supported.

- Prefer GORM methods over raw SQL.
- Use `commonGroupCol` and `commonKeyCol` for reserved columns like `group` and `key`.
- Use `commonTrueVal` / `commonFalseVal` and `common.UsingPostgreSQL` / `common.UsingSQLite` / `common.UsingMySQL` for DB-specific branches.
- Avoid DB-specific SQL or column types without cross-DB fallback.
- SQLite migrations should use add-column style workarounds rather than unsupported `ALTER COLUMN` flows.

### Relay DTO zero values

For request structs parsed from client JSON and re-marshaled upstream, optional scalar fields must use pointer types with `omitempty`. Absent fields should be `nil`; explicitly provided zero/false values must remain non-`nil` and be sent upstream.

### New provider/channel work

When adding a new channel, confirm whether the provider supports `StreamOptions`. If supported, add it to the stream-supported channel list.

### Billing expression system — read `pkg/billingexpr/expr.md`

When working on tiered/dynamic billing (expression-based pricing), you MUST read `pkg/billingexpr/expr.md` first. It documents the design philosophy, expression language (variables, functions, examples), full system architecture (editor → storage → pre-consume → settlement → log display), token normalization rules (`p`/`c` auto-exclusion), quota conversion, and expression versioning. All code changes to the billing expression system must follow the patterns described in that document.

## Internationalization

Backend i18n lives in `i18n/` using `nicksnyder/go-i18n/v2` with embedded YAML locale files.

Frontend i18n lives in `web/classic/src/i18n/` using `i18next` + `react-i18next` + browser language detection. Locale files are flat JSON under `web/classic/src/i18n/locales/{lang}.json`, wrapped under `translation`; keys are Chinese source strings. Current frontend languages include `zh-CN`, `zh-TW`, `zh`, `en`, `fr`, `ja`, `ru`, and `vi`.

> Note: `web/default/src/i18n/` (the v1.0 frontend) uses English source strings as keys instead. We don't ship default, but a future B-route migration would need to re-author overlays in that style.

## Channel benchmarking

The `scripts/channel-benchmark/` directory is a self-contained toolkit for measuring channel liveness, load capacity, output quality, and model-substitution integrity. It is independent of the main backend (its own `go.mod` in `go/`, its own `pyproject.toml` in `py/`) so it can be run on any host that has network access to the gateway.

Layout:

```text
scripts/channel-benchmark/
├── README.md                       top-level navigation (how Go and Python tools relate)
├── go/                             zero-dep Go smoke + Prometheus exporter
│   ├── main.go / runner.go / client.go / admin.go
│   ├── prometheus.go               exposition-format daemon (no prom client dep)
│   └── channel-benchmark.yaml
└── py/                             three CLIs sharing one venv + JSONL schema
    ├── pyproject.toml              entry points: fy-loadtest, fy-quality, fy-canary
    ├── fy_loadtest/                concurrency-ramp load testing
    ├── fy_quality/                 golden-JSONL quality scorecard + 7 graders + dual judge
    │   ├── perturbation.py         deterministic contamination-defense perturbations
    │   └── datasets/
    │       ├── public/             starter suite (committed, assumed-memorized)
    │       └── private/            user-private prompts (gitignored)
    └── fy_canary/                  baseline + audit + verify-baseline
        └── baseline.py             v2 schema w/ recorded_at_iso + health metadata
```

Key invariants when working in this tree:

- **Never rewrite the Go tool to depend on `prometheus/client_golang`.** The zero-dep exposition in `prometheus.go` is deliberate so the binary stays drop-on-prod.
- **Never add brand words (TraceNex / Fy-api) to the starter `public/quality.jsonl`.** It is the ONLY dataset meant to be committed; keeping it brand-neutral avoids the file being a signal leak.
- **Never bypass `row.wire_prompt()` in the quality runner.** Perturbations must be applied before hitting the channel, and the cache key must be derived from the perturbed text so schema changes invalidate caches reliably.
- **Never have a channel judge its own output in `fy-quality`.** Judges are configured independently from channels — keep it that way.
- **Baseline files are data, not code.** `fy_canary/baseline.py`'s load path must stay backwards-compatible with v1 files; upgrades happen on next save, not on load.

Commands:

```bash
# Smoke / Prometheus
cd scripts/channel-benchmark/go
go test -race ./...                  # full Go test suite
go run . -config channel-benchmark.yaml                        # one-shot
go run . -config channel-benchmark.yaml -prom-listen :9090 -prom-interval 5m   # daemon

# Python tools
cd scripts/channel-benchmark/py
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
source .venv/bin/activate
pytest                               # all 47 tests (loadtest + quality + canary)
fy-loadtest -c loadtest.yaml
fy-quality  -c quality.yaml
fy-canary   baseline         -c canary.yaml
fy-canary   audit            -c canary.yaml
fy-canary   verify-baseline  -c canary.yaml
```

When extending this toolkit, log the change in `OVERLAY.md` entry **B-7** (same file that tracks all TraceNex customizations) so the next upstream sync doesn't lose context.

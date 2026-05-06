# AGENTS.md — Project Conventions for TraceNex / Fy-api

This file provides guidance to coding agents when working in this repository.

## Repository Identity

This repository is **TraceNex**, the user-facing product brand, implemented in the repo directory and GitHub project still named **Fy-api**. It is a downstream fork of `QuantumNous/new-api` with a small overlay of customizations.

- The Go module path intentionally remains `github.com/QuantumNous/new-api`; do not rename it.
- Upstream gateway behavior comes from `QuantumNous/new-api`; TraceNex-specific changes should stay small and merge-friendly.
- Before changing anything, read `OVERLAY.md`. It is the source of truth for TraceNex-specific changes and merge-conflict expectations.
- In the parent workspace, only `Fy-api/` is normally edited. `new-api/` and `old_code/` are read-only references.

## Common Commands

### Full-stack dev

```bash
make all              # build frontend, then start backend dev server
make build-frontend   # bun install + bun run build in web/
make start-backend    # go run main.go
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

### Frontend (`web/`)

Use Bun for frontend package management and scripts.

```bash
cd web
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

The root `fabfile.py` implements the current deployment flow: local git push, server git fetch/checkout, server Podman build, push to intranet ACR, then blue-green deploy using `scripts/prod/06-deploy-blue-green.sh`.

```bash
conda run -n fy-api-deploy fab check
conda run -n fy-api-deploy fab release --tag=v0.9.8 --ref=origin/main
conda run -n fy-api-deploy fab deploy --tag=v0.9.8
conda run -n fy-api-deploy fab rollback --tag=v0.9.7
conda run -n fy-api-deploy fab status
conda run -n fy-api-deploy fab logs --tail=200
conda run -n fy-api-deploy fab health
```

### Upstream sync orientation

```bash
git fetch upstream
git rev-list --count HEAD..upstream/main
git log HEAD..upstream/main --oneline | head
```

Follow `docs/Monthly-upstream-sync-runbook.md` for the full monthly sync process.

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
pkg/           Internal packages such as cachex and ionet.
web/           React + Vite frontend.
```

Key architecture patterns:

- **Startup flow**: env/config -> DB open + migrate -> option map -> Redis -> cache -> OAuth registry -> router setup -> HTTP server.
- **Channel adapters**: each provider implements `relay/channel/adapter.go` (`Init`, `GetRequestURL`, `SetupRequestHeader`, `ConvertRequest`, `DoRequest`, `DoResponse`).
- **RelayInfo**: `relay/common/relay_info.go` carries per-request channel, model, user, price, stream, retry, and request-id state through the relay path.
- **Billing pipeline**: `middleware/distributor.go` selects a channel, relay adapters call upstream, then `service/text_quota.go` or `service/task_billing.go` records actual consumption. Usage details must flow into `dto.Usage` for billing.
- **Request ID**: middleware generates `X-Oneapi-Request-Id`; consume/error logs store `model.RequestId`; log endpoints support `?request_id=` filtering.
- **Channel affinity**: `service/channel_affinity.go` keeps consecutive requests from the same user/group on the same upstream channel when configured.

## TraceNex Overlay Rules

- Prefer new files over editing upstream files.
- If an upstream file must be touched, add a `// Fy-api overlay:` comment in Go or `{/* Fy-api overlay: */}` in JSX.
- Update `OVERLAY.md` in the same change when adding, removing, or changing TraceNex-specific behavior.
- Preserve upstream attribution: `LICENSE`, notices, copyright headers, and the Go module path.
- TraceNex brand customizations are allowed for user-facing brand text, logo/favicon, README additions, and package names called out in `OVERLAY.md` / `CLAUDE.md`.
- Do not introduce brand words into frontend i18n keys; keys are Chinese source strings and brand replacement is value-side.

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

## Internationalization

Backend i18n lives in `i18n/` using `nicksnyder/go-i18n/v2` with embedded YAML locale files.

Frontend i18n lives in `web/src/i18n/` using `i18next` + `react-i18next` + browser language detection. Locale files are flat JSON under `web/src/i18n/locales/{lang}.json`, wrapped under `translation`; keys are Chinese source strings. Current frontend languages include `zh-CN`, `zh-TW`, `en`, `fr`, `ja`, `ru`, and `vi`.

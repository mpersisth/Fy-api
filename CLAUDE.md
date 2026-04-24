# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Identity

This is **Fy-api**, a downstream fork of [QuantumNous/new-api](https://github.com/QuantumNous/new-api) with a small overlay of customizations. Everything the gateway itself does (provider adapters, relay, billing, admin dashboard, subscription, channel affinity, etc.) comes from upstream and is kept in sync on a monthly cadence.

**Before changing anything, read [`OVERLAY.md`](./OVERLAY.md).** It is the single source of truth for which files are Fy-api customizations vs pure upstream, and what will/won't conflict on the next `upstream/main` merge. Preserving its accuracy is as important as the code changes themselves.

Upstream remote is configured read-only:
```
origin    git@github.com:seraph0017/Fy-api.git  (your remote)
upstream  https://github.com/QuantumNous/new-api.git  (read-only)
```

The Go module path is intentionally kept as `github.com/QuantumNous/new-api` — changing it would rewrite thousands of imports and break upstream mergeability.

## Tech Stack

- **Backend**: Go 1.25+ (module says 1.25.1), Gin web framework, GORM v2 ORM
- **Frontend**: React 18, Vite, Semi Design UI (`@douyinfe/semi-ui`)
- **Databases**: SQLite, MySQL ≥ 5.7.8, PostgreSQL ≥ 9.6 — **all three must be supported simultaneously**
- **Cache**: Redis (go-redis) + in-memory cache
- **Auth**: JWT, WebAuthn/Passkeys, OAuth (GitHub, Discord, LinuxDo, OIDC, WeChat, Telegram)
- **Frontend package manager**: Bun (preferred over npm/yarn/pnpm)

## Common Commands

### Full-stack dev (Makefile)

```bash
make all              # builds frontend + starts backend dev server
make build-frontend   # bun install + bun run build in web/
make start-backend    # go run main.go
```

### Backend

```bash
go mod tidy
go build -o bin/fy-api
./bin/fy-api                    # runs at :3000 by default; uses SQLite unless SQL_DSN is set

# Tests
go test ./...                   # all tests
go test ./... -race             # with race detector (standard per project convention)
go test ./relay/channel/gemini/ -race -run TestBuildUsageFromGeminiMetadata  # one test
go test -cover ./service/...    # with coverage
```

### Frontend (`web/`)

```bash
cd web
bun install
bun run dev          # vite dev server
bun run build        # production build
bun run lint         # prettier check
bun run lint:fix     # prettier write
bun run eslint       # eslint with cache
bun run eslint:fix   # eslint --fix

# i18n tooling (run from web/)
bun run i18n:extract
bun run i18n:status
bun run i18n:sync
bun run i18n:lint
```

### Upstream sync

```bash
git fetch upstream
git rev-list --count HEAD..upstream/main     # drift count
git log HEAD..upstream/main --oneline | head # what's new upstream
```

Then follow [`docs/Monthly-upstream-sync-runbook.md`](./docs/Monthly-upstream-sync-runbook.md). There are two GitHub Actions that automate this: `.github/workflows/upstream-watch.yml` (weekly drift check) and `.github/workflows/upstream-sync.yml` (manual merge + auto-PR).

## High-Level Architecture

Layered architecture (Router → Controller → Service → Model), with the relay layer orthogonally plugging into the Controller for AI-provider routing:

```
router/        HTTP routing. api-router.go registers /api/* (admin + user);
               relay-router.go registers /v1/*, /v1beta/*, /v1/messages, etc.
controller/    HTTP handlers. Parse query/body, call service, return response.
service/       Business logic. Billing formulas, quota (text_quota.go /
               task_billing.go / violation_fee.go), pre-consume, channel
               selection, OAuth flows, subscription reset task, etc.
model/         GORM models and DB access. main.go drives AutoMigrate over
               all tables on startup.
relay/         AI API proxy core.
  relay/channel/        40+ provider adapters (openai/, claude/, gemini/,
                         aws/, ali/, volcengine/, minimax/, task/, codex/, ...).
                         Each provider implements the channel.Adapter
                         interface (Init, GetRequestURL, SetupRequestHeader,
                         ConvertRequest, DoRequest, DoResponse).
  relay/common/         Shared relay state (RelayInfo carries per-request
                         context across adapter calls), stream helpers,
                         request-body storage, billing helpers.
  relay/helper/         Stream scanning/parsing.
  relay/reasonmap/      Reasoning-effort suffix mapping (e.g. *-low / *-high).
middleware/    auth.go (JWT + token), distributor.go (channel selection),
               rate-limit.go, i18n.go, performance.go, body_cleanup.go,
               request-id.go (generates X-Oneapi-Request-Id).
setting/       Runtime configuration, organized by concern:
  setting/model_setting/       per-provider overrides (gemini.go, grok.go, ...)
  setting/operation_setting/   general settings, channel affinity, status-code
                                rules, checkin, token settings
  setting/ratio_setting/       model/group ratio configuration
  setting/performance_setting/ system-monitor thresholds, disk cache
common/        Shared utilities. common/json.go is the mandatory JSON wrapper
               (see Rule 1). disk_cache.go, system_monitor_*.go, env.go,
               crypto.go, go-channel.go, url_validator.go, etc.
dto/           Request/response DTOs. openai_*.go, claude.go, gemini.go,
               task.go, etc. See Rule 6 for pointer semantics.
constant/      API types, channel types, context keys, env keys.
types/         Typed errors (NewAPIError, ErrorCode), RelayFormat enum,
               generic Set[T], file source abstraction.
i18n/          Backend i18n (nicksnyder/go-i18n/v2). locales/*.yaml.
oauth/         Unified OAuth provider abstraction (registry + provider/
               github/discord/linuxdo/oidc/generic/types.go).
pkg/           Internal packages (cachex/ for hybrid cache, ionet/).
web/           React + Vite frontend. Bun is the toolchain.
  web/src/i18n/locales/   zh-CN, zh-TW, en, fr, ja, ru, vi JSON (flat).
```

### Key architectural patterns to know

- **Channel adapter pattern** (`relay/channel/adapter.go`): adding a new provider means implementing the `Adapter` interface and registering in `controller/relay.go`'s dispatcher. Each adapter owns its own `ConvertRequest` (client → upstream) and `DoResponse` (upstream → client), including streaming.
- **RelayInfo** (`relay/common/relay_info.go`): a per-request struct threaded through every adapter call. Holds channel, model name, user, price data, stream status, retry counters, request-id — the authoritative place to stash per-request state.
- **Billing pipeline**: `middleware/distributor.go` picks the channel → `relay/*` calls upstream → on completion `service/text_quota.go` or `service/task_billing.go` posts the actual consumption. Cache tokens (e.g. `usage.PromptTokensDetails.CachedTokens`) multiply by `PriceData.CacheRatio`; all details parsed from upstream usage metadata must flow into `dto.Usage` for billing to see them.
- **Request ID**: `middleware/request-id.go` generates it, sets `X-Oneapi-Request-Id` response header, and `model.RequestId` is written by `model/log.go`'s `RecordConsumeLog`/`RecordErrorLog`. The admin and self log endpoints accept `?request_id=` for exact filtering.
- **Channel affinity**: `service/channel_affinity.go` routes consecutive calls from the same user to the same upstream channel. Configurable per-group; see `setting/operation_setting/channel_affinity_setting.go`.
- **Startup flow** (`main.go`): env/config → DB open + migrate → option map load → Redis → cache init → OAuth registry init → router.SetRouter → server.Run.

## Internationalization

### Backend (`i18n/`)
- Library: `nicksnyder/go-i18n/v2`
- Locales embedded via `go:embed locales/*.yaml`
- User language preference > Accept-Language > default

### Frontend (`web/src/i18n/`)
- Library: `i18next` + `react-i18next` + `i18next-browser-languagedetector`
- Languages: zh-CN (fallback), zh-TW, en, fr, ru, ja, vi
- Translation files are flat JSON under `web/src/i18n/locales/{lang}.json`, wrapped under a `translation` key; **keys are Chinese source strings**
- Usage: `const { t } = useTranslation(); t('中文 key')`
- Semi UI locale synced via `SemiLocaleWrapper`
- Fy-api brand rebrand (`New API` → `Fy-api`) is re-applied automatically by the upstream-sync CI; do not bake brand words into keys

## Rules

### Rule 1: JSON Package — Use `common/json.go`

All JSON marshal/unmarshal operations MUST use the wrapper functions in `common/json.go`:

- `common.Marshal(v any) ([]byte, error)`
- `common.Unmarshal(data []byte, v any) error`
- `common.UnmarshalJsonStr(data string, v any) error`
- `common.DecodeJson(reader io.Reader, v any) error`
- `common.GetJsonType(data json.RawMessage) string`

Do NOT directly import or call `encoding/json` in business code. `json.RawMessage`, `json.Number`, and type definitions from `encoding/json` may still be referenced as types; only the marshal/unmarshal calls must go through `common.*`.

### Rule 2: Database Compatibility — SQLite, MySQL ≥ 5.7.8, PostgreSQL ≥ 9.6

All database code MUST be fully compatible with all three databases simultaneously.

**Use GORM abstractions:**
- Prefer GORM methods (`Create`, `Find`, `Where`, `Updates`, etc.) over raw SQL
- Let GORM handle primary key generation — do not use `AUTO_INCREMENT` or `SERIAL` directly

**When raw SQL is unavoidable:**
- Column quoting differs: PostgreSQL uses `"column"`, MySQL/SQLite uses `` `column` ``
- Use `commonGroupCol`, `commonKeyCol` variables from `model/main.go` for reserved-word columns like `group` and `key`
- Boolean values differ: PostgreSQL uses `true`/`false`, MySQL/SQLite uses `1`/`0`. Use `commonTrueVal`/`commonFalseVal`
- Use `common.UsingPostgreSQL`, `common.UsingSQLite`, `common.UsingMySQL` flags to branch DB-specific logic

**Forbidden without cross-DB fallback:**
- MySQL-only functions (e.g., `GROUP_CONCAT` without PostgreSQL `STRING_AGG` equivalent)
- PostgreSQL-only operators (e.g., `@>`, `?`, `JSONB` operators)
- `ALTER COLUMN` in SQLite (unsupported — use column-add workaround)
- Database-specific column types without fallback — use `TEXT` instead of `JSONB` for JSON storage

**Migrations:**
- All migrations must work on all three databases
- For SQLite, use `ALTER TABLE ... ADD COLUMN` instead of `ALTER COLUMN` (see `model/main.go` for patterns, e.g. `migrateTokenModelLimitsToText`)

### Rule 3: Frontend — Prefer Bun

Use `bun` as the package manager and script runner for the frontend (`web/` directory).

### Rule 4: New Channel StreamOptions Support

When implementing a new channel:
- Confirm whether the provider supports `StreamOptions`
- If supported, add the channel to `streamSupportedChannels`

### Rule 5: Upstream Attribution — Preserve Apache 2.0 Compliance

Fy-api is a downstream fork of **new-api** (`github.com/QuantumNous/new-api`, AGPLv3). The following **upstream attribution** MUST be preserved:

- `LICENSE` file — keep as-is
- `NOTICE` file (if present) — keep all upstream notices intact
- Original copyright headers inside source files referencing new-api / QuantumNous — keep intact
- Go module path `github.com/QuantumNous/new-api` — **never rename** (would break merge-ability with upstream)
- Docker image labels / LICENSE references / README sections attributing upstream

The following are **downstream customizations for Fy-api** and MAY be changed:

- `common.SystemName` (user-facing brand name)
- Footer / Header brand text
- i18n locale files (brand words only — CI re-applies `New API` → `Fy-api`)
- `web/public/new_logo.png` and favicon
- README additions describing Fy-api-specific features
- `package.json` name field in `electron/` and `web/`

When in doubt, preserve both sides rather than picking one.

### Rule 6: Upstream Relay Request DTOs — Preserve Explicit Zero Values

For request structs that are parsed from client JSON and then re-marshaled to upstream providers (relay/convert paths):

- Optional scalar fields MUST use pointer types with `omitempty` (e.g. `*int`, `*uint`, `*float64`, `*bool`), not non-pointer scalars
- Semantics:
  - field absent in client JSON → `nil` → omitted on marshal
  - field explicitly set to zero/false → non-`nil` pointer → must still be sent upstream
- Avoid non-pointer scalars with `omitempty` for optional request parameters; zero values (`0`, `0.0`, `false`) will be silently dropped during marshal

## Fy-api Customization Strategy

When adding new functionality:

1. **Prefer new files over edits to upstream files.** Example: CSV log export lives in `controller/log_export.go` + `model/log_export.go` + `web/src/components/table/usage-logs/UsageLogsExportButton.jsx`, not as edits to existing upstream files. This keeps future upstream merges conflict-free.
2. **When an upstream file must be touched**, tag the change with `// Fy-api overlay:` (Go) or `{/* Fy-api overlay: */}` (JSX) comments so it's findable during merges.
3. **Update `OVERLAY.md`** in the same commit. If a customization isn't listed there, it is considered drift and may be lost on the next upstream sync.
4. **Don't introduce brand words in i18n keys.** The rebrand runs as a value-side `gsub("New API", "Fy-api")` after each sync.

## Documentation Index

Fy-api-specific operational docs live under [`docs/`](./docs/):

- `Phase3-DB-migration-runbook.md` — zero-downtime DB migration (from older deployments)
- `Phase4-Build-runbook.md` — build-from-source + dependency upgrade notes
- `Phase5-Regression-checklist.md` — post-deploy regression list
- `Monthly-upstream-sync-runbook.md` — standard monthly upstream merge flow
- `Bug分析-Gemini缓存命中未计费.md` — post-mortem reference for cache-token billing (already fixed upstream)

For gateway features themselves (endpoints, billing formulas, provider quirks) see the upstream docs at <https://docs.newapi.pro>.

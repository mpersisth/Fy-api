# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Identity

This is **TraceNex**, a downstream fork of [QuantumNous/new-api](https://github.com/QuantumNous/new-api) with a small overlay of customizations. Everything the gateway itself does (provider adapters, relay, billing, admin dashboard, subscription, channel affinity, etc.) comes from upstream and is merged in **weekly** (with deployments triggered on demand — not on the same cadence as the merge). See [`docs/Weekly-upstream-sync-runbook.md`](./docs/Weekly-upstream-sync-runbook.md) for the merge process and the "trigger an immediate release" criteria.

**Before changing anything, read [`OVERLAY.md`](./OVERLAY.md).** It is the single source of truth for which files are TraceNex customizations vs pure upstream, and what will/won't conflict on the next `upstream/main` merge. Preserving its accuracy is as important as the code changes themselves.

Upstream remote is configured read-only:
```
origin    git@github.com:seraph0017/Fy-api.git  (your remote)
upstream  https://github.com/QuantumNous/new-api.git  (read-only)
```

The Go module path is intentionally kept as `github.com/QuantumNous/new-api` — changing it would rewrite thousands of imports and break upstream mergeability.

## Tech Stack

- **Backend**: Go 1.25+ (module says 1.25.1), Gin web framework, GORM v2 ORM
- **Frontend (active for TraceNex)**: React 18, Vite, Semi Design UI (`@douyinfe/semi-ui`) — lives at `web/classic/`
- **Frontend (upstream v1.0, not selected)**: React 19, TypeScript, Rsbuild, Base UI, Tailwind CSS — lives at `web/default/`
- **Databases**: SQLite, MySQL ≥ 5.7.8, PostgreSQL ≥ 9.6 — **all three must be supported simultaneously**
- **Cache**: Redis (go-redis) + in-memory cache
- **Auth**: JWT, WebAuthn/Passkeys, OAuth (GitHub, Discord, LinuxDo, OIDC, WeChat, Telegram)
- **Frontend package manager**: Bun (preferred over npm/yarn/pnpm)

## Common Commands

### Full-stack dev (Makefile)

```bash
make all                       # builds frontend + starts backend dev server
make build-frontend-classic    # bun install + bun run build in web/classic/
make build-frontend            # bun install + bun run build in web/default/ (upstream default; we don't ship it)
make build-all-frontends       # both
make start-backend             # go run main.go
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

### Frontend (`web/classic/`)

This is the only frontend TraceNex actually ships. Theme is locked to `"classic"` in `setting/system_setting/theme.go`.

```bash
cd web/classic
bun install
bun run dev          # vite dev server
bun run build        # production build
bun run lint         # prettier check
bun run lint:fix     # prettier write
bun run eslint       # eslint with cache
bun run eslint:fix   # eslint --fix

# i18n tooling (run from web/classic/)
bun run i18n:extract
bun run i18n:status
bun run i18n:sync
bun run i18n:lint
```

### Server-side deploy / operations (Fabric)

Use the root `fabfile.py` from the local repo. Python version is pinned via `.python-version` (pyenv), so `fab` works directly in the project directory.

```bash
fab info --target=cn
fab status --target=cn
fab logs --target=cn --tail=200
fab release --target=cn --tag=v0.9.8 --ref=origin/main

fab info --target=sg
fab status --target=sg
fab logs --target=sg --tail=200
fab deploy --target=sg --tag=sg-5d733d85

fab preflight --target=legacy

fab release --target=test --tag=1.2.3-tracenex --ref=origin/main
```

Known Fabric targets:

| target | Purpose | SSH | Notes |
|--------|---------|-----|-------|
| `cn` | Hangzhou production | `root@8.136.146.211:58422` via `~/.ssh/tracenex_XN.pem` | Builds from `/root/Fy-api`, runtime config in `/opt/fy-api/config/fy-api.env` |
| `sg` | Singapore production | `root@47.236.133.70:58422` via `~/.ssh/AI_tracenex.pem` | Public URL `https://api.aitracenex.com`; ACR namespace `ai_transnext`; active blue/green behind Nginx |
| `test` | Chengdu test env | `root@8.156.88.148:58422` via default SSH key/agent | Local build + deploy (no ACR); nginx at `/etc/nginx/conf.d/tracenex-test.conf`; domains `*-test.tracenex.cn` |
| `legacy` | Legacy source server | `root@8.222.175.17` via default SSH key/agent | Contains old `/root/TraceNex` deployment and local MySQL source data |

Fabric `release` does: server git fetch/checkout -> `git archive` to `/tmp/fy-api-build` -> server Podman build -> ACR push -> `scripts/prod/06-deploy-blue-green.sh`. For SG, current deployed image tag is `sg-5d733d85`.

### Migration context

The SG RDS database `transnext_db` was initialized from the legacy server's self-hosted MySQL on 2026-05-07:

- Source host: `legacy` / `8.222.175.17`
- Source DBs: `tracenex` plus `tracenex_log.logs`
- Target: SG RDS `transnext_db`
- Pre-migration SG backup: `/opt/fy-api/backup/transnext_db-before-legacy-migration-20260507-231343.sql.gz` on the SG server
- After importing the legacy schema/data, SG Fy-api was restarted to run AutoMigrate and recreate new upstream tables.

On the legacy host, MySQL is system-installed (`mysql.service`). Operational access is available via `/etc/mysql/debian.cnf`; application DSNs are in `/root/TraceNex/.env`. Do not print database passwords in logs or chat.

### Upstream sync

```bash
git fetch upstream
git rev-list --count HEAD..upstream/main     # drift count
git log HEAD..upstream/main --oneline | head # what's new upstream
```

Then follow [`docs/Weekly-upstream-sync-runbook.md`](./docs/Weekly-upstream-sync-runbook.md). There are two GitHub Actions that automate this: `.github/workflows/upstream-watch.yml` (weekly drift check, runs every Monday) and `.github/workflows/upstream-sync.yml` (manual trigger that auto-merges and opens a PR).

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
pkg/           Internal packages (cachex/ for hybrid cache, ionet/, perf_metrics/, billingexpr/).
web/           Frontend themes container.
  web/classic/   ★ Active TraceNex frontend (React 18 + Vite + Semi UI). All overlay edits live here.
                 Built into web/classic/dist; theme=classic in common.GetTheme() returns this.
  web/default/   Upstream v1.0 frontend (React 19 + Rsbuild + Base UI + Tailwind). Built but not selectable in TraceNex.
  web/classic/src/i18n/locales/   zh-CN, zh-TW, zh, en, fr, ja, ru, vi JSON (flat, Chinese keys).
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

### Frontend (`web/classic/src/i18n/`)
- Library: `i18next` + `react-i18next` + `i18next-browser-languagedetector`
- Languages: zh-CN (fallback), zh-TW, zh, en, fr, ru, ja, vi
- Translation files are flat JSON under `web/classic/src/i18n/locales/{lang}.json`, wrapped under a `translation` key; **keys are Chinese source strings**
- Usage: `const { t } = useTranslation(); t('中文 key')`
- Semi UI locale synced via `SemiLocaleWrapper`
- TraceNex brand rebrand (`New API` → `TraceNex`) is re-applied automatically by the upstream-sync CI; do not bake brand words into keys

> The upstream v1.0 default frontend at `web/default/src/i18n/` uses **English** source strings as keys instead. We do not ship it, but if/when we ever switch to B-route this is a behavioral difference to remember.

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

Use `bun` as the package manager and script runner for the active frontend at `web/classic/`. Same applies for `web/default/` if you ever need to rebuild it for upstream parity (we ship that dist but don't select it).

### Rule 4: New Channel StreamOptions Support

When implementing a new channel:
- Confirm whether the provider supports `StreamOptions`
- If supported, add the channel to `streamSupportedChannels`

### Rule 5: Upstream Attribution — Preserve Apache 2.0 Compliance

TraceNex is a downstream fork of **new-api** (`github.com/QuantumNous/new-api`, AGPLv3). The following **upstream attribution** MUST be preserved:

- `LICENSE` file — keep as-is
- `NOTICE` file (if present) — keep all upstream notices intact
- Original copyright headers inside source files referencing new-api / QuantumNous — keep intact
- Go module path `github.com/QuantumNous/new-api` — **never rename** (would break merge-ability with upstream)
- Docker image labels / LICENSE references / README sections attributing upstream

The following are **downstream customizations for TraceNex** and MAY be changed:

- `common.SystemName` (user-facing brand name)
- Footer / Header brand text
- i18n locale files (brand words only — CI re-applies `New API` → `TraceNex`)
- `web/classic/public/new_logo.png` and favicon
- README additions describing TraceNex-specific features
- `package.json` name field in `electron/` and `web/classic/`

When in doubt, preserve both sides rather than picking one.

### Rule 6: Upstream Relay Request DTOs — Preserve Explicit Zero Values

For request structs that are parsed from client JSON and then re-marshaled to upstream providers (relay/convert paths):

- Optional scalar fields MUST use pointer types with `omitempty` (e.g. `*int`, `*uint`, `*float64`, `*bool`), not non-pointer scalars
- Semantics:
  - field absent in client JSON → `nil` → omitted on marshal
  - field explicitly set to zero/false → non-`nil` pointer → must still be sent upstream
- Avoid non-pointer scalars with `omitempty` for optional request parameters; zero values (`0`, `0.0`, `false`) will be silently dropped during marshal

## TraceNex Customization Strategy

When adding new functionality:

1. **Prefer new files over edits to upstream files.** Example: CSV log export lives in `controller/log_export.go` + `model/log_export.go` + `web/classic/src/components/table/usage-logs/UsageLogsExportButton.jsx`, not as edits to existing upstream files. This keeps future upstream merges conflict-free.
2. **When an upstream file must be touched**, tag the change with `// Fy-api overlay:` (Go) or `{/* Fy-api overlay: */}` (JSX) comments so it's findable during merges.
3. **Update `OVERLAY.md`** in the same commit. If a customization isn't listed there, it is considered drift and may be lost on the next upstream sync.
4. **Don't introduce brand words in i18n keys.** The rebrand runs as a value-side `gsub("New API", "TraceNex")` after each sync.
5. **Frontend overlays target `web/classic/`** — never `web/default/`. The default frontend ships built but unselected; investing in default-side overlays would be path-B work that we have explicitly deferred.

## Documentation Index

TraceNex-specific operational docs live under [`docs/`](./docs/):

- `Phase3-DB-migration-runbook.md` — zero-downtime DB migration (from older deployments)
- `Phase4-Build-runbook.md` — build-from-source + dependency upgrade notes
- `Phase5-Regression-checklist.md` — post-deploy regression list
- `Weekly-upstream-sync-runbook.md` — weekly upstream merge flow + on-demand release decision tree
- `Bug分析-Gemini缓存命中未计费.md` — post-mortem reference for cache-token billing (already fixed upstream)

For gateway features themselves (endpoints, billing formulas, provider quirks) see the upstream docs at <https://docs.newapi.pro>.

## Channel benchmarking toolkit

Everything channel-quality-related lives under [`scripts/channel-benchmark/`](./scripts/channel-benchmark/). Two ecosystems, stacked not overlapping:

| Tool | Lang | What it answers |
|---|---|---|
| `go/` (single binary) | Go | "Are the channels alive? What's TTFT?" — zero-dep, drop-on-prod. `-prom-listen :9090` turns it into a Prometheus exporter. |
| `py/fy-loadtest` | Python | "Will this channel survive N concurrent?" — full E2E/TTFT/ITL/TPOT percentiles. |
| `py/fy-quality` | Python | "Is this channel answering correctly?" — 7 graders, dual-judge rubric, disk-cached generations. |
| `py/fy-canary` | Python | "Did this channel silently get swapped to a cheaper model?" — baseline + audit + `verify-baseline`; alignment / embedding-drift / MMD probes. |

Operational conventions:

- **Real traffic, real billing.** Every tool uses a regular `sk-...` user token and consumes real quota. The user's quota IS the budget cap.
- **Explicit model lists.** No tool has a magic default; config must spell out which models to test.
- **Contamination defense in `fy-quality`.** Golden prompts live in `py/fy_quality/datasets/private/` (gitignored); the public `quality.jsonl` is assumed-memorized and exists only for wiring smoke tests. Per-row `seed` + `perturbations` perturb the text on the wire.
- **Baseline health in `fy-canary`.** Baseline files carry v2 metadata; `audit` refuses stale baselines (> `baseline_max_age_days`, default 30) unless `--ignore-stale-baseline`. Use `fy-canary verify-baseline` to re-query the vendor and detect baseline-side drift.
- **Prometheus integration.** The Go tool's daemon mode emits `channel_benchmark_*` series (request_total / success_rate / e2e_seconds / ttft_seconds / tokens_per_sec / run_age_seconds / consecutive_runs_ok). Dashboards / alert rules live in that directory's `README.md`.

See `scripts/channel-benchmark/README.md` for the top-level navigation and per-tool READMEs for details.

### Rule 7: Billing Expression System — Read `pkg/billingexpr/expr.md`

When working on tiered/dynamic billing (expression-based pricing), you MUST read `pkg/billingexpr/expr.md` first. It documents the design philosophy, expression language (variables, functions, examples), full system architecture (editor → storage → pre-consume → settlement → log display), token normalization rules (`p`/`c` auto-exclusion), quota conversion, and expression versioning. All code changes to the billing expression system must follow the patterns described in that document.

### Rule 8: Versioning, Branches, and Docker Tags

Use the TraceNex release version format `x.x.x-tracenex` for shipped builds and Docker images.

**Version numbering:**
- Version numbers start at 1, not 0. The first release line starts at `1.1.1-tracenex`
- The middle number is the weekly release train. Each new week increments it and resets the patch number to 1: week 1 → `1.1.1-tracenex`, week 2 → `1.2.1-tracenex`, week 3 → `1.3.1-tracenex`
- The last number increments for every release within the same week, including bugfix releases. Example: if week 2 ships three times, the third build is `1.2.3-tracenex`
- Do not reuse a released version tag. If an image or release has been pushed, the next shipped build must increment the patch number

**Branching rules:**
- New feature → branch named `feature/xxxx`
- Bugfix → branch named `bugfix/xxxx`
- Keep `xxxx` short, lowercase, and descriptive; prefer hyphen-separated names, e.g. `feature/mns-refund-wiring` or `bugfix/idempotency-replay`

**Docker image tagging:**
- Every Docker image build intended for testing, staging, or production must have an explicit version tag
- Release images must use the exact TraceNex version tag, e.g. `1.2.3-tracenex`
- Do not push only `latest`. `latest` may be added as an extra convenience tag, but never as the only tag

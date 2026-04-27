<div align="center">

# TraceNex

🍥 **AI Gateway & Asset Management Platform — downstream fork of [new-api](https://github.com/QuantumNous/new-api)**

<p align="center">
  <a href="./README.zh_CN.md">简体中文</a> |
  <strong>English</strong> |
  <a href="./README.zh_TW.md">繁體中文 (upstream)</a> |
  <a href="./README.fr.md">Français (upstream)</a> |
  <a href="./README.ja.md">日本語 (upstream)</a>
</p>

<p align="center">
  <a href="./LICENSE">
    <img src="https://img.shields.io/badge/license-AGPL--3.0-brightgreen" alt="license">
  </a><!--
  --><a href="https://github.com/seraph0017/Fy-api/commits/main">
    <img src="https://img.shields.io/github/last-commit/seraph0017/Fy-api?color=brightgreen" alt="last commit">
  </a><!--
  --><a href="https://github.com/QuantumNous/new-api">
    <img src="https://img.shields.io/badge/upstream-QuantumNous%2Fnew--api-blue" alt="upstream">
  </a>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-what-fy-api-adds-on-top-of-new-api">What TraceNex Adds</a> •
  <a href="#-deployment">Deployment</a> •
  <a href="#-upstream-sync">Upstream Sync</a> •
  <a href="#-license--attribution">License</a>
</p>

</div>

## 📝 About TraceNex

TraceNex is a private-branded **downstream fork of [QuantumNous/new-api](https://github.com/QuantumNous/new-api)** with a small overlay of customizations on top. It preserves everything upstream offers (40+ LLM providers, unified API gateway, quota/billing, admin dashboard, Subscription, Channel Affinity, Gemini cached-token billing, etc.) and adds a handful of operator-friendly features.

> [!NOTE]
> - TraceNex tracks `upstream/main` on a monthly cadence — every upstream improvement (new model adapters, bug fixes, schema migrations) flows into TraceNex via the automated [upstream-sync workflow](./.github/workflows/upstream-sync.yml).
> - The Go module path stays `github.com/QuantumNous/new-api` so that merging upstream patches does not require rewriting thousands of imports.
> - Upstream attribution (LICENSE, copyright headers) is preserved per Apache 2.0 compliance. See [`OVERLAY.md`](./OVERLAY.md) for the full list of TraceNex-specific changes.

---

## ✨ What TraceNex Adds on Top of new-api

All of these are **additive** — `upstream/main` still works exactly the same through TraceNex.

| # | Feature | Surface | Status |
|---|---------|---------|:------:|
| 1 | **CSV log export** | `GET /api/log/export` (admin) + `GET /api/log/self/export` (user). UTF-8 + BOM, includes `request_id` column. | ✅ |
| 2 | **Export button on the Usage Logs page** | One-click CSV download that respects the active filter and up to `MaxLogExportItems=50000` rows. | ✅ |
| 3 | **Embedded product docs at `/docs`** | Markdown-driven manual with 18 screenshots, rendered via a new `NewMarkdownRender` component. | ✅ |
| 4 | **Email/username login shown first** | Login form reordered so the primary affordance isn't hidden behind OAuth buttons. | ✅ |
| 5 | **Register link always visible** | Removed the `self_use_mode` gate on the "Sign up" link (operators can still disable registration via backend setting). | ✅ |
| 6 | **TraceNex branding** | `SystemName` → `TraceNex`, new logo + favicon, all 7 locales (zh-CN / zh-TW / en / fr / ja / ru / vi) rebranded. | ✅ |
| 7 | **Upstream sync CI** | Two GitHub Actions — a weekly watch that warns when drift exceeds 100 commits, and a manual sync that opens a conflict-ready PR. | ✅ |

> For the full source-level diff see [`OVERLAY.md`](./OVERLAY.md). For upgrade planning and runbooks see [`docs/`](./docs/).

---

## 🚀 Quick Start

### Using Docker Compose (recommended)

```bash
# Clone TraceNex (not upstream)
git clone git@github.com:seraph0017/Fy-api.git
cd TraceNex

# Edit docker-compose.yml as needed
nano docker-compose.yml

# Start
docker-compose up -d
```

Visit <http://localhost:3000>. Default admin credentials follow the upstream convention (see upstream docs below).

### Building from source

```bash
# Backend
go mod tidy
go build -o bin/fy-api

# Frontend (bun is the preferred toolchain upstream — see CLAUDE.md Rule 3)
cd web
bun install
bun run build
```

See [`docs/Phase4-Build-runbook.md`](./docs/Phase4-Build-runbook.md) for a full pre-release build checklist.

---

## 🚢 Deployment

TraceNex is a drop-in replacement for a new-api deployment: same ports, same environment variables, same SQL schema (plus the upstream's ongoing migrations, which are auto-applied via GORM `AutoMigrate`).

### Requirements

| Component | Requirement |
|-----------|-------------|
| **Local database** | SQLite (mount `/data`) |
| **Remote database** | MySQL ≥ 5.7.8 or PostgreSQL ≥ 9.6 |
| **Container engine** | Docker / Docker Compose |

### Migrating from TraceNex or older new-api

If you are upgrading an existing deployment (e.g. TraceNex) **do the DB migration dry-run first** — upstream adds ~8 new tables (`checkins`, `subscription_*`, `custom_oauth_providers`, `user_oauth_bindings`, …) and extends several existing ones.

Follow [`docs/Phase3-DB-migration-runbook.md`](./docs/Phase3-DB-migration-runbook.md) for:

- Preparing a sanitized copy of the production DB
- Online DDL recipes for large `logs` tables (MySQL `ALGORITHM=INPLACE, LOCK=NONE`, PostgreSQL `CREATE INDEX CONCURRENTLY`)
- A SQL template for migrating legacy OAuth bindings into the new `user_oauth_bindings` table
- Smoke-test checklist
- Hot-rollback procedure

### Environment variables

TraceNex inherits the full upstream environment variable set. See the [upstream docs](https://docs.newapi.pro/en/docs/installation/config-maintenance/environment-variables) for the canonical list.

TraceNex adds:

| Variable | Description | Default |
|----------|-------------|---------|
| `MaxLogExportItems` *(Go const, not env)* | Max rows returned by `/api/log/export` | `50000` |

---

## 🔄 Upstream Sync

TraceNex is designed to stay close to upstream rather than drift. The philosophy:

1. **Additive customizations only** — customizations live in overlay files (e.g. `controller/log_export.go`, `web/src/pages/FyApiDocs/`) to minimize merge conflicts.
2. **Monthly sync cadence** — conflict cost grows exponentially with drift (~1 month → 0-2 conflicts; ~6 months → 20+). See [`docs/Monthly-upstream-sync-runbook.md`](./docs/Monthly-upstream-sync-runbook.md).
3. **Automated watch** — `.github/workflows/upstream-watch.yml` runs every Monday and warns when TraceNex is > 100 commits behind; fails hard at > 500.
4. **One-click sync PR** — `.github/workflows/upstream-sync.yml` (manual trigger) merges `upstream/main`, re-applies the i18n rebrand, and opens a PR.

```bash
# Check drift locally
git fetch upstream
git rev-list --count HEAD..upstream/main

# See what's new
git log HEAD..upstream/main --oneline | head -30
```

### What customizations survive a sync

See [`OVERLAY.md`](./OVERLAY.md). Briefly:

- **Zero-conflict** (new files): CSV export backend + frontend, FyApiDocs page, Markdown renderer, GitHub Actions, OVERLAY.md itself
- **Low-conflict** (small inline markers `// Fy-api overlay:`): `common/constants.go` `SystemName`, `web/index.html` title, `web/src/App.jsx` route wiring, `LoginForm.jsx` reordering
- **Automatable** (CI re-runs the transformation): i18n rebrand

---

## 📚 Upstream Documentation

For everything the gateway itself does (channels, relay protocols, billing formulas, admin settings, API reference, etc.) refer to the **upstream documentation** — nothing there has been removed or re-pointed:

- 📘 [new-api Official Docs](https://docs.newapi.pro/en/docs)
- 🧪 [DeepWiki](https://deepwiki.com/QuantumNous/new-api)
- 🚀 [Installation Guide](https://docs.newapi.pro/en/docs/installation)
- ⚙️ [Environment Variables](https://docs.newapi.pro/en/docs/installation/config-maintenance/environment-variables)
- 📡 [API Reference](https://docs.newapi.pro/en/docs/api)
- ❓ [FAQ](https://docs.newapi.pro/en/docs/support/faq)

For TraceNex-specific documentation see [`docs/`](./docs/):

| File | Purpose |
|------|---------|
| `docs/Phase3-DB-migration-runbook.md` | Zero-downtime DB migration for existing TraceNex deployments |
| `docs/Phase4-Build-runbook.md` | Build-from-source recipe including dep upgrades |
| `docs/Phase5-Regression-checklist.md` | Post-deploy regression test list |
| `docs/Monthly-upstream-sync-runbook.md` | Standard monthly upstream merge flow |
| `docs/Bug分析-Gemini缓存命中未计费.md` | Post-mortem on a Gemini cached-token billing bug (already fixed upstream) |

---

## 📜 License & Attribution

TraceNex is distributed under the [GNU Affero General Public License v3.0 (AGPLv3)](./LICENSE), inheriting the upstream license.

**Upstream:** [QuantumNous/new-api](https://github.com/QuantumNous/new-api) — AGPLv3
**Original base:** [songquanpeng/one-api](https://github.com/songquanpeng/one-api) — MIT

TraceNex preserves all upstream copyright notices, the LICENSE file, and the Go module path `github.com/QuantumNous/new-api`. See [`OVERLAY.md`](./OVERLAY.md) for the exact scope of downstream modifications.

For commercial licensing of the upstream project, contact the upstream maintainers at [support@quantumnous.com](mailto:support@quantumnous.com). TraceNex itself is an internal deployment and does not offer separate commercial licensing.

---

## 🙏 Acknowledgements

Huge thanks to:

- **[QuantumNous](https://github.com/QuantumNous)** and all new-api contributors — TraceNex is 99% their work
- **[songquanpeng](https://github.com/songquanpeng)** for the original One API foundation
- **[JetBrains](https://www.jetbrains.com/?from=new-api)** for providing free open-source development licenses to the upstream project

---

<div align="center">

### 💖 Thanks for using TraceNex

<sub>A small overlay on the shoulders of <a href="https://github.com/QuantumNous/new-api">new-api</a>.</sub>

</div>

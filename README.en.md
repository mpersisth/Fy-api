<div align="center">

# TraceNex

🍥 **AI Gateway & Asset Management Platform — downstream fork of [new-api](https://github.com/QuantumNous/new-api)**

<p align="center">
  <a href="./README.md">简体中文</a> |
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
  <a href="#-what-tracenex-adds-on-top-of-new-api">What TraceNex Adds</a> •
  <a href="#-production-deployment">Production Deployment</a> •
  <a href="#-upstream-sync">Upstream Sync</a> •
  <a href="#-license--attribution">License</a>
</p>

</div>

## 📝 About TraceNex

TraceNex is a private-branded **downstream fork of [QuantumNous/new-api](https://github.com/QuantumNous/new-api)** with a small overlay of customizations on top. It preserves everything upstream offers (40+ LLM providers, unified API gateway, quota/billing, admin dashboard, Subscription, Channel Affinity, Gemini cached-token billing, etc.) and adds a handful of operator-friendly features plus a full production deployment toolchain.

> [!NOTE]
> - **Repository identity**: the code and GitHub repo are named `Fy-api` for continuity (remote: `github.com/seraph0017/Fy-api`); **TraceNex is the product brand** displayed to end users (`SystemName = "TraceNex"`, UI, docs). The two names are deliberately distinct — see `CLAUDE.md` for the design decision.
> - TraceNex tracks `upstream/main` on a monthly cadence — every upstream improvement (new model adapters, bug fixes, schema migrations) flows into TraceNex via the automated [upstream-sync workflow](./.github/workflows/upstream-sync.yml).
> - The Go module path stays `github.com/QuantumNous/new-api` so that merging upstream patches does not require rewriting thousands of imports.
> - Upstream attribution (LICENSE, copyright headers) is preserved per AGPLv3 compliance. See [`OVERLAY.md`](./OVERLAY.md) for the full list of TraceNex-specific changes.

---

## ✨ What TraceNex Adds on Top of new-api

All of these are **additive** — `upstream/main` still works exactly the same through TraceNex.

### Product layer

| # | Feature | Surface | Status |
|---|---------|---------|:------:|
| 1 | **CSV log export** | `GET /api/log/export` (admin) + `GET /api/log/self/export` (user). UTF-8 + BOM, includes `request_id` column. | ✅ |
| 2 | **Export button on the Usage Logs page** | One-click CSV download that respects the active filter and up to `MaxLogExportItems=50000` rows. | ✅ |
| 3 | **Embedded product docs at `/docs`** | Markdown-driven manual with screenshots, rendered via a `NewMarkdownRender` component. | ✅ |
| 4 | **Email/username login shown first** | Login form reordered so the primary affordance isn't hidden behind OAuth buttons. | ✅ |
| 5 | **Register link always visible** | Removed the `self_use_mode` gate on the "Sign up" link (operators can still disable registration via backend setting). | ✅ |
| 6 | **TraceNex branding** | `SystemName` → `TraceNex`, logo + favicon + HTML title, all 7 locales (zh-CN / zh-TW / en / fr / ja / ru / vi) rebranded. | ✅ |

### Platform layer

| # | Feature | Surface | Status |
|---|---------|---------|:------:|
| 7 | **Upstream sync CI** | Two GitHub Actions — a weekly watch that warns when drift exceeds 100 commits, and a manual sync that opens a conflict-ready PR and re-applies the brand rewrite. | ✅ |
| 8 | **Production deployment toolkit** | Seven idempotent scripts under [`scripts/prod/`](./scripts/prod/) that take a fresh Aliyun ECS from zero to a blue/green, HTTPS-terminated, log-shipped, rate-limited production in ~30 minutes. | ✅ |
| 9 | **Blue-green deploy automation** | [`06-deploy-blue-green.sh`](./scripts/prod/06-deploy-blue-green.sh) — detect active color, pull new image, health check, swap Nginx upstream, drain, stop old. Zero-downtime. | ✅ |
| 10 | **Deployment runbooks** | Full coverage under [`docs/deploy/`](./docs/deploy/): ACK (Kubernetes), single-host Podman, observability (SLS + Prometheus), rate limiting, local dev. | ✅ |

> For the full source-level diff see [`OVERLAY.md`](./OVERLAY.md).

---

## 🚀 Quick Start

### Option 1 — Docker / Podman (testing)

```bash
git clone git@github.com:seraph0017/Fy-api.git
cd Fy-api

# Start with the dev compose file (SQLite + in-memory cache)
docker compose up -d
# or with podman
podman-compose up -d
```

Visit <http://localhost:3000>. Default admin credentials follow the upstream convention (first-user setup at `/api/setup`).

### Option 2 — Build from source

```bash
# Backend
go mod tidy
go build -o bin/tracenex

# Frontend (bun is the preferred toolchain — see CLAUDE.md Rule 3)
cd web
bun install
bun run build
```

The three-stage `Dockerfile` (bun → golang → debian) handles this automatically for container builds, so you don't need bun or Go installed on the host if you're going via `docker build` / `podman build`.

See [`docs/deploy/local-dev.md`](./docs/deploy/local-dev.md) for the full local development setup.

---

## 🚢 Production Deployment

TraceNex ships with a **production-grade deployment toolchain** validated on Alibaba Cloud single-host (ECS 16c32g) and Kubernetes (ACK). The single-host path is documented end-to-end; the Kubernetes path uses standard Helm-style values.

### Supported topologies

| Topology | Status | Guide |
|----------|:------:|-------|
| **Single-host Podman** (ECS / bare metal) | ✅ Production-proven | [`docs/deploy/prod-podman-single.md`](./docs/deploy/prod-podman-single.md) |
| **Aliyun ACK (Kubernetes)** | ✅ Documented | [`docs/deploy/prod-ack.md`](./docs/deploy/prod-ack.md) |
| **Local Podman (test)** | ✅ For QA | [`docs/deploy/test-podman.md`](./docs/deploy/test-podman.md) |

### One-shot setup on a fresh ECS

Copy [`scripts/prod/`](./scripts/prod/) to the server and run the scripts in order. Each is **idempotent** and **fails loudly on the first error**. Typical total time: **~30 minutes** including Let's Encrypt issuance.

```bash
# On your laptop
scp -r scripts/prod config/fy-api.env.example root@<ECS-IP>:/root/

# On the ECS (as root)
cd /root/prod

sudo ./01-setup-system.sh                     # kernel params, ulimits, podman, nginx, firewall
./02-install-logtail.sh                       # Aliyun SLS log agent
sudo DOMAIN=api.example.com EMAIL=... \
  ./03-setup-nginx.sh                         # Nginx + Let's Encrypt (HTTPS)
#  optional: ./03b-add-redirect-domain.sh     # www → api 301 redirect
#  optional: ./03c-add-alias-domain.sh        # www as a parallel alias
IMAGE_TAG=v0.9.6 ./04-deploy-fyapi.sh         # first deploy of the blue container
./05-enable-rate-limit.sh                     # turn on model-request rate-limit + group quotas
sudo ./07-setup-logrotate.sh                  # log rotation for nginx + container logs

# Every subsequent release (zero-downtime)
./06-deploy-blue-green.sh v0.9.7
```

See [`scripts/prod/README.md`](./scripts/prod/README.md) for the full checklist, prerequisites, and rollback procedure.

### Blue-green deploy at a glance

[`06-deploy-blue-green.sh`](./scripts/prod/06-deploy-blue-green.sh) implements zero-downtime rollouts:

1. Detects the currently active color (`blue` @ 3001 or `green` @ 3002)
2. Pulls the new image from Aliyun ACR
3. Starts the standby container with the new image
4. Health-checks `/api/status` for up to 60 seconds
5. Rewrites Nginx upstream port → `nginx -t` → `systemctl reload nginx`
6. Sleeps 30 seconds to drain old connections
7. Stops (but does not remove) the old container — available for rollback

### Observability

- **Logs to disk** — `--log-dir=/app/logs` on `podman run`; rotated daily via `logrotate`
- **Logs to Aliyun SLS** — Logtail picks up both container stdout and disk logs, split across four logstores (`app`, `consume`, `nginx-access`, `nginx-error`)
- **Metrics** — Prometheus stack defined under [`docs/deploy/monitoring/`](./docs/deploy/monitoring/) (Prometheus + Alertmanager + Blackbox + Grafana datasources + 15 alert rules)

See [`docs/deploy/observability.md`](./docs/deploy/observability.md) for the full data path and dashboards.

### Rate limiting (hot-reloadable)

Per-user and per-group quotas (e.g. `default: 60/min`, `vip: 5000/min`) are set via the admin API and take effect immediately without restarting the container. See [`docs/deploy/rate-limiting.md`](./docs/deploy/rate-limiting.md) and [`05-enable-rate-limit.sh`](./scripts/prod/05-enable-rate-limit.sh).

### Production validation

A formal load test was performed on 2026-04-28 against the single-host Aliyun deployment (ECS 16c32g):

- **2,477 requests** across 5 prompt-length tiers (1K / 6K / 9K / 16K / 50K tokens)
- **32 concurrent workers** against `kimi-k2.5` via Moonshot
- **100% success rate** from the client, **0 5xx** on the server
- **Peak container resource**: CPU 6.5% / MEM 58MB — roughly **16× CPU headroom** on this node size
- Zero panics, zero DB errors, zero Redis pool timeouts

See [upstream deployment docs](./docs/deploy/) for full methodology and detail.

---

## 🔄 Upstream Sync

TraceNex is designed to stay close to upstream rather than drift. The philosophy:

1. **Additive customizations only** — customizations live in overlay files (e.g. `controller/log_export.go`, `web/src/pages/FyApiDocs/`) to minimize merge conflicts.
2. **Monthly sync cadence** — conflict cost grows exponentially with drift (~1 month → 0-2 conflicts; ~6 months → 20+).
3. **Automated watch** — `.github/workflows/upstream-watch.yml` runs every Monday and warns when TraceNex is > 100 commits behind; fails hard at > 500.
4. **One-click sync PR** — `.github/workflows/upstream-sync.yml` (manual trigger) merges `upstream/main`, re-applies the i18n rebrand (`New API` → `TraceNex`), and opens a PR.

```bash
# Check drift locally
git fetch upstream
git rev-list --count HEAD..upstream/main

# See what's new
git log HEAD..upstream/main --oneline | head -30
```

### What customizations survive a sync

See [`OVERLAY.md`](./OVERLAY.md). Briefly:

- **Zero-conflict** (new files): CSV export backend + frontend, FyApiDocs page, Markdown renderer, GitHub Actions, production scripts, deployment docs, OVERLAY.md itself
- **Low-conflict** (small inline markers `// Fy-api overlay:`): `common/constants.go` `SystemName`, `web/index.html` title, `web/src/App.jsx` route wiring, `LoginForm.jsx` reordering
- **Automatable** (CI re-runs the transformation): i18n rebrand via the upstream-sync workflow

---

## 📚 Upstream Documentation

For everything the gateway itself does (channels, relay protocols, billing formulas, admin settings, API reference, etc.) refer to the **upstream documentation** — nothing there has been removed or re-pointed:

- 📘 [new-api Official Docs](https://docs.newapi.pro/en/docs)
- 🧪 [DeepWiki](https://deepwiki.com/QuantumNous/new-api)
- 🚀 [Installation Guide](https://docs.newapi.pro/en/docs/installation)
- ⚙️ [Environment Variables](https://docs.newapi.pro/en/docs/installation/config-maintenance/environment-variables)
- 📡 [API Reference](https://docs.newapi.pro/en/docs/api)
- ❓ [FAQ](https://docs.newapi.pro/en/docs/support/faq)

### TraceNex-specific docs

| File | Purpose |
|------|---------|
| [`OVERLAY.md`](./OVERLAY.md) | Source-of-truth list of every TraceNex customization vs upstream |
| [`CLAUDE.md`](./CLAUDE.md) | Architecture overview + Rules for AI-assisted development |
| [`scripts/prod/README.md`](./scripts/prod/README.md) | Production deployment scripts overview |
| [`docs/deploy/prod-podman-single.md`](./docs/deploy/prod-podman-single.md) | Full single-host production runbook |
| [`docs/deploy/prod-ack.md`](./docs/deploy/prod-ack.md) | Kubernetes (Aliyun ACK) deployment |
| [`docs/deploy/observability.md`](./docs/deploy/observability.md) | Logs, metrics, alerts, dashboards |
| [`docs/deploy/rate-limiting.md`](./docs/deploy/rate-limiting.md) | Per-user and per-group quota configuration |
| [`docs/deploy/local-dev.md`](./docs/deploy/local-dev.md) | Local development setup |
| [`docs/deploy/test-podman.md`](./docs/deploy/test-podman.md) | QA/staging Podman setup |

Cross-project analysis, DB migration runbooks, and historical post-mortems (spanning multiple sibling projects such as legacy forks and pure upstream) live under `~/Projects/apiGateway/docs/` in the workspace parent directory, outside this repo.

---

## 📜 License & Attribution

TraceNex is distributed under the [GNU Affero General Public License v3.0 (AGPLv3)](./LICENSE), inheriting the upstream license.

- **Upstream**: [QuantumNous/new-api](https://github.com/QuantumNous/new-api) — AGPLv3
- **Original base**: [songquanpeng/one-api](https://github.com/songquanpeng/one-api) — MIT

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

<div align="center">

# TraceNex

🍥 **AI 网关与资产管理平台 — [new-api](https://github.com/QuantumNous/new-api) 的下游 fork**

<p align="center">
  <strong>简体中文</strong> |
  <a href="./README.en.md">English</a> |
  <a href="./README.zh_TW.md">繁體中文（上游）</a> |
  <a href="./README.fr.md">Français（上游）</a> |
  <a href="./README.ja.md">日本語（上游）</a>
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
  <a href="#-快速开始">快速开始</a> •
  <a href="#-tracenex-在-new-api-之上增加了什么">TraceNex 增量</a> •
  <a href="#-生产环境部署">生产环境部署</a> •
  <a href="#-渠道基准测试channel-benchmark">渠道基准测试</a> •
  <a href="#-上游同步">上游同步</a> •
  <a href="#-许可证与归属">许可证</a>
</p>

</div>

## 📝 关于 TraceNex

TraceNex 是 **[QuantumNous/new-api](https://github.com/QuantumNous/new-api) 的私有品牌化 fork**，在上游之上叠加了一层小而精的定制。上游提供的一切——40+ LLM 供应商适配、统一 API 网关、额度/计费、管理后台、Subscription、Channel Affinity、Gemini 缓存命中计费——在 TraceNex 里都**完整保留**，并额外增加了几项面向运营人员的功能，以及一整套可直接投产的部署工具链。

> [!NOTE]
> - **仓库身份**：代码和 GitHub 仓库名沿用 `Fy-api`（远端：`github.com/seraph0017/Fy-api`）以保持连续性；**TraceNex 是对外的产品品牌**（`SystemName = "TraceNex"`，体现在 UI、文档、用户界面）。两个名字是故意区分开的——设计原因见 `CLAUDE.md`。
> - TraceNex 以**月度节奏**从 `upstream/main` 拉取新功能：每一个上游改进（新模型适配、bug 修复、schema 迁移）都会通过 [upstream-sync workflow](./.github/workflows/upstream-sync.yml) 自动流入 TraceNex。
> - Go module path 保持为 `github.com/QuantumNous/new-api`，这样合并上游补丁时不需要重写数千个 import。
> - 严格遵守 AGPLv3 合规：LICENSE、版权头、上游 attribution **完整保留**。TraceNex 专属改动的详细清单见 [`OVERLAY.md`](./OVERLAY.md)。

---

## ✨ TraceNex 在 new-api 之上增加了什么

以下全部是**增量**——upstream 的所有能力在 TraceNex 中依然完整可用。

### 产品层

| # | 功能 | 位置 | 状态 |
|---|------|------|:----:|
| 1 | **日志 CSV 导出** | `GET /api/log/export`（管理员）+ `GET /api/log/self/export`（用户）。UTF-8 + BOM，包含 `request_id` 列。 | ✅ |
| 2 | **用量日志页的「导出」按钮** | 一键下载当前筛选条件下的 CSV，上限 `MaxLogExportItems=50000` 行。 | ✅ |
| 3 | **`/docs` 内嵌产品文档** | 基于 Markdown 的用户手册（附截图），用新的 `NewMarkdownRender` 组件渲染。 | ✅ |
| 4 | **邮箱/用户名登录按钮前置** | 登录表单重新排序，主要入口不再被 OAuth 按钮遮住。 | ✅ |
| 5 | **「没有账户？注册」 始终显示** | 去掉了 `self_use_mode` 条件限制（如需禁用注册，仍可在后台配置）。 | ✅ |
| 6 | **TraceNex 品牌化** | `SystemName` → `TraceNex`，新 logo / favicon / 浏览器 title，7 种语言（zh-CN / zh-TW / en / fr / ja / ru / vi）品牌词统一。 | ✅ |

### 平台层

| # | 功能 | 位置 | 状态 |
|---|------|------|:----:|
| 7 | **上游同步 CI** | 两个 GitHub Actions：每周一检测积压 > 100 commits 告警；手动触发的 sync 自动合并、重跑品牌替换、开 PR。 | ✅ |
| 8 | **生产部署工具链** | [`scripts/prod/`](./scripts/prod/) 下 7 个幂等脚本，从裸 ECS 到完整蓝绿 + HTTPS + 日志接入 + 限流的生产环境，**~30 分钟完成**。 | ✅ |
| 9 | **蓝绿发版自动化** | [`06-deploy-blue-green.sh`](./scripts/prod/06-deploy-blue-green.sh) —— 自动检测活跃色、拉新镜像、健康检查、切 Nginx upstream、连接排空、停旧容器。**零中断**。 | ✅ |
| 10 | **部署 runbook 完整覆盖** | [`docs/deploy/`](./docs/deploy/) 下涵盖 ACK（Kubernetes）、单机 Podman、可观测性（SLS + Prometheus）、限流、本地开发。 | ✅ |
| 11 | **渠道基准测试工具链** | [`scripts/channel-benchmark/`](./scripts/channel-benchmark/) —— Go 烟测器（零依赖，内置 Prometheus 导出器）+ 三件套 Python 工具（`fy-loadtest` 并发压测 / `fy-quality` 双裁判质量评分 / `fy-canary` 模型替换检测），47 + 7 个测试覆盖。 | ✅ |

> 完整源码级改动清单见 [`OVERLAY.md`](./OVERLAY.md)。

---

## 🚀 快速开始

### 方式 1 — Docker / Podman（测试环境）

```bash
git clone git@github.com:seraph0017/Fy-api.git
cd Fy-api

# 开发环境 compose（SQLite + 内存 cache）
docker compose up -d
# 或用 podman
podman-compose up -d
```

访问 <http://localhost:3000>。默认管理员账号通过首次访问 `/api/setup` 创建，沿用上游约定。

### 方式 2 — 从源码构建

```bash
# 后端
go mod tidy
go build -o bin/tracenex

# 前端（CLAUDE.md Rule 3 约定使用 bun）
cd web
bun install
bun run build
```

仓库根目录的三阶段 `Dockerfile`（bun → golang → debian）会自动处理这些步骤，所以如果你用 `docker build` / `podman build`，**宿主机无需安装 bun 或 Go**。

本地开发完整指引见 [`docs/deploy/local-dev.md`](./docs/deploy/local-dev.md)。

---

## 🚢 生产环境部署

TraceNex 附带**经过生产验证的部署工具链**，在阿里云单机（ECS 16c32g）和 Kubernetes（ACK）两种拓扑上都有完整 runbook。单机部署有端到端验证；Kubernetes 侧使用标准 Helm-style values。

### 支持的拓扑

| 拓扑 | 状态 | 指南 |
|------|:----:|------|
| **单机 Podman**（阿里云 ECS / 物理机） | ✅ 生产验证通过 | [`docs/deploy/prod-podman-single.md`](./docs/deploy/prod-podman-single.md) |
| **阿里云 ACK（Kubernetes）** | ✅ 完整文档 | [`docs/deploy/prod-ack.md`](./docs/deploy/prod-ack.md) |
| **本地 Podman（测试）** | ✅ QA 用 | [`docs/deploy/test-podman.md`](./docs/deploy/test-podman.md) |

### 裸 ECS 一键建站

把 [`scripts/prod/`](./scripts/prod/) 整个目录拷到服务器，按编号顺序执行。每个脚本都是**幂等的**，且**第一时间失败并红字提示**。典型总耗时：**~30 分钟**（含 Let's Encrypt 签发）。

```bash
# 在本地
scp -r scripts/prod config/fy-api.env.example root@<ECS-IP>:/root/

# 在 ECS 上（root 执行）
cd /root/prod

sudo ./01-setup-system.sh                     # 内核参数、ulimit、podman、nginx、防火墙
./02-install-logtail.sh                       # 阿里云 SLS 日志 agent
sudo DOMAIN=api.example.com EMAIL=... \
  ./03-setup-nginx.sh                         # Nginx + Let's Encrypt (HTTPS)
#  可选: ./03b-add-redirect-domain.sh         # www → api 301 跳转
#  可选: ./03c-add-alias-domain.sh            # www 作为并列别名域
IMAGE_TAG=v0.9.6 ./04-deploy-fyapi.sh         # 首次起 blue 容器
./05-enable-rate-limit.sh                     # 打开模型请求限流 + 分组配额
sudo ./07-setup-logrotate.sh                  # nginx + 容器日志轮转

# 以后每次发版（零中断）
./06-deploy-blue-green.sh v0.9.7
```

完整清单、前置要求、回滚步骤见 [`scripts/prod/README.md`](./scripts/prod/README.md)。

### 蓝绿发版原理

[`06-deploy-blue-green.sh`](./scripts/prod/06-deploy-blue-green.sh) 实现零中断发版：

1. 检测当前活跃色（`blue` @ 3001 或 `green` @ 3002）
2. 从阿里云 ACR 拉新镜像
3. 启动备用容器
4. 健康检查 `/api/status`，最多 60 秒
5. 改写 Nginx upstream 端口 → `nginx -t` → `systemctl reload nginx`
6. 睡眠 30 秒等旧连接排空
7. 停旧容器（但保留,以便紧急回滚）

### 可观测性

- **日志落盘** —— 启动容器时带 `--log-dir=/app/logs`；`logrotate` 每日轮转
- **日志接入阿里云 SLS** —— Logtail 同时采集容器 stdout 和落盘日志，分为 4 个 logstore（`app`、`consume`、`nginx-access`、`nginx-error`）
- **指标监控** —— Prometheus 技术栈见 [`docs/deploy/monitoring/`](./docs/deploy/monitoring/)（Prometheus + Alertmanager + Blackbox + Grafana 数据源 + 15 条告警规则）

完整数据链路与看板见 [`docs/deploy/observability.md`](./docs/deploy/observability.md)。

### 限流配置（热更新）

按用户和按分组的配额（例如 `default: 60/min`、`vip: 5000/min`）通过管理后台 API 设置，**立即生效、无需重启容器**。详见 [`docs/deploy/rate-limiting.md`](./docs/deploy/rate-limiting.md) 和 [`05-enable-rate-limit.sh`](./scripts/prod/05-enable-rate-limit.sh)。

### 生产验收数据

2026-04-28 在阿里云单机（ECS 16c32g）上完成了一次正式压测：

- **2,477 个请求**，5 档 prompt 长度（1K / 6K / 9K / 16K / 50K tokens）
- **32 并发**，模型 `kimi-k2.5`（通过 Moonshot）
- **客户端 100% 成功率**，**服务端 0 个 5xx**
- **容器资源峰值**：CPU 6.5% / 内存 58MB —— **约 16 倍 CPU 余量**
- 0 panic、0 DB 错误、0 Redis 池超时

完整方法论和细节见 [部署文档目录](./docs/deploy/)。

---

## 📊 渠道基准测试（channel-benchmark）

生产上线的渠道**只有跑过一遍基线测量才算真的上线**。[`scripts/channel-benchmark/`](./scripts/channel-benchmark/) 是这套测量工具链，分两个层次：

### Go 烟测器（零依赖）

```bash
cd scripts/channel-benchmark/go
go run . -config channel-benchmark.yaml                           # 一次性跑
go run . -config channel-benchmark.yaml -prom-listen :9090 -prom-interval 5m   # 常驻，暴露 Prometheus /metrics
```

- 走真实 `/v1/chat/completions` 路径（不是那个只返回 `{success, time}` 的管理按钮），拿到 TTFT / ITL / usage / cached_tokens
- 线性插值分位数，和 NumPy / llmperf / genai-perf 一致
- `-prom-listen` 模式下长期驻留，暴露 `channel_benchmark_ttft_seconds{channel,model,quantile}`、`channel_benchmark_request_total{outcome=...}`、`channel_benchmark_run_age_seconds` 等序列，直接接 Grafana 告警
- `go.mod` 只依赖 `gopkg.in/yaml.v3`，可以直接 scp 到任何一台 Linux 跑

### Python 三件套

```bash
cd scripts/channel-benchmark/py
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
source .venv/bin/activate
```

| 工具 | 子命令 | 回答什么问题 |
|---|---|---|
| **fy-loadtest** | `fy-loadtest -c loadtest.yaml` | 这个渠道扛得住 N 并发吗？E2E / TTFT / ITL / TPOT 分位 + goodput-vs-SLO |
| **fy-quality** | `fy-quality -c quality.yaml` | 这个渠道答得对吗？7 种 grader（exact / regex / contains / json_schema / rubric / similarity / pairwise）+ 双裁判（默认 Claude Haiku + Gemini Flash）+ 磁盘缓存 |
| **fy-canary** | `fy-canary baseline / audit / verify-baseline` | 这个渠道被静默换模型了吗？先对可信 vendor 录 baseline，再周期审计网关；alignment + embedding drift + MMD 三种探针 |

两个特别值得提的能力：

- **数据集污染防御**（fy-quality）—— `datasets/public/` 是起手烟测用、assumed-memorized 的样本；真实题库放 `datasets/private/`（整个目录 gitignore）。每行 JSON 支持 `seed` + `perturbations: ["whitespace", "trailing_marker", "synonym"]`，在命中模型前做**确定性**扰动，让网线上的文本和仓库里的文本永远不完全一致，防止训练集污染把评测变成记忆测验
- **Baseline 健康检查**（fy-canary）—— baseline 文件带 `recorded_at_iso` / `n_probes` / `fy_canary_version` 元数据；`audit` 默认拒绝超过 `baseline_max_age_days`（30 天）的 baseline；新的 `verify-baseline` 子命令**再查一遍 vendor 直连**，检测 baseline 自身是否因为模型升级而漂移

全套 47 个 Python 测试 + 7 个 Go 测试（均 `-race`）通过；详见 [`scripts/channel-benchmark/README.md`](./scripts/channel-benchmark/README.md)。

---

## 🔄 上游同步

TraceNex 的设计原则是**紧跟上游而不是渐行渐远**。核心理念：

1. **只做增量定制** —— 定制代码尽量放到新增文件里（如 `controller/log_export.go`、`web/src/pages/FyApiDocs/`），降低合并冲突
2. **月度同步节奏** —— 冲突成本随 drift 时长呈**指数增长**（~1 个月 0-2 处冲突；~6 个月 20+ 处）
3. **自动监控** —— `.github/workflows/upstream-watch.yml` 每周一自动跑，积压 > 100 commits 时告警；> 500 直接 fail
4. **一键同步 PR** —— `.github/workflows/upstream-sync.yml`（手动触发）合并 `upstream/main`、重新应用 i18n 品牌替换（`New API` → `TraceNex`）、开 PR 等人工 review

```bash
# 本地查看 drift
git fetch upstream
git rev-list --count HEAD..upstream/main

# 看看新增了什么
git log HEAD..upstream/main --oneline | head -30
```

### 哪些定制能扛过一次 sync

详见 [`OVERLAY.md`](./OVERLAY.md)。简要分类：

- **零冲突**（新增文件）：CSV 导出后端 + 前端、FyApiDocs 页面、Markdown 渲染组件、GitHub Actions、生产脚本、部署文档、OVERLAY.md 本身
- **低冲突**（带 `// Fy-api overlay:` 标记的小改动）：`common/constants.go` 的 `SystemName`、`web/index.html` 的 title、`web/src/App.jsx` 路由注册、`LoginForm.jsx` 重排
- **可自动化**（CI 自动重新应用）：i18n 品牌替换，由 upstream-sync workflow 处理

---

## 📚 上游文档

网关本身的所有能力（通道、relay 协议、计费公式、管理后台、API 参考等）请参考**上游文档**——所有链接均未改动、未重定向：

- 📘 [new-api 官方文档](https://docs.newapi.pro/zh/docs)
- 🧪 [DeepWiki](https://deepwiki.com/QuantumNous/new-api)
- 🚀 [部署指南](https://docs.newapi.pro/zh/docs/installation)
- ⚙️ [环境变量](https://docs.newapi.pro/zh/docs/installation/config-maintenance/environment-variables)
- 📡 [API 参考](https://docs.newapi.pro/zh/docs/api)
- ❓ [常见问题](https://docs.newapi.pro/zh/docs/support/faq)

### TraceNex 专属文档

| 文件 | 用途 |
|------|------|
| [`OVERLAY.md`](./OVERLAY.md) | TraceNex 相对于上游所有定制的**权威清单** |
| [`CLAUDE.md`](./CLAUDE.md) | 架构概览 + AI 辅助开发的规则 |
| [`scripts/prod/README.md`](./scripts/prod/README.md) | 生产部署脚本总览 |
| [`docs/deploy/prod-podman-single.md`](./docs/deploy/prod-podman-single.md) | 单机生产部署完整 runbook |
| [`docs/deploy/prod-ack.md`](./docs/deploy/prod-ack.md) | Kubernetes（阿里云 ACK）部署 |
| [`docs/deploy/observability.md`](./docs/deploy/observability.md) | 日志、指标、告警、看板 |
| [`docs/deploy/rate-limiting.md`](./docs/deploy/rate-limiting.md) | 按用户/分组的限流配置详解 |
| [`docs/deploy/local-dev.md`](./docs/deploy/local-dev.md) | 本地开发环境搭建 |
| [`docs/deploy/test-podman.md`](./docs/deploy/test-podman.md) | QA/staging 的 Podman 部署 |

跨项目的对比分析、DB 迁移 runbook、历史 bug 复盘等文档（横跨多个 sibling 项目如历史 fork、纯上游副本）位于 workspace 父目录的 `~/Projects/apiGateway/docs/` 下，不在本仓库内部。

---

## 📜 许可证与归属

TraceNex 采用 [GNU Affero 通用公共许可证 v3.0（AGPLv3）](./LICENSE)，继承自上游。

- **上游**：[QuantumNous/new-api](https://github.com/QuantumNous/new-api) — AGPLv3
- **原始基础**：[songquanpeng/one-api](https://github.com/songquanpeng/one-api) — MIT

TraceNex 完整保留了上游的版权声明、LICENSE 文件，以及 Go module path `github.com/QuantumNous/new-api`。下游修改的完整范围见 [`OVERLAY.md`](./OVERLAY.md)。

上游项目的商业授权请联系上游维护者：[support@quantumnous.com](mailto:support@quantumnous.com)。TraceNex 本身作为内部部署，不提供独立的商业授权。

---

## 🙏 致谢

特别感谢：

- **[QuantumNous](https://github.com/QuantumNous)** 及所有 new-api 贡献者——TraceNex 99% 的工作都来自他们
- **[songquanpeng](https://github.com/songquanpeng)** 提供的 One API 原始基础
- **[JetBrains](https://www.jetbrains.com/?from=new-api)** 为上游项目提供的免费开源开发授权

---

<div align="center">

### 💖 感谢使用 TraceNex

<sub>在 <a href="https://github.com/QuantumNous/new-api">new-api</a> 的肩膀上，叠一层轻量 overlay。</sub>

</div>

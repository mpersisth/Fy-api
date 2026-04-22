<div align="center">

![Fy-api](/web/public/new_logo.png)

# Fy-api

🍥 **AI 网关与资产管理平台 — [new-api](https://github.com/QuantumNous/new-api) 的下游 fork**

<p align="center">
  <strong>简体中文</strong> |
  <a href="./README.md">English</a> |
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
  <a href="#-fy-api-在-new-api-之上增加了什么">Fy-api 增量</a> •
  <a href="#-部署">部署</a> •
  <a href="#-上游同步">上游同步</a> •
  <a href="#-许可证与归属">许可证</a>
</p>

</div>

## 📝 关于 Fy-api

Fy-api 是 **[QuantumNous/new-api](https://github.com/QuantumNous/new-api) 的私有品牌化 fork**，在上游之上叠加了一层小而精的定制。上游提供的一切——40+ LLM 供应商适配、统一 API 网关、额度/计费、管理后台、Subscription、Channel Affinity、Gemini 缓存命中计费——在 Fy-api 里都**完整保留**，并额外增加了几项面向运营人员的功能。

> [!NOTE]
> - Fy-api 以**月度节奏**从 `upstream/main` 拉取新功能：每一个上游改进（新模型适配、bug 修复、schema 迁移）都会通过 [upstream-sync workflow](./.github/workflows/upstream-sync.yml) 自动流入 Fy-api。
> - Go module path 保持为 `github.com/QuantumNous/new-api`，这样合并上游补丁时不需要重写数千个 import。
> - 严格遵守 Apache 2.0 合规：LICENSE、版权头、上游 attribution **完整保留**。Fy-api 专属改动的详细清单见 [`OVERLAY.md`](./OVERLAY.md)。

---

## ✨ Fy-api 在 new-api 之上增加了什么

以下全部是**增量**——upstream 的所有能力在 Fy-api 中依然完整可用。

| # | 功能 | 位置 | 状态 |
|---|------|------|:----:|
| 1 | **日志 CSV 导出** | `GET /api/log/export`（管理员）+ `GET /api/log/self/export`（用户）。UTF-8 + BOM，包含 `request_id` 列。 | ✅ |
| 2 | **用量日志页的"导出"按钮** | 一键下载当前筛选条件下的 CSV，上限 `MaxLogExportItems=50000` 行。 | ✅ |
| 3 | **`/docs` 内嵌产品文档** | 基于 Markdown 的用户手册（18 张截图），用新的 `NewMarkdownRender` 组件渲染。 | ✅ |
| 4 | **邮箱/用户名登录按钮前置** | 登录表单重新排序，主要入口不再被 OAuth 按钮遮住。 | ✅ |
| 5 | **"没有账户？注册" 始终显示** | 去掉了 `self_use_mode` 条件限制（如需禁用注册，仍可在后台配置）。 | ✅ |
| 6 | **Fy-api 品牌化** | `SystemName` → `Fy-api`，新 logo 和 favicon，7 种语言（zh-CN / zh-TW / en / fr / ja / ru / vi）品牌词统一。 | ✅ |
| 7 | **上游同步 CI** | 两个 GitHub Actions：每周一检测积压 > 100 commits 告警，手动触发的 sync 自动开 PR。 | ✅ |

> 完整源码级改动清单见 [`OVERLAY.md`](./OVERLAY.md)。升级规划与操作手册见 [`docs/`](./docs/)。

---

## 🚀 快速开始

### 使用 Docker Compose（推荐）

```bash
# 克隆 Fy-api（不是上游）
git clone git@github.com:seraph0017/Fy-api.git
cd Fy-api

# 按需修改 docker-compose.yml
nano docker-compose.yml

# 启动
docker-compose up -d
```

访问 <http://localhost:3000>。默认管理员账号沿用上游的约定（详见上游文档链接）。

### 从源码构建

```bash
# 后端
go mod tidy
go build -o bin/fy-api

# 前端（CLAUDE.md Rule 3 约定使用 bun）
cd web
bun install
bun run build
```

发布前完整构建清单详见 [`docs/Phase4-Build-runbook.md`](./docs/Phase4-Build-runbook.md)。

---

## 🚢 部署

Fy-api 可作为 new-api 部署的**无感替换**：同一个端口、同一套环境变量、兼容同一份 SQL schema（再加上游后续增加的若干张表，由 GORM `AutoMigrate` 自动迁移）。

### 系统要求

| 组件 | 要求 |
|------|------|
| **本地数据库** | SQLite（需挂载 `/data` 目录）|
| **远程数据库** | MySQL ≥ 5.7.8 或 PostgreSQL ≥ 9.6 |
| **容器引擎** | Docker / Docker Compose |

### 从 TraceNex 或旧版 new-api 升级

如果你要从已有部署（例如 TraceNex）升级，**先做数据库迁移演练** —— 上游新增了约 8 张表（`checkins`、`subscription_*`、`custom_oauth_providers`、`user_oauth_bindings`…）并对多张现有表做了字段扩展。

按照 [`docs/Phase3-DB-migration-runbook.md`](./docs/Phase3-DB-migration-runbook.md) 执行：

- 准备生产库的脱敏副本
- 大表加索引的 online DDL 方案（MySQL 用 `ALGORITHM=INPLACE, LOCK=NONE`；PostgreSQL 用 `CREATE INDEX CONCURRENTLY`）
- 存量 OAuth 绑定迁移到新的 `user_oauth_bindings` 表的 SQL 模板
- 冒烟测试清单
- 热回滚预案

### 环境变量

Fy-api 继承了上游完整的环境变量集合。完整列表以[上游官方文档](https://docs.newapi.pro/zh/docs/installation/config-maintenance/environment-variables)为准。

Fy-api 新增：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MaxLogExportItems` *（Go 常量，非环境变量）* | `/api/log/export` 返回的最大行数 | `50000` |

---

## 🔄 上游同步

Fy-api 的设计原则是**紧跟上游而不是渐行渐远**。核心理念：

1. **只做增量定制** —— 定制代码尽量放到新增文件里（如 `controller/log_export.go`、`web/src/pages/FyApiDocs/`），降低合并冲突
2. **月度同步节奏** —— 冲突成本随 drift 时长呈**指数增长**（~1 个月 0-2 处冲突；~6 个月 20+ 处）。详见 [`docs/Monthly-upstream-sync-runbook.md`](./docs/Monthly-upstream-sync-runbook.md)
3. **自动监控** —— `.github/workflows/upstream-watch.yml` 每周一自动跑，积压 > 100 commits 时告警；> 500 直接 fail
4. **一键同步 PR** —— `.github/workflows/upstream-sync.yml`（手动触发）合并 `upstream/main`、重新应用 i18n 品牌替换、开 PR 等人工 review

```bash
# 本地查看 drift
git fetch upstream
git rev-list --count HEAD..upstream/main

# 看看新增了什么
git log HEAD..upstream/main --oneline | head -30
```

### 哪些定制能扛过一次 sync

详见 [`OVERLAY.md`](./OVERLAY.md)。简要分类：

- **零冲突**（新增文件）：CSV 导出后端 + 前端、FyApiDocs 页面、Markdown 渲染组件、GitHub Actions、OVERLAY.md 本身
- **低冲突**（带 `// Fy-api overlay:` 标记的小改动）：`common/constants.go` 的 `SystemName`、`web/index.html` 的 title、`web/src/App.jsx` 路由注册、`LoginForm.jsx` 重排
- **可自动化**（CI 自动重新应用）：i18n 品牌替换

---

## 📚 上游文档

网关本身的所有能力（通道、relay 协议、计费公式、管理后台、API 参考等）请参考**上游文档**——所有链接均未改动、未重定向：

- 📘 [new-api 官方文档](https://docs.newapi.pro/zh/docs)
- 🧪 [DeepWiki](https://deepwiki.com/QuantumNous/new-api)
- 🚀 [部署指南](https://docs.newapi.pro/zh/docs/installation)
- ⚙️ [环境变量](https://docs.newapi.pro/zh/docs/installation/config-maintenance/environment-variables)
- 📡 [API 参考](https://docs.newapi.pro/zh/docs/api)
- ❓ [常见问题](https://docs.newapi.pro/zh/docs/support/faq)

Fy-api 专属文档在 [`docs/`](./docs/) 下：

| 文件 | 用途 |
|------|------|
| `docs/Phase3-DB-migration-runbook.md` | 从 TraceNex 等旧部署平滑迁移 DB |
| `docs/Phase4-Build-runbook.md` | 从源码构建，含依赖升级 |
| `docs/Phase5-Regression-checklist.md` | 部署后的回归测试清单 |
| `docs/Monthly-upstream-sync-runbook.md` | 每月上游合并的标准流程 |
| `docs/Bug分析-Gemini缓存命中未计费.md` | 一则 Gemini 缓存计费 bug 的事后分析（上游已修复） |

---

## 📜 许可证与归属

Fy-api 采用 [GNU Affero 通用公共许可证 v3.0（AGPLv3）](./LICENSE)，继承自上游。

**上游：** [QuantumNous/new-api](https://github.com/QuantumNous/new-api) — AGPLv3
**原始基础：** [songquanpeng/one-api](https://github.com/songquanpeng/one-api) — MIT

Fy-api 完整保留了上游的版权声明、LICENSE 文件，以及 Go module path `github.com/QuantumNous/new-api`。下游修改的完整范围见 [`OVERLAY.md`](./OVERLAY.md)。

上游项目的商业授权请联系上游维护者：[support@quantumnous.com](mailto:support@quantumnous.com)。Fy-api 本身作为内部部署，不提供独立的商业授权。

---

## 🙏 致谢

特别感谢：

- **[QuantumNous](https://github.com/QuantumNous)** 及所有 new-api 贡献者——Fy-api 99% 的工作都来自他们
- **[songquanpeng](https://github.com/songquanpeng)** 提供的 One API 原始基础
- **[JetBrains](https://www.jetbrains.com/?from=new-api)** 为上游项目提供的免费开源开发授权

---

<div align="center">

### 💖 感谢使用 Fy-api

<sub>在 <a href="https://github.com/QuantumNous/new-api">new-api</a> 的肩膀上，叠一层轻量 overlay。</sub>

</div>

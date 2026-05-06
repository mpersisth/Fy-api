# TraceNex 定制清单（OVERLAY.md）

> 最后更新：2026-04-22
> 维护人：<你的名字>
> 上游基线：new-api @ `f995a868` (2026-04-18)

本文件记录所有 TraceNex 相对于 `upstream/main` (QuantumNous/new-api) 的私有改动。
每次从上游 merge 时对照本清单处理冲突。

---

## 维护约定

- **新增文件优先**：定制能力尽量做成独立文件（`*_export.go`, `UsageLogsExportButton.jsx` 等），零上游冲突
- **必须修改上游文件时**：加 `// Fy-api overlay: ...` 注释，便于 merge 时辨认
- **每次 upstream sync 后**：检查本文件所有条目是否还有效

---

## 后端定制

### B-1 [brand] 系统名
- **文件**：`common/constants.go`
- **修改**：`var SystemName = "TraceNex"`（原 `"New API"`）
- **新增变量**：`var MaxLogExportItems = 50000`
- **冲突风险**：低（上游很少改这两行）
- **Merge 策略**：如果 upstream 又加了变量，手动合并到此文件
- **建议长期改造**：改成 overlay/brand/brand.go 里的 init() 函数覆盖，避免 merge

### B-1.1 [brand] 启动日志品牌化
- **文件**：`main.go` 第 ~52 行
- **修改**：`common.SysLog("New API " + ...)` → `common.SysLog(common.SystemName + " " + ...)`
- **目的**：让启动日志 `TraceNex started` 跟随 SystemName，避免写两处
- **冲突风险**：低（单行，带 `// Fy-api overlay:` 注释）

### B-2 [csv-export] 日志 CSV 导出（新增）
- **新增文件**：
  - `controller/log_export.go`（ExportAllLogs / ExportUserLogs / writeLogsCSV）
  - `model/log_export.go`（GetAllLogsForExport / GetUserLogsForExport / attachChannelNames）
- **修改文件**：`router/api-router.go`（注册 /api/log/export + /api/log/self/export，仅 2 行）
- **冲突风险**：低（独立文件 + 2 行 router 注册）
- **Merge 策略**：router 两行加在 `logRoute.GET("/self/search", ...)` 之后，若 upstream 也改了 logRoute，手动对齐位置

### B-3 [docs] CLAUDE.md Rule 5 改写
- **文件**：`CLAUDE.md` 第 5 条 Rule
- **修改**：把"禁止修改 new-api 品牌"改为"Apache 2.0 合规 attribution + 允许品牌定制"
- **冲突风险**：高（上游 Rule 5 会持续维护）
- **Merge 策略**：每次 upstream 改 Rule 5 都需要人工对齐，保留 Apache 2.0 合规措辞

### B-4 [gitignore] `.cursor/`、`*.log` 和 Python 缓存
- **文件**：`.gitignore`
- **修改**：新增 `.cursor/`、`*.log` 和 `__pycache__/`
- **冲突风险**：极低

### B-5 [docker] Dockerfile 国内部署适配
- **文件**：`Dockerfile`
- **修改**：
  1. **去掉 `@sha256` 摘要 pin**：三个 base image（`oven/bun:1`、`golang:1.26.1-alpine`、`debian:bookworm-slim`）只留 tag
  2. **添加 Go 模块国内代理**：`ENV GOPROXY=https://goproxy.cn,direct` + `ENV GOSUMDB=sum.golang.google.cn`
- **原因**：
  1. 阿里云 Container Registry mirror 不支持按摘要拉取（返回 `denied: requested access to the resource is denied`）
  2. 国内 build 主机无法直连 `proxy.golang.org`（Google 域被墙），`go mod download` 超时
- **代价**：失去摘要级可重现性（见下）；`direct` fallback 允许仍能从原始 VCS 拉模块
- **兜底**：供应链完整性由 `go.sum` / `bun.lock` 保证，base image 小浮动不影响产物
- **冲突风险**：低（上游偶尔刷 SHA；GOPROXY 注入属于 build-env 配置，不太可能冲突）
- **Merge 策略**：上游 bump SHA 时，把新 SHA 更新到文件顶部注释里；保留 tag-only 的 FROM 行和 GOPROXY ENV

### B-6 [deploy] Fabric 服务端构建发布自动化
- **新增文件**：`fabfile.py`
- **用途**：本地只执行 Fabric；远端 ECS 拉取 Git ref、Podman 构建镜像、推送内网 ACR，再调用 `scripts/prod/06-deploy-blue-green.sh` 蓝绿发布
- **默认连接**：`root@8.136.146.211:58422`，密钥路径 `~/.ssh/tracenex_XN.pem`；均可用 `FYAPI_*` 环境变量覆盖
- **冲突风险**：极低（新增根目录运维入口，不改 upstream 业务代码）
- **Merge 策略**：保留文件；若部署脚本参数变化，同步更新 `deploy` / `release` 任务

---

## 前端定制

### F-1 [brand] 浏览器 tab + icon
- **文件**：`web/index.html`
- **修改**：`<title>TraceNex</title>` + `<link rel="icon" href="/new_logo.png?v=2" />`
- **冲突风险**：中（上游会改 meta description）
- **Merge 策略**：title 和 icon 两处坚持用 TraceNex；meta description 可接受 upstream

### F-2 [brand] Logo 和 favicon
- **新增**：`web/public/new_logo.png` (3.4 MB)
- **替换**：`web/public/favicon.ico`
- **冲突风险**：低（上游偶尔更新 logo.png，我们用 new_logo.png 独立）

### F-3 [i18n] 品牌词替换
- **修改文件**：`web/src/i18n/locales/{zh-CN,zh-TW,en,fr,ja,ru,vi}.json`
- **变化**：所有 value 中 `New API` → `TraceNex`，`TraceNex` → `TraceNex`（历史遗留）
- **冲突风险**：高（上游每月增改几十个翻译 key）
- **Merge 策略**：
  ```bash
  # 每次 merge 上游的 locales 之后：
  for lang in zh-CN zh-TW en fr ja ru vi; do
    f="web/src/i18n/locales/${lang}.json"
    jq '(.translation |= with_entries(.value |= gsub("New API"; "TraceNex")))' "$f" > "/tmp/rebrand-${lang}.json"
    cp "/tmp/rebrand-${lang}.json" "$f"
  done
  ```
- **建议长期改造**：上游增加 ESLint 规则禁止未来翻译 value 出现 "New API"

### F-4 [docs] 内嵌产品文档页
- **新增文件**：
  - `web/src/pages/FyApiDocs/index.jsx`（重命名自 TraceNexDocs）
  - `web/src/components/common/NewMarkdownRender/NewMarkdownRender.jsx`
  - `web/public/product-docs/TraceNex.md`
  - `web/public/product-docs/images/image1.png` ~ `image18.png`
- **修改文件**：`web/src/App.jsx`
  - 第 ~59 行：`const FyApiDocs = lazy(() => import('./pages/FyApiDocs'));`
  - 第 ~365 行：`<Route path='/docs' element={<Suspense>...</Suspense>} />`
- **冲突风险**：低（App.jsx 两处小改，Suspense pattern 和 upstream 一致）
- **注意**：物理目录必须是 `product-docs/` 而不是 `docs/`，否则与 SPA 路由 `/docs` 冲突（static 中间件 301 到尾斜杠，前端路由再 301 去掉斜杠 → 死循环）。markdown 内图片路径全部用绝对路径 `/product-docs/images/...`。

### F-5 [csv-export] 日志页 "导出 CSV" 按钮
- **新增文件**：`web/src/components/table/usage-logs/UsageLogsExportButton.jsx`
- **修改文件**：`web/src/components/table/usage-logs/index.jsx`（把 `statsArea` 的 LogsActions 包一层 flex，加入 ExportButton）
- **冲突风险**：中（upstream 改 index.jsx 布局会冲突）

### F-6 [login] 登录表单定制
- **文件**：`web/src/components/auth/LoginForm.jsx`
- **修改**：
  1. 邮箱/用户名登录按钮移到最前 + 加 Divider（L520-540）
  2. 注册入口总显示（两处 L700 和 L854 的 `!status.self_use_mode_enabled` → `true`）
- **冲突风险**：高（upstream 会持续优化登录表单 UI）
- **Merge 策略**：两个定制都加了 `// Fy-api overlay:` 注释方便辨认

---

## 不 port 的 TraceNex 改动（技术债 / 已失效 / 上游已取代）

### X-1 ❌ middleware/auth.go 的 debugNDJSON
- 硬编码 Windows 路径 `d:\谷歌浏览器\new-api-main\.cursor\debug.log`
- **原因**：违反跨平台原则 + 安全隐患

### X-2 ❌ .cursor/debug.log 入库
- 开发者调试文件误提交
- **原因**：已 `.gitignore`

### X-3 ❌ web/dist 入库
- 前端构建产物
- **原因**：上游规范不入库，走 CI 构建

### X-4 ❌ 旧 OAuth controller（discord/github/linuxdo/oidc.go）
- **原因**：upstream 已统一到 `oauth/` registry 模式

### X-5 ❌ controller/task_video.go
- **原因**：upstream 已下沉到 `relay/channel/task/taskcommon/` + `relay/channel/task/gemini/`

### X-6 ❌ service/pre_consume_quota.go
- **原因**：upstream 已拆为 `text_quota.go` + `task_billing.go` + `violation_fee.go` + `funding_source.go`

### X-7 ❌ Home/index.jsx 微调（gap/图标）
- **原因**：cosmetic 调整，上游 Home 页已大量演进；需要时作为独立 UX 任务

---

## 待办（Pending port）

### ~~P-1 GroupRatioSettings 双维 port~~（已决议：不做）
- **状态**：**CLOSED**（2026-04-22）
- **原因**：TraceNex 基线的 `GroupRatioSettings.jsx` 已经同时提供「可视化编辑」和「手动 JSON 编辑」两种模式，且可视化模式下使用 `GroupTable` / `AutoGroupList` / `GroupGroupRatioRules` / `GroupSpecialUsableRules` 四个表格化子组件（基于 `CardTable + InputNumber + Checkbox`），是 TraceNex 当年 +976 行表格 UI 的完整**超集**（还多出 AutoGroups / DefaultUseAutoGroup / 内嵌使用说明 SideSheet 等能力）。TraceNex 的改动是在它 fork 时的老 new-api 上自己造的表格；上游官方随后也做了这个能力并做得更全。port 过来只会丢失新能力，价值为零。
- **参考子计划（已作废）**：`docs/Phase2.5-GroupRatioSettings-port-plan.md`

### P-2 存量 OAuth 用户迁移脚本
- **状态**：待写（Phase 3 Runbook §2.1 有 SQL 模板）
- **触发条件**：当 TraceNex 生产库有 discord/github/linuxdo 活跃用户

### P-3 Gemini 计费补偿审计
- **状态**：待做（用户已确认）
- **操作**：扫 TraceNex 2026-01-06 至修复生效前的 Gemini 日志，估算多扣金额，运营侧补偿

---

## 上游同步流程

见 `docs/Monthly-upstream-sync-runbook.md`。

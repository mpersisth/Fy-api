# TraceNex 定制清单（OVERLAY.md）

> 最后更新：2026-05-09（路径迁移到 `web/classic/`，吸收上游 v1.0 双前端架构）
> 维护人：<你的名字>
> 上游基线：new-api @ `543cc64ea` (2026-05-08)
>
> **重要：上游 v1.0（commit `a42b39760`，2026-04-28）把整个老前端搬到了 `web/classic/`，并行新建了 `web/default/`（React 19 + TypeScript + Rsbuild + Base UI + Tailwind）。TraceNex 选择路径 A：所有前端 overlay 跟随 `web/classic/` 路径，runtime theme 锁死在 `"classic"`，不允许切到 default。详见 `docs/上游v1.0前端重写炸弹-影响分析与对策.md`。**

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
- **用途**：本地只执行 Fabric；远端 ECS 在 `/root/Fy-api` 拉取 Git ref，用 `git archive` 生成干净临时构建目录后 Podman 构建镜像、推送 ACR，再调用 `scripts/prod/06-deploy-blue-green.sh` 蓝绿发布；也支持新加坡新机 `bootstrap-system`
- **默认连接**：`cn=root@8.136.146.211:58422`（`~/.ssh/tracenex_XN.pem`），`sg=root@47.236.133.70:58422`（`~/.ssh/AI_tracenex.pem`，ACR namespace `ai_transnext`），`legacy=root@8.222.175.17`（默认 SSH key/agent）；默认源码目录 `/root/Fy-api`；均可用 `FYAPI_*` 环境变量覆盖
- **冲突风险**：极低（新增根目录运维入口，不改 upstream 业务代码）
- **Merge 策略**：保留文件；若部署脚本参数变化，同步更新 `deploy` / `release` 任务

### B-7 [benchmark] 渠道基准测试工具链（channel-benchmark）
- **新增目录**：`scripts/channel-benchmark/`
  - `README.md` —— 顶层导航，解释 Go / Python 两套工具的分工
  - `go/` —— 零依赖 Go 烟测器（single binary）
    - `main.go` / `runner.go` / `client.go` / `admin.go` / `config.go` / `metrics.go` / `exporter.go`
    - `prometheus.go` —— 自写零依赖 exposition 格式导出器（`-prom-listen :9090` 进 daemon 模式）
    - `prometheus_test.go` / `e2e_test.go` / `testhelper_test.go` —— 全部 `-race` 通过
    - `channel-benchmark.yaml` —— 示例配置（`${VAR}` / `${VAR:-default}` 注入环境变量）
    - `go.mod` 只依赖 `gopkg.in/yaml.v3`
  - `py/` —— 三件套 Python 工具（共享一个 venv / 一个 JSONL schema）
    - `fy_loadtest/` —— 并发阶梯压测（E2E/TTFT/ITL/TPOT 分位、RPS、goodput）
    - `fy_quality/` —— 质量评分（7 种 grader + 双裁判 rubric + 磁盘缓存）
      - `perturbation.py` —— 确定性扰动（`whitespace` / `trailing_marker` / `synonym`）防训练集污染
      - `datasets/public/quality.jsonl` —— 起手 15 条烟测样本（带扰动示例）
      - `datasets/private/` —— 用户私有题库目录（整个目录 gitignore）
    - `fy_canary/` —— 模型替换检测（alignment + drift + MMD 三种探针）
      - `baseline.py` —— v2 schema 带 `recorded_at_iso` / `n_probes` / `total_samples` / `fy_canary_version`；v1 文件向后兼容
      - `cli.py` —— `baseline` / `audit` / `verify-baseline` 三个子命令；`audit` 默认拒绝超过 30 天的 baseline
    - `tests/` + `tests_quality/` + `tests_canary/` —— 47 个 `httpx.MockTransport` e2e 测试
- **用途**：
  1. **烟测**（Go）—— 生产机上零依赖跑一遍所有渠道，看 TTFT / 存活 / usage 是否正常
  2. **压测**（fy-loadtest）—— 灰度上线前验证单渠道在 N 并发下的分位延迟
  3. **质量**（fy-quality）—— 新渠道接入前用金标 JSONL + 双裁判评分
  4. **反替换**（fy-canary）—— 先对可信上游 vendor 直连录 baseline，再周期性审计网关下游是否被静默换模型
  5. **监控接入**（Go Prometheus mode）—— `go run . -prom-listen :9090 -prom-interval 5m` 常驻，给 Grafana 暴露 `channel_benchmark_ttft_seconds` / `_request_total{outcome=...}` / `_run_age_seconds` 等序列
- **冲突风险**：极低（完整独立子目录，不触碰 upstream 业务代码或构建链）
- **Merge 策略**：整个子树随上游同步走；唯一需要人工 review 的是 Go 那边的 `go.mod` 模块路径（`github.com/seraph0017/Fy-api/scripts/channel-benchmark`），不要跟主仓的 Go 模块搞混

### B-8 [gemini] 原生 pass-through 入口保留客户端 API 版本
- **修改文件**：`relay/channel/gemini/adaptor.go`（`GetRequestURL`，紧接 `GetGeminiVersionSetting` 调用之后追加分支）
- **修改文件**：`setting/model_setting/gemini.go`（默认 `VersionSettings` map 显式钉住 image-preview 模型到 `v1beta`）
- **新增测试**：`relay/channel/gemini/relay_gemini_usage_test.go::TestGeminiAdaptorGetRequestURLPreservesNativeVersion`（4 个 sub-test，覆盖 native v1beta、native v1、imagen native v1beta、OpenAI 兼容入口回落 model_setting 四条路径）
- **背景**：海外 SG 部署反馈 `gemini-3-pro-image-preview` 的 native Gemini 调用报 `is not found for API version v1`。Upstream 原始实现里 `GetRequestURL` 一律读 `model_setting.GetGeminiVersionSetting()`，无视客户端写的 `/v1beta/...` 还是 `/v1/...` 路径；后台 `VersionSettings.default` 为 `v1beta` 但任何管理员改动或显式映射都会把 image-preview 这种只在 `v1beta` 暴露的模型踩坑
- **修复**：在 `RelayMode == constant.RelayModeGemini` 的 native pass-through 路径下，按 `info.RequestURLPath` 前缀（`/v1beta/` 或 `/v1/`）覆盖 `version`；OpenAI / Claude 兼容入口（`RelayModeChatCompletions` 等）继续使用 `model_setting`，行为不变。配合 `gemini.go` 显式钉住的 image-preview 模型，原生入口 + 兼容入口双层兜底
- **冲突风险**：低（adaptor.go 内只新增一段 `if` 分支，带 `// Fy-api overlay:` 注释；gemini.go 默认 map 只新增 key）
- **Merge 策略**：上游若改动 `GetRequestURL` 的版本拼接逻辑，保留这段 native pass-through 覆盖；若上游改 `defaultGeminiSettings.VersionSettings`，按需合并新增的 image-preview 项

### B-9 [claude] 默认过滤 `context_management` beta 字段
- **修改文件**：
  - `dto/channel_settings.go`（`ChannelOtherSettings` 新增 `AllowContextManagement bool`，紧跟 `AllowSpeed` 后）
  - `relay/common/relay_info.go`（`RemoveDisabledFields` 中新增一段 `delete(data, "context_management")`，并在函数顶部 doc 中登记该字段）
- **新增测试**：`relay/common/override_test.go::TestRemoveDisabledFieldsContextManagement`（3 个 sub-test：默认裁剪 / `AllowContextManagement=true` 保留 / channel pass-through 保留），同时把 `TestRemoveDisabledFieldsDefaultFiltering` 的输入扩展为含 `context_management`
- **背景**：海外 SG 反馈 `claude-sonnet-4-6` 调用上游返回 `context_management: Extra inputs are not permitted`。Anthropic 的 `context_management` 是 beta 功能，必须配合 `anthropic-beta: context-management-2025-...` header 才能用。客户端（含部分 SDK）会只在 body 里塞这个字段、不带 header，被 Anthropic schema 校验直接拒
- **upstream 现状**：upstream 在 `30cb3b8b`（2025-09-30 "feat: claude context editing"）只在 `dto.ClaudeRequest` 里加了 `ContextManagement` 字段做识别，没有像 `inference_geo / speed / service_tier` 那样接入 `RemoveDisabledFields` 默认过滤体系。这是 upstream 的遗漏；本仓库选择直接 overlay 修复，**不**向 upstream 提 PR
- **冲突风险**：低（结构体新增一行字段；过滤函数新增一段独立 `if`，带 `// Fy-api overlay:` 注释）
- **Merge 策略**：若 upstream 后续自行补上同样的过滤（推测会用同名字段 `AllowContextManagement`），merge 时直接 take theirs 即可；若它选了别的字段名，按 upstream 命名重写本 overlay

### B-10 [relay] 请求体反序列化失误：500 → 400 + Go 字段名脱敏
- **新增文件**：
  - `common/json_error_sanitizer.go`（`SanitizeJSONUnmarshalError`：把 stdlib `*json.UnmarshalTypeError` / `*json.SyntaxError` / 字符串型 wrapped 错误转成用户安全格式 `invalid type for field "X": expected <json type>, got <json type>`，去掉 Go 结构体路径）
  - `common/json_error_sanitizer_test.go`（typed errors / syntax / wrapped string fallback / nil-safe / 切片包路径泄漏 / 嵌套 struct 字段）
  - `common/gin_unmarshal_test.go`（黑盒验证 `UnmarshalBodyReusable` 不再向调用方泄露 Go 路径）
- **修改文件**：
  - `common/gin.go`（`UnmarshalBodyReusable` 错误返回前过 `SanitizeJSONUnmarshalError`）
  - `controller/relay.go`（顶层 dispatcher 把 `ErrorCodeInvalidRequest` 用 `NewErrorWithStatusCode(... StatusBadRequest)` 显式钉到 400，原 `NewError` 默认 500 是 upstream 遗留 bug）
- **背景**：客户端传错参数类型（如 `max_tokens="abc"`）时，upstream new-api 在 `controller/relay.go:114` 用 `types.NewError(err, ErrorCodeInvalidRequest)` 没指定 statusCode → 默认 500。响应组装路径（`controller/relay.go:91 SetMessage(newAPIError.Error())`）跳过了 `MaskSensitiveError`，把 stdlib 错误 `json: cannot unmarshal string into Go struct field GeneralOpenAIRequest.max_tokens of type uint` 原样吐给客户端，既破坏 HTTP 语义（4xx 客户端错被当 5xx 服务端错，触发客户端误重试 / 误熔断），又泄露 Go 内部结构。客户的 v2.1 测试报告里 17 条 500 错误都是这一个根因
- **upstream 现状**：仓库里 `compatible_handler.go / claude_handler.go / gemini_handler.go / image_handler.go / rerank_handler.go / embedding_handler.go` 6 处都正确用了 `NewErrorWithStatusCode(... StatusBadRequest)`，唯独顶层 dispatcher 漏了一处。stdlib 错误脱敏 upstream 完全没做。**不向 upstream 提 PR**
- **冲突风险**：低（顶层 dispatcher 改 1 行；UnmarshalBodyReusable 错误处加 1 行；新增的 sanitizer 是独立文件）
- **Merge 策略**：若 upstream 修了同一行（用同样的 `NewErrorWithStatusCode`），merge 时 take theirs 即可；sanitizer 独立文件不会冲突

### B-11 [relay] /v1/messages 二级反序列化泄漏 + 图片块 nil-deref panic
- **新增文件**：
  - `dto/claude_parse_content_test.go`（`ClaudeMessage.ParseContent` 防泄漏：scalar content / 错类型 text 块 / 合法快路径）
- **修改文件**：
  - `common/utils.go`（`Any2Type[T]` 第二阶段 `json.Unmarshal` 错误也过 `SanitizeJSONUnmarshalError`，关闭 `dto.ClaudeMessage.ParseContent` / `ParseSystem` / `service.ClaudeToOpenAIRequest` 等所有借道 Any2Type 的二级 unmarshal 泄漏面）
  - `common/json_error_sanitizer.go`（`goTypeToJSONType` 增加切片/map/包路径折叠：`[]dto.ClaudeMediaMessage` → `array`、`map[K]V` → `object`、含点的包路径 → `object`，避免类型名直接外泄）
  - `service/convert.go`（`case "image"` 块前增加 `mediaMsg.Source == nil` 守卫，原代码在 `mediaMsg.Source.MediaType` 直接解引用，遇到 `{"type":"image"}` 缺 `source` 字段时 panic）
  - `relay/claude_handler.go`（两处 `ConvertRequestFailed` 改为 `NewErrorWithStatusCode(... StatusBadRequest)`，与 B-10 在 `/v1/chat/completions` 路径上的处理保持一致）
- **背景**：v1.5（B-10）只覆盖 `/v1/chat/completions` 的顶层 unmarshal。2026-05-09 用 fy-conformance 跑 CN 基线时发现 `/v1/messages` 路径上有 4 个同类 bug：
  1. `content=42` / `content=true` → 500 + `json: cannot unmarshal number into Go value of type []***.ClaudeMediaMessage`（`dto.ClaudeMessage.ParseContent` → `common.Any2Type` 二级 unmarshal 没经过 sanitizer）
  2. `content=[{type:text,text:42}]` → 500 + `Go struct field ***.text of type string`（同链路）
  3. `content=[{type:image}]` 缺 `source` 字段 → 500 + PANIC `runtime error: invalid memory address or nil pointer dereference`（`service/convert.go:161` 直接 `mediaMsg.Source.MediaType`）
- **upstream 现状**：upstream `Any2Type` 是直通函数，`service/convert.go` image 块也是无 nil 检查；同样的 panic 在 upstream 仍然存在。**不向 upstream 提 PR**（与 B-10 同策略，等下次周度同步时观察 upstream 是否补）
- **冲突风险**：中（`Any2Type` 是高频函数，签名不变；image case 加 4 行守卫；`claude_handler.go` 改 2 个 if 分支）
- **Merge 策略**：upstream 若改 `Any2Type` 签名（不太可能），同步时把 sanitizer 调用迁过去；image nil 守卫若 upstream 自行修了就 take theirs

### B-12 [relay] base64 图片缺失 MIME type 自动识别
- **修改文件**：
  - `dto/openai_request.go`（`MessageImageUrl.MimeType` 增加 `json:"mime_type,omitempty"`，`Message.ParseContent` 保留 `image_url.mime_type/mimeType`）
  - `service/file_service.go`（raw base64 文件在 MIME 为空时通过图片解码 / content sniff / HEIF 检测补齐 MIME）
- **新增测试**：
  - `dto/openai_request_mime_test.go::TestMessageParseContentPreservesImageURLMimeType`
  - `service/file_service_test.go::TestGetBase64DataInfersMimeTypeForRawBase64Image`
- **背景**：用例明细 116 的 `content_image_url` 传裸 base64 且不带 MIME type 时，TraceNex/上游适配层可能拒绝或向 Claude/Gemini 发送空 MIME，触发 `base64 mime type can not be empty`。现在能识别的图片会自动补成 `image/png` / `image/jpeg` 等；无法识别的内容仍交给后续 provider 白名单校验拒绝
- **冲突风险**：低（小范围解析/文件服务增强）
- **Merge 策略**：若 upstream 后续支持同类自动 MIME 推断，优先采用 upstream 实现，但保留 OpenAI `image_url.mime_type` 不丢失的测试语义

---

## 前端定制

> ⚠️ **路径前缀提示（v1.0 后）**：所有前端 overlay 文件物理路径都在 `web/classic/` 下面。`web/default/` 是上游 v1.0 引入的全新前端，TraceNex 不在那里维护任何 overlay，runtime theme 也不允许切到 default（见 `setting/system_setting/theme.go` + `controller/option.go` 的 `// Fy-api overlay:` 双锁）。

### F-1 [brand] 浏览器 tab + icon
- **文件**：`web/classic/index.html`
- **修改**：`<title>TraceNex</title>` + `<link rel="icon" href="/new_logo.png?v=2" />`
- **冲突风险**：中（上游会改 meta description）
- **Merge 策略**：title 和 icon 两处坚持用 TraceNex；meta description 可接受 upstream

### F-2 [brand] Logo 和 favicon
- **新增**：`web/classic/public/new_logo.png` (3.4 MB)
- **替换**：`web/classic/public/favicon.ico`
- **冲突风险**：低（上游偶尔更新 logo.png，我们用 new_logo.png 独立）
- **注意**：v1.0 merge 时 git 的 directory-rename 启发式会把 public 资源建议到 `web/default/public/`，**必须手动改到 `web/classic/public/`**

### F-3 [i18n] 品牌词替换
- **修改文件**：`web/classic/src/i18n/locales/{zh-CN,zh-TW,zh,en,fr,ja,ru,vi}.json`（v1.0 合并后多了一个 `zh.json`）
- **变化**：所有 value 中 `New API` → `TraceNex`，`TraceNex` → `TraceNex`（历史遗留）
- **冲突风险**：高（上游每月增改几十个翻译 key）
- **Merge 策略**：
  ```bash
  # 每次 merge 上游的 locales 之后：
  for lang in zh-CN zh-TW zh en fr ja ru vi; do
    f="web/classic/src/i18n/locales/${lang}.json"
    jq '(.translation |= with_entries(.value |= gsub("New API"; "TraceNex")))' "$f" > "/tmp/rebrand-${lang}.json"
    cp "/tmp/rebrand-${lang}.json" "$f"
  done
  ```
- **建议长期改造**：上游增加 ESLint 规则禁止未来翻译 value 出现 "New API"

### F-4 [docs] 内嵌产品文档页
- **新增文件**：
  - `web/classic/src/pages/FyApiDocs/index.jsx`（重命名自 TraceNexDocs）
  - `web/classic/src/components/common/NewMarkdownRender/NewMarkdownRender.jsx`
  - `web/classic/public/product-docs/TraceNex.md`
  - `web/classic/public/product-docs/images/image1.png` ~ `image18.png`
- **修改文件**：`web/classic/src/App.jsx`
  - 第 ~59 行：`const FyApiDocs = lazy(() => import('./pages/FyApiDocs'));`
  - 第 ~365 行：`<Route path='/docs' element={<Suspense>...</Suspense>} />`
- **冲突风险**：低（App.jsx 两处小改，Suspense pattern 和 upstream 一致）
- **注意**：物理目录必须是 `product-docs/` 而不是 `docs/`，否则与 SPA 路由 `/docs` 冲突（static 中间件 301 到尾斜杠，前端路由再 301 去掉斜杠 → 死循环）。markdown 内图片路径全部用绝对路径 `/product-docs/images/...`。

### F-5 [csv-export] 日志页 "导出 CSV" 按钮
- **新增文件**：`web/classic/src/components/table/usage-logs/UsageLogsExportButton.jsx`
- **修改文件**：`web/classic/src/components/table/usage-logs/index.jsx`（把 `statsArea` 的 LogsActions 包一层 flex，加入 ExportButton）
- **冲突风险**：中（upstream 改 index.jsx 布局会冲突）

### F-6 [login] 登录表单定制
- **文件**：`web/classic/src/components/auth/LoginForm.jsx`
- **修改**：
  1. 邮箱/用户名登录按钮移到最前 + 加 Divider（L520-540）
  2. 注册入口总显示（两处 L700 和 L854 的 `!status.self_use_mode_enabled` → `true`）
- **冲突风险**：高（upstream 会持续优化登录表单 UI）
- **Merge 策略**：两个定制都加了 `// Fy-api overlay:` 注释方便辨认

### F-7 [theme-lock] 强制 classic 主题（新增于 2026-05-09）
- **修改文件**：
  - `setting/system_setting/theme.go`（`syncThemeToCommon` 强制把 `themeSettings.Frontend` 写回 `"classic"`）
  - `controller/option.go`（`case "theme.frontend":` 拒绝 `"default"` 写入）
- **目的**：上游 v1.0 后 `theme.frontend` 是一个 admin 后台可改的 option（5/1 之后的 `e0b6eb3a5` 还修了它从 DB 加载的同步问题）。TraceNex 选择路径 A：仅 ship classic，所以做了**双锁**——即使有人手动改 DB option 表也会在启动时被 `syncThemeToCommon` 覆写回 classic
- **冲突风险**：低（两处都是带 `// Fy-api overlay:` 注释的小改动）
- **可逆**：删掉这两处 overlay 即可回到上游"两套前端可切换"的行为，配合在 `web/default/` 重做 F-1~F-6 即为路径 B 的最小迁移路径

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

见 `docs/Weekly-upstream-sync-runbook.md`（周合并 + 按需发版）。

# TraceNex 定制清单（OVERLAY.md）

> 最后更新：2026-06-07（同步上游 `upstream/main`，保留 TraceNex overlay）
> 维护人：<你的名字>
> 上游基线：new-api @ `4ca47ee2` (2026-06-07)
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
    - `fy_image_loadtest/` —— 图片生成持续压测（固定每渠道 worker 数、打 `/v1/images/generations`、支持多渠道同时 pin 到指定 channel id，持续跑到 Ctrl+C）
    - `fy_quality/` —— 质量评分（7 种 grader + 双裁判 rubric + 磁盘缓存）
      - `perturbation.py` —— 确定性扰动（`whitespace` / `trailing_marker` / `synonym`）防训练集污染
      - `datasets/public/quality.jsonl` —— 起手 15 条烟测样本（带扰动示例）
      - `datasets/private/` —— 用户私有题库目录（整个目录 gitignore）
    - `fy_canary/` —— 模型替换检测（alignment + drift + MMD 三种探针）
      - `baseline.py` —— v2 schema 带 `recorded_at_iso` / `n_probes` / `total_samples` / `fy_canary_version`；v1 文件向后兼容
      - `cli.py` —— `baseline` / `audit` / `verify-baseline` 三个子命令；`audit` 默认拒绝超过 30 天的 baseline
    - `fy_image_conformance/` —— 图片协议一致性 + 质量 + 安全（六阶段：探针→冒烟→API兼容→输出验证→Phase A/B 质量→安全）
    - `fy_image_canary/` —— 图片金丝雀真实性检测（5A vendor 对比 + 5B 指纹/跨渠道/能力边界）
    - `fy_score/` —— 统一评分器（五维度加权，文本/图片不同权重，产出 scorecard）
    - `tests/` + `tests_quality/` + `tests_canary/` + `tests_image_canary/` —— 102 个 e2e 测试
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

### B-10 [billing] wan2.6 图像/视频模型定价与展示
- **修改文件**：
  - `setting/ratio_setting/model_ratio.go`（新增 `wan2.6-i2v` / `wan2.6-r2v` / `wan2.6-r2v-flash` 视频基础倍率、`wan2.6-t2i` 单图价格）
  - `relay/channel/task/ali/constants.go`（Ali 视频模型列表新增 `wan2.6-i2v` / `wan2.6-r2v` / `wan2.6-r2v-flash`）
  - `relay/channel/task/ali/adaptor.go`（Ali 计费倍率新增 `wan2.6-r2v` / `wan2.6-r2v-flash`；`wan2.6` 默认分辨率改为 `720P`，其中 `wan2.6-r2v*` 默认 `size=1280*720`；分辨率校验前移到 `ValidateRequestAndSetAction` 并在 metadata 反序列化后再校验最终值；`wan2.6-r2v*` 使用 DashScope `input.reference_urls` + `parameters.size/audio/shot_type/watermark`，向后兼容 `input_reference` + `metadata.last_frame_url` 旧传参，且在没有顶层统一字段时保留 `metadata.input.reference_urls` 原生透传；`wan2.7-r2v*` 保留 `input.media` 数组；新增 `AliMediaItem` 结构体、`AliVideoInput.ReferenceURLs` / `AliVideoInput.Media` 字段、`AliVideoParameters.Ratio` / `ShotType` 字段；任务完成时按阿里 `usage.duration` 做实际秒数差额结算）
  - `relay/channel/task/ali/adaptor_test.go`（覆盖 `wan2.6` 默认分辨率、非法分辨率返回 400、完成态按实际时长结算、`wan2.6-r2v*` reference_urls 转换、`wan2.7-r2v*` media 转换、last_frame_url 向后兼容、Ratio metadata 透传、metadata 原生 reference_urls 保留、JSON 序列化断言）
  - `relay/common/relay_info.go`（新增 `TaskMediaItem` 结构体，以及 `TaskSubmitReq.ReferenceURLs` / `Media` / `Audio` / `ShotType` / `Watermark` 字段，支持用户通过 reference_urls 或 media 传入多个参考素材）
  - `service/task_polling.go`（`// Fy-api overlay:`：任务完成态先执行 adaptor 的实际用量结算，再让 per-call 任务跳过 token 重算，避免按次预扣的视频模型跳过 `usage.duration` 差额结算）
  - `service/task_billing.go`（`// Fy-api overlay:`：任务消费日志把 `OtherRatios` 写入 `other` JSON，便于 `seconds` / `resolution-*` 报表和 e2e 断言）
  - `web/classic/src/components/table/model-pricing/modal/components/ModelPricingTable.jsx`（遇到 `wan2.6` 视频模型时改为“视频计费”展示）
  - `web/classic/src/components/table/model-pricing/modal/components/VideoPricingDisplay.jsx`（新增分辨率/每秒价格表）
  - `scripts/ops/media_billing_e2e.py`（新增 cn-test/staging 媒体计费 e2e，用公开 API 覆盖图片固定价格、i2v/r2v 视频任务计费与日志结构化字段）
  - `docs/reports/2026-06-06-media-billing-audit.md`（生产只读聚合、根因、测试矩阵和 e2e 运行说明）
  - `docs/操作手册-视频模型接入.md`（对客户和运营说明 TraceNex 只暴露统一 OpenAI-like 视频任务协议，阿里 `input` / `parameters` 通过顶层便捷字段或 `metadata` 透传）
- **定价规则**：
  - `wan2.6-t2i`：`0.2` 元/张
  - `wan2.6-i2v` / `wan2.6-r2v` / `wan2.6-r2v-flash`：`720P=0.3` 元/秒，`1080P=0.5` 元/秒
- **背景**：上游现有模型价格表偏 token/quota 展示，不适合阿里万相这类按分辨率、按秒结算的视频模型；2026-06-10 复测浙江算力/ DashScope 示例后确认 `wan2.6-r2v*` 应使用 `input.reference_urls`（`input.media` 是 `wan2.7-r2v` 形态）；旧字段或错用 media 会导致上游不返回 task_id。最初版本还存在默认分辨率偏成 `1080P`、非法分辨率在预扣费后才 500、成功任务未按阿里实际时长结算的问题。2026-06-06 复查生产媒体日志时发现 `PerCallBilling` 会在轮询完成态提前跳过 adaptor 实际用量结算，已改为 adaptor actual quota 优先；同时补结构化 `OtherRatios` 日志字段和 cn-test e2e。
- **冲突风险**：中（`model_ratio.go` 和 `ModelPricingTable.jsx` 都是上游常改文件；`adaptor.go` 未来若继续扩充 Ali metadata 映射也可能冲突；`relay_info.go` 新增字段不影响上游已有字段）
- **Merge 策略**：上游若调整视频计费展示或 Ali adaptor，保留这组 `wan2.6` 固定价格规则；`wan2.6-r2v*` 必须使用 `input.reference_urls`，`wan2.7-r2v*` 才使用 `input.media`；不得回退到 `first_frame_url`/`last_frame_url`

### B-11 [relay] 请求体反序列化失误：500 → 400 + Go 字段名脱敏
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

### B-12 [relay] /v1/messages 二级反序列化泄漏 + 图片块 nil-deref panic
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

### B-13 [relay] base64 图片缺失 MIME type 自动识别
- **修改文件**：
  - `dto/openai_request.go`（`MessageImageUrl.MimeType` 增加 `json:"mime_type,omitempty"`，`Message.ParseContent` 保留 `image_url.mime_type/mimeType`）
  - `service/file_service.go`（raw base64 文件在 MIME 为空时通过图片解码 / content sniff / HEIF 检测补齐 MIME）
- **新增测试**：
  - `dto/openai_request_mime_test.go::TestMessageParseContentPreservesImageURLMimeType`
  - `service/file_service_test.go::TestGetBase64DataInfersMimeTypeForRawBase64Image`
- **背景**：用例明细 116 的 `content_image_url` 传裸 base64 且不带 MIME type 时，TraceNex/上游适配层可能拒绝或向 Claude/Gemini 发送空 MIME，触发 `base64 mime type can not be empty`。现在能识别的图片会自动补成 `image/png` / `image/jpeg` 等；无法识别的内容仍交给后续 provider 白名单校验拒绝
- **冲突风险**：低（小范围解析/文件服务增强）
- **Merge 策略**：若 upstream 后续支持同类自动 MIME 推断，优先采用 upstream 实现，但保留 OpenAI `image_url.mime_type` 不丢失的测试语义

### B-14 [benchmark/image] gpt-image-2 压测工具与图片质量日志修正
- **新增目录**：`scripts/channel-benchmark/py/fy_image_loadtest/`
  - `cli.py` / `config.py` / `client.py` / `runner.py` / `report.py`
  - 命令：`fy-image-loadtest`
  - 用途：针对 `/v1/images/generations` 的持续压测，支持多渠道 pin、每渠道独立并发、`duration_sec` / `max_requests_per_channel` 结束条件、429 `retry-after` 冷却
- **新增文件**：`scripts/channel-benchmark/py/image-loadtest.yaml`
- **本地配置约定**：`scripts/channel-benchmark/py/image-loadtest.local.yaml`（gitignore，不入库；需要时从 `image-loadtest.yaml` 复制生成）
- **修改文件**：
  - `scripts/channel-benchmark/py/pyproject.toml`（注册 `fy-image-loadtest` CLI）
  - `scripts/channel-benchmark/README.md` / `scripts/channel-benchmark/py/README.md`（补图片压测说明）
  - `relay/image_handler.go`（`// Fy-api overlay:`：图片消费日志保留实际 `quality` 值，不再把 `high/medium/low` 统记成 `standard`）
  - `relay/channel/openai/adaptor.go`（`// Fy-api overlay:`：Azure `gpt-image-*` / `chatgpt-image-latest` 图片生成请求丢弃 `response_format`，避免 Azure 上游拒绝不支持的参数）
- **新增测试**：
  - `relay/channel/openai/adaptor_image_test.go::TestConvertImageRequestDropsAzureGPTImageResponseFormat`
  - `relay/channel/openai/adaptor_image_test.go::TestConvertImageRequestKeepsDallEResponseFormat`
- **背景**：
  1. 现有 `fy-loadtest` 固定打 `/v1/chat/completions`，不适合 `gpt-image-2`
  2. Azure `gpt-image-2` 链路对 `response_format` 不兼容，工具默认不再发送该字段；网关运行时也会在命中 Azure GPT image 模型时删除该字段，旧 DALL-E 模型继续保留
  3. 2026-05-15 CN 线上排查确认：channel `42` 和 `43` 共享同一个 Azure `base_url + key`，并非独立配额桶；本地图片压测配置已按此降并发标注
- **冲突风险**：低（benchmark 子树独立；`relay/image_handler.go` 仅一小段日志文案逻辑）
- **Merge 策略**：benchmark 子树整体保留；若 upstream 后续自带 image loadtest，可比较后择优；`relay/image_handler.go` 若 upstream 修复同类质量标签记录问题，merge 时优先采用 upstream 实现

### B-15 [benchmark/image] 余额不足自动停机
- **修改文件**：
  - `scripts/channel-benchmark/py/fy_image_loadtest/client.py`（识别余额不足类错误）
  - `scripts/channel-benchmark/py/fy_image_loadtest/runner.py`（任一请求命中后触发全局优雅停机）
  - `scripts/channel-benchmark/py/tests/test_image_loadtest.py`（新增停机回归测试）
- **行为**：`fy-image-loadtest` 遇到 `余额不足` / `额度不足` / `insufficient quota` 等错误时，立刻停止继续发新请求，已在飞请求自然收尾后输出最终报告
- **冲突风险**：低（仅 benchmark 子树）

### B-15.1 [benchmark/image] 图片协议一致性 + 质量 + 安全测试套件
- **新增目录**：`scripts/channel-benchmark/py/fy_image_conformance/`
  - `cli.py` / `config.py` / `client.py` / `probe.py` / `budget.py` / `report.py`
  - `suites/api_compat.py` / `output_valid.py` / `prompt_follow.py` / `perf.py` / `safety.py`
  - 命令：`fy-image-conformance`
  - 用途：图片渠道六阶段测试（探针 → 冒烟 → API 兼容 → 输出验证 → 内容质量 Phase A/B → 安全抽样），单命令产出结构化 JSON + markdown 报告
- **新增测试**：`scripts/channel-benchmark/py/tests/test_image_conformance_json.py`、`scripts/channel-benchmark/py/tests/test_phase2_phase3.py`
- **修改文件**：`scripts/channel-benchmark/py/pyproject.toml`（注册 `fy-image-conformance` CLI）
- **冲突风险**：极低（benchmark 子树独立新增目录）

### B-15.2 [benchmark/image] 图片金丝雀真实性检测（5A/5B）
- **新增目录**：`scripts/channel-benchmark/py/fy_image_canary/`
  - `cli.py` / `config.py` / `client.py` / `verdict.py` / `runner.py` / `runner_5a.py` / `calibrate.py` / `report.py`
  - `comparators/clip.py` / `histogram.py` / `vlm_judge.py`（5A 三维对比器）
  - `probes/fingerprint.py` / `cross_channel.py` / `capability.py`（5B 探针）
  - 命令：`fy-image-canary`
  - 用途：检测图片渠道是否被静默替换为劣质模型。5A = vendor 直连对比（CLIP + 颜色直方图 + VLM），5B = 无 key 指纹/跨渠道/能力边界探针
- **新增测试**：`scripts/channel-benchmark/py/tests_image_canary/test_verdict_fix.py`、`test_runner.py`、`test_comparators.py`、`test_fingerprint.py`、`test_config.py`
- **修改文件**：`scripts/channel-benchmark/py/pyproject.toml`（注册 `fy-image-canary` CLI + `[image-canary]` extras）
- **冲突风险**：极低（benchmark 子树独立新增目录）

### B-15.3 [benchmark] 统一评分器（文本 + 图片）
- **新增目录**：`scripts/channel-benchmark/py/fy_score/`
  - `cli.py` / `scorer.py` / `loader.py` / `report.py`
  - 命令：`fy-score`
  - 用途：汇总 loadtest / quality / canary / conformance / integrity 各工具输出，按五维度（可用性/性能/质量/真实性/合规性）加权评分，产出 scorecard.json + scorecard.md。文本和图片使用不同权重体系
- **修改文件**：`scripts/channel-benchmark/py/pyproject.toml`（注册 `fy-score` CLI）
- **冲突风险**：极低（benchmark 子树独立新增目录）

### B-16 [aws/bedrock] Claude `anthropic-beta` 兼容过滤
- **修改文件**：
  - `relay/channel/aws/dto.go`（Bedrock AK/SK 路径把 `anthropic-beta` header 转成 body `anthropic_beta` 前，先按 Bedrock 支持列表映射/过滤；无兼容 token 时不发送该字段；同时丢弃客户端 body 自带的 `anthropic_beta` / `output_config`）
  - `relay/channel/aws/relay-aws.go`（AWS Claude pass-through body 路径同样删除 `anthropic_beta` / `output_config`，避免绕过 `formatRequest` 的兜底过滤）
  - `relay/channel/aws/relay_aws_test.go`（覆盖支持 token 保留、`advanced-tool-use-2025-11-20` 映射成 `tool-search-tool-2025-10-19`、不支持 token 被删除、body 自带 beta 删除、pass-through 删除 Bedrock 不兼容字段）
- **背景**：SG 生产 Bedrock 流式调用报 `ValidationException: invalid beta flag`，后续日志还出现 `output_config.format: Extra inputs are not permitted`。直连 Anthropic 支持的 beta/header/body 扩展集合大于 AWS Bedrock 支持集合，原实现把 `anthropic-beta` 原样拆分写入 Bedrock body 的 `anthropic_beta`，且 pass-through 会把原始 body 的不兼容字段直接发给 Bedrock。
- **行为**：后端默认按 AWS Bedrock Claude Messages 当前支持的 beta token 做白名单，并保留已有前端 “AWS Bedrock Claude 兼容模板” 的核心映射语义；渠道级 header override 仍先执行，随后 Bedrock adaptor 做最后兜底过滤。客户端 body 自带的 `anthropic_beta` 不被信任，统一由过滤后的 header 重建；`output_config` 暂按 Bedrock 不兼容字段在 AWS 边界删除。
- **冲突风险**：低（AWS adaptor 局部新增 helper；若 upstream 后续实现 Bedrock beta 白名单或官方支持 `output_config`，合并时对齐官方支持列表即可）

### B-17 [aws/bedrock] 不支持的 tool type 过滤
- **新增文件**：
  - `relay/channel/aws/bedrock_tools_filter.go`（Bedrock 支持的 tool type 白名单 + pass-through / struct 两条路径的过滤函数）
  - `relay/channel/aws/bedrock_tools_filter_test.go`（覆盖：保留支持类型、删除不支持类型、全部不支持时删除 tools 字段、无 type 字段保留）
- **修改文件**：
  - `relay/channel/aws/relay-aws.go`（`sanitizeBedrockClaudeRawFields` +1 行调用 `filterBedrockToolsRaw`）
  - `relay/channel/aws/dto.go`（`sanitizeBedrockClaudeRawFieldsFromStruct` +1 行调用 `filterBedrockToolsFromStruct`）
- **背景**：SG 生产 momo 客户流量出现 400 `ValidationException: tools.N: Input tag 'web_search_20250305' / 'advisor_20260301' found using 'type' does not match any of the expected tags`。Bedrock Claude Messages 仅接受有限的 tool type 集合，Anthropic 直连支持的扩展 tool type 在 Bedrock 侧会被拒绝。

- **行为**：在发送请求到 Bedrock 前，按白名单过滤 tools 数组中不支持的 type，静默丢弃不兼容工具而非返回 400 给客户端。
- **冲突风险**：极低（独立新文件 + 两处各 +1 行；与 B-16 同一函数但不同行，合并时仅需保留两行调用）

### B-27 [ops/report] 毛利报表脚本与 agent skill
- **新增/修改文件**：
  - `scripts/ops/gross_profit_report.py`（CN/SG 多环境毛利 CSV 报表；本地 RDS 直连失败时自动 SSH 到生产机本地 MySQL 聚合查询；`detail.csv` 按运营表格格式输出：`日期 / 环境 / 用户 / 渠道ID / 渠道 / 模型 / 请求数 / 输入Tokens / 输出Tokens / 折扣倍率 / 收入(USD) / 成本(USD) / 毛利(USD) / 毛利率(%)`）
  - `scripts/ops/test_gross_profit_report.py`（锁定明细 CSV 表头、列顺序、日志有效折扣倍率、缺失倍率不伪装为 1）
  - `.agents/skills/gross-profit-report/SKILL.md`（Codex 项目内技能；全局副本需同步到 `~/.codex/skills/gross-profit-report` 和 `~/.claude/skills/gross-profit-report`）
- **行为**：`折扣倍率` 来自日志 `other.group_ratio` 的聚合有效倍率 `SUM(quota) / SUM(quota / group_ratio)`；缺失/非法倍率在 CSV 中显示 `缺失` 并写入 `warnings.csv`，不能临时写死为 `1`。`channel_costs.yaml` 的 `cost_factor` 只用于成本修正，不作为折扣倍率列展示。日期格式为运营表格使用的 `YYYY/M/D`。
- **冲突风险**：低（独立运维脚本和 agent 文档，不改 upstream 业务代码）

### B-18 [aws/bedrock] deprecated temperature 参数过滤
- **新增文件**：
  - `relay/channel/aws/bedrock_temperature_filter.go`（模型黑名单 + struct / pass-through 两条路径的 temperature 剥离函数）
  - `relay/channel/aws/bedrock_temperature_filter_test.go`（覆盖：黑名单模型剥离、非黑名单模型保留、raw map 路径）
- **修改文件**：
  - `relay/channel/aws/relay-aws.go`（`doAwsClientRequest` +1 行 `stripBedrockDeprecatedTemperature`；`buildAwsRequestBody` +1 行 `stripBedrockDeprecatedTemperatureRaw`）
- **背景**：SG 生产 momo 客户流量出现 400 `ValidationException: 'temperature' is deprecated for this model`（claude-opus-4-7 via Bedrock channel #27）。Bedrock 对部分新模型不再接受 temperature 参数。
- **行为**：在发送请求到 Bedrock 前，根据模型黑名单剥离 temperature 字段，静默丢弃而非返回 400 给客户端。
- **冲突风险**：极低（独立新文件 + relay-aws.go 两处各 +1 行；与 B-17 同一区域但不同行）

### B-19 [monitoring] Prometheus metrics overlay
- **新增文件**：
  - `middleware/prometheus_overlay.go`（Prometheus histogram/counter 定义 + Gin middleware + TTFT ResponseWriter wrapper）
  - `router/prometheus_overlay.go`（`/metrics` 端点注册 + middleware 挂载，受 `PROMETHEUS_METRICS=1` 环境变量控制）
  - `docs/prometheus-monitoring.md`（部署文档：Prometheus + Grafana + AlertManager 接入指南）
  - `pkg/prommetrics/relay_cache.go`（缓存命中率 + 亲和性命中率 Prometheus 指标定义 + Collector）
- **修改文件**：`router/main.go`（1 行：`SetPrometheusRouter(router)` 调用）、`middleware/distributor.go`（亲和性 lookup 指标记录）、`service/text_quota.go`（缓存 token 指标记录）、`service/channel_affinity.go`（注册 stats provider）
- **指标**：`fy_relay_requests_total`, `fy_relay_errors_total`, `fy_relay_duration_seconds`, `fy_relay_ttft_seconds`, `fy_image_duration_seconds`, `fy_relay_retries_total`, `fy_relay_prompt_tokens_total`, `fy_relay_cached_tokens_total`, `fy_relay_cache_creation_tokens_total`, `fy_affinity_lookups_total`, `fy_affinity_active_entries`
- **冲突风险**：极低（新增文件 + main.go 1 行注册；upstream 不太可能在同一位置加同名函数）
- **Merge 策略**：若 upstream 未来自己加 Prometheus 支持，评估是否迁移到 upstream 实现

### B-18 [tencent/aiart] GPT 图片生成同步伪装
- **新增文件**：
  - `relay/channel/tencent/aiart_image.go`（Tencent AIArt 图片请求转换、TC3 签名、提交/轮询、OpenAI 图片响应转换）
  - `relay/channel/tencent/aiart_image_test.go`（AIArt host 分支、请求转换、签名 scope、响应转换、提交+查询流程测试、ResultImages 多形态兼容、错误码 string 兼容、prompt 引号与安全词回归）
- **修改文件**：`relay/channel/tencent/adaptor.go`（`ConvertImageRequest` / `DoRequest` / `DoResponse` 三处薄分支，均带 `// Fy-api overlay:` 注释）
- **行为**：后台仍使用现有 `腾讯混元 / Tencent` 渠道和 `AppId|SecretId|SecretKey` 密钥格式；当 `RelayModeImagesGenerations` 且渠道 `base_url` host 为 `aiart.tencentcloudapi.com` 时，后端把 OpenAI `/v1/images/generations` 请求转成腾讯 AIArt `SubmitContentToImageGPTJob`，每 5 秒轮询 `DescribeContentToImageGPTJob`，最长同步等待 10 分钟，然后返回 OpenAI 兼容 image response。
- **兼容修复**：
  1. AIArt `Error.Code` 与普通 Tencent chat `Error.Code` 均兼容腾讯返回 string / number 两种形态，避免 `"0"` 触发反序列化失败。
  2. AIArt 查询结果里的 `ResultImage` / `ResultImages` / `ImageUrls` / `Images` 兼容 string、array（含 object array）和 object，object 中优先抽取 `Url` / `ImageUrl` / `B64Json` 等常见字段。
  3. AIArt 请求转换阶段对明确的 sexual / violence prompt 做前置 `moderation_blocked` 兜底，并在图片 handler 中按 OpenAI 图片错误语义返回 400，避免腾讯侧混合词审核漏拦时直接出图。
  4. OpenAI 图片兼容响应默认返回 `b64_json`；只有客户端显式传 `response_format: "url"` 时才透出腾讯 COS URL，保持早期测试通过时的默认行为。
- **配置**：后台不改 UI。渠道类型选 Tencent，Base URL 填 `https://aiart.tencentcloudapi.com`，模型填 `gpt-image-2`，密钥填 `AppId|SecretId|SecretKey`。不要为该渠道启用 pass-through body；图片价格/倍率仍需按模型单独配置。
- **冲突风险**：低（核心逻辑在新增文件；`adaptor.go` 仅三处小分支）
- **Merge 策略**：upstream 若改 Tencent 文本 adaptor，保留 AIArt 分支即可；若 upstream 后续原生支持 AIArt 或图片异步任务，可优先迁移到 upstream 实现，但保留 `AppId|SecretId|SecretKey` 后台兼容配置。

> Note: tnbiz integration code comments still reference the historical B-12..B-18 identifiers from the original overlay branch. The list below is renumbered here to avoid colliding with later main-branch overlay entries.

### B-19 [tnbiz] TraceNex Partner 集成内部 API 路由 + HMAC 鉴权
- **新增文件**：
  - `router/api-internal-router.go`（`/api/internal/*` 独立路由组，**不**继承 `/api` 全局 `GlobalAPIRateLimit`；改用 per-kid quota 占位）
  - `middleware/internal_auth.go` + `middleware/internal_auth_test.go`（HMAC-SHA256 timestamp ±5min + nonce SETNX 24h（go-redis/v8 ctx-first） + body sha256 + endpoint allowlist 精确匹配）
- **修改文件**：`router/main.go`（+1 行 `SetInternalRouter(router)`，紧接 `SetVideoRouter` 之后）
- **冲突风险**：低（router/main.go 1 行 + 独立路由文件）
- **Merge 策略**：upstream 若改 `SetRouter` 签名同步对齐，独立 file 零冲突
- **Feature flag**：`overlay.internal_api_enabled`（OVERLAY_INTERNAL_API），prod 默认 false

### B-20 [tnbiz] 内部 controllers + ChannelLogSetting upsert
- **新增文件**：
  - `controller/tnbiz_internal/health.go`（GET /api/internal/health 自检 + envelope helper）
  - `controller/tnbiz_internal/token.go`（POST /api/internal/token/create — partner 永远不可见 sk-key 明文，仅返回 token_id + masked_key + 5 分钟一次性 delivery_handle）
  - `controller/tnbiz_internal/user.go`（POST topup / quota/adjust / refund / erase，PUT group，GET quota）
  - `controller/tnbiz_internal/settings.go`（POST group_ratio_override/upsert + channel_log_settings/upsert）
  - `controller/tnbiz_internal/context.go`（context helper）
  - `controller/tnbiz_internal/health_test.go`
  - `model/channel_log_settings.go`（schema-only，B-13 partner 维度 channel log 配置）
- **冲突风险**：极低（独立子包 + 新增 model 表）
- **Feature flag**：与 B-12 共用 `overlay.internal_api_enabled`

### B-21 [tnbiz] OVERLAY feature flag 框架（biz_setting + 5-15s polling）
- **新增文件**：
  - `setting/overlay_flag/flag.go`（5 个 atomic-cached flag + ctx-first poller；优先读 `common.OptionMap`，env 兜底）
  - `setting/overlay_flag/flag_test.go`
- **修改文件**：`main.go`（`+overlayCtx := context.Background()` + `overlay_flag.StartPoller(overlayCtx)`，紧接 Redis 初始化之后）
- **冲突风险**：低（main.go 集中 patch + 新建独立子包）
- **Merge 策略**：5 个 flag key 写入 `OptionMap`，prod 默认全 false / shadow，灰度时按 PR 单独切换

### B-22 [tnbiz] User per-customer GroupRatioOverride（hot path 6 调用站 / 4 文件）
- **新增文件**：
  - `model/group_ratio_override.go`（`group_ratio_override` 表 + Upsert + LookupUserOverride DAO）
  - `setting/ratio_setting/effective_group_ratio.go`（`ApplyOverride(override, fallback) float64` —— hot path 唯一入口，atomic flag check + 1 float compare）
  - `setting/ratio_setting/effective_group_ratio_test.go`
  - `relay/common/override_lookup.go`（callback registry 避免 relay/common → model 的潜在循环依赖）
- **修改文件**（每处加 `// Fy-api overlay: B-15 ...` 注释）：
  - `relay/common/relay_info.go`（struct 末尾 +`UserGroupRatioOverride float64`；`GenRelayInfo` best-effort 从 context 拷入；context 缺失时回库一次）
  - `constant/context_key.go`（+`ContextKeyUserGroupRatioOverride`）
  - `service/quota.go`（行 110 / 115 / 121-124 三处 `GetGroupRatio` / `GetGroupGroupRatio` 后串 `ApplyOverride`）
  - `relay/helper/price.go`（行 53-62 user-group / normal-group 两个分支都串 `ApplyOverride`）
  - `service/task_billing.go`（行 276-277 task 路径用 `model.LookupUserOverride(task.UserId, group)` 因为没有 RelayInfo）
  - `service/group.go`（新增 `GetUserGroupRatioWithOverride(userId, userGroup, group)` 给 cold path 调用方）
  - `main.go`（+`relaycommon.SetOverrideLookup(model.LookupUserOverride)` 注入 callback）
- **冲突风险**：HIGH（ratio 系统是上游持续演进区；4 文件都打上 overlay 注释，merge 时 grep `B-15` 即可定位）
- **Feature flag**：`overlay.group_ratio_override`（prod 默认 false，逐 partner 灰度）

### B-23 [tnbiz] consume_log_outbox + RecordConsumeLog 同事务写 outbox
- **新增文件**：
  - `model/consume_log_outbox.go`（落 LOG_DB；status: pending/in_flight/published/failed/dead_letter；`(data_region, status)` + `(status, locked_until)` 双索引；`InsertOutboxInTx` / `LeaseOutboxBatch` / `MarkOutboxPublished` / `MarkOutboxFailed` 含 retry_count++ ≤ 10 → DLQ）
  - `model/log_outbox.go`（`recordConsumeLogWithOutbox` —— flag off 走原 `LOG_DB.Create(log)` 单语句；flag on 用 `LOG_DB.Transaction` 同时 Create(log)+Create(outbox)，任一失败整批回滚）
  - `model/log_outbox_integration_test.go`（in-memory sqlite 验证 flag on / flag off 两条路径）
- **修改文件**：
  - `model/log.go::RecordConsumeLog`（`LOG_DB.Create(log).Error` → `recordConsumeLogWithOutbox(...)`；函数顶部加注释规约「**仅** LogTypeConsume 走 outbox；行 87 / 112 / 139 / 183 / 292 等 5 个非 consume LOG_DB.Create 调用站一律 NOT outbox-eligible」；LogQuotaData fire-and-forget goroutine 仍在 TX 之后异步触发）
  - `model/main.go::migrateLOGDB`（+`LOG_DB.AutoMigrate(&ConsumeLogOutbox{})`）
- **冲突风险**：HIGH（log.go 是上游高活跃区；overlay 集中在 helper 函数，注释打满）
- **Feature flag**：`overlay.outbox_tx_enabled` + `overlay.outbox_mode`（off / shadow / enabled）

### B-24 [tnbiz] Outbox publisher (Aliyun MNS, shadow + enabled)
- **新增文件**：
  - `service/outbox/runner.go`（Publisher interface + NoopPublisher + Runner with batch/lease/interval；shadow 模式下 publisher inject NoopPublisher，仅 simulate；MNS SDK 接入留 Phase 2A）
  - `service/outbox/runner_test.go`
- **修改文件**：`main.go`（+`outbox.NewRunner(region, topic, nil).Start(overlayCtx)`，紧接 flag poller / OverrideLookup 注入之后）
- **冲突风险**：极低（独立子包 + main.go 一段 patch）
- **Feature flag**：`overlay.outbox_mode`（off / shadow / enabled），region 由 `DATA_REGION` 环境变量注入（cn / sg），强制 region 隔离 invariant

### B-25 [tnbiz] internal_idempotency + internal_api_key (HMAC keystore + idempotency 表)
- **新增文件**：
  - `model/internal_api_key.go` + `model/internal_api_key_test.go`（HMAC keystore：key_id / secret_cipher（AES-GCM with `common.CryptoSecret` 派生 KEK）/ region / status / allowed_endpoints JSON / created_at / rotated_at；明文 secret 永不入库）
  - `model/internal_idempotency.go`（`internal_idempotency` 表，UNIQUE(auth_kid, idempotency_key, endpoint)；`Lookup` / `Save` / `CleanupExpiredIdempotency`；7 天 TTL leader-only cron 兜底）
  - `middleware/internal_idempotency.go`（middleware：命中 (auth_kid, idem_key, endpoint) 三元组直接 replay 200，body hash 不一致则 409）
- **修改文件**：`model/main.go`（+`InternalAPIKey{}` / `InternalIdempotencyRecord{}` / `GroupRatioOverride{}` / `ChannelLogSetting{}` 进 AutoMigrate）
- **冲突风险**：极低（全独立 model + middleware）
- **Feature flag**：`overlay.hmac_keystore_enabled`（B-12 InternalAuth middleware 双 flag 校验：InternalAPI ON + HMACKeystore ON 才放行；任一 OFF 即 503）

### B-26 [aws/bedrock] cache_control.scope 剥离 + 空 text content block 过滤
- **新增文件**：
  - `relay/channel/aws/bedrock_content_filter.go`（cache_control.scope 剥离 + 空 text block 过滤，struct / pass-through 双路径）
  - `relay/channel/aws/bedrock_content_filter_test.go`（7 个 table-driven 测试覆盖：scope 剥离、保留无 scope 的 cache_control、string content 不 panic、空 text 过滤、非 text 保留、raw map 路径）
- **修改文件**：
  - `relay/channel/aws/dto.go`（`sanitizeBedrockClaudeRawFieldsFromStruct` +2 行调用）
  - `relay/channel/aws/relay-aws.go`（`sanitizeBedrockClaudeRawFields` +2 行调用）
- **背景**：SG 生产 momo 客户流量（channel #27 AWS Bedrock）5/21 出现 18 次 400 `cache_control.ephemeral.scope: Extra inputs are not permitted` 和 5 次 `text content blocks must be non-empty`。Bedrock schema 校验比 Anthropic 原生 API 更严格：(1) 不接受 `cache_control` 内的 `scope` 字段；(2) 不允许 `type:"text"` 且 `text:""` 的空块。
- **行为**：请求发往 Bedrock 前，静默移除 system/messages 中所有 `cache_control.scope`，并过滤掉空 text content block。不改变请求语义——`cache_control.type` 保留，非空 text block 保留。
- **冲突风险**：极低（独立新文件 + 两处各 +2 行，与 B-17 同一函数但不同行）

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

### F-8 [model-pricing] 管理端模型价格币种自选
- **修改文件**：
  - `web/classic/src/pages/Setting/Ratio/hooks/modelPricingCurrency.js`（新增 USD/CNY suffix、输入/展示换算、摘要换算 helpers；state 和后端 option 始终保持 USD）
  - `web/classic/src/pages/Setting/Ratio/hooks/useModelPricingEditorState.js`（接入币种换算 helpers 到编辑 state）
  - `web/classic/src/pages/Setting/Ratio/hooks/useModelPricingEditorState.test.js`（新增币种 suffix、汇率 fallback、USD/CNY 换算、摘要显示单元测试）
  - `web/classic/src/pages/Setting/Ratio/components/ModelPricingEditor.jsx`（计费方式下方新增币种按钮组，并为按量/按次价格输入动态 suffix 和展示值）
- **背景**：管理员模型价格编辑页原来硬编码 `$/1M tokens` / `$/次`，只能按美元输入；需求 REQ-20260512-01 要求可切换人民币输入，但后端 option 存储仍保持 USD。
- **行为**：默认 USD；所有计费模式都显示 USD/CNY 按钮；CNY 模式下输入框与左侧价格摘要按 `StatusContext.status.usd_exchange_rate` 展示人民币，输入时转换回 USD 存入编辑 state；表达式/阶梯计费本身仍按表达式原文保存。
- **冲突风险**：中（该编辑器是 upstream 设置页活跃区域）
- **Merge 策略**：若 upstream 重构模型价格编辑器，保留“UI 输入币种可选但 option 始终 USD”的存储语义，换算继续复用 `usd_exchange_rate`。

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

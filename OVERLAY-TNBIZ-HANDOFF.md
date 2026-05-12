# OVERLAY-TNBIZ-HANDOFF.md (B-12 .. B-18)

> 分支：`overlay/tnbiz-b12-b18`
> Round-3 FINAL ACCEPT 后 Fy-api 团队侧 PR-2 / PR-3 / PR-4 / PR-5 联合落地。
> PR-1（BIGINT migration）由 ops 走 gh-ost，**不属于本分支**。
>
> **2026-05-12 更新**：partner-api 消费方已落（`~/Projects/apiGateway/TraceNexBiz/apps/partner-api`，W3 Fix-A/B'/C/D 全部完成，commits `a5728b8` → `ec367d2`）。HMAC parity 双向已对齐：partner-api 客户端 `internal/infra/fyapi/client.go::sign` 与本目录 `middleware/internal_auth.go::BuildCanonical` 字节级一致（`TestSign_FyApiParity` 6 个 case 全通过）。Round-2 五方 review 0 CRITICAL，进入 Round-3 收口（详见 `~/Projects/apiGateway/docs/TraceNexBiz-Round3-接手清单-20260512.md`）。

## 1. 新增文件清单（25 个）

### B-12 + B-13 + B-18：路由 / controllers / HMAC 鉴权 / 幂等

| 文件 | LOC | 用途 |
|---|---|---|
| `router/api-internal-router.go` | 43 | `/api/internal/*` 独立路由组（**不**继承 `apiRouter` 的 `GlobalAPIRateLimit`） |
| `middleware/internal_auth.go` | 193 | HMAC-SHA256 + timestamp ±5min + nonce SETNX 24h（go-redis/v8 ctx-first）+ endpoint allowlist 精确匹配 |
| `middleware/internal_auth_test.go` | 47 | HMAC determinism / abs64 / canonical 形态 |
| `middleware/internal_idempotency.go` | 108 | Idempotency-Key 三元组 lookup / replay / 409 conflict |
| `model/internal_api_key.go` | 149 | HMAC keystore + AES-GCM 加密落库（KEK = sha256(CryptoSecret \|\| "tnbiz/internal-api/v1")） |
| `model/internal_api_key_test.go` | 58 | 加解密 roundtrip / wrong-key / 短 cipher 防 panic |
| `model/internal_idempotency.go` | 77 | `internal_idempotency` 表 DAO + cleanup cron |
| `model/channel_log_settings.go` | 42 | partner 维度 channel log 配置 schema |
| `controller/tnbiz_internal/health.go` | 60 | GET /health 自检 + envelope helper |
| `controller/tnbiz_internal/token.go` | 115 | POST /token/create（partner 永远不可见 sk-key 明文，仅返回 5min 一次性 delivery_handle） |
| `controller/tnbiz_internal/user.go` | 191 | POST topup / quota/adjust / refund + GET quota |
| `controller/tnbiz_internal/settings.go` | 89 | POST group_ratio_override/upsert + channel_log_settings/upsert |
| `controller/tnbiz_internal/context.go` | 13 | context helper |
| `controller/tnbiz_internal/health_test.go` | 72 | health endpoint flag-state surfacing + envelope shape |

### B-14：Feature flag 框架

| 文件 | LOC | 用途 |
|---|---|---|
| `setting/overlay_flag/flag.go` | 208 | 5 flag + atomic-cached + ctx-first 10s poller + biz_setting backend |
| `setting/overlay_flag/flag_test.go` | 83 | 默认值 / override / parseBool fallback |

### B-15：GroupRatioOverride hot path

| 文件 | LOC | 用途 |
|---|---|---|
| `model/group_ratio_override.go` | 69 | `group_ratio_override` 表 + Upsert + LookupUserOverride |
| `setting/ratio_setting/effective_group_ratio.go` | 26 | hot-path `ApplyOverride(override, fallback) float64` |
| `setting/ratio_setting/effective_group_ratio_test.go` | 34 | flag on/off + zero/negative override |
| `relay/common/override_lookup.go` | 44 | callback registry（避免 relay/common → model 的导入循环） |

### B-16 + B-17：consume_log_outbox + publisher

| 文件 | LOC | 用途 |
|---|---|---|
| `model/consume_log_outbox.go` | 151 | `consume_log_outbox` 表 DAO + 5 态状态机 + retry≤10 → DLQ + region 隔离索引 |
| `model/log_outbox.go` | 71 | `recordConsumeLogWithOutbox`：flag off → 单语句；flag on → `LOG_DB.Transaction(Create log + Create outbox)` |
| `model/log_outbox_integration_test.go` | 104 | sqlite in-memory：flag on → 双写；flag off → 仅 log，零 outbox 记录 |
| `service/outbox/runner.go` | 131 | Publisher interface + NoopPublisher + Runner（batch=50 / lease=30s / interval=2s） |
| `service/outbox/runner_test.go` | 51 | Noop 计数 / err publisher / 默认值 |

**新增 LOC 合计**：~2228 行（含测试）；非测试 ~1828 行。

## 2. 修改文件清单（11 个 upstream patch）

| 文件 | 改动 | OVERLAY 编号 |
|---|---|---|
| `OVERLAY.md` | +74 行（B-12..B-18 七条） | — |
| `router/main.go` | +1 行 `SetInternalRouter(router)` | B-12 |
| `main.go` | +`context` import；启动期 `overlay_flag.StartPoller` + `relaycommon.SetOverrideLookup` + `outbox.NewRunner(...).Start` | B-14 / B-15 / B-17 |
| `model/main.go` | AutoMigrate 加 4 个表 + LOG_DB 加 ConsumeLogOutbox | B-13 / B-15 / B-16 / B-18 |
| `model/log.go::RecordConsumeLog` | 单语句 → `recordConsumeLogWithOutbox(...)` + 顶部 invariant 注释 | B-16 |
| `relay/common/relay_info.go` | struct 末尾 +`UserGroupRatioOverride float64` + GenRelayInfo 注入 | B-15 |
| `constant/context_key.go` | +`ContextKeyUserGroupRatioOverride` | B-15 |
| `service/quota.go` | 行 110/115/121 三处 `ApplyOverride` | B-15 |
| `relay/helper/price.go` | 行 53-62 user-group / normal-group 两个分支都 `ApplyOverride` | B-15 |
| `service/task_billing.go` | 行 276-277 用 `model.LookupUserOverride` 因为没有 RelayInfo | B-15 |
| `service/group.go` | +`GetUserGroupRatioWithOverride(userId, ...)` for cold-path callers | B-15 |

## 3. 关键 API（OpenAPI §2 契约对齐）

| Method | Path | 说明 |
|---|---|---|
| GET | /api/internal/health | 自检（HMAC 通过即 200） |
| POST | /api/internal/token/create | partner 替 customer 发 sk-key（仅返回 masked + 5min delivery_handle） |
| POST | /api/internal/user/topup | 充值 quota |
| GET | /api/internal/user/quota?user_id= | 查余额 |
| POST | /api/internal/user/quota/adjust | quota saga 调整（含 saga_id） |
| POST | /api/internal/user/refund | 退款 saga |
| POST | /api/internal/group_ratio_override/upsert | partner 维度 group_ratio override |
| POST | /api/internal/channel_log_settings/upsert | partner 维度 channel log 设置 |

**HMAC 头**：`X-Auth-KeyId` / `X-Auth-Timestamp` / `X-Auth-Nonce` / `X-Signature`
（**注**：v1 初稿用 `X-Tnb-*` 前缀；落地时统一为 `X-Auth-*` 与 `docs/integration-design.md` v1.2 §1.1.3 一致，partner-api 客户端 `fyapi/client.go::sign` 与本目录 `middleware/internal_auth.go::BuildCanonical` 严格对齐。）
**幂等头**：`Idempotency-Key`（写接口必传，三元组键 = `auth_kid + idem_key + endpoint`）
**响应回放头**：命中幂等记录回放时附 `X-Tnb-Idempotent-Replay: 1`（响应头保留 `X-Tnb-` 前缀作为本 overlay 的标识，与请求侧 `X-Auth-` 不同）

## 4. Feature flag（biz_setting / OptionMap）

| Key | 取值 | 默认（prod-safe） | 控制范围 |
|---|---|---|---|
| `overlay.internal_api_enabled` | bool | false | B-12 InternalAuth middleware；off 即 503 |
| `overlay.hmac_keystore_enabled` | bool | false | B-12 keystore 校验；与 internal_api 双 flag AND |
| `overlay.outbox_mode` | off / shadow / enabled | off | B-16/B-17 outbox 行为档位 |
| `overlay.outbox_tx_enabled` | bool | false | B-16 同事务写 outbox（off 时 log.go 行为不变） |
| `overlay.group_ratio_override` | bool | false | B-15 hot-path 是否查 override |

Poller：10s ticker（落在 §14.1 推荐的 5-15s 区间），ctx-cancel 即停。

## 5. 测试覆盖

| Package | Test | Type |
|---|---|---|
| `setting/overlay_flag` | TestFlagDefaults / TestFlagOverrides / TestParseBoolFallback | unit |
| `model` | TestEncryptDecryptRoundtrip / TestDecryptWithWrongKey / TestDeriveKEKDeterministic / TestDecryptCipherTooShort | unit |
| `model` | TestOutboxTxWriteWhenFlagOn / TestOutboxTxBypassWhenFlagOff | integration（in-memory sqlite） |
| `middleware` | TestComputeHMACDeterministic / TestComputeHMACDiffersOnInputChange / TestAbs64 / TestCanonicalIncludesAllFields | unit |
| `service/outbox` | TestNoopPublisherCounts / TestErrPublisherReturnsError / TestRunnerNewDefaults | unit |
| `setting/ratio_setting` | TestApplyOverrideFlagOff / TestApplyOverrideFlagOnPositiveOverride / TestApplyOverrideFlagOnZeroOverride | unit |
| `controller/tnbiz_internal` | TestHealthEndpointReturnsFlagsState / TestRespondErrorEnvelope | unit（gin httptest） |

全部跑通：`go test ./setting/overlay_flag/ ./middleware/ ./model/ ./setting/ratio_setting/ ./service/outbox/ ./controller/tnbiz_internal/ -race` → ok。

`go build ./...` 全包零错误。

## 6. 安全 / Region 红线落实

- **HMAC secret 加密落库**：`InternalAPIKey.SecretCipher` 用 AES-GCM + 来自 `common.CryptoSecret` 派生的 KEK；明文 secret 永不入库（model/internal_api_key.go::CreateInternalAPIKey）。
- **nonce 重放**：`SetNX(ctx, "tnbiz:nonce:"+nonce, "1", 24h)` go-redis/v8 ctx-first；redis 不可用时 fail-closed 拒绝。
- **clock skew**：±5min 硬约束（`hmacClockSkew` 常量）。
- **endpoint allowlist**：case-sensitive 精确匹配（防前缀绕过）。
- **region 隔离**：`ConsumeLogOutbox.DataRegion` 来自 `DATA_REGION` 环境变量（cn / sg），`(data_region, status)` 联合索引强制 publisher 按 region 拉取；CN 事件不会被 SG publisher 拉走。
- **idempotency 冲突**：同 (auth_kid, idem_key, endpoint) 但 body 不同 → 409 Conflict。

## 7. 不变性 / 范围切出

- **未做**：PR-1 BIGINT migration（ops gh-ost 单独走）；MNS SDK 实际接入（Phase 2A）；KMS envelope（Phase 2A）；Nginx mTLS vhost；i18n 新文案。
- **未动**：既有 `/api/*` `/v1/*` `/dashboard/*` 路由及其 controller / billing 行为（向后兼容硬要求）—— 所有改动均 flag-gated。
- **flag 全 off 时**：行为与 main 分支字节级一致（`recordConsumeLogWithOutbox` flag off → 走原 `LOG_DB.Create(log)` 单语句；`ApplyOverride` flag off → 直接返回 fallback）。

## 8. 启动顺序（main.go 关键路径）

```
InitDB → InitOptionMap → InitLogDB → InitRedisClient
  ↓
overlay_flag.StartPoller(ctx)              # B-14：先于一切（其他 overlay 都读 flag）
relaycommon.SetOverrideLookup(...)         # B-15：注入 RelayInfo callback
outbox.NewRunner(...).Start(ctx)           # B-16/B-17：后台 publisher（NoopPublisher 占位）
  ↓
SetRouter → SetInternalRouter              # B-12
```

## 9. 已知未做事项 / 长期 debt

1. **MNS publisher 真实接入**：~~Phase 2A debt~~ — partner-api 侧 W3 已自行落 raw-HTTP MNS publisher/consumer（`TraceNexBiz/apps/partner-api/internal/outbox/aliyun_mns_{publisher,consumer}.go`），Fy-api 侧 `service/outbox/runner.go` 仍是 NoopPublisher。Fy-api 这边什么时候真接由产品决定（partner-api 已能独立 publish 到 MNS，Fy-api outbox 仍可保持 noop / shadow 模式）。
2. **internal_idempotency cleanup cron**：仍未挂上后台 leader-only 调度（review §11 LOW）。partner-api 侧已用 Redis SETNX leader（`pkg/leader/redis.go`），Fy-api 这边可参考同 pattern 把 `CleanupExpiredIdempotency` 接到 `subscription_reset_task.go` 启动期 cron 框架。
3. **CSPRNG keystore secret rotation Pub/Sub** + admin endpoint `/api/admin/internal/keys/rotate`：本 PR 仅落 model + create 函数，rotate API 留给运维实际需要时再加。
4. **`/user/group` 与 `/user/erase` handler 缺失**：partner-api 侧 `fyapi.Client.UpdateUserGroup` / `EraseUser` 因 Fy-api 端没有对应 handler 而返回非 retryable "not yet implemented" 错误（client_test.go::TestUpdateUserGroup_NotImplemented / TestEraseUser_NotImplemented）。`/user/erase` 是 PIPL §47 路径，Fy-api 这边需要补一个 handler；`/user/group` 由 customer transfer 流程触发。两者建议下一个 overlay PR 解决。
5. **Spec drift（partner-api 侧已记录）**：
   - `/user/refund`（Fy-api router）vs `/user/deduct`（integration-design §2.2.3 文本）—— 以 Fy-api router 为准
   - `quota` 字段（Fy-api 期望）vs `amount` 字段（spec 文本）—— 以 Fy-api 为准
   - `group_ratio_override/upsert` 用 POST（Fy-api router）vs PUT（spec 文本）—— 以 Fy-api 为准
   - 这些 drift 在 partner-api 客户端代码注释 + commit `07305d3` body 里都明确标注。本文档 §3 表格反映 Fy-api 实际 router 状态。

## 10. partner-api 消费方现状（2026-05-12）

partner-api 侧（`~/Projects/apiGateway/TraceNexBiz`）已落以下与本 overlay 对接的代码：

| 文件 | 用途 |
|---|---|
| `apps/partner-api/internal/infra/fyapi/client.go` | HMAC 4 元组 + 5 个真实方法（TopupCustomer/RefundCustomer/GetUserQuota/TokenCreate/GroupRatioOverrideUpsert）+ 2 stub（UpdateUserGroup/EraseUser，等 Fy-api handler） |
| `apps/partner-api/internal/infra/fyapi/client_test.go::TestSign_FyApiParity` | partner-api 客户端 sign() 与本目录 `middleware/internal_auth.go::BuildCanonical` 字节级一致性测试（6 个 case） |
| `apps/partner-api/internal/saga/{registry,instance,orchestrator}.go` | saga retry sweep step registry（消费 Fy-api 侧 5xx → retry，4xx → fail-fast）|
| `apps/partner-api/internal/outbox/aliyun_mns_*.go` | 独立 MNS publisher/consumer，与 Fy-api `service/outbox/runner.go` 解耦 |

partner-api 侧 Round-3 任务清单详见 `~/Projects/apiGateway/docs/TraceNexBiz-Round3-接手清单-20260512.md`。其中与 Fy-api overlay 相关的：
- partner-api `RefundCustomer` 把 `saga_id=idemKey` `order_ref=traceID` 字段反了（Round-2 Fy-api team review NEW-H1），需要核对本目录 `controller/tnbiz_internal/user.go::Refund` 期望的字段映射后修正
- partner-api `GetUserQuota` 只返回 `Quota int64`，丢了本目录 handler 返回的 `used_quota` / `aff_quota`（NEW-H2），改成结构体返回

---

— Fy-api OVERLAY 团队，2026-05-14（PR-2..PR-5 完成）

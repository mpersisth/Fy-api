# 2026-06-06 媒体模型计费审计报告

## 结论

本次排查发现一个会影响视频任务实际用量结算的代码问题：

- `service/task_polling.go::settleTaskBillingOnComplete` 原先在 `PerCallBilling=true` 时直接返回，导致 adaptor 的 `AdjustBillingOnComplete` 不会执行。
- 对 `wan2.6-i2v` / `wan2.6-r2v` 这类提交时预扣、完成后按上游 `usage.duration` 修正的任务，若被标记为按次计费，会跳过实际时长差额结算。
- 已修复为：先执行 adaptor 明确给出的实际额度，再让 per-call 任务跳过 token 重算。

另外发现生产可观测性不足：

- 任务日志的 `seconds`、`resolution-1080P` 等倍率只写在中文 `content` 中，`other` JSON 没有结构化字段。
- 已修复为：`LogTaskConsumption` 将 `PriceData.OtherRatios` 同步写入 `other`，便于报表和 e2e 断言。

## 生产只读检查

检查对象：CN 生产最近 14-30 天消费日志，只读聚合，不打印 DSN/密码。

关键结果：

| 模型 | 请求数 | quota 合计 | 平均 quota | 形态 |
| --- | ---: | ---: | ---: | --- |
| `gpt-image-2` | 692 | 50,739,728 | 73,323 | 图片同步日志，部分有 `image` / `image_output` |
| `gemini-3.1-flash-image-preview` | 619 | 21,104,922 | 34,095 | 图片/多模态 token 日志 |
| `wan2.6-i2v` | 27 | 48,744,188 | 1,805,340 | 视频任务日志，有 `seconds` 文本 |
| `wan2.6-t2i` | 10 | 3,424,660 | 342,466 | 图片固定价格日志 |
| `wan2.6-r2v` | 4 | 1,369,864 | 342,466 | 最近样本表现为图片固定价格日志 |

观察：

- `wan2.6-i2v` 任务预扣与 `seconds` / `resolution` 文本一致，例如 720P 5 秒约 1,712,325 quota，1080P 5 秒约 2,853,875 quota。
- `wan2.6-r2v` 最近几条是 `prompt_tokens=1`、`is_task` 为空、固定 342,466 quota，说明这些请求当时走了图片同步计费形态，而不是视频任务形态。需要在 cn-test 用新 e2e 脚本验证当前路由/渠道配置是否仍会复现。
- 生产旧日志 `other.seconds` / `other["resolution-1080P"]` 为 NULL 是历史可观测性问题，新代码会写结构化字段。

## 上游对照

上游 new-api v0.13.0 发布说明将 `expr` 表达式计费定位为动态/阶梯计费能力。当前本仓库实现也符合这个边界：表达式结算在文本/usage 路径，任务视频仍走 `ModelPriceHelperPerCall` + `OtherRatios` + 轮询完成差额结算。

上游近期也有图片计费相关修复（例如 release note 中的 `fix(image): only price image model use N ratio`），说明图片 N 倍率与 token usage 计费容易重复或漏算。本次单测覆盖了 `img` / `img_o` 自动排除，避免表达式计费重复收取图片输入/输出 token。

## 已改动

- `service/task_polling.go`
  - 调整完成态结算顺序：`AdjustBillingOnComplete` 优先于 `PerCallBilling` skip。
  - per-call 仍然跳过 `TotalTokens` token 重算，避免固定按次任务被 token 回包误改价。

- `service/task_billing.go`
  - `LogTaskConsumption` 将 `OtherRatios` 写入日志 `other`。

- `service/task_billing_test.go`
  - 新增/更新 per-call + adaptor 结算回归。
  - 新增任务日志 `other.seconds` / `other["resolution-1080P"]` 结构化字段测试。

- `service/tiered_settle_test.go`
  - 新增表达式图片输入 `img` 使用/不使用两类用例。
  - 新增表达式图片输出 `img_o` 使用/不使用两类用例。

- `scripts/ops/media_billing_e2e.py`
  - 新增 cn-test/staging 可运行 e2e。
  - 覆盖图片固定价格、i2v 视频任务计费、r2v 视频任务计费、日志结构化字段。

## 单元测试矩阵

| 层级 | 用例 | 预期 |
| --- | --- | --- |
| 表达式 | GPT usage 使用 `img` | `img` 从 `p` 扣除，单独按图片输入价计费 |
| 表达式 | GPT usage 不使用 `img` | 图片输入留在 `p` 兜底计费 |
| 表达式 | GPT usage 使用 `img_o` | `img_o` 从 `c` 扣除，单独按图片输出价计费 |
| 表达式 | GPT usage 不使用 `img_o` | 图片输出留在 `c` 兜底计费 |
| 任务结算 | `PerCallBilling=true` 且 adaptor 返回 actual quota | 执行差额结算 |
| 任务结算 | `PerCallBilling=true` 且只有 total_tokens | 跳过 token 重算 |
| 任务日志 | `OtherRatios` 有 seconds/resolution | 写入日志 `other` |
| Ali adaptor | `wan2.6` 默认 720P、非法分辨率 400、r2v media 数组、完成态 usage.duration | 保持现有回归通过 |

## cn-test e2e

安装依赖：

```bash
cd /Users/jimmy/go/src/tracenex/fy-api
python3 -m pip install -r scripts/ops/requirements.txt
```

Dry run：

```bash
FYAPI_E2E_BASE_URL=https://api-test.tracenex.cn \
FYAPI_E2E_TOKEN=sk-xxx \
python3 scripts/ops/media_billing_e2e.py --dry-run
```

只跑图片固定价格：

```bash
FYAPI_E2E_BASE_URL=https://api-test.tracenex.cn \
FYAPI_E2E_TOKEN=sk-xxx \
python3 scripts/ops/media_billing_e2e.py --skip-video
```

跑完整图片 + i2v + r2v：

```bash
FYAPI_E2E_BASE_URL=https://api-test.tracenex.cn \
FYAPI_E2E_TOKEN=sk-xxx \
FYAPI_E2E_IMAGE_URL=https://example.com/test-frame.png \
python3 scripts/ops/media_billing_e2e.py
```

通过标准：

- `image-fixed-price`：返回图片数据，最近日志 quota > 0，且不是 task 形态。
- `video-wan2.6-i2v`：任务完成，最近日志 `other.is_task=true`，`other.seconds > 0`，quota > 0。
- `video-wan2.6-r2v`：同上，必须是视频任务形态；如果仍然出现固定图片价日志，说明路由/渠道配置仍有问题。

## 剩余风险

- 生产旧日志不能补齐结构化 `other` 字段，只能从 `content` 解析历史倍率。
- `wan2.6-r2v` 生产样本显示过图片同步计费形态，需要在 cn-test 上用当前代码和当前渠道配置重跑确认。
- e2e 脚本依赖 `/api/log/token` 可用；若测试环境关闭该接口，需要改用管理员日志 API 或数据库只读校验。

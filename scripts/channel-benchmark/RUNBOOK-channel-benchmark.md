---
name: channel-benchmark
description: Use when testing a new channel's connectivity, load capacity, protocol compliance, token inflation (注水), model substitution (canary), and answer quality. Runs the full Go + Python test suite and generates a report.
---

# Channel Benchmark — 全量渠道测试

## Overview

对指定渠道执行完整的 6 项测试，生成综合报告。所有测试项**不可跳过**。

## Prerequisites

运行前需要收集以下信息（如不清楚，交互询问用户）：

1. **测试环境 URL** — 例如 `https://api-test.tracenex.cn`
2. **渠道 ID** — 要测试的目标渠道编号
3. **User Token** — `sk-...` 格式，必须属于 admin 用户（pin_channel 需要）
4. **Admin Token** — 用于 GET /api/channel/ 查询渠道信息
5. **Baseline 渠道 ID** — canary 测试用的可信对照渠道（提供相同模型）

如果 admin_token 未知，可通过 SSH 到测试服务器查询数据库获取。

## Test Suite

```dot
digraph test_flow {
  rankdir=LR;
  node [shape=box];
  "1. Go Smoke" -> "2. Loadtest" -> "3. Conformance" -> "4. Integrity" -> "5. Canary" -> "6. Quality" -> "Report";
}
```

所有测试均在 `scripts/channel-benchmark/` 下执行。

### Step 0: 查询渠道信息

```bash
curl -s -H "Authorization: $ADMIN_TOKEN" -H "New-Api-User: 1" \
  "$BASE_URL/api/channel/$CHANNEL_ID"
```

确认渠道名称和支持的模型列表，用于后续配置。

### Step 1: Go Benchmark（基础连通 + 延迟）

工具：`scripts/channel-benchmark/go/`

创建配置文件（或复用 `benchmark.local.yaml`），关键字段：
- `gateway.base_url` — 测试环境地址
- `gateway.admin_token` — admin access_token
- `gateway.user_token` — sk-... admin 用户 token
- `test.pin_channel: true` — 锁定渠道
- `channels[].id` — 目标渠道 ID
- `channels[].test_models` — 从 Step 0 获取的模型列表

```bash
cd scripts/channel-benchmark/go
go run . -config <config>.yaml
```

验证：所有请求成功率 100%，记录 E2E/TTFT P95。

### Step 2: fy-loadtest（负载压测）

工具：`scripts/channel-benchmark/py/`

配置关键字段：
- `gateway.channels[].pin_channel_id` — 目标渠道
- `load.models` — 测试模型列表
- `load.concurrency_levels` — 建议 [1, 5, 10, 20]
- `load.requests_per_level` — 建议 10+

```bash
cd scripts/channel-benchmark/py
source .venv/bin/activate
fy-loadtest -c <config>.yaml
```

验证：关注成功率、429/5xx/超时计数、吞吐拐点。

### Step 3: fy-conformance（协议合规 / 客户端兼容）

配置关键字段：
- `gateway.pin_channel_id` — 目标渠道
- `target.model` — 选一个渠道支持的模型

```bash
fy-conformance -c <config>.yaml
```

验证：关注 pass_rate，重点看 client_compat_* 和 openai_features 类别。

### Step 4: fy-integrity（诚信审计 + 注水检测）⚠️ 不可跳过

**token_inflation 探针必须成功执行，不能因为缺少 tiktoken 而跳过。**

前置条件：
```bash
uv pip install --python .venv/bin/python tiktoken
```

配置关键字段：
- `gateway.pin_channel_id` — 目标渠道
- `target.model` — 逐一测试每个模型
- `probes.inflation.enabled: true`
- `probes.inflation.tolerance_tokens: 10`

```bash
python -m fy_integrity run -c <config>.yaml
```

**对每个模型分别执行一次**。验证：
- token_inflation 必须显示 PASS 或 FAIL（不能是 SKIP）
- 如果 SKIP，排查 tiktoken 安装问题后重跑
- tool_use_passthrough 对非 Anthropic 模型会报 FAIL（`call_` vs `toolu_` 前缀），属于误报

### Step 5: fy-canary（模型一致性 / 替换检测）⚠️ 不可跳过

需要一个**可信对照渠道**作为 baseline。询问用户提供 baseline 渠道 ID。

两步走：

**5a. 录制 baseline（从对照渠道）：**
```bash
fy-canary baseline -c <baseline-config>.yaml
# source.pin_channel_id = 对照渠道 ID
```

**5b. 审计目标渠道：**
```bash
fy-canary audit -c <audit-config>.yaml
# source.pin_channel_id = 目标渠道 ID
# source.name 必须和 baseline 配置一致
```

**对每个模型分别执行 baseline + audit**。

验证：
- alignment 探针 edit-sim >= 0.70 为 PASS
- 如果 alignment 全部失败（edit-sim < 0.30），说明两渠道背后可能是不同模型版本
- drift 探针需要配置 embedding 客户端，否则会跳过

### Step 6: fy-quality（回答质量）

配置关键字段：
- `channels[].pin_channel_id` — 目标渠道
- `judges` — 配置两个 judge（双 judge 模式）
- `dataset` — 使用 `fy_quality/datasets/public/quality.jsonl`

```bash
fy-quality -c <config>.yaml
```

验证：关注 pass_rate、分类别通过率、具体失败原因。

## Step 7: 生成综合报告

将所有测试结果汇总为一份 markdown 报告，保存到 `scripts/channel-benchmark/py/reports/`。

报告结构：
1. **总评表** — 每项测试的结果和评级（优秀/良好/一般/需关注）
2. **各项测试详情** — 数据表格 + 失败项分析
3. **关键发现与建议** — 亮点、需关注项、后续建议

## 注意事项

### 不可跳过的测试
- token_inflation（注水检测）— 如果 tiktoken 未安装，必须先装好再跑
- fy-canary（模型一致性）— 必须有对照渠道，不能省略

### 常见问题
| 问题 | 解决方案 |
|------|----------|
| admin_token 未知 | SSH 到服务器查数据库：`SELECT access_token FROM users WHERE role=100` |
| admin_token 为 NULL | 生成一个：`UPDATE users SET access_token='<random>' WHERE id=1` |
| tiktoken 未安装 | `uv pip install --python .venv/bin/python tiktoken` |
| tool_use FAIL (call_ vs toolu_) | 非 Anthropic 模型的正常行为，属于误报 |
| canary alignment 全部失败 | 两渠道可能是不同模型版本，需向供应商确认 |
| drift 探针跳过 | 需配置 embedding 客户端（需要 OpenAI API key） |

### 交互式输入清单

执行前必须向用户确认：
- [ ] 测试环境 URL
- [ ] 目标渠道 ID 和名称
- [ ] User Token（sk-... 格式，admin 用户）
- [ ] Admin Token（如未知，是否允许从服务器数据库查询/生成）
- [ ] Baseline 对照渠道 ID（canary 用）
- [ ] 要测试的模型列表（或从 API 自动获取）

### Pin Channel 要求

所有测试都使用 `pin_channel` 功能锁定目标渠道。这要求：
- user_token 必须属于 admin 用户（role=100）
- 非 admin token 会收到 403："普通用户不支持指定渠道"

### 配置文件命名约定

为每次测试创建独立配置文件，命名格式：`<tool>-ch<id>.yaml`
- `benchmark-ch6.yaml`
- `loadtest-ch6.yaml`
- `conformance-ch6.yaml`
- `integrity-ch6.yaml`
- `canary-ch6-baseline.yaml`
- `canary-ch6-audit.yaml`
- `quality-ch6.yaml`

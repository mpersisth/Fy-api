# channel-benchmark

A small toolkit for **measuring Fy-api channels** along five orthogonal axes. The tools live in two language ecosystems on purpose — pick the one whose constraints match the question you're asking.

```
channel-benchmark/
├── go/                Smoke tester. Single binary, zero deps. Run on prod.
├── py/                Five CLIs sharing one venv:
│   ├── fy-loadtest     Concurrency-ramp load testing
│   ├── fy-quality      Quality scorecard (multi-grader, dual LLM judge)
│   ├── fy-canary       Model-substitution / drift detection
│   ├── fy-conformance  Protocol-conformance assertions (4xx vs 5xx, leak checks)
│   └── fy-score        Channel scorecard (SLO-anchored absolute rating, A/B/C/D/F)
└── incidents/         Case studies that produced regression artifacts.
                       Each card pairs a customer outage with the test
                       hooks added across go/ + py/ to detect a recurrence.
```

Everything talks to Fy-api over the OpenAI-compatible `/v1/chat/completions` path with a real user token, so runs are billed as real traffic. Keep the user's quota modest — it doubles as a budget cap.

## Pick a tool by the question you're asking

| Question | Tool | Why this one |
|---|---|---|
| "Are these channels even alive right now? Who's slow?" | **`go/`** | Zero-dep binary, can run on any prod box, hits real relay path so it sees TTFT + usage (unlike the built-in 测试 button which only returns `{success, time}`). |
| "Will this channel survive 50 concurrent users?" | **`fy-loadtest`** | 1→N concurrency ramp, full E2E/TTFT/ITL/TPOT percentile suite, goodput-vs-SLO. |
| "Is this channel actually answering correctly?" | **`fy-quality`** | Golden JSONL + 7 graders (exact / regex / contains / json_schema / rubric / similarity / pairwise) + dual-judge to cut false positives. |
| "Has this channel been silently swapped to a cheaper model?" | **`fy-canary`** | Records a trusted baseline against the vendor API directly, then audits the gateway for divergence via alignment-template / embedding-drift / MMD. |
| "Does the gateway return 4xx (not 5xx) for client errors, and not leak Go internals?" | **`fy-conformance`** | 94+ deterministic assertions on parameter-validation, malformed-JSON, auth, and field-presence cases. Locks in HTTP-semantics regressions like the `cannot unmarshal ... GeneralOpenAIRequest.max_tokens` leak fixed in 2026-05. |
| "How does this channel compare overall? Give me a single grade." | **`fy-score`** | Reads results from the other tools, applies SLO-anchored scoring (4 dimensions: availability/performance/quality/authenticity), outputs A/B/C/D/F grade per (channel, model). |

## How they relate (and don't)

The five are **stacked, not interchangeable**:

```
        ┌───────────────────────────────────────────────────────────┐
        │  Layer 0 — go/                                            │
        │  liveness · TTFT · usage sanity · explicit model list     │
        │  (run in prod; safe to put on a 5-min cron)               │
        └─────────────────────────┬─────────────────────────────────┘
                                  │ when a channel passes layer 0
                                  ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ fy-loadtest      │  │ fy-quality       │  │ fy-canary        │
   │ (capacity)       │  │ (correctness)    │  │ (substitution)   │
   │                  │  │                  │  │                  │
   │ before scaling   │  │ before promoting │  │ ongoing trust    │
   │ traffic to it    │  │ to a group       │  │ (weekly audit)   │
   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
            │                      │                      │
            └──────────────────────┼──────────────────────┘
                                   ▼
                        ┌──────────────────┐
                        │ fy-score         │  Aggregates all results into
                        │ (scorecard)      │  a single A-F grade per
                        │                  │  (channel, model) pair.
                        │ after all tests  │
                        └──────────────────┘

   ┌──────────────────┐
   │ fy-conformance   │   Cross-cutting: run after every Fy-api
   │ (HTTP semantics) │   release as a regression gate.
   │                  │   Asserts on the GATEWAY's behavior, not
   │ after gateway    │   the upstream model — independent of the
   │ deploys          │   other four.
   └──────────────────┘
```

What's **shared**:

- All four target Fy-api's `/v1/chat/completions`, OpenAI-compatible schema.
- `fy-quality` and `fy-canary` share a single JSONL row format (`{id, kind, prompt, ...}`).
- TTFT / ITL / E2E percentile math is consistent (linear-interpolation, NumPy-compatible) between the Go tool and `fy-loadtest`.

What's **deliberately not shared**:

- Configs, CLIs, and report formats are independent. A change in one tool doesn't ripple to the others.
- Go and Python don't import from each other — the Go binary stays drop-on-prod simple; the Python tools are free to pull torch / SDKs / numpy.

## Why two languages

| Concern | Decision |
|---|---|
| "I want to ssh into a prod box and check if a channel is dead." | Go. No `pip install`, no venv, no torch. One static binary. |
| "I want to run an MMD two-sample test from Gao et al." | Python. `model-equality-testing` exists, scipy/torch exist. Re-implementing in Go is a research project we're not doing. |
| "I need OpenAI/Anthropic/Gemini SDKs for embeddings + judge calls." | Python. Vendor SDKs land there first and are most stable. |
| "I want pytest + httpx.MockTransport e2e tests against the full SSE/grader chain." | Python. 31 tests run with no network. |

The split is along **deployment surface**, not language preference.

## Conventions across the toolkit

1. **Real traffic, real billing.** Every tool authenticates with a regular `sk-...` user token and consumes real quota. There is no shadow path. The user's quota is your budget cap.
2. **Explicit model lists.** No tool falls back to a "default model" — you spell out which models to test, every time. Silent billing surprises are worse than loud config errors.
3. **Stream + non-stream.** Defaults exercise both. Channels often misbehave only under streaming; you want to see it.
4. **Env-var interpolation in YAML.** All configs accept `${VAR}` and `${VAR:-default}`. Keep secrets out of the file.
5. **JSON + CSV + Markdown reports.** Every run drops machine-readable JSON/CSV plus a human-readable Markdown summary into a per-tool `*-results/` directory.

## Getting started

```bash
# Layer 0 — Go smoke test (one-shot)
cd go && go run . -config channel-benchmark.yaml

# Same binary as a long-lived Prometheus exporter:
cd go && go run . -config channel-benchmark.yaml -prom-listen :9090 -prom-interval 5m

# Layer 1 — Python tools (one venv, three CLIs)
cd py
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .            # all three CLIs
uv pip install --python .venv/bin/python -e ".[canary]"  # adds MMD (torch, ~1.5GB)
source .venv/bin/activate

fy-loadtest -c loadtest.yaml
fy-quality  -c quality.yaml
fy-canary   baseline         -c canary.yaml   # record trusted baseline
fy-canary   audit            -c canary.yaml   # refuse if baseline > 30d
fy-canary   verify-baseline  -c canary.yaml   # re-record mini-baseline, flag source drift
fy-conformance -c conformance.yaml             # 4xx semantics + leak checks

# Layer 2 — Scorecard (aggregates results from above)
fy-score --loadtest-dir loadtest-results/ \
         --canary-dir canary-results/ \
         --quality-dir quality-results/ \
         --output scorecard.json --markdown scorecard.md
```

See `go/README.md` and `py/README.md` for tool-specific details.

## Recent upgrades (2026-05)

- **Prometheus exporter** in the Go tool (`-prom-listen`, `-prom-interval`).
  Zero-dep exposition; emits `channel_benchmark_ttft_seconds`,
  `channel_benchmark_request_total{outcome=...}`,
  `channel_benchmark_run_age_seconds`, etc.
- **Baseline health checks** in `fy-canary`. Every baseline file carries
  `recorded_at_iso` + `n_probes` + version metadata. `audit` refuses to run
  against a baseline older than `baseline_max_age_days` (default 30). The
  new `verify-baseline` subcommand re-queries the SAME source to detect
  vendor-side drift before that drift poisons audit results.
- **Dataset contamination defense** in `fy-quality`. Two layers:
    - `fy_quality/datasets/public/` (committed, starter suite) vs
      `fy_quality/datasets/private/` (gitignored — your real prompts).
    - Per-row `seed` + `perturbations` apply deterministic, semantics-
      preserving tweaks (ZWSP insertion, trailing HTML marker, reviewed
      synonym map) so the text the model sees is never byte-identical
      to anything that might be in its training data.

## What's intentionally out of scope (today)

- **No CI hooks / scheduler.** These are manual diagnostic tools. When you want them on a cron, wire one yourself.
- **No retries.** Smoke + load + quality + canary are all diagnostics; retries hide flakiness.
- **No central database.** Results are files on disk. Aggregation across runs is your problem (and a small one).
- **No distributed load generation.** `fy-loadtest` is single-process. If you need >1k RPS sustained, run multiple instances against the same target.
- **No judge-of-judges calibration.** The dual-judge in `fy-quality` is a heuristic, not a calibrated detector.

## Incident-driven regressions

`incidents/` is the long-term memory of the toolkit. Every customer-facing
failure that produced a permanent test hook gets a card here. The card is
not a post-mortem — it's the human-readable index for the regression
artifacts scattered across `go/` and `py/`. Read the card first, then the
artifacts it points at.

| Card | What broke | Regression artifacts shipped |
|---|---|---|
| [`2026-05-11-long-reasoning-timeout.md`](./incidents/2026-05-11-long-reasoning-timeout.md) | aime25 / gpqa-diamond on `kimi-k2-thinking` cut at 600s by Fy-api `RELAY_TIMEOUT`; nginx 900s couldn't save it | `go/ -long-thinking` flag + preset; `py/loadtest.long-thinking.yaml`; `py/fy_loadtest/fixtures/` long-reasoning prompts; `py/tests_conformance/test_long_reasoning_timeout.py` |
---

## 综合压测提示词模板

以下模板用于指导 AI 助手执行完整的渠道质量评估流程。使用时替换 `xxx` 为实际值即可。

```
对 Fy-api 网关进行综合通道质量评估，生成完整测试报告markdown文件。

测试目标
  - 网关地址: https://api-test.tracenex.cn/
  - Token: sk-xxx（必须属于 admin 用户，pin_channel 需要 admin 权限）
  - 测试模型（文本）: xxx, xxx
  - 测试模型（图片，如有）: xxx
  - 渠道 ID: xxx（渠道名: xxx）
  - Baseline 对照渠道 ID: xxx（canary 用，提供相同模型的可信渠道）
  - Judge 模型（用于 fy-quality rubric 评分）: claude-haiku-4-5-20251001
  - Embedding 模型（用于 fy-quality similarity + fy-canary drift）: text-embedding-v1
    注意: 使用前先确认网关上该 embedding 模型可用（GET /v1/models 检查）。
    如果不可用，换成网关已配置的 embedding 模型。
  - Admin Token（Go 工具用，可选）: xxx
    注意: Go 工具的 admin_token 是后台登录的 access_token（不带 sk- 前缀），
    不是 API key。如果只有 sk- token，跳过 Go smoke，用 fy-loadtest C=1 替代。

前置步骤：环境准备（必须在任何测试执行前完成）

  A. Python 依赖安装
     ```bash
     cd scripts/channel-benchmark/py
     source .venv/bin/activate  # 如果 venv 不存在则先创建
     # 确认关键依赖已安装：
     pip install tiktoken       # fy-integrity 注水探针必需，缺少会 SKIP
     ```

  B. 验证 embedding 模型可用性
     ```bash
     curl -s -H "Authorization: Bearer $TOKEN" $BASE_URL/v1/models | grep -i embed
     ```
     如果目标 embedding 模型不在列表中，需要换一个可用的。
     embedding 模型不可用会导致：fy-canary drift 探针无法计算 centroid、
     fy-quality similarity grader 跳过。

  C. 配置所有 YAML
     在开始测试前，使用上面提供的参数重新生成所有配置文件。不要沿用旧配置。
     同一个网关地址和 Token 同时用于被测渠道、judge 调用和 embedding 调用。
     配置文件命名约定：`<tool>-ch<渠道ID>.yaml`

     1. `py/loadtest-ch{id}.yaml` — 确保包含：
        - gateway.base_url / user_token
        - gateway.channels[].pin_channel_id 指向目标渠道
        - load.models 列出所有测试模型
        - load.concurrency_levels: [1, 10, 30, 50, 100]
        - load.requests_per_level: 30
        - load.stream: true
        - export.formats: [json, csv, markdown]（不含 pdf，避免 reportlab 依赖）

     2. `py/quality-ch{id}.yaml` — 确保包含：
        - channels[] 列出每个 (模型, 渠道) 组合，带 pin_channel_id
        - judges[] 配置至少一个 judge（用同网关 + token + judge 模型）
          ⚠ judge 模型不能是被测模型本身（不能自己评自己）
          ⚠ 如果 judge 模型不在当前渠道上，不要给 judge 配 pin_channel_id
        - embedding 配置（用同网关 + token + embedding 模型）
        - output_formats 不含 pdf（避免 reportlab 依赖问题）

     3. `py/canary-ch{id}-baseline.yaml` + `py/canary-ch{id}-audit.yaml`
        每个测试模型需要独立的 source 配置：
        - source.name = "{model}-ch{channel_id}"（baseline 和 audit 必须一致）
        - baseline 配置: source.pin_channel_id = 对照渠道 ID
        - audit 配置: source.pin_channel_id = 目标渠道 ID
        - embedding 配置（同网关 + token + embedding 模型）
        - ⚠ embedding 模型必须在网关上可用，否则 drift 探针无法计算 centroid
        - ⚠ 如果无对照渠道，可只跑 metadata + tokenizer 探针（无需 baseline）

     4. `py/conformance-ch{id}.yaml` — 确保包含：
        - gateway.base_url / user_token / pin_channel_id
        - target.model — 选一个渠道支持的模型
        - target.baseline_request — 标准请求体
        - dataset: fy_conformance/datasets/public/conformance.jsonl

     5. `py/integrity-ch{id}.yaml` — 确保包含：
        - gateway.base_url / user_token / pin_channel_id
        - target.model — 每个模型需要单独执行一次
        - probes.inflation.enabled: true
        - probes.inflation.tolerance_tokens: 10
        - probes.cache.enabled: true
        - probes.determinism.enabled: true
        - probes.tool_use.enabled: true
        - probes.stream.enabled: true
        - probes.filtering.enabled: true
        - probes.isolation.enabled: false（需要 secondary_token，没有则关闭）
        ⚠ 如果有第二个 token（不同用户），设置 gateway.secondary_token 并开启 isolation

     6. `go/benchmark-ch{id}.yaml`（如有 admin access token）：
        - gateway.admin_token — 后台 access_token（不带 sk-）
        - gateway.user_token — sk-... admin 用户 token
        - test.pin_channel: true
        - channels[].id / test_models

     7. `py/image-loadtest-ch{id}.yaml`（如有图片模型）：
        - gateway.base_url / user_token
        - gateway.channels[].pin_channel_id
        - image.model — 图片模型名
        - image.size / quality / response_format

     8. `py/image-conformance-ch{id}.yaml`（如有图片模型）：
        - gateway.base_url / user_token
        - gateway.channels[].pin_channel_id
        - model.name — 图片模型名
        - suites 各项开关

进行以下八轮测试（按顺序执行，前一步失败不阻塞后续步骤）

  1. 存活性 + TTFT 冒烟
     - 优先用 go 工具（需要 admin access token）：
       ```bash
       cd scripts/channel-benchmark/go
       go run . -config benchmark-ch{id}.yaml
       ```
     - 如果只有 sk- API key，用 fy-loadtest 替代：
       ```bash
       fy-loadtest -c loadtest-ch{id}.yaml --concurrencies 1 --reps 5
       ```
     - 验证：所有请求成功率 100%，记录 E2E/TTFT P95

  2. 协议合规（fy-conformance）⚠ 不可跳过
     ```bash
     fy-conformance -c conformance-ch{id}.yaml
     ```
     - 验证：关注 pass_rate，重点看 client_compat_* 和 openai_features 类别
     - 如果 pass_rate < 80%，说明渠道有严重的协议兼容问题
     - 关注是否泄漏 Go struct 字段名（如 `GeneralOpenAIRequest.max_tokens`）

  3. 诚信审计 + 注水检测（fy-integrity）⚠ 不可跳过
     **token_inflation 探针必须成功执行，不能因为缺少 tiktoken 而 SKIP。**

     对每个模型分别执行一次（修改 integrity.yaml 的 target.model）：
     ```bash
     python -m fy_integrity run -c integrity-ch{id}.yaml
     ```

     验证：
     - token_inflation 必须显示 PASS 或 FAIL（不能是 SKIP）
       如果 SKIP，排查 tiktoken 安装问题后重跑：
       `pip install tiktoken && python -m fy_integrity run -c integrity-ch{id}.yaml`
     - cache_integrity: PASS = 渠道正确处理缓存
     - determinism: PASS = 温度=0 时输出一致
     - stream_repackaging: PASS = 流式 chunk 未被重新打包
     - tool_use_passthrough: 对非 Anthropic 模型会报 FAIL（`call_` vs `toolu_` 前缀），
       属于已知误报，在报告中标注即可
     - content_filtering: PASS = 渠道未额外过滤上游允许的内容

  4. 并发压测（fy-loadtest）
     ```bash
     fy-loadtest -c loadtest-ch{id}.yaml
     ```
     - 并发阶梯: 1, 10, 30, 50, 100
     - 每级请求数: 30
     - stream: true
     - 注意: 多模型 suite 模式会为每个模型单独生成文件。如果观察到
       输出文件只有一个模型的数据，说明文件名冲突被覆盖了——
       此时改为每个模型单独执行（用 --model 参数）。
     - 验证：关注成功率、429/5xx/超时计数、吞吐拐点

  5. 质量评估（fy-quality）
     ```bash
     fy-quality -c quality-ch{id}.yaml
     ```
     - 确认 judges 和 embedding 已配置，否则 rubric/similarity 题会跳过
     - 如果 judge 模型不在当前渠道上，不要 pin_channel_id judge 的请求
     - 验证：关注 pass_rate、分类别通过率、具体失败原因

  6. 金丝雀检测（fy-canary）⚠ 不可跳过

     6a. 快速检测（无需 baseline，立即可用）：
         metadata + tokenizer 探针是 stateless 的，不需要对照渠道。
         如果 canaries.jsonl 中包含 method="metadata" 和 method="tokenizer" 的行，
         且数据集中只有这些行，可以直接 audit 无需 baseline：
         ```bash
         fy-canary audit -c canary-ch{id}-audit.yaml
         ```
         这会验证：
         - 响应 model 字段是否与请求模型一致（检测偷换）
         - usage.prompt_tokens 是否在预期范围内（检测 tokenizer 替换）
         - finish_reason / role / content 结构完整性

     6b. 完整检测（需要 baseline）：
         需要一个可信对照渠道。
         - 检查 canary-baselines/ 是否有对应 baseline 文件
         - 没有则先录 baseline（从对照渠道）：
           ```bash
           fy-canary baseline -c canary-ch{id}-baseline.yaml
           ```
         - 录完后验证 baseline 质量：
           - alignment 探针应有 samples（n_samples 在 canaries.jsonl 中配置）
           - drift 探针应有 centroid（需要 embedding 配置正确且模型可用）
           - 如果 centroid 为空，说明 embedding 调用失败——检查模型名是否正确
           - metadata/tokenizer 行会显示 "stateless probe, skipped baseline"（正常）
         - baseline 就绪后执行 audit：
           ```bash
           fy-canary audit -c canary-ch{id}-audit.yaml
           ```
         - 每个模型分别执行（修改 canary.yaml 的 source.name 和 model）

     验证：
     - alignment: edit-sim >= 0.70 为 PASS
     - drift: centroid-cos >= 0.93 为 PASS
     - metadata: 6 项全通过为 PASS（model/usage/tokens/finish/role/content）
     - tokenizer: prompt_tokens 在预期范围内为 PASS
     - 如果 alignment 全部失败（edit-sim < 0.30），说明两渠道背后可能是不同模型

  7. 图片模型测试（如有图片模型）

     7a. 图片合规（fy-image-conformance）：
         ```bash
         python -m fy_image_conformance -c image-conformance-ch{id}.yaml
         ```
         验证：API 兼容性、输出有效性、安全性

     7b. 图片压测（fy-image-loadtest）：
         ```bash
         fy-image-loadtest -c image-loadtest-ch{id}.yaml
         ```
         验证：成功率、生成延迟、并发稳定性

     如果没有图片模型，跳过此步并在报告中说明。

  8. 综合评分（fy-score）
     ```bash
     fy-score --loadtest-dir loadtest-results/ \
              --quality-dir quality-results/ \
              --canary-dir canary-results/ \
              --conformance-dir conformance-results/ \
              --integrity-dir integrity-results/ \
              --channel-id {渠道ID} --channel-name "{渠道名}" \
              --output scorecard.json --markdown scorecard.md
     ```

输出要求

  汇总一份中文 markdown 报告，保存到 `scripts/channel-benchmark/py/reports/` 目录。
  严格按以下顺序组织：

  1. 总体结论（报告第一段）
     - 第一句话直接给出判定：渠道可用/不可用
     - 紧接着用 scorecard 等级表（A/B/C/D/F）概括各渠道各模型的综合评分
     - 然后说明：哪个模型适合高并发、哪个延迟更低

  2. Scorecard 详情
     - 五维度分数表（可用性/性能/质量/真实性/合规性）
     - 被门槛否决的渠道单独标注原因
     - 如有 flag（如"疑似模型替换"、"token 注水"），醒目提示

  3. 优化问题（按优先级 P0→P3 排序）
     P0: 模型替换/注水/协议严重不兼容
     P1: 高并发下错误率 > 5% / 限流严重
     P2: 质量评分低于 80% / 延迟偏高
     P3: 非关键项失败（如 tool_use 前缀差异）

  4. 工具选型说明
     - 列出全部 11 个工具模块
     - 每个工具是否使用，未使用给出原因
     - 特别标注：integrity 和 canary 是否成功执行（不可跳过）

  5. 存活性 + TTFT 冒烟详细分析
  6. 协议合规详细分析
     - pass_rate 总览
     - 失败用例分类（参数校验/结构/认证/畸形请求）
     - 是否有 Go 内部信息泄漏
  7. 诚信审计详细分析
     - 各探针结果表（PASS/FAIL/SKIP）
     - token_inflation: 本地计数 vs API 报告的 delta 值
     - 已知误报标注（如 tool_use 的 call_ vs toolu_）
  8. 质量评估详细分析
  9. 金丝雀检测详细分析
     - stateless 探针结果（metadata/tokenizer）
     - stateful 探针结果（alignment/drift/mmd）
     - 如有 model mismatch 或 tokenizer 偏移，醒目标注
  10. 并发压测详细分析
      - 各并发级别的 RPM / TPM / 延迟 / 错误率对比表
      - 瓶颈并发点分析
      - 是否触发限流（429）、是否有服务端错误（5xx）
  11. 图片模型测试详细分析（如有）
  12. 原始数据索引
      - 保留原始 JSON 路径供后续分析
      - 包含 scorecard.json 路径

常见问题排查

  | 问题 | 解决方案 |
  |------|----------|
  | admin_token 未知 | SSH 到服务器查数据库：SELECT access_token FROM users WHERE role=100 |
  | admin_token 为 NULL | 生成一个：UPDATE users SET access_token='<random>' WHERE id=1 |
  | tiktoken 未安装导致 inflation SKIP | pip install tiktoken，然后重跑 integrity |
  | tool_use FAIL (call_ vs toolu_) | 非 Anthropic 模型的正常行为，属于误报 |
  | canary alignment 全部失败 | 两渠道可能是不同模型版本，需向供应商确认 |
  | drift 探针跳过 | 需配置 embedding 客户端且模型在网关上可用 |
  | canary 无对照渠道 | 只跑 metadata + tokenizer（stateless，无需 baseline） |
  | embedding 模型不可用 | curl 检查 /v1/models，换成网关已有的 embedding 模型 |
  | 多模型 loadtest 文件覆盖 | 改为每个模型单独执行（--model 参数） |
  | pin_channel 403 | token 必须属于 admin 用户（role=100） |
  | reportlab 依赖缺失 | export.formats 中去掉 pdf |
  | conformance 大量 5xx | 渠道可能不支持该模型，确认 model 在渠道的 models 列表中 |
```

# channel-benchmark

A small toolkit for **measuring Fy-api channels** along five orthogonal axes. The tools live in two language ecosystems on purpose — pick the one whose constraints match the question you're asking.

```
channel-benchmark/
├── go/                Smoke tester. Single binary, zero deps. Run on prod.
└── py/                Five CLIs sharing one venv:
    ├── fy-loadtest     Concurrency-ramp load testing
    ├── fy-quality      Quality scorecard (multi-grader, dual LLM judge)
    ├── fy-canary       Model-substitution / drift detection
    ├── fy-conformance  Protocol-conformance assertions (4xx vs 5xx, leak checks)
    └── fy-score        Channel scorecard (SLO-anchored absolute rating, A/B/C/D/F)
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

---

## 综合压测提示词模板

以下模板用于指导 AI 助手执行完整的渠道质量评估流程。使用时替换 `xxx` 为实际值即可。

```
对 Fy-api 网关进行综合通道质量评估，生成完整测试报告markdown文件。

测试目标
  - 网关地址: https://api-test.tracenex.cn/
  - Token: sk-xxx
  - 测试模型: xxx
  - 渠道 ID: xxx（渠道名: xxx）
  - Judge 模型（用于 fy-quality rubric 评分）: claude-haiku-4-5-20251001
  - Embedding 模型（用于 fy-quality similarity + fy-canary drift）: text-embedding-v1
    注意: 使用前先确认网关上该 embedding 模型可用（GET /v1/models 检查）。
    如果不可用，换成网关已配置的 embedding 模型。

前置步骤：配置所有 YAML（必须在任何测试执行前完成）

  在开始测试前，使用上面提供的参数重新生成所有配置文件。不要沿用旧配置。
  同一个网关地址和 Token 同时用于被测渠道、judge 调用和 embedding 调用。

  1. `py/loadtest.yaml` — 确保包含：
     - gateway.base_url / user_token
     - channels[].pin_channel_id 指向目标渠道
     - load.models 列出所有测试模型
     - 并发阶梯、请求数、stream、导出格式

  2. `py/quality.yaml` — 确保包含：
     - channels[] 列出每个 (模型, 渠道) 组合，带 pin_channel_id
     - judges[] 配置至少一个 judge（用同网关 + token + judge 模型）
     - embedding 配置（用同网关 + token + embedding 模型，必须与 canary.yaml 一致）
     - output_formats 不含 pdf（避免 reportlab 依赖问题）

  3. `py/canary.yaml` — 每个测试模型需要独立的 source 配置：
     - source.name = "{model}-ch{channel_id}"
     - source.model / base_url / api_key / pin_channel_id
     - embedding 配置（同网关 + token + embedding 模型）
     - ⚠ embedding 模型必须在网关上可用，否则 drift 探针无法计算 centroid。
       配置前先验证：`curl -H "Authorization: Bearer $TOKEN" $BASE_URL/v1/models | grep embed`

  4. `go/xxx.yaml`（如有 admin access token）：
     - 注意: Go 工具的 admin_token 需要的是后台登录的 access_token
       （不带 sk- 前缀），不是 API key。如果只有 sk- token，
       跳过 Go smoke，用 fy-loadtest C=1 替代存活性检测。

进行以下五轮测试
  1. 存活性 + TTFT 冒烟
     - 优先用 go 工具（需要 admin access token）
     - 如果只有 sk- API key，用 fy-loadtest --concurrencies 1 --reps 5 替代

  2. 并发压测（fy-loadtest）
     - 并发阶梯: 1, 10, 30, 50, 100
     - 每级请求数: 30
     - stream: true
     - 注意: 多模型 suite 模式会为每个模型单独生成文件。如果观察到
       输出文件只有一个模型的数据，说明文件名冲突被覆盖了——
       此时改为每个模型单独执行（用 --model 参数）。

  3. 质量评估（fy-quality）
     - 确认 judges 和 embedding 已配置，否则 rubric/similarity 题会跳过
     - 如果 judge 模型不在当前渠道上，不要 pin_channel_id judge 的请求

  4. 金丝雀检测（fy-canary）
     - 检查 canary-baselines/ 是否有对应 baseline 文件
     - 没有则先录 baseline：`fy-canary baseline -c canary.yaml`
     - 录完后验证 baseline 质量：
       - alignment 探针应有 ≥5 个 samples（n_samples 在 canaries.jsonl 中配置）
       - drift 探针应有 centroid（需要 embedding 配置正确且模型可用）
       - 如果 centroid 为空，说明 embedding 调用失败——检查模型名是否正确
     - baseline 就绪后执行 audit：`fy-canary audit -c canary.yaml`
     - 每个模型分别执行（修改 canary.yaml 的 source.name 和 model）

  5. 综合评分（fy-score）
     - 用 --channel-id 和 --channel-name 确保各工具数据正确合并
     ```bash
     fy-score --loadtest-dir loadtest-results/ \
              --quality-dir quality-results/ \
              --canary-dir canary-results/ \
              --channel-id {渠道ID} --channel-name "{渠道名}" \
              --output scorecard.json --markdown scorecard.md
     ```

输出要求

  汇总一份中文 markdown 报告，严格按以下顺序组织：
  1. 总体结论（报告第一段）
     - 第一句话直接给出判定：渠道可用/不可用
     - 紧接着用 scorecard 等级表（A/B/C/D/F）概括各渠道各模型的综合评分
     - 然后说明：哪个模型适合高并发、哪个延迟更低

  2. Scorecard 详情
     - 四维度分数表（可用性/性能/质量/真实性）
     - 被门槛否决的渠道单独标注原因
     - 如有 flag（如"疑似模型替换"），醒目提示

  3. 优化问题（按优先级 P0→P3 排序）

  4. 工具选型说明
     - 每个工具是否使用，未使用给出原因

  5. 存活性 + TTFT 冒烟详细分析
  6. 质量评估详细分析
  7. 金丝雀检测详细分析
  8. 并发压测详细分析
     - 各并发级别的 RPM / TPM / 延迟 / 错误率对比表
     - 瓶颈并发点分析
     - 是否触发限流（429）、是否有服务端错误（5xx）
  9. 原始数据索引
     - 保留原始 JSON 路径供后续分析
     - 包含 scorecard.json 路径
```

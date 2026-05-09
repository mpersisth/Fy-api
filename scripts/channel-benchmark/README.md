# channel-benchmark

A small toolkit for **measuring Fy-api channels** along four orthogonal axes. The tools live in two language ecosystems on purpose — pick the one whose constraints match the question you're asking.

```
channel-benchmark/
├── go/                Smoke tester. Single binary, zero deps. Run on prod.
└── py/                Three CLIs sharing one venv:
    ├── fy-loadtest    Concurrency-ramp load testing
    ├── fy-quality     Quality scorecard (multi-grader, dual LLM judge)
    └── fy-canary      Model-substitution / drift detection
```

Everything talks to Fy-api over the OpenAI-compatible `/v1/chat/completions` path with a real user token, so runs are billed as real traffic. Keep the user's quota modest — it doubles as a budget cap.

## Pick a tool by the question you're asking

| Question | Tool | Why this one |
|---|---|---|
| "Are these channels even alive right now? Who's slow?" | **`go/`** | Zero-dep binary, can run on any prod box, hits real relay path so it sees TTFT + usage (unlike the built-in 测试 button which only returns `{success, time}`). |
| "Will this channel survive 50 concurrent users?" | **`fy-loadtest`** | 1→N concurrency ramp, full E2E/TTFT/ITL/TPOT percentile suite, goodput-vs-SLO. |
| "Is this channel actually answering correctly?" | **`fy-quality`** | Golden JSONL + 7 graders (exact / regex / contains / json_schema / rubric / similarity / pairwise) + dual-judge to cut false positives. |
| "Has this channel been silently swapped to a cheaper model?" | **`fy-canary`** | Records a trusted baseline against the vendor API directly, then audits the gateway for divergence via alignment-template / embedding-drift / MMD. |

## How they relate (and don't)

The four are **stacked, not interchangeable**:

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
   └──────────────────┘  └──────────────────┘  └──────────────────┘
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

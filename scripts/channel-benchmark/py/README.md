# fy-loadtest — Python load tester for Fy-api

Concurrency-ramp load tester that hits `/v1/chat/completions` on an
OpenAI-compatible gateway (Fy-api in particular) and reports per-
concurrency-level latency, throughput, and token-usage metrics.

Complements the Go smoke tool in `../go/`:

| Tool | Purpose | When |
|---|---|---|
| `../go/channel-benchmark` | Smoke — is each channel alive? TTFT/token/error per channel×model | Before every release, after config changes |
| `./fy_loadtest`            | Load — how does ONE channel perform under concurrency 1→100?      | Capacity planning, SLO verification |

## Install

Requires Python 3.11+.

```bash
cd scripts/channel-benchmark/py
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .
# or dev extras for running tests:
uv pip install --python .venv/bin/python -e ".[dev]"
source .venv/bin/activate
```

## Run

```bash
export FY_API_URL=http://localhost:3000
export FY_API_USER_TOKEN=sk-...

fy-loadtest -c loadtest.yaml
```

Quick override examples:

```bash
fy-loadtest -c loadtest.yaml --concurrencies 1,5,25 --reps 20
fy-loadtest -c loadtest.yaml --dry-run                  # validate config only
fy-loadtest -c loadtest.yaml --formats markdown        # skip json+csv
```

Outputs land in `loadtest-results/` (or `export.output_dir`):

```
loadtest-results/
├── loadtest_2026-05-08_13-42-00.json   # full results, programmatic
├── loadtest_2026-05-08_13-42-00.csv    # one row per concurrency level
└── loadtest_2026-05-08_13-42-00.md     # markdown table for eyeballing
```

## Metrics

All latency numbers are milliseconds; percentiles use linear interpolation.

Per concurrency level:

| Metric | What it means |
|---|---|
| `e2e_p50/p95/p99_ms` | Full request latency (send → stream closed) |
| `ttft_p50/p95/p99_ms` | **First CONTENT chunk** — role-only preamble excluded |
| `itl_p50/p95_ms` | Gaps between content chunks (TTFT **not** included) |
| `tpot_p50/p95_ms` | `(e2e − ttft) / (output_tokens − 1)` |
| `rps` | `ok_requests / wall_time_s` for the level |
| `aggregate_tok_per_s` | `Σ completion_tokens / wall_time_s` (system decode) |
| `per_req_tok_per_s_avg` | mean decode throughput per single request |
| `avg_prompt_tokens` / `avg_completion_tokens` / `avg_cached_tokens` | from upstream `usage` |
| `goodput_req_per_s` | (optional) req/s meeting the configured SLO |

Read the **knee** of `e2e_p95_ms` as the level climbs — that's the channel's
capacity.  A good channel shows a long flat region, then a sharp hockey
stick; a bad one degrades starting at C=2.

## Design choices

- **TTFT skips the role-only preamble chunk** that many providers send. This
  intentionally diverges from `genai-perf`, which anchors TTFT on the first
  SSE chunk regardless of content. The Go smoke tool makes the same choice,
  so smoke + load-test numbers can be directly compared.
- **ITL excludes TTFT.** `llmperf` famously conflates them; we don't.
- **Token counts come from the server.** We pass `stream_options.include_usage=true`
  and harvest from the final pre-[DONE] chunk. We do not tokenize locally.
- **`asyncio.Semaphore` closed-loop concurrency.** Keep exactly N in flight.
  Simple to reason about, matches what llmperf/genai-perf's `--concurrency`
  does. Poisson arrivals would be more realistic but noisier.
- **No retries.** A load test is a diagnostic; retrying masks the thing
  you're trying to measure. Errors are counted and surfaced in the report.

## Testing

```bash
pytest
```

The end-to-end tests use `httpx.MockTransport` to fake an upstream server
so CI can run them without network. They verify the TTFT/ITL/usage
contracts documented above.

## Not in scope (yet)

- LLM-as-judge quality scoring
- Canary / model-substitution drift detection
- Persistent history / regression comparison
- Distributed load generation (single host is fine for testing a gateway,
  not the backends behind it)

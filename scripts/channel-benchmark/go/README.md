# Channel Benchmark

A Go CLI that smokes Fy-api channels by hitting `/v1/chat/completions` with
real requests and reporting per-(channel × model × mode) latency, TTFT,
inter-token latency, throughput, token accounting, and error breakdowns.

It is NOT:
- a load tester (no concurrency ramp; see `../py/` when that's built)
- a quality evaluator (no LLM-as-judge; future)
- a replacement for Fy-api's built-in `测试` button (which is a liveness check
  and reports only wall-clock E2E)

It IS the smallest thing that gives you real, comparable numbers across channels.

## Layout

```
go/
├── main.go       CLI entrypoint, flags, summary table
├── config.go     YAML + ${ENV} expansion + validation
├── admin.go      GET /api/channel/ (AdminAuth: no-Bearer token + New-Api-User)
├── client.go     OpenAI-compat chat client, real SSE parsing for TTFT/ITL/usage
├── runner.go     channel × model × mode × reps fan-out, bounded worker pool
├── metrics.go    percentile / stddev / throughput aggregation
├── exporter.go   JSON + CSV output
└── channel-benchmark.yaml   example config
```

## Quick start

```bash
cd scripts/channel-benchmark/go

# 1. Install dependencies
go mod download

# 2. Copy and edit the config (or export env vars the example references)
cp channel-benchmark.yaml my.yaml
export FY_API_URL=https://api.example.com
export FY_API_ADMIN_TOKEN=...         # admin user's access_token
export FY_API_ADMIN_USER_ID=1         # that admin's numeric id
export FY_API_USER_TOKEN=sk-...       # regular user token; billed as real traffic

# 3. Run
go run . -config my.yaml

# 4. Results land in ./benchmark-results/benchmark_<timestamp>.{json,csv}
```

Override any field from the CLI:

```bash
go run . -config my.yaml -concurrency 8 -reps 5 -formats json
go run . -config my.yaml -dry-run    # validate config, no requests
```

## Metrics reported

Per (channel, model, streamed/non-streamed) case:

| Metric | Meaning |
|---|---|
| `success_rate_pct` | (ok / total) × 100 |
| `e2e_p50/p95/p99_ms` | End-to-end latency percentiles |
| `ttft_p50/p95/p99_ms` | Time to first content chunk (streamed only) |
| `itl_p50/p95_ms` | Inter-chunk gaps (streamed only); excludes TTFT |
| `tokens_per_sec_avg` | Decode throughput: completion_tokens / (E2E − TTFT) |
| `avg_prompt_tokens` | From `usage.prompt_tokens` in upstream response |
| `avg_completion_tokens` | From `usage.completion_tokens` |
| `avg_cached_tokens` | From `usage.prompt_tokens_details.cached_tokens` |
| `top_error` | Most frequent failure signature if any case failed |

All latency numbers use linear-interpolation percentiles (matches NumPy,
llmperf, and genai-perf).

## Design choices worth calling out

- **Hits `/v1/chat/completions`, not `/api/channel/test/:id`.** The admin
  test endpoint returns only `{success, time}` — we need usage and TTFT too.
  So we go through the real relay path with a user token. Trade-off: smoke
  runs consume real quota.
- **Explicit model list per channel.** The config requires you to spell out
  which models to test; the tool never falls back to a magic default. This is
  deliberate — silent billing surprises are worse than a loud error.
- **Stream + non-stream both default on.** If a channel misbehaves only
  under streaming (partial chunks, broken usage), you want to see it.
- **Admin auth contract is the non-obvious part.** Fy-api's `AdminAuth`
  middleware expects `Authorization: <raw_token>` (NO `Bearer` prefix) plus a
  `New-Api-User: <user_id>` header. That's why this tool asks for both.

## What's intentionally not here

- Retries on failure. Smoke is a diagnostic; retries hide flakiness.
- Database persistence. JSON/CSV on disk is enough for now.
- Scheduling (cron / systemd). Run it by hand or wrap it yourself.
- Quality scoring, canary drift, load ramps. Those live in `../py/` when added.

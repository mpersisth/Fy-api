# Channel Benchmark

A Go CLI that smokes Fy-api channels by hitting `/v1/chat/completions` with
real requests and reporting per-(channel × model × mode) latency, TTFT,
inter-token latency, throughput, token accounting, and error breakdowns.

It is NOT:
- a load tester (no concurrency ramp; use `../py/fy_loadtest` for that)
- a quality evaluator (no LLM-as-judge; use `../py/fy_quality` for that)
- a model-substitution detector (no baseline diffing; use `../py/fy_canary`)
- a replacement for Fy-api's built-in `测试` button (which is a liveness check
  and reports only wall-clock E2E)

It IS the smallest thing that gives you real, comparable numbers across channels,
and — with `-prom-listen` — a long-running Prometheus exporter that your Grafana
can scrape.

## Layout

```
go/
├── main.go         CLI entrypoint, flags, summary table, daemon loop
├── config.go       YAML + ${ENV} expansion + validation
├── admin.go        GET /api/channel/ (AdminAuth: no-Bearer token + New-Api-User)
├── client.go       OpenAI-compat chat client, real SSE parsing for TTFT/ITL/usage
├── runner.go       channel × model × mode × reps fan-out, bounded worker pool
├── metrics.go      percentile / stddev / throughput aggregation
├── exporter.go     JSON + CSV output
├── prometheus.go   zero-dep Prometheus exposition-format writer + /metrics handler
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

## Daemon mode — Prometheus exporter

Pass `-prom-listen` to keep the binary running and expose metrics for
Prometheus to scrape. The benchmark is re-run on `-prom-interval` (default 5m)
and the most recent results are surfaced at `/metrics`.

```bash
go run . -config my.yaml -prom-listen :9090 -prom-interval 5m
# or, to skip JSON/CSV files in long-lived deployments:
go run . -config my.yaml -prom-listen :9090 -no-export
```

Series exposed (all under the `channel_benchmark_` prefix):

| Metric | Type | Labels |
|---|---|---|
| `channel_benchmark_request_total` | counter | channel, model, streamed, outcome |
| `channel_benchmark_success_rate` | gauge (0-1) | channel, model, streamed |
| `channel_benchmark_e2e_seconds` | gauge | channel, model, streamed, quantile |
| `channel_benchmark_ttft_seconds` | gauge (stream only) | channel, model, streamed, quantile |
| `channel_benchmark_tokens_per_sec` | gauge | channel, model, streamed |
| `channel_benchmark_run_age_seconds` | gauge | — |
| `channel_benchmark_last_run_unix_seconds` | gauge | — |
| `channel_benchmark_consecutive_runs_ok` | gauge | — |

Quantiles are emitted as separate gauges (p50/p95/p99) rather than histogram
buckets — for "p95 of channel X" dashboards that's all you need, and the
exposition is 10× smaller. The implementation is dependency-free; `go.mod`
still has only `gopkg.in/yaml.v3`.

Alerting recipes (PromQL):

```promql
# Channel down for >15min: zero successful requests in the latest run
sum by (channel, model) (
  rate(channel_benchmark_request_total{outcome="ok"}[15m])
) == 0

# TTFT regression: p95 above 3s for 10min
channel_benchmark_ttft_seconds{quantile="0.95"} > 3

# Stale exporter: data older than 2× the configured interval
channel_benchmark_run_age_seconds > 600
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
- **Channel pinning is opt-in (`test.pin_channel: true`).** Without it,
  requests go through Fy-api's distributor (group + priority + weight +
  affinity), which means a model offered by N channels may be routed to one
  you didn't list. With `pin_channel` on, the tool appends `-{channel_id}` to
  `gateway.user_token` — Fy-api parses this in `middleware/auth.go` (around
  line 431) and forces the request to that exact channel. Pinning requires
  `user_token` to belong to an admin user; non-admin tokens with the suffix
  get a 403 from the gateway.

## What's intentionally not here

- Retries on failure. Smoke is a diagnostic; retries hide flakiness.
- Database persistence. JSON/CSV on disk is enough for now.
- External scheduler (cron / systemd). The daemon mode (`-prom-listen`) is the
  closest thing we ship; for one-shot `cron -e` invocations, just wrap the binary.
- Quality scoring, canary drift, load ramps. Those live in `../py/` (`fy-quality`,
  `fy-canary`, `fy-loadtest` respectively).
- `prometheus/client_golang` dependency. The exposition format is trivial and we
  deliberately keep `go.mod` to `gopkg.in/yaml.v3` only so this binary stays
  drop-on-prod simple.

## Tests

```bash
go test -race ./...
```

7 tests: end-to-end (mock gateway with SSE), Prometheus exposition format,
counter accumulation, label escaping, handler routing, and float formatting.
All green with `-race`.

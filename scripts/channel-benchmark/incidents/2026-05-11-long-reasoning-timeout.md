# Incident 2026-05-11 — Long-reasoning streams cut at 600s

**Severity**: customer-visible quality regression on long-thinking benchmarks.
**Detected by**: customer running `aime25` / `gpqa-diamond` against 概泽 channels
(model: `kimi-k2-thinking`). They saw `aime25 = 55.63%` and `gpqa-diamond = 76%`,
both well below the vendor's published numbers (96% / 86%).
**Resolved by**: nginx + `RELAY_TIMEOUT` + `STREAMING_TIMEOUT` raised on CN + SG,
no code change. Blue-green redeploy at 2026-05-11 15:51 (CN) / 15:52 (SG).
**Root cause class**: timeout-layer mismatch — the inner layer (Fy-api HTTP
client) was tighter than the outer layer (nginx), so streams that the gateway
*could* have proxied died early.

---

## Symptoms (as the customer saw them)

- aime25 / gpqa-diamond benchmarks: ~30 questions per run.
- A meaningful fraction of items returned no answer at all, or returned a
  truncated answer that scored 0.
- The customer's own log line: *"tracenex 平台 nginx 有限制 900s 未给出答案就报错失败了"*.
  They blamed the 900s nginx layer because that's the one they could see.
- Vendor's official model (Kimi K2.5 direct) scored within published range on
  the same dataset: aime25 ~ 19/30, gpqa-diamond ~ 19/30.

The customer's diagnosis was almost right but pointed at the wrong layer.

## Real root cause

Fy-api's outbound HTTP client to upstream providers had

```
RELAY_TIMEOUT=600       # 10 min, applies to the entire upstream call (incl. stream)
STREAMING_TIMEOUT=300   # 5 min, applies to inter-token gap on the SSE stream
```

with nginx in front configured at

```
proxy_send_timeout    900s
proxy_read_timeout    900s
send_timeout          900s
```

For aime25 / gpqa-diamond on a thinking model:

- Pre-token "thinking" delay can be 30-180s.
- Stream emission then runs for another 5-25 minutes per question.
- Cumulative single-question time observed: 10-30 minutes.

The 600s `RELAY_TIMEOUT` fires *before* nginx's 900s ever has a chance to,
so:

1. Stream cuts at exactly 600s on the upstream side.
2. Fy-api logs `context deadline exceeded` and emits the customer-facing wording
   (their SDK rendered it as "Receive batching backend response failed").
3. nginx sees a clean upstream close — no nginx-level timeout, no 5xx.

The customer assumed the only timeout in the chain was the visible 900s nginx
one. The invisible 600s `RELAY_TIMEOUT` was the actual ceiling.

## Fix (config-only, no code, no rebuild)

Both production nodes (CN `8.136.146.211`, SG `47.236.133.70`):

| Layer | Before | After |
|---|---|---|
| nginx `proxy_send_timeout` | 900s | 1800s |
| nginx `proxy_read_timeout` | 900s | 1800s |
| nginx `send_timeout`       | 900s | 1800s |
| Fy-api `RELAY_TIMEOUT`     | 600  | 1800 |
| Fy-api `STREAMING_TIMEOUT` | 300  | 600  |

Invariant we re-established: **outer layer ≥ inner layer**. nginx (1800s) now
≥ Fy-api `RELAY_TIMEOUT` (1800s), so any timeout that fires is the *innermost*
one (the upstream provider's own client-side limit). Backups of both files
saved as `*.bak.YYYYMMDD-HHMMSS` on each host before the edit.

Container env is loaded from `/opt/fy-api/config/fy-api.env` at startup, so
the new values needed a blue-green restart to take effect (image
`v1.7-tracenex` unchanged).

## Why it slipped past existing tooling

We had no test or canary that exercised a single request lasting more than
~120 seconds. Specifically:

- `go/` smoke timeout default is 60s.
- `py/loadtest.yaml` defaults `request_timeout_sec: 120`.
- `py/conformance.yaml` defaults `request_timeout_sec: 5`.
- `py/canary.yaml` and `py/quality.yaml` exercise ≤ 256 max_tokens prompts.

So the entire toolkit stayed under 2 minutes per request. A 600s `RELAY_TIMEOUT`
was free to lurk indefinitely as long as nobody asked the gateway to stream
for 10+ minutes.

## Regression artifacts (what to grep for if this happens again)

This incident now ships with three regression hooks across the toolkit:

1. **`go/` `-long-thinking` flag** — opt-in mode that swaps in a long
   reasoning prompt + bumps `timeout_seconds` to 1800s + sets `max_tokens` high.
   Use it as a manual cron:
   ```
   ./channel-benchmark -config gauze-cn.yaml -long-thinking
   ```
   If a single rep exits in less than ~5s with success, you're not actually
   exercising a thinking model — fail loud.

2. **`py/loadtest.long-thinking.yaml`** — preset config + a fixture prompt
   under `py/fy_loadtest/fixtures/long_reasoning_prompts.py` containing
   AIME-style and GPQA-style problems. Concurrency=1, requests_per_level=2,
   request_timeout_sec=1800. Asserts the channel can sustain a single 10+
   minute stream end-to-end without truncation.
   ```
   fy-loadtest -c loadtest.long-thinking.yaml
   ```

3. **`py/tests_conformance/test_long_reasoning_timeout.py`** — pytest case
   that uses `httpx.MockTransport` to simulate a slow stream (first byte at
   601s post-fix would have been cut; first byte at 601s post-fix passes
   with `request_timeout_sec=1800`). Locks in the configuration math so we
   don't quietly slide back to a 600s default.

When all three pass, the timeout chain is at least as deep as the customer's
worst observed query (~25 minutes). When any of them fails, suspect
`RELAY_TIMEOUT` / `STREAMING_TIMEOUT` regressed in env, or nginx fell back
to its default `proxy_read_timeout` (60s).

## Operator runbook (if it returns)

Symptoms in logs:
```
use_time_seconds: 600
scanner_error: context deadline exceeded
```
… on a model whose vendor needs >10 min for a single question.

Verify:
```
ssh root@<node> 'grep -E "RELAY_TIMEOUT|STREAMING_TIMEOUT" /opt/fy-api/config/fy-api.env'
ssh root@<node> 'grep -E "proxy_(read|send)_timeout|^\s*send_timeout" /etc/nginx/conf.d/fy-api.conf'
```

Expect (post-2026-05-11):
```
RELAY_TIMEOUT=1800
STREAMING_TIMEOUT=600
proxy_send_timeout    1800s
proxy_read_timeout    1800s
send_timeout          1800s
```

If they regressed, repeat the fix steps from "Fix" above and blue-green
redeploy with the same tag. No code change required.

## Related files

- `incidents/2026-05-11-long-reasoning-timeout.md` — this card.
- `go/` — `-long-thinking` mode (config.go, runner.go, main.go).
- `py/loadtest.long-thinking.yaml` — preset.
- `py/fy_loadtest/fixtures/long_reasoning_prompts.py` — prompt fixtures.
- `py/tests_conformance/test_long_reasoning_timeout.py` — pytest regression.

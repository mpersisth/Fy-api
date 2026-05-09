# fy-conformance

Protocol-conformance test harness for Fy-api channels. The premise is
narrow on purpose: when a client sends a malformed or boundary-value
parameter, **does the gateway respond correctly?** Specifically:

1. Status code lands in the right class — `4xx` for client errors,
   not `5xx` (which would erroneously flag a server-side outage and
   trigger client retries / circuit-breakers).
2. The response body mentions the offending field so the client can fix
   it.
3. The response body does **not** leak internal Go struct paths like
   `GeneralOpenAIRequest.max_tokens of type uint`.
4. Known-valid boundary values are accepted, not bounced.

The corpus is a JSONL file. Each row is one assertion. The default
public corpus has 144 cases pulled from a customer functional-test
xlsx covering OpenAI Chat Completions, Anthropic Messages, tools/
function-calling, structured outputs, reasoning, auth, and malformed
requests.

## Backend scoping (important)

Different upstream backends have different validation strictness. The
canonical example: `temperature=1.5` is rejected by OpenAI/DeepSeek/Kimi
but silently clamped by Anthropic. Without scoping, our test corpus
would falsely flag Anthropic as buggy.

Each case can declare an `applies_to_backends` list. The run config
sets `target.backend`, and cases whose list does **not** include that
backend become `SKIP` (not `FAIL`). When a case omits the list, it
applies to all backends.

```yaml
target:
  model: claude-haiku-4-5-20251001
  backend: claude   # <— filters out cases scoped to other backends
  ...
```

Cases that universally apply (type errors, structural negatives, leak
guards) have no `applies_to_backends` and run against everything. Cases
that depend on strict OpenAI semantics (`temperature` clamping,
`max_tokens` lower bound, etc.) carry `applies_to_backends: ["openai",
"deepseek", "kimi", "qwen"]`. Cases for the Anthropic-native
`/v1/messages` endpoint carry `applies_to_backends: ["claude"]`.

## Why this is its own tool

- `fy-quality` checks **answer correctness** with LLM judges. Its grader
  is fuzzy on purpose. Conformance is the opposite: every assertion is
  deterministic.
- `fy-canary` checks **model identity** by comparing distributions.
  Conformance is per-request behavior.
- `fy-loadtest` checks **capacity**. Conformance ignores latency.

A failing conformance case usually points at a gateway/adapter bug, not
an upstream model issue — exactly the class of bug the Fy-api `2026-05-09`
hot-fix addressed (`controller/relay.go` mapping invalid-request to 500
instead of 400, and leaking `json: cannot unmarshal ... Go struct field
GeneralOpenAIRequest.max_tokens of type uint`).

## Quickstart

```bash
cd scripts/channel-benchmark/py
uv pip install -e .
export FY_API_URL=https://www.tracenex.cn
export FY_API_USER_TOKEN=sk-...

cp conformance.yaml conformance.local.yaml   # edit baseline.model

# Full sweep:
fy-conformance -c conformance.local.yaml

# One category at a time:
fy-conformance -c conformance.local.yaml --category param_validation_auto

# One case for fast iteration:
fy-conformance -c conformance.local.yaml --id auto-max_tokens-ec_invalid_type_string

# Smoke (first 10):
fy-conformance -c conformance.local.yaml --limit 10
```

## Exit codes

- `0`  — all cases PASS
- `1`  — at least one FAIL or transport ERROR (suitable for CI gating)
- `2`  — invocation error (bad config, no cases match filter)

## Output

A timestamped triple is written to `output_dir/`:

```
conformance-<model>-<UTC>.jsonl         # one verdict per line
conformance-<model>-<UTC>.summary.json  # totals + by-category breakdown
conformance-<model>-<UTC>.md            # human-readable failure report
```

## Case schema

A row in `conformance.jsonl` looks like:

```jsonc
{
  "id": "auto-max_tokens-ec-type-string",
  "category": "param_validation_auto",
  "description": "max_tokens=\"abc\" — string where int",
  "endpoint": "/v1/chat/completions",
  "method": "POST",

  // OPTIONAL — restrict to backends. Omit = applies everywhere.
  // "applies_to_backends": ["openai", "deepseek"],

  // mutate the baseline request — pick at most one shape:
  "override_field": "max_tokens",         // dotted paths supported, e.g. "messages.0.role"
  "override_value": "abc",
  // "remove_field":   "messages",
  // "raw_body":       "{not json",
  // "override_auth":  "Bearer sk-INVALID",
  // "extra_body":     {"tools": [...], "tool_choice": "auto"},
  // "body_replace":   {"max_tokens": 16, "messages": [...]},  // full replace

  // assertions (zero or more):
  "expect_status_class": "4xx",            // 2xx | 4xx | 5xx | 4xx_or_5xx | 2xx_or_4xx
  "expect_status_code": 401,               // exact code
  "expect_message_contains": ["max_tokens"],
  "must_not_contain": ["Go struct field", "GeneralOpenAIRequest"],
  "expect_response_field": "choices.0.message.content"  // dotted path must exist
}
```

`expect_status_class` accepts:
- `2xx` / `4xx` / `5xx` — exact bucket
- `4xx_or_5xx` — either is defensible (model not found can be 404 or 503)
- `2xx_or_4xx` — leak guard mode: "as long as it's not 5xx, we're fine".
  Use this for range/semantic violations on tolerant backends — the
  `must_not_contain` leak guards still catch any 5xx with a Go struct
  path leak, which is the actual regression we're defending against.

Substring matches are case-insensitive.

## Authoring new cases

The fast path is to write a row by hand. The bulk path used to seed the
public corpus was extracting from a customer functional-test xlsx; see
`fy_conformance/datasets/extract_from_report.py.txt` (intentionally not
executable; it's a reference snippet) if you want to repeat that.

Three things to keep in mind:

- **One assertion per case.** If you want to check "field X must be in
  response AND status must be 400 AND `Go struct field` must NOT be in
  response", that's *one* case (it has multiple expectations on one
  request). If you want to check "value=X gives 4xx" and "value=Y gives
  2xx", that's *two* cases.
- **Don't expect specific error wording.** Vendors change strings. Match
  on the **field name** and structural facts (status class, no leaks)
  only.
- **Prefer `4xx_or_5xx`** if you genuinely don't know whether your
  gateway emits 404 vs 503 for a given path — write the strict expectation
  later when you've decided the policy.

## Running against multiple channels

`fy-conformance` only knows about one channel at a time (whichever the
configured token routes to). To compare across channels, run it once
per channel/token and diff the summary JSON files. A purpose-built
matrix runner lives in `fy-quality` if you need that ergonomic; for
conformance the manual `for token in ...; do fy-conformance ...; done`
is fine because the per-run cost is small.

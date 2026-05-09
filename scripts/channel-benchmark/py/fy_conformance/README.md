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
public corpus has 94 cases extracted from real-world functional test
reports against Kimi/Claude/etc.

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
  "id": "auto-max_tokens-ec_invalid_type_string",
  "category": "param_validation_auto",
  "endpoint": "/v1/chat/completions",
  "method": "POST",

  // mutate exactly one field of the baseline request:
  "override_field": "max_tokens",
  "override_value": "abc",
  // -- alternatives --
  // "remove_field":   "messages",
  // "raw_body":       "{not json",
  // "override_auth":  "Bearer sk-INVALID",

  // assertions:
  "expect_status_class": "4xx",
  "expect_message_contains": ["max_tokens"],
  "must_not_contain": ["Go struct field", "GeneralOpenAIRequest"]
}
```

`expect_status_class` accepts `2xx` / `4xx` / `5xx` / `4xx_or_5xx` (the
last one is for cases where either is defensible — e.g. "model not
found" can be 404 or 503 depending on the gateway's policy).

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

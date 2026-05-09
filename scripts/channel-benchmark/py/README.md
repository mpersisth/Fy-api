# Fy-api channel QA — Python tools

Three Python tools sharing one package, one venv, one JSONL schema:

| Tool | Command | Purpose |
|---|---|---|
| `fy_loadtest` | `fy-loadtest` | Concurrency-ramp load testing. Hits one channel at 1→N in-flight and reports latency/throughput per level. |
| `fy_quality`  | `fy-quality`  | Quality scorecard. Runs a golden JSONL suite against N channels, grades each output (exact / regex / contains / json-schema / LLM-rubric / similarity / pairwise), emits a scoring matrix. |
| `fy_canary`   | `fy-canary`   | Model-substitution detection. Records a trusted baseline, then audits a suspect channel for divergence via alignment-template similarity, embedding drift, and (optional) MMD two-sample test. |

The Go smoke tool in `../go/` is the first layer (liveness + TTFT per channel); these three Python tools extend it.

## Install

Python 3.11+ required.

```bash
cd scripts/channel-benchmark/py
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .          # base: loadtest + quality + canary (no MMD)
uv pip install --python .venv/bin/python -e ".[canary]"  # adds MMD via model-equality-testing (pulls torch, ~1.5GB)
uv pip install --python .venv/bin/python -e ".[dev]"     # pytest for running the suite
source .venv/bin/activate
```

## fy-loadtest — concurrency-ramp load testing

```bash
export FY_API_URL=http://localhost:3000
export FY_API_USER_TOKEN=sk-...

fy-loadtest -c loadtest.yaml
fy-loadtest -c loadtest.yaml --concurrencies 1,5,25 --reps 20
fy-loadtest -c loadtest.yaml --dry-run
```

Outputs JSON, CSV, and markdown summary per concurrency level.
Metrics: E2E / TTFT / ITL / TPOT percentiles, RPS, aggregate tok/s, goodput vs SLO.

## fy-quality — quality scorecard

```bash
export FY_TOKEN_OPENAI=sk-...          # token for channel 1
export FY_TOKEN_ANTHROPIC=sk-...
export ANTHROPIC_API_KEY=sk-ant-...    # judge 1
export GEMINI_API_KEY=...              # judge 2
export OPENAI_API_KEY=sk-...           # embeddings for similarity grader

fy-quality -c quality.yaml
```

Graders:

| Grader | When to use | Notes |
|---|---|---|
| `exact` | Unambiguous one-token answers (math, facts) | Strips surrounding quotes / whitespace |
| `regex` | Structural format checks ("three words", "N.NN decimal") | Python `re.search` semantics |
| `contains` | "Must mention X" — case-insensitive | Good for loose factual checks |
| `json_schema` | Structured-output tests | Minimal JSON Schema subset: type, required, const, enum, additionalProperties |
| `rubric` | Open-ended answers | **Dual-judge mode** by default: both judges must score ≥ `pass_score` (1-5) |
| `similarity` | Paraphrases, translations | Embedding cosine ≥ `similarity_threshold` |
| `pairwise` | A vs B head-to-head | Runs both orderings, ties-count-as-passing |

Dual-judge defaults to Claude Haiku + Gemini Flash. Never configure a channel's own model as a judge.

Output: JSON + CSV + a markdown scorecard with per-channel pass rate, per-category breakdown, and a failures table.

### Dataset layout (contamination defense)

```
fy_quality/datasets/
├── README.md                which to use when, and which perturbations are safe per grader
├── public/quality.jsonl     15-row starter suite. COMMITTED. Assume every model has seen it.
└── private/                 YOUR real grading prompts. Gitignored. Back up out-of-band.
```

Every row can opt into deterministic on-the-wire perturbations via `seed` + `perturbations`:

```json
{"id":"math-01","kind":"quality","grader":"exact",
 "prompt":"What is 17 + 28?", "expected":"45",
 "seed": 42, "perturbations": ["whitespace", "trailing_marker"]}
```

Strategies (from `fy_quality/perturbation.py`):

| Strategy | What it does | Safe for |
|---|---|---|
| `whitespace` | Inserts one U+200B zero-width-space at a hash-derived index between two letters | Every grader |
| `trailing_marker` | Appends ` <!--fqNNNNNN-->` where the 6-digit nonce is deterministic on `(seed, prompt_id)` | Every grader |
| `synonym` | Swaps the first whole-word hit against a reviewed 10-word map, preserving case + trailing punctuation | rubric / similarity / pairwise (only — on exact/regex double-check manually) |

Perturbations are deterministic, so disk cache keys stay stable. Schema changes (different seed or different strategy list) naturally invalidate the cache because the wire text changes.

## fy-canary — model-substitution detection

Three-step workflow:

```bash
# 1. Record a trusted baseline (point at the vendor API directly).
export CANARY_BASE_URL=https://api.openai.com
export CANARY_API_KEY=sk-...
fy-canary baseline -c canary.yaml

# 2. Audit the suspect channel (point at the Fy-api gateway).
export CANARY_BASE_URL=https://your-fy-api.example.com
export CANARY_API_KEY=sk-user-on-fyapi
fy-canary audit -c canary.yaml

# 3. Periodically verify the baseline itself hasn't drifted (re-query vendor direct).
export CANARY_BASE_URL=https://api.openai.com
export CANARY_API_KEY=sk-...
fy-canary verify-baseline -c canary.yaml
```

Probes:

| Method | What it catches | Cost per probe | Config |
|---|---|---|---|
| `alignment` | Cross-family substitutions (GPT→Claude etc.) via refusal-template drift | 1 request | Always on |
| `drift` | Within-family substitutions via output-embedding centroid cosine | N requests + N embeddings | Requires `embedding:` block in config |
| `mmd` | Quantization / distillation via MMD+Hamming+permutation p-value | N requests per prompt, ~10 is enough per Gao et al. | `mmd_enabled: true` + `pip install -e .[canary]` |

Baselines are per-`source.name` JSON files in `canary-baselines/`. Keep that dir tracked manually or gitignored as you prefer — they shouldn't contain secrets but they DO contain model outputs.

### Baseline health checks

Every baseline file carries v2 metadata:

- `schema_version` — always 2 on new saves; v1 files still load
- `recorded_at_iso` — human-readable timestamp
- `n_probes` / `total_samples` — the audit sizes this was calibrated for
- `fy_canary_version` — the tool version that wrote it

`fy-canary audit` refuses to run against a baseline older than `baseline_max_age_days` (default 30) unless you pass `--ignore-stale-baseline`. That threshold lives in `canary.yaml`.

`fy-canary verify-baseline` is the audit flipped: it re-queries the SAME source the baseline was recorded from and runs the exact alignment/drift comparison. A failure there means the vendor itself has changed (model updated, system prompt tweaked, API migrated) and the baseline needs to be re-recorded before its next audit run makes sense.

## Shared JSONL dataset schema

Both `fy-quality` and `fy-canary` read the same flavor of JSONL. Each row:

```json
{"id": "...", "kind": "quality" | "canary", "prompt": "...", "..."}
```

See `fy_quality/datasets/public/quality.jsonl` (15 starter prompts) and
`fy_canary/datasets/canaries.jsonl` (8 starter probes).

## Design choices worth calling out

- **Three CLIs, one package.** `pip install -e .` gives you all three; `[canary]` is the only weight-bearing extra.
- **Judge isolation.** Judges are configured independently from the channels under test — the code cannot accidentally have a channel judge its own output.
- **Dual-judge rubric.** Two judges must BOTH score ≥ pass_score. Cuts false-positives at the cost of 2× judge spend.
- **Position-randomized pairwise.** A-vs-B and B-vs-A are both asked; a flip counts as a tie.
- **Disk cache for quality generations.** Re-running the suite after a grader tweak is near-free.
- **Baseline-first canary.** The real test is "did outputs diverge from what this channel used to produce?" — you can't detect that without recording a trusted snapshot first.
- **Channel pinning is opt-in across all four tools.** Each tool's config has a
  `pin_channel_id` field (gateway-level for loadtest/conformance, per-channel
  for quality, per-source for canary). When set, the tool appends
  `-{channel_id}` to the user token, and Fy-api parses this in
  `middleware/auth.go` (~line 431) as a forced channel selection. Required
  to be admin's user token, otherwise the gateway 403s with "普通用户不支持
  指定渠道". Without it, requests go through the normal distributor (group +
  priority + weight + affinity), which can route a model offered by N
  channels to one you didn't intend to test. Mirrors `go/`'s `pin_channel`
  flag.
- **No CI integration, no scheduler.** These are manual runs. When you want a scheduler, wire one yourself.

## Testing

```bash
pytest
```

83 end-to-end tests using `httpx.MockTransport` — no network. Covers:

- fy_loadtest: TTFT-skip-preamble, usage harvesting, ramp, auth contract, channel-pin token suffix
- fy_quality: each grader's happy path and failure modes, dataset loader,
  full runner with mock upstream, dual-judge verdict composition,
  deterministic perturbations, runner-sends-perturbed-prompt, unknown-strategy errors,
  channel-pin runner + config round-trip
- fy_canary: Levenshtein, drift centroid, baseline v2 metadata + v1 backwards-compat,
  substitution detection, baseline-health staleness, verify-baseline source-drift,
  channel-pin client + config round-trip
- fy_conformance: dataset loader, runner with mock gateway, leak-guards corpus,
  channel-pin runner + config round-trip

## Not in scope (yet)

- LLM-as-judge judge-of-judges calibration
- Automatic baseline rotation / drift detection on the baseline itself
  (you have `verify-baseline` but re-recording is still manual)
- Distributed load generation
- Any CI hooks

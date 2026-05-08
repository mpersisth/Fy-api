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

## fy-canary — model-substitution detection

Two-step workflow:

```bash
# 1. Record a trusted baseline (point at the vendor API directly).
export CANARY_BASE_URL=https://api.openai.com
export CANARY_API_KEY=sk-...
fy-canary baseline -c canary.yaml

# 2. Audit the suspect channel (point at the Fy-api gateway).
export CANARY_BASE_URL=https://your-fy-api.example.com
export CANARY_API_KEY=sk-user-on-fyapi
fy-canary audit -c canary.yaml
```

Probes:

| Method | What it catches | Cost per probe | Config |
|---|---|---|---|
| `alignment` | Cross-family substitutions (GPT→Claude etc.) via refusal-template drift | 1 request | Always on |
| `drift` | Within-family substitutions via output-embedding centroid cosine | N requests + N embeddings | Requires `embedding:` block in config |
| `mmd` | Quantization / distillation via MMD+Hamming+permutation p-value | N requests per prompt, ~10 is enough per Gao et al. | `mmd_enabled: true` + `pip install -e .[canary]` |

Baselines are per-`source.name` JSON files in `canary-baselines/`. Keep that dir tracked manually or gitignored as you prefer — they shouldn't contain secrets but they DO contain model outputs.

## Shared JSONL dataset schema

Both `fy-quality` and `fy-canary` read the same flavor of JSONL. Each row:

```json
{"id": "...", "kind": "quality" | "canary", "prompt": "...", "..."}
```

See `fy_quality/datasets/quality.jsonl` (15 starter prompts) and
`fy_canary/datasets/canaries.jsonl` (8 starter probes).

## Design choices worth calling out

- **Three CLIs, one package.** `pip install -e .` gives you all three; `[canary]` is the only weight-bearing extra.
- **Judge isolation.** Judges are configured independently from the channels under test — the code cannot accidentally have a channel judge its own output.
- **Dual-judge rubric.** Two judges must BOTH score ≥ pass_score. Cuts false-positives at the cost of 2× judge spend.
- **Position-randomized pairwise.** A-vs-B and B-vs-A are both asked; a flip counts as a tie.
- **Disk cache for quality generations.** Re-running the suite after a grader tweak is near-free.
- **Baseline-first canary.** The real test is "did outputs diverge from what this channel used to produce?" — you can't detect that without recording a trusted snapshot first.
- **No CI integration, no scheduler.** These are manual runs. When you want a scheduler, wire one yourself.

## Testing

```bash
pytest
```

31 end-to-end tests using `httpx.MockTransport` — no network. Covers:

- fy_loadtest: TTFT-skip-preamble, usage harvesting, ramp, auth contract
- fy_quality: each grader's happy path and failure modes, dataset loader,
  full runner with mock upstream, dual-judge verdict composition
- fy_canary: Levenshtein, drift centroid, baseline roundtrip, substitution detection

## Not in scope (yet)

- LLM-as-judge judge-of-judges calibration
- Automatic baseline rotation / drift detection on the baseline itself
- Distributed load generation
- Any CI hooks

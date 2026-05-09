# fy-quality datasets

- `public/quality.jsonl` — the canonical **starter smoke suite** (15 prompts).
  It ships with the repo and is safe to assume a model has seen it. Good for:
    - smoke-testing the fy-quality wiring
    - regression on graders themselves
    - not useful for measuring model quality honestly

- `private/*.jsonl` — YOUR real evaluation prompts. This directory is
  `.gitignore`d deliberately. **Do not commit prompts you care about
  here.** Once a prompt lands in any public git repo, you must assume
  future model versions have memorized it and your pass-rate on it
  measures memorization rather than capability.

## Two defenses — use both

1. **Keep grading prompts out of version control.** Put them under
   `private/`. Back them up out-of-band (password manager, private
   S3 bucket, wherever).

2. **Perturb on the wire.** Even when running `public/quality.jsonl`,
   set `seed` and `perturbations` on each row so the text the model
   actually sees is not byte-identical to the file. See
   `../perturbation.py` for available strategies.

```json
{"id":"math-01","kind":"quality","grader":"exact",
 "prompt":"What is 17 + 28?",
 "expected":"45",
 "seed": 42,
 "perturbations": ["whitespace","trailing_marker"]}
```

Perturbations are deterministic on `(seed, prompt_id, strategy)` so
cache keys stay stable and re-runs reproduce.

## Which perturbations are safe for which grader

| Grader | whitespace | trailing_marker | synonym |
|---|---|---|---|
| exact | ✅ | ✅ | ⚠️ if mapping preserves the answer word |
| regex | ✅ | ✅ | ⚠️ review the regex still matches |
| contains | ✅ | ✅ | ⚠️ |
| json_schema | ✅ | ✅ | ⚠️ |
| rubric | ✅ | ✅ | ✅ |
| similarity | ✅ | ✅ | ✅ |
| pairwise | ✅ | ✅ | ✅ |

Start with `["whitespace", "trailing_marker"]` everywhere; add
`synonym` only on free-form graders after eyeballing the output.

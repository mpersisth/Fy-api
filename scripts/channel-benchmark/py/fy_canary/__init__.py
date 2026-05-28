"""Model-substitution detection for Fy-api channels.

A minimal audit harness that implements five independent probes:

  1. MMD two-sample test on text completions (model-equality-testing, optional).
  2. Alignment / refusal-template fingerprints: ask a deflection-inducing
     question and compare the response shape against a stored baseline.
  3. Embedding drift: run a short prompt set, embed outputs, compare the
     centroid to the baseline centroid.
  4. Metadata: stateless validation of response model field, usage, and
     finish_reason against expected values.
  5. Tokenizer fingerprint: stateless prompt_tokens range check to detect
     tokenizer (and thus model family) substitution.

Use case: catch a gateway (yours, a partner's, or a public aggregator)
silently routing a paid request to a cheaper model.

See `canary.yaml` for configuration and `fy_canary/datasets/` for prompts.
"""

__version__ = "0.3.0"

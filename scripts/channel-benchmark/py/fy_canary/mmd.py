"""MMD two-sample test probe (Gao et al., arXiv 2410.20247).

Implemented by wrapping the `model-equality-testing` PyPI package. Loaded
lazily so users who don't want the torch dependency can skip this module
entirely by setting `mmd_enabled: false` in the canary config.

Algorithm summary:
  - Collect N samples from baseline source and N from current source.
  - Convert text to a fixed-length byte sequence (the package handles this).
  - Compute MMD with the Hamming kernel.
  - Calibrate p-value by permutation test.
  - Reject "same distribution" at p < alpha.

With N=10, Gao et al. report ~77% power against quantization substitution.
We use N from config (default 10) and alpha=0.01 for publication-like FPR.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MmdVerdict:
    prompt_id: str
    p_value: float
    statistic: float
    n_baseline: int
    n_current: int
    alpha: float
    passed: bool        # True = "same distribution" (no flag)


def mmd_available() -> bool:
    """Check whether the MMD backend is importable. Cheap enough to call repeatedly."""
    try:
        import model_equality_testing  # noqa: F401
        return True
    except ImportError:
        return False


def evaluate_mmd(
    *,
    prompt_id: str,
    baseline_samples: list[str],
    current_samples: list[str],
    alpha: float = 0.01,
) -> MmdVerdict:
    """Run a permutation MMD test. Returns a verdict with p-value.

    If p < alpha, we flag the channel (passed=False). If p >= alpha, we
    accept "same distribution" (passed=True).

    Raises ImportError if `model_equality_testing` isn't installed; callers
    should gate on `mmd_available()` before invoking.
    """
    # Import lazily so that a repo without canary extras still imports cleanly.
    from model_equality_testing.algorithm import run_two_sample_test
    from model_equality_testing.distribution import CompletionSample

    # The package expects CompletionSample objects keyed by the same prompt.
    # We represent each sample as an empty prompt + the completion text so
    # the Hamming kernel operates on completions only. If we later use
    # multiple prompts we split per-prompt and aggregate.
    baseline = CompletionSample(prompts=[""] * len(baseline_samples),
                                 completions=baseline_samples)
    current = CompletionSample(prompts=[""] * len(current_samples),
                                completions=current_samples)

    out = run_two_sample_test(
        baseline,
        current,
        stat_type="mmd_hamming",
        pvalue_type="permutation_pvalue",
    )
    # The package returns a dict-like object; extract p and statistic robustly.
    p_value = float(getattr(out, "p_value", None) or out["p_value"])
    statistic = float(getattr(out, "statistic", None) or out.get("statistic", 0.0))

    return MmdVerdict(
        prompt_id=prompt_id,
        p_value=p_value,
        statistic=statistic,
        n_baseline=len(baseline_samples),
        n_current=len(current_samples),
        alpha=alpha,
        passed=p_value >= alpha,
    )

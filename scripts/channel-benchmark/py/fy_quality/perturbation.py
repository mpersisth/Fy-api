"""Deterministic prompt perturbations for contamination defense.

Problem: if you commit `quality.jsonl` to any repo that a model provider
ever crawls (public GitHub mirrors, npm-style caches, internal telemetry),
your "golden" prompts become training data. After that, a provider's pass
rate on the suite tells you nothing about general quality — it's an
evaluation of how well they memorized your fixtures.

Defense: perturb each prompt deterministically before sending, so the
text that actually hits the model differs from what's in the file. A
contaminated model has learned the FILE text, not the perturbed text.

Design constraints:
  1. Deterministic. Same (seed, prompt_id, strategy) → same perturbation.
     This is essential for caching + reproducibility.
  2. Semantics-preserving. A human reading the perturbed prompt should
     arrive at the same answer as the original. Paraphrasing is out;
     only lexically trivial tweaks that a competent model will still
     answer correctly.
  3. Invisible to grader. `expected` / `rubric` / `reference` still
     describe the ORIGINAL intent, so perturbations must not change
     what a correct answer looks like.
  4. Stackable. Each strategy is independent; multiple strategies can
     apply in a fixed order.

Available strategies:

  - `whitespace`:  Insert a U+200B zero-width-space at a deterministic
                   character position. Invisible, semantics-preserving,
                   but textually distinct from the file.
  - `trailing_marker`: Append ` <!--N-->` where N is a hashed nonce.
                   Most models treat trailing HTML comments as noise.
  - `synonym`:     Replace one occurrence of a mapped word with a
                   synonym (e.g. 'compute' → 'calculate'). Safer than
                   general paraphrasing because the mapping is a fixed,
                   reviewable table.

The intended use is `whitespace` + `trailing_marker` on everything, and
`synonym` only on prompts where the mapping has been reviewed against
the grader (e.g. free-form rubric; NOT exact-match).
"""

from __future__ import annotations

import hashlib
from typing import Iterable

ZERO_WIDTH_SPACE = "​"

# Small, conservative, human-reviewed synonym map. Keys are lowercased
# tokens; we only replace the FIRST whole-word occurrence to keep
# perturbations minimal.
_SYNONYMS: dict[str, str] = {
    "compute": "calculate",
    "calculate": "compute",
    "reply": "respond",
    "respond": "reply",
    "write": "produce",
    "give": "provide",
    "provide": "give",
    "one-line": "single-line",
    "single-line": "one-line",
    "exactly": "precisely",
    "explain": "describe",
    "describe": "explain",
}


def _deterministic_int(seed: int, prompt_id: str, salt: str) -> int:
    """Return a reproducible non-negative int from (seed, prompt_id, salt)."""
    h = hashlib.sha256(f"{seed}:{prompt_id}:{salt}".encode()).digest()
    return int.from_bytes(h[:8], "big")


def _apply_whitespace(prompt: str, seed: int, prompt_id: str) -> str:
    """Insert a single ZWSP at a deterministic index.

    Placed between two ASCII letters so tokenizers that split on
    character classes don't just roll it into a single token with
    the neighbour. The exact index is hash-derived so the same
    (seed, prompt_id) always picks the same slot.
    """
    if not prompt:
        return prompt
    candidates = [
        i for i in range(1, len(prompt))
        if prompt[i - 1].isalpha() and prompt[i].isalpha()
    ]
    if not candidates:
        return prompt
    n = _deterministic_int(seed, prompt_id, "whitespace")
    idx = candidates[n % len(candidates)]
    return prompt[:idx] + ZERO_WIDTH_SPACE + prompt[idx:]


def _apply_trailing_marker(prompt: str, seed: int, prompt_id: str) -> str:
    """Append a deterministic HTML-comment marker.

    Models overwhelmingly ignore trailing HTML comments when following
    instructions, so a rubric/grader on the output is unaffected.
    """
    nonce = _deterministic_int(seed, prompt_id, "marker") % 1_000_000
    suffix = f" <!--fq{nonce:06d}-->"
    # Keep existing trailing whitespace stripped so we don't double-space.
    return prompt.rstrip() + suffix


def _apply_synonym(prompt: str, seed: int, prompt_id: str) -> str:
    """Replace the first whole-word hit in the synonym map."""
    words = prompt.split(" ")
    chosen_idx: int | None = None
    for i, w in enumerate(words):
        key = _strip_punct(w).lower()
        if key in _SYNONYMS:
            chosen_idx = i
            break
    if chosen_idx is None:
        return prompt
    original = words[chosen_idx]
    key = _strip_punct(original).lower()
    replacement = _SYNONYMS[key]
    # Preserve capitalization of the first character.
    if original[:1].isupper():
        replacement = replacement.capitalize()
    # Preserve trailing punctuation: "calculate," → "compute,"
    trailing = ""
    for c in reversed(original):
        if not c.isalpha():
            trailing = c + trailing
        else:
            break
    words[chosen_idx] = replacement + trailing
    _ = seed, prompt_id  # determinism comes from "first hit", not the seed
    return " ".join(words)


def _strip_punct(s: str) -> str:
    return "".join(c for c in s if c.isalpha())


_STRATEGIES = {
    "whitespace": _apply_whitespace,
    "trailing_marker": _apply_trailing_marker,
    "synonym": _apply_synonym,
}


def apply_perturbations(
    prompt: str,
    *,
    seed: int,
    prompt_id: str,
    strategies: Iterable[str],
) -> str:
    """Apply the given strategies in order; unknown names raise ValueError.

    Strategies are applied left-to-right so callers can order them
    deterministically — e.g. synonym-then-marker makes the marker fall
    after the substituted word, which is usually what you want.
    """
    out = prompt
    for s in strategies:
        fn = _STRATEGIES.get(s)
        if fn is None:
            raise ValueError(
                f"unknown perturbation strategy {s!r}; "
                f"known: {sorted(_STRATEGIES)}"
            )
        out = fn(out, seed, prompt_id)
    return out


def known_strategies() -> list[str]:
    return sorted(_STRATEGIES)

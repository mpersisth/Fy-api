"""Grader — turn a single (case, response) pair into a verdict.

A verdict is one of:
  - PASS  : every assertion held
  - FAIL  : some assertion was violated; the .reasons list explains why
  - ERROR : the request itself failed (network/timeout); never to be confused
            with FAIL, because the gateway didn't get a chance to reply
  - SKIP  : the case was filtered out (e.g. applies_to_backends mismatch)

The grader knows nothing about how the request was sent. It only sees the
case definition and the (status_code, body_text) tuple, which keeps it
trivial to test with httpx.MockTransport.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from fy_conformance.dataset import Case


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIP = "SKIP"


@dataclass
class Result:
    case_id: str
    category: str
    verdict: Verdict
    status_code: Optional[int]
    body_excerpt: str
    reasons: list[str] = field(default_factory=list)
    transport_error: Optional[str] = None
    skip_reason: Optional[str] = None

    def is_pass(self) -> bool:
        return self.verdict is Verdict.PASS


def status_class(code: int) -> str:
    if 200 <= code < 300: return "2xx"
    if 300 <= code < 400: return "3xx"
    if 400 <= code < 500: return "4xx"
    if 500 <= code < 600: return "5xx"
    return f"{code // 100}xx"


def _walk_path(obj: Any, path: str) -> tuple[bool, Any]:
    """Walk a dotted path through a JSON-like object. Returns (found, value)."""
    cur: Any = obj
    for seg in path.split("."):
        if seg.isdigit() or (seg.startswith("-") and seg[1:].isdigit()):
            idx = int(seg)
            if not isinstance(cur, list) or not (-len(cur) <= idx < len(cur)):
                return False, None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or seg not in cur:
                return False, None
            cur = cur[seg]
    return True, cur


def grade(case: Case, status_code: int, body_text: str) -> Result:
    reasons: list[str] = []

    # Exact status code
    if case.expect_status_code is not None:
        if status_code != case.expect_status_code:
            reasons.append(
                f"expected status_code={case.expect_status_code}, got {status_code}"
            )

    # Status class
    if case.expect_status_class:
        actual = status_class(status_code)
        ok = False
        for expected in [s.strip() for s in case.expect_status_class.split("_or_")]:
            if expected == actual:
                ok = True
                break
        if not ok:
            reasons.append(
                f"expected status_class={case.expect_status_class}, got {actual} ({status_code})"
            )

    # Substring expectations on the response body
    body_lower = body_text.lower()
    for needle in case.expect_message_contains:
        if needle.lower() not in body_lower:
            reasons.append(f'expected response body to contain "{needle}"')
    for forbidden in case.must_not_contain:
        if forbidden.lower() in body_lower:
            reasons.append(f'response body leaked forbidden marker "{forbidden}"')

    # JSON path expectation
    if case.expect_response_field:
        try:
            parsed = json.loads(body_text)
        except (ValueError, TypeError):
            reasons.append(
                f'expected JSON response with path "{case.expect_response_field}", but body is not JSON'
            )
        else:
            found, _ = _walk_path(parsed, case.expect_response_field)
            if not found:
                reasons.append(
                    f'expected response field "{case.expect_response_field}" to exist'
                )

    verdict = Verdict.PASS if not reasons else Verdict.FAIL
    return Result(
        case_id=case.id,
        category=case.category,
        verdict=verdict,
        status_code=status_code,
        body_excerpt=body_text[:300],
        reasons=reasons,
    )


def grade_skip(case: Case, reason: str) -> Result:
    return Result(
        case_id=case.id,
        category=case.category,
        verdict=Verdict.SKIP,
        status_code=None,
        body_excerpt="",
        reasons=[],
        skip_reason=reason,
    )


def grade_transport_error(case: Case, exc: Exception) -> Result:
    return Result(
        case_id=case.id,
        category=case.category,
        verdict=Verdict.ERROR,
        status_code=None,
        body_excerpt="",
        reasons=[],
        transport_error=f"{type(exc).__name__}: {exc}",
    )

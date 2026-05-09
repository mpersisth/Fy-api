"""Conformance test cases — JSONL dataset loader.

Each row in the dataset describes a single assertion against the gateway.
We keep the schema deliberately small so it's easy to author by hand or
extract from existing test reports.

Schema fields (all optional except `id`):

    id                       — unique case identifier (string)
    category                 — free-form bucket for the report
    description              — human-readable note (shown in failure markdown)
    endpoint                 — request path, e.g. "/v1/chat/completions"
    method                   — HTTP method, default POST
    applies_to_backends      — list of backend tags this case targets, e.g.
                                ["openai", "deepseek"]. When the run config sets
                                target.backend, cases whose list does NOT include
                                that backend are SKIPPED. Default = applies to all.

    # ---- request mutation: pick at most one shape ----
    raw_body                 — send this string verbatim (e.g. malformed JSON)
    override_field           — set <field> to override_value in the baseline
                                Supports dotted paths: "messages.0.role" navigates
                                into nested dict/list structure.
    override_value           — JSON-able value (str/number/bool/null/list/dict)
    remove_field             — delete <field> from the baseline before sending
                                Supports dotted paths.
    override_auth            — replace the Authorization header for this case
    extra_body               — dict merged into the baseline body AFTER override/
                                remove. Lets a case add multiple fields at once
                                (e.g. {"tools":[...], "tool_choice":"required"}).
    body_replace             — dict that REPLACES the baseline body entirely
                                (still gets `model` filled in if missing)

    # ---- expectations ----
    expect_status_code       — exact status (e.g. 401)
    expect_status_class      — "2xx" / "4xx" / "5xx" / "4xx_or_5xx" / "2xx_or_4xx"
    expect_message_contains  — list of substrings the response body must contain
    must_not_contain         — list of substrings the response body must NOT contain
    expect_response_field    — dotted path that must exist in the JSON response,
                                e.g. "choices.0.message.content"
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# Sentinel used internally so we can tell "value is None" from "value not set".
_UNSET: Any = object()


@dataclass
class Case:
    id: str
    category: str = "uncategorized"
    description: str = ""
    endpoint: str = "/v1/chat/completions"
    method: str = "POST"
    applies_to_backends: list[str] = field(default_factory=list)

    raw_body: Optional[str] = None
    override_field: Optional[str] = None
    override_value: Any = _UNSET
    remove_field: Optional[str] = None
    override_auth: Optional[str] = None
    extra_body: Optional[dict] = None
    body_replace: Optional[dict] = None

    expect_status_code: Optional[int] = None
    expect_status_class: Optional[str] = None
    expect_message_contains: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    expect_response_field: Optional[str] = None

    raw: dict = field(default_factory=dict)  # preserved for the report

    def has_override_value(self) -> bool:
        return self.override_value is not _UNSET

    def applies_to(self, backend: Optional[str]) -> bool:
        """Return True if this case should run against the given backend.

        - applies_to_backends empty / unset → applies to all backends
        - backend is None (config didn't set one) → run everything
        """
        if not self.applies_to_backends:
            return True
        if backend is None:
            return True
        return backend.lower() in {b.lower() for b in self.applies_to_backends}


def load(path: str | Path) -> list[Case]:
    cases: list[Case] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        cases.append(_from_row(row))
    return cases


def _from_row(row: dict) -> Case:
    case_id = row.get("id")
    if not case_id:
        raise ValueError(f"case is missing required 'id' field: {row!r}")
    kwargs: dict = {
        "id": case_id,
        "category": row.get("category", "uncategorized"),
        "description": row.get("description", ""),
        "endpoint": row.get("endpoint", "/v1/chat/completions"),
        "method": row.get("method", "POST"),
        "applies_to_backends": list(row.get("applies_to_backends") or []),
        "raw_body": row.get("raw_body"),
        "override_field": row.get("override_field"),
        "remove_field": row.get("remove_field"),
        "override_auth": row.get("override_auth"),
        "extra_body": row.get("extra_body"),
        "body_replace": row.get("body_replace"),
        "expect_status_code": row.get("expect_status_code"),
        "expect_status_class": row.get("expect_status_class"),
        "expect_message_contains": list(row.get("expect_message_contains") or []),
        "must_not_contain": list(row.get("must_not_contain") or []),
        "expect_response_field": row.get("expect_response_field"),
        "raw": row,
    }
    if "override_value" in row:
        kwargs["override_value"] = row["override_value"]
    return Case(**kwargs)


def _split_path(path: str) -> list[str | int]:
    """Split a dotted path into segments. Numeric segments become ints (list indices)."""
    out: list[str | int] = []
    for seg in path.split("."):
        if seg.isdigit() or (seg.startswith("-") and seg[1:].isdigit()):
            out.append(int(seg))
        else:
            out.append(seg)
    return out


def _set_path(obj: Any, path: str, value: Any) -> None:
    """Set obj[path] = value, navigating dicts and lists by dotted path.

    Intermediate containers must already exist; we don't auto-create them
    (a typo in the path should fail loudly, not silently create empty dicts).
    """
    segs = _split_path(path)
    cur = obj
    for s in segs[:-1]:
        cur = cur[s]
    cur[segs[-1]] = value


def _del_path(obj: Any, path: str) -> None:
    segs = _split_path(path)
    cur = obj
    for s in segs[:-1]:
        cur = cur[s]
    last = segs[-1]
    if isinstance(cur, list):
        del cur[last]  # type: ignore[index]
    else:
        cur.pop(last, None)


def build_request_body(case: Case, baseline: dict) -> tuple[Optional[str], Optional[dict]]:
    """Return (raw_body, json_body) — at most one of them is non-None.

    raw_body is set when the case wants to send a literal string (e.g.
    malformed JSON). Otherwise we deep-copy baseline and apply the
    override/remove/extra_body operations in order.
    """
    if case.raw_body is not None:
        return case.raw_body, None
    if case.body_replace is not None:
        body = json.loads(json.dumps(case.body_replace))  # deep copy
        # Always preserve `model` if not explicitly supplied
        body.setdefault("model", baseline.get("model"))
        return None, body
    body = json.loads(json.dumps(baseline))  # deep copy via JSON round-trip
    if case.remove_field:
        _del_path(body, case.remove_field)
    if case.override_field is not None and case.has_override_value():
        if "." in case.override_field:
            _set_path(body, case.override_field, case.override_value)
        else:
            body[case.override_field] = case.override_value
    if case.extra_body:
        for k, v in case.extra_body.items():
            body[k] = v
    return None, body


def iter_cases(cases: Iterable[Case], filter_category: Optional[str] = None) -> Iterable[Case]:
    for c in cases:
        if filter_category and c.category != filter_category:
            continue
        yield c

"""Conformance test cases — JSONL dataset loader.

Each row in the dataset describes a single assertion against the gateway.
We keep the schema deliberately small so it's easy to author by hand or
extract from existing test reports.

Schema fields (all optional except `id`):

    id                       — unique case identifier (string)
    category                 — free-form bucket for the report
    endpoint                 — request path, e.g. "/v1/chat/completions"
    method                   — HTTP method, default POST

    # ---- request mutation: pick at most one shape ----
    raw_body                 — send this string verbatim (e.g. malformed JSON)
    override_field           — set <field> to override_value in the baseline
    override_value           — JSON-able value (str/number/bool/null/list/dict)
    remove_field             — delete <field> from the baseline before sending
    override_auth            — replace the Authorization header for this case

    # ---- expectations ----
    expect_status_code       — exact status (e.g. 401)
    expect_status_class      — "2xx" / "4xx" / "5xx" / "4xx_or_5xx"
    expect_message_contains  — list of substrings the response body must contain
    must_not_contain         — list of substrings the response body must NOT contain
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
    endpoint: str = "/v1/chat/completions"
    method: str = "POST"

    raw_body: Optional[str] = None
    override_field: Optional[str] = None
    override_value: Any = _UNSET
    remove_field: Optional[str] = None
    override_auth: Optional[str] = None

    expect_status_code: Optional[int] = None
    expect_status_class: Optional[str] = None
    expect_message_contains: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)

    raw: dict = field(default_factory=dict)  # preserved for the report

    def has_override_value(self) -> bool:
        return self.override_value is not _UNSET


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
        "endpoint": row.get("endpoint", "/v1/chat/completions"),
        "method": row.get("method", "POST"),
        "raw_body": row.get("raw_body"),
        "override_field": row.get("override_field"),
        "remove_field": row.get("remove_field"),
        "override_auth": row.get("override_auth"),
        "expect_status_code": row.get("expect_status_code"),
        "expect_status_class": row.get("expect_status_class"),
        "expect_message_contains": list(row.get("expect_message_contains") or []),
        "must_not_contain": list(row.get("must_not_contain") or []),
        "raw": row,
    }
    if "override_value" in row:
        kwargs["override_value"] = row["override_value"]
    return Case(**kwargs)


def build_request_body(case: Case, baseline: dict) -> tuple[Optional[str], Optional[dict]]:
    """Return (raw_body, json_body) — at most one of them is non-None.

    raw_body is set when the case wants to send a literal string (e.g.
    malformed JSON). Otherwise we deep-copy baseline and apply the
    override/remove operation.
    """
    if case.raw_body is not None:
        return case.raw_body, None
    body = json.loads(json.dumps(baseline))  # deep copy via JSON round-trip
    if case.remove_field:
        body.pop(case.remove_field, None)
    if case.override_field is not None and case.has_override_value():
        body[case.override_field] = case.override_value
    return None, body


def iter_cases(cases: Iterable[Case], filter_category: Optional[str] = None) -> Iterable[Case]:
    for c in cases:
        if filter_category and c.category != filter_category:
            continue
        yield c

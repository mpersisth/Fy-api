"""Pure-Python graders: exact, regex, contains, json_schema.

These never touch the network. Use them for deterministic prompts where a
correct answer is unambiguous and cheap to verify.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..dataset import PromptRow
from . import GradeResult


def _normalize(s: str) -> str:
    """Trim whitespace and surrounding quotes that models sometimes add."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'", "`"}:
        s = s[1:-1].strip()
    return s


@dataclass
class ExactGrader:
    name: str = "exact"

    async def grade(self, row: PromptRow, output: str) -> GradeResult:
        if row.expected is None:
            return GradeResult(False, 0.0, "exact grader requires 'expected'")
        got = _normalize(output)
        want = _normalize(str(row.expected))
        ok = got == want
        return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else f"got={got!r} want={want!r}")


@dataclass
class RegexGrader:
    name: str = "regex"

    async def grade(self, row: PromptRow, output: str) -> GradeResult:
        if row.expected is None:
            return GradeResult(False, 0.0, "regex grader requires 'expected' (the pattern)")
        try:
            pat = re.compile(str(row.expected))
        except re.error as e:
            return GradeResult(False, 0.0, f"bad regex: {e}")
        ok = bool(pat.search(output))
        return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else f"no match: {output[:120]!r}")


@dataclass
class ContainsGrader:
    name: str = "contains"

    async def grade(self, row: PromptRow, output: str) -> GradeResult:
        if row.expected is None:
            return GradeResult(False, 0.0, "contains grader requires 'expected'")
        want = str(row.expected)
        ok = want.lower() in output.lower()
        return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else f"missing {want!r} in {output[:120]!r}")


@dataclass
class JsonSchemaGrader:
    """Check that output is valid JSON conforming to a (tiny) subset of JSON Schema.

    We implement just enough schema features to cover the everyday cases:
    type, required, properties (with const / enum), additionalProperties:false.
    This avoids pulling in jsonschema just for a few deterministic checks.
    """

    name: str = "json_schema"

    async def grade(self, row: PromptRow, output: str) -> GradeResult:
        if row.expected is None:
            return GradeResult(False, 0.0, "json_schema grader requires 'expected' (schema string)")
        try:
            schema = json.loads(row.expected) if isinstance(row.expected, str) else row.expected
        except json.JSONDecodeError as e:
            return GradeResult(False, 0.0, f"schema is not valid JSON: {e}")

        extracted = _extract_first_json(output)
        if extracted is None:
            return GradeResult(False, 0.0, "no JSON object found in output")

        err = _validate(extracted, schema)
        if err is None:
            return GradeResult(True, 1.0, "")
        return GradeResult(False, 0.0, err)


def _extract_first_json(s: str) -> Any | None:
    """Find the first balanced-brace JSON object/array in `s` and parse it."""
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = s.find(opener)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(s)):
            if s[i] == opener:
                depth += 1
            elif s[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except json.JSONDecodeError:
                        break  # try the other opener
        # fall through to try the other pair
    # Fallback: maybe it parses as-is.
    try:
        return json.loads(s.strip())
    except json.JSONDecodeError:
        return None


def _validate(value: Any, schema: dict) -> str | None:
    """Minimal validator — return None on success, else error message."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return f"expected object, got {type(value).__name__}"
        for req in schema.get("required", []):
            if req not in value:
                return f"missing required key {req!r}"
        props: dict = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                return f"unexpected keys {sorted(extra)!r}"
        for k, sub_schema in props.items():
            if k in value:
                err = _validate(value[k], sub_schema)
                if err:
                    return f".{k}: {err}"
        return None
    if t == "array":
        if not isinstance(value, list):
            return f"expected array, got {type(value).__name__}"
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(value):
                err = _validate(item, items_schema)
                if err:
                    return f"[{i}]: {err}"
        return None
    if t == "string":
        if not isinstance(value, str):
            return f"expected string, got {type(value).__name__}"
    elif t == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"expected number, got {type(value).__name__}"
    elif t == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return f"expected integer, got {type(value).__name__}"
    elif t == "boolean":
        if not isinstance(value, bool):
            return f"expected boolean, got {type(value).__name__}"

    if "const" in schema and value != schema["const"]:
        return f"expected {schema['const']!r}, got {value!r}"
    if "enum" in schema and value not in schema["enum"]:
        return f"value {value!r} not in enum {schema['enum']!r}"
    return None

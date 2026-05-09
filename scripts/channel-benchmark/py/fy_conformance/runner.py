"""Runner — execute conformance cases against a live Fy-api gateway."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from fy_conformance.config import Config
from fy_conformance.dataset import Case, build_request_body
from fy_conformance.grader import Result, grade, grade_skip, grade_transport_error


def _request_kwargs(case: Case, body_json: Optional[dict], body_raw: Optional[str]) -> dict:
    kwargs: dict[str, Any] = {}
    if body_raw is not None:
        kwargs["content"] = body_raw.encode("utf-8")
    elif body_json is not None:
        kwargs["json"] = body_json
    return kwargs


async def run_one(client: httpx.AsyncClient, case: Case, baseline: dict, default_auth: str) -> Result:
    raw_body, json_body = build_request_body(case, baseline)
    headers = {"Content-Type": "application/json"}
    if case.override_auth is not None:
        headers["Authorization"] = case.override_auth
    elif default_auth:
        headers["Authorization"] = default_auth

    started = time.perf_counter()
    try:
        resp = await client.request(
            case.method,
            case.endpoint,
            headers=headers,
            **_request_kwargs(case, json_body, raw_body),
        )
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:  # pragma: no cover (covered by unit test of grader)
        return grade_transport_error(case, exc)
    elapsed_ms = (time.perf_counter() - started) * 1000
    body_text = resp.text or ""
    result = grade(case, resp.status_code, body_text)
    # Stash elapsed_ms for the report.
    result.body_excerpt = body_text[:500]
    return result


async def run_all(cfg: Config, cases: list[Case]) -> list[Result]:
    backend = cfg.target.backend
    async with httpx.AsyncClient(
        base_url=cfg.gateway.base_url,
        timeout=cfg.request_timeout_sec,
        headers={**cfg.extra_headers},
    ) as client:
        sem = asyncio.Semaphore(cfg.concurrency)
        # Channel-pin: when configured, append "-{id}" to user_token so the
        # gateway routes every conformance probe to the same channel. See
        # middleware/auth.go ~line 431; admin user_token required.
        token = cfg.gateway.user_token
        if cfg.gateway.pin_channel_id is not None:
            token = f"{token}-{cfg.gateway.pin_channel_id}"
        default_auth = f"Bearer {token}"

        async def worker(case: Case) -> Result:
            if not case.applies_to(backend):
                return grade_skip(
                    case,
                    f"applies_to_backends={case.applies_to_backends} excludes target.backend={backend!r}",
                )
            async with sem:
                return await run_one(client, case, cfg.target.baseline_request, default_auth)

        return await asyncio.gather(*(worker(c) for c in cases))


def aggregate(results: list[Result]) -> dict:
    total = len(results)
    passes = sum(1 for r in results if r.is_pass())
    fails  = sum(1 for r in results if r.verdict.value == "FAIL")
    errors = sum(1 for r in results if r.verdict.value == "ERROR")
    skips  = sum(1 for r in results if r.verdict.value == "SKIP")
    by_cat: dict[str, dict] = {}
    for r in results:
        c = by_cat.setdefault(r.category, {"total": 0, "pass": 0, "fail": 0, "error": 0, "skip": 0})
        c["total"] += 1
        c[r.verdict.value.lower()] = c.get(r.verdict.value.lower(), 0) + 1
    # pass_rate is computed against executed (non-SKIP) cases
    executed = total - skips
    return {
        "total": total,
        "executed": executed,
        "pass": passes,
        "fail": fails,
        "error": errors,
        "skip": skips,
        "pass_rate": round(passes / executed, 4) if executed else 0.0,
        "by_category": by_cat,
    }


def to_jsonl(results: list[Result]) -> str:
    return "\n".join(
        json.dumps(
            {
                "id": r.case_id,
                "category": r.category,
                "verdict": r.verdict.value,
                "status_code": r.status_code,
                "reasons": r.reasons,
                "transport_error": r.transport_error,
                "skip_reason": r.skip_reason,
                "body_excerpt": r.body_excerpt,
            },
            ensure_ascii=False,
        )
        for r in results
    )


def to_markdown(results: list[Result], summary: dict) -> str:
    lines: list[str] = []
    lines.append("# fy-conformance results\n")
    lines.append(f"- **total**: {summary['total']}")
    lines.append(f"- **executed**: {summary.get('executed', summary['total'])}")
    lines.append(f"- **pass**: {summary['pass']} ({summary['pass_rate']*100:.1f}% of executed)")
    lines.append(f"- **fail**: {summary['fail']}")
    lines.append(f"- **error**: {summary['error']}")
    lines.append(f"- **skip**: {summary.get('skip', 0)}")
    lines.append("\n## By category\n")
    lines.append("| category | total | pass | fail | error | skip |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for cat, c in sorted(summary["by_category"].items()):
        lines.append(
            f"| {cat} | {c['total']} | {c.get('pass',0)} | {c.get('fail',0)} | {c.get('error',0)} | {c.get('skip',0)} |"
        )
    fails = [r for r in results if r.verdict.value == "FAIL"]
    if fails:
        lines.append("\n## Failures\n")
        for r in fails:
            lines.append(f"### `{r.case_id}` ({r.category})")
            lines.append(f"- status_code: {r.status_code}")
            for reason in r.reasons:
                lines.append(f"- ❌ {reason}")
            if r.body_excerpt:
                lines.append(f"- body excerpt: `{r.body_excerpt[:200]}`")
            lines.append("")
    return "\n".join(lines) + "\n"

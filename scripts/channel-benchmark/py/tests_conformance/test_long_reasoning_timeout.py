"""Regression test for incident 2026-05-11 — long-reasoning streams cut at 600s.

Background:
    On 2026-05-11 a customer reported aime25 / gpqa-diamond benchmarks scoring
    far below vendor numbers because Fy-api's upstream HTTP client had
    `RELAY_TIMEOUT=600s`, which fired before any of their long-reasoning
    streams (10-30 min wall time) could complete. nginx in front (900s) could
    not save them — the inner layer was tighter than the outer layer.

    The fix was config-only (RELAY_TIMEOUT=1800, STREAMING_TIMEOUT=600,
    nginx 1800s) on both CN + SG, no code change.

What this test guards:
    Independent of the gateway-side env, the *client-side* behavior is that
    `request_timeout_sec` in the conformance config decides when the client
    gives up. We lock in two shapes:

      1. When `request_timeout_sec` < simulated upstream response time, the
         result is ERROR (transport_error mentions a timeout). This is the
         shape that the customer's SDK rendered as "Receive batching backend
         response failed".

      2. When `request_timeout_sec` >= simulated upstream response time, the
         result is PASS. This is the post-fix shape — long streams complete.

    We do NOT attempt to test gateway-side env values from a unit test; that
    requires a real Fy-api process. We DO assert that `loadtest.long-thinking.
    yaml` ships with `request_timeout_sec: 1800` so it would survive a 25-min
    response, plus `incidents/2026-05-11-long-reasoning-timeout.md` exists so
    the human runbook is colocated with the regression artifact.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
import yaml

# Allow running directly without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fy_conformance.config import Config, GatewayCfg, TargetCfg
from fy_conformance.dataset import Case
from fy_conformance.grader import Verdict
from fy_conformance.runner import run_all


# ---------------------------------------------------------------------------
# Slow async transport — used to simulate an upstream that takes N seconds
# before returning the first byte. httpx.MockTransport's sync handler can't
# do this because time.sleep would block the event loop and the transport
# layer's timeout cancellation can't fire while it's blocked.
# ---------------------------------------------------------------------------


class _SlowAsyncTransport(httpx.AsyncBaseTransport):
    """Awaits `delay_s` seconds before returning a canned 200 response.

    This mirrors what a long-reasoning model looks like to the client: the
    full HTTP response simply takes a long time to come back. We don't
    bother modelling SSE here — for timeout regression purposes the only
    distinction is "did the client wait long enough to receive ANYTHING".

    Custom transports must enforce httpx timeouts themselves; the timeout
    is exposed on `request.extensions["timeout"]` as a dict of
    {connect, read, write, pool}. We honor `read` because that's the limit
    that fires when an upstream is slow to send the response body.
    """

    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout") or {}
        read_timeout = timeout.get("read")
        # The runner sets `timeout=cfg.request_timeout_sec` as a single float,
        # which httpx maps to all four buckets. Treat read_timeout as the cap.
        if read_timeout is not None and self._delay_s > read_timeout:
            await asyncio.sleep(read_timeout)
            raise httpx.ReadTimeout(
                f"simulated upstream took {self._delay_s}s, client read timeout {read_timeout}s",
                request=request,
            )
        await asyncio.sleep(self._delay_s)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "QED"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            },
        )


def _make_cfg(tmp_path: Path, *, request_timeout_sec: float) -> Config:
    return Config(
        gateway=GatewayCfg(base_url="http://mock", user_token="sk-test"),
        target=TargetCfg(
            model="kimi-k2-thinking",
            baseline_request={
                "model": "kimi-k2-thinking",
                "messages": [{"role": "user", "content": "Prove..."}],
                "max_tokens": 32000,
            },
        ),
        dataset=tmp_path / "unused.jsonl",
        output_dir=tmp_path / "out",
        concurrency=1,
        request_timeout_sec=request_timeout_sec,
        extra_headers={},
    )


def _long_reasoning_case() -> Case:
    return Case(
        id="long-reasoning-completion",
        category="long_reasoning",
        expect_status_class="2xx",
    )


# ---------------------------------------------------------------------------
# Layer 1 — the timeout boundary itself.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_timeout_errors_on_slow_upstream(tmp_path: Path, monkeypatch):
    """When client request_timeout_sec < upstream response time, the case
    must come back as ERROR with a transport_error mentioning a timeout.

    This is the EXACT shape the customer hit pre-fix: gateway took 10+
    minutes, client gave up, customer's SDK rendered it as 'failed'.
    """
    transport = _SlowAsyncTransport(delay_s=0.5)
    orig_init = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient,
        "__init__",
        lambda self, *a, **k: orig_init(self, *a, **{**k, "transport": transport}),
    )

    cfg = _make_cfg(tmp_path, request_timeout_sec=0.1)  # 100ms < 500ms
    results = await run_all(cfg, [_long_reasoning_case()])
    assert len(results) == 1
    r = results[0]
    assert r.verdict is Verdict.ERROR, (
        f"expected ERROR (transport timeout), got {r.verdict.value}: {r.reasons}"
    )
    assert r.transport_error is not None
    # httpx raises ReadTimeout / ConnectTimeout / TimeoutException — all of
    # them format with 'Timeout' in the type name. Be lenient on wording.
    assert "Timeout" in r.transport_error or "timeout" in r.transport_error.lower(), (
        f"transport_error did not mention a timeout: {r.transport_error}"
    )


@pytest.mark.asyncio
async def test_long_timeout_completes_slow_upstream(tmp_path: Path, monkeypatch):
    """When client request_timeout_sec >= upstream response time, the case
    must PASS. This is the post-fix shape — long streams complete cleanly.
    """
    transport = _SlowAsyncTransport(delay_s=0.2)
    orig_init = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient,
        "__init__",
        lambda self, *a, **k: orig_init(self, *a, **{**k, "transport": transport}),
    )

    cfg = _make_cfg(tmp_path, request_timeout_sec=2.0)  # 2s > 200ms
    results = await run_all(cfg, [_long_reasoning_case()])
    assert len(results) == 1
    r = results[0]
    assert r.verdict is Verdict.PASS, (
        f"expected PASS, got {r.verdict.value}: {r.reasons} / {r.transport_error}"
    )


# ---------------------------------------------------------------------------
# Layer 2 — the artifacts we ship to operators must encode the post-fix
# numbers, otherwise a future "well-meaning revert" would silently hand us
# back the customer's bug.
# ---------------------------------------------------------------------------


# Resolve relative to this file so the test passes regardless of CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOADTEST_LONG_THINKING_YAML = _REPO_ROOT / "loadtest.long-thinking.yaml"
_INCIDENT_CARD = (
    _REPO_ROOT.parent / "incidents" / "2026-05-11-long-reasoning-timeout.md"
)


def test_long_thinking_yaml_keeps_30min_timeout():
    """The long-thinking preset must keep request_timeout_sec >= 1800.

    Anything lower means a 25-min response (the customer's actual workload)
    would hit our client-side timeout and be reported as ERROR — re-creating
    exactly the failure mode we just fixed. This guards the YAML against a
    well-meaning 'let's lower the timeout' refactor.
    """
    assert _LOADTEST_LONG_THINKING_YAML.exists(), (
        f"long-thinking preset missing at {_LOADTEST_LONG_THINKING_YAML}; "
        "this preset is the operator-facing way to verify the timeout chain"
    )
    data = yaml.safe_load(_LOADTEST_LONG_THINKING_YAML.read_text())
    timeout = data["load"]["request_timeout_sec"]
    assert timeout >= 1800, (
        f"loadtest.long-thinking.yaml load.request_timeout_sec={timeout}; "
        "must be >= 1800s to survive a 25-min long-reasoning stream like the "
        "one in the 2026-05-11 incident"
    )
    # Must use stream:true — non-streaming masks the STREAMING_TIMEOUT layer
    # because there are no inter-token gaps to trigger it.
    assert data["load"]["stream"] is True, (
        "long-thinking preset must use stream:true to exercise STREAMING_TIMEOUT"
    )
    # Must use concurrency=1 — the test is about the upper-bound of one
    # stream, not capacity. concurrency>1 would obscure single-stream timeout
    # debugging.
    assert data["load"]["concurrency_levels"] == [1], (
        f"long-thinking preset concurrency_levels={data['load']['concurrency_levels']}; "
        "must be [1] to isolate the timeout-chain variable"
    )


def test_incident_card_exists():
    """The human runbook must live next to the regression test. If the card
    is missing, a future operator who hits 'Receive batching backend
    response failed' has nothing to grep — they'd repeat the diagnosis from
    scratch.
    """
    assert _INCIDENT_CARD.exists(), (
        f"incident card missing at {_INCIDENT_CARD}; the regression test "
        "is meaningless without the runbook explaining what the regression IS"
    )
    text = _INCIDENT_CARD.read_text()
    # Sanity: the card must mention the four numbers any operator will need.
    for expected in ("RELAY_TIMEOUT", "STREAMING_TIMEOUT", "1800", "600"):
        assert expected in text, (
            f"incident card missing keyword {expected!r}; this is part of the "
            "minimum information set an operator needs to diagnose a recurrence"
        )

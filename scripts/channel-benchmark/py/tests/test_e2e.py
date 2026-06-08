"""End-to-end tests using httpx.MockTransport to fake an upstream gateway.

These tests prove that:
  - TTFT anchors on the FIRST content chunk (role-only preamble doesn't count)
  - ITL samples are gaps between consecutive content chunks, not including TTFT
  - usage is harvested from the final pre-[DONE] chunk in streaming mode
  - non-streaming path works and reads usage from JSON body
  - Authorization: Bearer <token> header is sent correctly
  - the concurrency ramp produces one aggregate per level
  - report writers emit json/csv/markdown files without crashing
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from fy_loadtest.client import ChatClient
from fy_loadtest.config import Config, ExportConfig, Gateway, LoadProfile, Slo
from fy_loadtest.metrics import aggregate_level
from fy_loadtest.report import write_reports
from fy_loadtest.runner import Ramp


def _sse_frame(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def make_mock_transport(*, stream_delay_ms: int = 10) -> httpx.MockTransport:
    """Mock server: /v1/chat/completions that streams a role-preamble chunk
    then two content chunks ('po', 'ng') then a usage chunk then [DONE].
    Non-streaming responses return full JSON with usage.
    """
    received_auth: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received_auth["auth"] = request.headers.get("authorization", "")
        body = json.loads(request.content.decode())

        if not body.get("stream"):
            return httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
                },
            )

        # Streaming: build SSE as a generator of bytes chunks.
        def stream_body():
            # role preamble — should NOT anchor TTFT
            yield _sse_frame({"choices": [{"delta": {"role": "assistant"}}]})
            # simulate latency via time.sleep; httpx.MockTransport runs sync
            time.sleep(stream_delay_ms / 1000.0)
            yield _sse_frame({"choices": [{"delta": {"content": "po"}}]})
            time.sleep(stream_delay_ms / 1000.0)
            yield _sse_frame({"choices": [{"delta": {"content": "ng"}}]})
            yield _sse_frame({
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
            })
            yield b"data: [DONE]\n\n"

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"".join(stream_body()),
        )

    transport = httpx.MockTransport(handler)
    # Stash the captured headers on the transport itself so tests can inspect.
    transport._received_auth = received_auth  # type: ignore[attr-defined]
    return transport


def _patched_client(token: str, transport: httpx.MockTransport) -> ChatClient:
    """Build a ChatClient that routes all traffic through the mock transport."""
    c = ChatClient("http://mock", token=token, request_timeout=10.0)
    # Swap the underlying AsyncClient for one using the mock transport so that
    # connection pooling, header defaults, and timeouts still come from our code.
    # We preserve headers/timeout from the real client.
    orig = c._client  # noqa: SLF001
    c._client = httpx.AsyncClient(  # noqa: SLF001
        transport=transport,
        timeout=orig.timeout,
        headers=orig.headers,
    )
    # original client is unused now — close it to free the pool
    asyncio.get_event_loop().run_until_complete(orig.aclose())
    return c


@pytest.mark.asyncio
async def test_stream_ttft_skips_role_preamble():
    transport = make_mock_transport(stream_delay_ms=25)
    async with ChatClient("http://mock", "sk-test", request_timeout=10.0) as c:
        # Replace transport
        old = c._client  # noqa: SLF001
        c._client = httpx.AsyncClient(transport=transport, timeout=old.timeout, headers=old.headers)
        await old.aclose()

        r = await c.chat(
            model="gpt-4o-mini", prompt="hi", max_tokens=16, temperature=0.0, stream=True
        )
    assert r.success, r.error
    # The 25ms sleep is between the role preamble and the first content chunk.
    # If our TTFT correctly anchored on the content chunk, TTFT >= 25ms.
    assert r.ttft_s * 1000 >= 20, f"TTFT too low: {r.ttft_s*1000:.1f}ms"
    # And TTFT must be strictly less than E2E.
    assert r.ttft_s < r.e2e_s
    # Two content chunks → one ITL gap sample.
    assert len(r.inter_token_gaps_s) == 1
    # usage pulled from final chunk.
    assert r.usage.completion_tokens == 2
    assert r.usage.prompt_tokens == 9
    # content reconstruction.
    assert r.content_chars == len("pong")
    assert r.finish_reason == "stop"


@pytest.mark.asyncio
async def test_non_stream_usage():
    transport = make_mock_transport()
    async with ChatClient("http://mock", "sk-test", request_timeout=10.0) as c:
        old = c._client  # noqa: SLF001
        c._client = httpx.AsyncClient(transport=transport, timeout=old.timeout, headers=old.headers)
        await old.aclose()
        r = await c.chat(
            model="gpt-4o-mini", prompt="hi", max_tokens=16, temperature=0.0, stream=False
        )
    assert r.success
    assert r.ttft_s == 0
    assert r.inter_token_gaps_s == []
    assert r.usage.completion_tokens == 1
    assert r.finish_reason == "stop"


@pytest.mark.asyncio
async def test_auth_header_uses_bearer():
    transport = make_mock_transport()
    async with ChatClient("http://mock", "sk-mytoken", request_timeout=10.0) as c:
        old = c._client  # noqa: SLF001
        c._client = httpx.AsyncClient(transport=transport, timeout=old.timeout, headers=old.headers)
        await old.aclose()
        await c.chat(model="x", prompt="hi", max_tokens=4, temperature=0, stream=False)
    assert transport._received_auth["auth"] == "Bearer sk-mytoken"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pin_channel_appends_token_suffix():
    """When pin_channel_id is set, the Authorization header carries
    `Bearer <token>-<channel_id>` — the admin-only syntax Fy-api parses in
    middleware/auth.go (~line 431) to force a specific channel."""
    transport = make_mock_transport()
    async with ChatClient(
        "http://mock", "sk-mytoken", request_timeout=10.0, pin_channel_id=42
    ) as c:
        old = c._client  # noqa: SLF001
        c._client = httpx.AsyncClient(transport=transport, timeout=old.timeout, headers=old.headers)
        await old.aclose()
        await c.chat(model="x", prompt="hi", max_tokens=4, temperature=0, stream=False)
    assert transport._received_auth["auth"] == "Bearer sk-mytoken-42"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pin_channel_default_off():
    """Without pin_channel_id, no suffix is appended (back-compat with
    pre-Stage-2 configs)."""
    transport = make_mock_transport()
    async with ChatClient("http://mock", "sk-mytoken", request_timeout=10.0) as c:
        old = c._client  # noqa: SLF001
        c._client = httpx.AsyncClient(transport=transport, timeout=old.timeout, headers=old.headers)
        await old.aclose()
        await c.chat(model="x", prompt="hi", max_tokens=4, temperature=0, stream=False)
    assert transport._received_auth["auth"] == "Bearer sk-mytoken"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_ramp_produces_one_aggregate_per_level(tmp_path: Path):
    cfg = Config(
        gateway=Gateway(base_url="http://mock", user_token="sk-test"),
        load=LoadProfile(
            model="gpt-4o-mini",
            concurrency_levels=[1, 2],
            requests_per_level=3,
            warmup_requests=0,
            stream=True,
            request_timeout_sec=5.0,
        ),
        slo=Slo(ttft_p95_ms=5000, itl_p95_ms=5000),
        export=ExportConfig(formats=["json", "csv", "markdown"], output_dir=str(tmp_path)),
    )
    cfg.validate()

    # Monkey-patch ChatClient to route through mock transport.
    transport = make_mock_transport(stream_delay_ms=5)
    real_init = ChatClient.__init__

    def patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        old = self._client
        self._client = httpx.AsyncClient(
            transport=transport, timeout=old.timeout, headers=old.headers
        )

    ChatClient.__init__ = patched_init  # type: ignore[method-assign]
    try:
        mc_result = await Ramp(cfg).run()
    finally:
        ChatClient.__init__ = real_init  # type: ignore[method-assign]

    assert len(mc_result.results) == 1
    result = mc_result.results[0]
    assert len(result.levels) == 2
    for lv in result.levels:
        assert lv.total == 3
        assert lv.ok == 3, f"errors: {lv.error_breakdown}"
        assert lv.ttft.samples == 3
        # one ITL sample per request, three requests
        assert lv.itl.samples == 3
        assert lv.avg_completion_tokens == 2
        assert lv.goodput_req_per_s is not None
        assert lv.goodput_req_per_s > 0

    files = write_reports(mc_result, ["json", "csv", "markdown"], tmp_path)
    assert len(files) == 3
    for f in files:
        assert f.exists() and f.stat().st_size > 0


def test_aggregate_level_handles_empty():
    a = aggregate_level(concurrency=1, results=[], wall_time_s=0.1)
    assert a.total == 0
    assert a.ok == 0
    assert a.success_rate_pct == 0
    assert a.e2e.samples == 0


def test_loadtest_config_parses_pin_channel_id(tmp_path: Path):
    """YAML round-trip: gateway.pin_channel_id is read back as int when
    present, None otherwise. Back-compat: configs without the field still parse."""
    p = tmp_path / "lt.yaml"
    p.write_text(
        "gateway:\n"
        "  base_url: http://mock\n"
        "  user_token: sk-admin\n"
        "  pin_channel_id: 8\n"
        "load:\n"
        "  model: m\n"
    )
    cfg = Config.load(p)
    assert cfg.gateway.pin_channel_id == 8

    p2 = tmp_path / "lt2.yaml"
    p2.write_text(
        "gateway:\n"
        "  base_url: http://mock\n"
        "  user_token: sk\n"
        "load:\n"
        "  model: m\n"
    )
    cfg2 = Config.load(p2)
    assert cfg2.gateway.pin_channel_id is None


def test_loadtest_config_rejects_nonpositive_pin(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "gateway:\n"
        "  base_url: http://mock\n"
        "  user_token: sk\n"
        "  pin_channel_id: 0\n"
        "load:\n"
        "  model: m\n"
    )
    cfg = Config.load(p)
    import pytest
    with pytest.raises(ValueError, match="must be > 0"):
        cfg.validate()

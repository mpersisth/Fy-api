from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from fy_image_loadtest.client import ImageClient
from fy_image_loadtest.config import Config, ExportConfig, Gateway, ImageProfile, ChannelTarget
from fy_image_loadtest.report import write_reports
from fy_image_loadtest.runner import ImageRamp


def _make_transport() -> httpx.MockTransport:
    received_auth: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received_auth["auth"] = request.headers.get("authorization", "")
        body = json.loads(request.content.decode())
        prompt = body.get("prompt", "")
        return httpx.Response(
            200,
            json={
                "created": 123,
                "data": [
                    {
                        "b64_json": "ZmFrZQ==",
                        "revised_prompt": prompt,
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    transport._received_auth = received_auth  # type: ignore[attr-defined]
    return transport


@pytest.mark.asyncio
async def test_image_client_pins_channel_in_bearer():
    transport = _make_transport()
    async with ImageClient(
        "http://mock",
        "sk-admin",
        request_timeout=10.0,
        pin_channel_id=28,
    ) as client:
        old = client._client  # noqa: SLF001
        client._client = httpx.AsyncClient(transport=transport, timeout=old.timeout, headers=old.headers)
        await old.aclose()
        result = await client.generate(
            {
                "model": "gpt-image-2",
                "prompt": "test",
                "size": "1024x1024",
                "quality": "low",
                "n": 1,
                "response_format": "b64_json",
            }
        )
    assert result.success
    assert result.images == 1
    assert result.has_b64_json is True
    assert transport._received_auth["auth"] == "Bearer sk-admin-28"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_image_ramp_runs_multi_channel_and_writes_reports(tmp_path: Path):
    cfg = Config(
        gateway=Gateway(
            base_url="http://mock",
            user_token="sk-admin",
            channels=[
                ChannelTarget(name="c28", pin_channel_id=28),
                ChannelTarget(name="c41", pin_channel_id=41),
            ],
        ),
        image=ImageProfile(
            model="gpt-image-2",
            prompt="test prompt",
            concurrency_per_channel=2,
            request_timeout_sec=5.0,
            report_interval_sec=3600.0,
            continuous=False,
            duration_sec=30.0,
            max_requests_per_channel=3,
        ),
        export=ExportConfig(formats=["json", "csv", "markdown"], output_dir=str(tmp_path)),
    )
    cfg.validate()

    transport = _make_transport()
    real_init = ImageClient.__init__

    def patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        old = self._client
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=old.timeout,
            headers=old.headers,
        )

    ImageClient.__init__ = patched_init  # type: ignore[method-assign]
    try:
        result = await ImageRamp(cfg).run()
    finally:
        ImageClient.__init__ = real_init  # type: ignore[method-assign]

    assert len(result.channels) == 2
    for ch in result.channels:
        assert ch.total >= 3
        assert ch.ok == ch.total
        assert ch.images == ch.total
        assert ch.e2e_p95_ms() >= 0

    files = write_reports(result, ["json", "csv", "markdown"], tmp_path)
    assert len(files) == 3
    for f in files:
        assert f.exists() and f.stat().st_size > 0


def test_image_loadtest_config_round_trip(tmp_path: Path):
    p = tmp_path / "image-loadtest.yaml"
    p.write_text(
        "gateway:\n"
        "  base_url: http://mock\n"
        "  user_token: sk-admin\n"
        "  channels:\n"
        "    - name: eastus2\n"
        "      pin_channel_id: 28\n"
        "image:\n"
        "  model: gpt-image-2\n"
        "  prompt: draw a mug\n"
        "  duration_sec: 120\n"
        "  concurrency_per_channel: 2\n"
        "  max_requests_per_channel: 5\n"
        "  continuous: false\n",
        encoding="utf-8",
    )
    cfg = Config.load(p)
    assert cfg.gateway.channels[0].pin_channel_id == 28
    assert cfg.image.model == "gpt-image-2"
    assert cfg.image.duration_sec == 120
    assert cfg.image.max_requests_per_channel == 5


@pytest.mark.asyncio
async def test_image_ramp_stops_on_insufficient_quota_error():
    quota_hit = {"seen": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if auth.endswith("-28"):
            quota_hit["seen"] += 1
            return httpx.Response(
                403,
                text="余额不足: user quota is not enough",
            )
        return httpx.Response(
            200,
            json={
                "created": 123,
                "data": [
                    {
                        "b64_json": "ZmFrZQ==",
                    }
                ],
            },
        )

    cfg = Config(
        gateway=Gateway(
            base_url="http://mock",
            user_token="sk-admin",
            channels=[
                ChannelTarget(name="c28", pin_channel_id=28),
                ChannelTarget(name="c41", pin_channel_id=41),
            ],
        ),
        image=ImageProfile(
            model="gpt-image-2",
            prompt="test prompt",
            concurrency_per_channel=1,
            request_timeout_sec=5.0,
            report_interval_sec=3600.0,
            continuous=True,
        ),
        export=ExportConfig(formats=["json"], output_dir="unused"),
    )
    cfg.validate()

    transport = httpx.MockTransport(handler)
    real_init = ImageClient.__init__

    def patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        old = self._client
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=old.timeout,
            headers=old.headers,
        )

    ImageClient.__init__ = patched_init  # type: ignore[method-assign]
    try:
        result = await ImageRamp(cfg).run()
    finally:
        ImageClient.__init__ = real_init  # type: ignore[method-assign]

    assert quota_hit["seen"] >= 1
    assert result.stopped_reason == "insufficient quota"
    assert result.channels[0].failed >= 1

"""Test harness — manages channel/token/config lifecycle for each test case."""

import json
import time
from dataclasses import dataclass, field

from .client import FyApiClient


@dataclass
class ChannelConfig:
    name: str
    priority: int = 100
    weight: int = 100
    channel_id: int = 0


@dataclass
class TestContext:
    channels: list[ChannelConfig] = field(default_factory=list)
    channel_ids: list[int] = field(default_factory=list)
    channel_names: dict[int, str] = field(default_factory=dict)
    token_id: int = 0
    token_key: str = ""


class TestHarness:
    def __init__(self, client: FyApiClient, config: dict):
        self.client = client
        self.config = config
        self.model = config["test"]["model"]
        self.group = config["test"]["group"]
        self.upstream_key = config["env"]["upstream_api_key"]
        self.channel_type = config["channels"]["type"]
        self.tag_prefix = config["channels"]["tag_prefix"]

    def setup_channels(self, channels: list[ChannelConfig]) -> TestContext:
        """Create test channels and a token, return context."""
        ctx = TestContext(channels=channels)
        tag = f"{self.tag_prefix}-{int(time.time())}"

        for ch in channels:
            data = {
                "name": ch.name,
                "type": self.channel_type,
                "key": self.upstream_key,
                "models": self.model,
                "groups": self.group,
                "priority": ch.priority,
                "weight": ch.weight,
                "status": 1,
                "tag": tag,
            }
            resp = self.client.create_channel(data)
            if not resp.get("success"):
                raise RuntimeError(f"Failed to create channel {ch.name}: {resp}")
            ch_id = resp["data"]
            ch.channel_id = ch_id
            ctx.channel_ids.append(ch_id)
            ctx.channel_names[ch_id] = ch.name

        token_resp = self.client.create_token(f"dist-test-{int(time.time())}")
        if not token_resp.get("success"):
            raise RuntimeError(f"Failed to create token: {token_resp}")
        ctx.token_id = token_resp["data"]
        ctx.token_key = self.client.get_token_key(ctx.token_id)
        return ctx

    def disable_channel(self, channel_id: int):
        self.client.set_channel_status(channel_id, 2)

    def enable_channel(self, channel_id: int):
        self.client.set_channel_status(channel_id, 1)

    def update_affinity_setting(self, setting: dict):
        self.client.update_option("channel_affinity_setting", json.dumps(setting))

    def clear_affinity_cache(self):
        self.client.clear_affinity_cache()

    def teardown(self, ctx: TestContext):
        """Clean up test channels and token."""
        for ch_id in ctx.channel_ids:
            try:
                self.client.delete_channel(ch_id)
            except Exception:
                pass
        if ctx.token_id:
            try:
                self.client.delete_token(ctx.token_id)
            except Exception:
                pass

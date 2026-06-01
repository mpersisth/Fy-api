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
    token_name: str = ""


class TestHarness:
    def __init__(self, client: FyApiClient, config: dict):
        self.client = client
        self.config = config
        self.model = config["test"]["model"]
        self.group = config["test"]["group"]
        self.upstream_key = config["env"]["upstream_api_key"]
        self.channel_type = config["channels"]["type"]
        self.tag_prefix = config["channels"]["tag_prefix"]
        self.channel_base_url = config["channels"].get("base_url", "")
        self._saved_affinity_rules: str | None = None

    def setup_channels(self, channels: list[ChannelConfig]) -> TestContext:
        """Create test channels and a token, return context."""
        ctx = TestContext(channels=channels)
        tag = f"{self.tag_prefix}-{int(time.time())}"
        name_prefix = f"dt-{int(time.time()) % 100000}"

        for ch in channels:
            ch_full_name = f"{name_prefix}-{ch.name}"
            data = {
                "name": ch_full_name,
                "type": self.channel_type,
                "key": self.upstream_key,
                "models": self.model,
                "groups": self.group,
                "priority": ch.priority,
                "weight": ch.weight,
                "status": 1,
                "tag": tag,
                "base_url": self.channel_base_url,
            }
            resp = self.client.create_channel(data)
            if not resp.get("success"):
                raise RuntimeError(f"Failed to create channel {ch_full_name}: {resp}")

        time.sleep(1)
        search_resp = self.client.search_channels(keyword=name_prefix)
        items = search_resp.get("data", {}).get("items", [])
        if len(items) < len(channels):
            raise RuntimeError(
                f"Expected {len(channels)} channels with prefix={name_prefix}, found {len(items)}"
            )

        for ch in channels:
            ch_full_name = f"{name_prefix}-{ch.name}"
            matched = [item for item in items if item["name"] == ch_full_name]
            if not matched:
                raise RuntimeError(f"Channel {ch_full_name} not found after creation")
            ch_id = matched[0]["id"]
            ch.channel_id = ch_id
            ctx.channel_ids.append(ch_id)
            ctx.channel_names[ch_id] = ch.name

        # Trigger channel cache refresh — AddChannel doesn't call InitChannelCache,
        # but UpdateChannel does. A no-op status update forces the refresh.
        self.client.set_channel_status(ctx.channel_ids[0], 1)
        time.sleep(1)

        token_name = f"dist-test-{int(time.time())}"
        token_resp = self.client.create_token(token_name)
        if not token_resp.get("success"):
            raise RuntimeError(f"Failed to create token: {token_resp}")
        time.sleep(0.5)
        search_resp = self.client.search_tokens("")
        items = search_resp.get("data", {}).get("items", [])
        matched = [t for t in items if t["name"] == token_name]
        if not matched:
            raise RuntimeError(f"Token {token_name} not found after creation")
        ctx.token_id = matched[0]["id"]
        ctx.token_name = token_name
        ctx.token_key = self.client.get_token_key(ctx.token_id)
        if not ctx.token_key:
            raise RuntimeError(f"Failed to retrieve token key for id={ctx.token_id}")
        return ctx

    def disable_channel(self, channel_id: int):
        self.client.set_channel_status(channel_id, 2)

    def enable_channel(self, channel_id: int):
        self.client.set_channel_status(channel_id, 1)

    def save_affinity_setting(self):
        """Save current affinity rules so they can be restored later."""
        resp = self.client.get_options()
        for opt in resp.get("data", []):
            if opt.get("key") == "channel_affinity_setting.rules":
                self._saved_affinity_rules = opt.get("value", "[]")
                return
        self._saved_affinity_rules = "[]"

    def restore_affinity_setting(self):
        """Restore affinity rules saved by save_affinity_setting."""
        if self._saved_affinity_rules is not None:
            self.client.update_option("channel_affinity_setting.rules", self._saved_affinity_rules)
            self._saved_affinity_rules = None

    def update_affinity_setting(self, setting: dict):
        if self._saved_affinity_rules is None:
            self.save_affinity_setting()
        prefix = "channel_affinity_setting"
        self.client.update_option(f"{prefix}.enabled", str(setting.get("enabled", True)).lower())
        self.client.update_option(f"{prefix}.switch_on_success", str(setting.get("switch_on_success", True)).lower())
        self.client.update_option(f"{prefix}.max_entries", str(setting.get("max_entries", 100000)))
        self.client.update_option(f"{prefix}.default_ttl_seconds", str(setting.get("default_ttl_seconds", 3600)))
        self.client.update_option(f"{prefix}.rules", json.dumps(setting.get("rules", [])))

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
        self.restore_affinity_setting()

"""Test YAML config parsing for cache affinity benchmark."""
import pytest
import tempfile
from pathlib import Path
from fy_cache_affinity.config import Config


MINIMAL_YAML = """\
base_url: "http://localhost:3000"
token: "sk-test"
model: "deepseek-chat"

conversation:
  seed_topic: "Go concurrency"
  max_turns: 10
  max_prompt_tokens: 30000
  temperature: 0.7
  max_tokens: 2048
  stream: true

repetitions: 3

groups:
  - name: "single_channel"
    pin_channel_id: 6
  - name: "affinity_header"
    headers:
      X-Session-Id: "auto"
  - name: "affinity_token"
  - name: "no_affinity"
"""


def test_load_minimal_config():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(MINIMAL_YAML)
        f.flush()
        cfg = Config.load(f.name)

    assert cfg.base_url == "http://localhost:3000"
    assert cfg.token == "sk-test"
    assert cfg.model == "deepseek-chat"
    assert cfg.conversation.max_turns == 10
    assert cfg.repetitions == 3
    assert len(cfg.groups) == 4
    assert cfg.groups[0].name == "single_channel"
    assert cfg.groups[0].pin_channel_id == 6
    assert cfg.groups[1].headers == {"X-Session-Id": "auto"}


def test_missing_base_url_raises():
    yaml_str = "token: sk-test\nmodel: m\ngroups:\n  - name: x\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_str)
        f.flush()
        with pytest.raises(ValueError, match="base_url"):
            Config.load(f.name)


def test_validate_max_turns_positive():
    yaml_str = "base_url: http://x\ntoken: t\nmodel: m\nconversation:\n  max_turns: 0\ngroups:\n  - name: x\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_str)
        f.flush()
        cfg = Config.load(f.name)
        with pytest.raises(ValueError, match="max_turns"):
            cfg.validate()

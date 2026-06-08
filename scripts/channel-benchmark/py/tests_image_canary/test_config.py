"""Config parsing tests for fy_image_canary."""

import os
import tempfile

import yaml


def test_config_minimal():
    from fy_image_canary.config import ImageCanaryConfig

    data = {
        "gateway": {
            "name": "test",
            "base_url": "http://localhost:3000",
            "user_token": "sk-test",
            "pin_channel_id": 1,
            "model": "dall-e-3",
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        f.flush()
        cfg = ImageCanaryConfig.load(f.name)

    assert cfg.gateway.name == "test"
    assert cfg.gateway.model == "dall-e-3"
    assert cfg.vendor is None
    assert len(cfg.test_prompts) == 8
    assert cfg.judge.repeat == 3


def test_config_with_vendor():
    from fy_image_canary.config import ImageCanaryConfig

    data = {
        "gateway": {
            "base_url": "http://localhost:3000",
            "user_token": "sk-test",
            "pin_channel_id": 1,
            "model": "dall-e-3",
        },
        "vendor": {
            "base_url": "https://api.openai.com",
            "api_key": "sk-openai-xxx",
            "model": "dall-e-3",
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        f.flush()
        cfg = ImageCanaryConfig.load(f.name)

    assert cfg.vendor is not None
    assert cfg.vendor.base_url == "https://api.openai.com"


def test_config_env_expansion(monkeypatch):
    from fy_image_canary.config import ImageCanaryConfig

    monkeypatch.setenv("TEST_URL", "http://myhost:3000")
    monkeypatch.setenv("TEST_TOKEN", "sk-env-token")

    raw = """
gateway:
  base_url: ${TEST_URL}
  user_token: ${TEST_TOKEN}
  pin_channel_id: 5
  model: dall-e-3
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(raw)
        f.flush()
        cfg = ImageCanaryConfig.load(f.name)

    assert cfg.gateway.base_url == "http://myhost:3000"
    assert cfg.gateway.user_token == "sk-env-token"


def test_config_missing_model_raises():
    from fy_image_canary.config import ImageCanaryConfig
    import pytest

    data = {
        "gateway": {
            "base_url": "http://localhost:3000",
            "user_token": "sk-test",
            "pin_channel_id": 1,
            "model": "",
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        f.flush()
        with pytest.raises(ValueError, match="model"):
            ImageCanaryConfig.load(f.name)

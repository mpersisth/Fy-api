"""Unified config for channel evaluation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(raw: str) -> str:
    missing: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        missing.append(name)
        return ""

    expanded = []
    for line in raw.splitlines():
        if line.lstrip().startswith("#"):
            expanded.append(line)
        else:
            expanded.append(_ENV_RE.sub(_sub, line))
    if missing:
        raise ValueError(f"undefined env vars: {sorted(set(missing))}")
    return "\n".join(expanded)


@dataclass
class Channel:
    name: str
    pin_channel_id: int
    base_url: str
    user_token: str


@dataclass
class TextTestConfig:
    smoke: bool = True
    load: dict = field(default_factory=lambda: {"concurrency": 5, "duration_sec": 60})
    quality: dict = field(default_factory=lambda: {"sample_count": 10})
    canary: bool = False


@dataclass
class ImageTestConfig:
    smoke: bool = True
    load: dict = field(default_factory=lambda: {"concurrency": 2, "duration_sec": 60})
    quality: dict = field(default_factory=lambda: {"enabled": False, "judge_model": "gpt-4o"})
    safety: bool = True


@dataclass
class VideoTestConfig:
    smoke: bool = True
    load: dict = field(default_factory=lambda: {"concurrency": 1, "max_requests": 3})


@dataclass
class TextModels:
    models: list[str] = field(default_factory=list)
    tests: TextTestConfig = field(default_factory=TextTestConfig)


@dataclass
class ImageModels:
    models: list[str] = field(default_factory=list)
    auto_probe: bool = False
    tests: ImageTestConfig = field(default_factory=ImageTestConfig)


@dataclass
class VideoModels:
    models: list[str] = field(default_factory=list)
    tests: VideoTestConfig = field(default_factory=VideoTestConfig)


@dataclass
class ReportConfig:
    output_dir: str = "eval-results"


@dataclass
class Config:
    channel: Channel
    text_models: TextModels | None = None
    image_models: ImageModels | None = None
    video_models: VideoModels | None = None
    report: ReportConfig = field(default_factory=ReportConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(_expand_env(text)) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> Config:
        ch = d.get("channel") or {}
        if not ch.get("name"):
            raise ValueError("channel.name is required")
        if not ch.get("base_url"):
            raise ValueError("channel.base_url is required")
        if not ch.get("user_token"):
            raise ValueError("channel.user_token is required")

        channel = Channel(
            name=str(ch["name"]),
            pin_channel_id=int(ch.get("pin_channel_id", 0)),
            base_url=str(ch["base_url"]),
            user_token=str(ch["user_token"]),
        )

        text = _parse_text_models(d.get("text_models"))
        image = _parse_image_models(d.get("image_models"))
        video = _parse_video_models(d.get("video_models"))
        rpt = d.get("report") or {}

        return cls(
            channel=channel,
            text_models=text,
            image_models=image,
            video_models=video,
            report=ReportConfig(output_dir=str(rpt.get("output_dir", "eval-results"))),
        )


def _parse_text_models(raw: dict | None) -> TextModels | None:
    if not raw:
        return None
    tests = raw.get("tests") or {}
    return TextModels(
        models=raw.get("models") or [],
        tests=TextTestConfig(
            smoke=bool(tests.get("smoke", True)),
            load=tests.get("load") or {"concurrency": 5, "duration_sec": 60},
            quality=tests.get("quality") or {"sample_count": 10},
            canary=bool(tests.get("canary", False)),
        ),
    )


def _parse_image_models(raw: dict | None) -> ImageModels | None:
    if not raw:
        return None
    tests = raw.get("tests") or {}
    models = raw.get("models") or []
    auto_probe = models == "auto" or (not models)
    if auto_probe:
        models = []
    return ImageModels(
        models=models if isinstance(models, list) else [],
        auto_probe=auto_probe,
        tests=ImageTestConfig(
            smoke=bool(tests.get("smoke", True)),
            load=tests.get("load") or {"concurrency": 2, "duration_sec": 60},
            quality=tests.get("quality") or {"enabled": False, "judge_model": "gpt-4o"},
            safety=bool(tests.get("safety", True)),
        ),
    )


def _parse_video_models(raw: dict | None) -> VideoModels | None:
    if not raw:
        return None
    tests = raw.get("tests") or {}
    return VideoModels(
        models=raw.get("models") or [],
        tests=VideoTestConfig(
            smoke=bool(tests.get("smoke", True)),
            load=tests.get("load") or {"concurrency": 1, "max_requests": 3},
        ),
    )

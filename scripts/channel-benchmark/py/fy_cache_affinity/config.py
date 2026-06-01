"""YAML config parsing for cache affinity benchmark."""

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

    expanded = _ENV_RE.sub(_sub, raw)
    if missing:
        raise ValueError(f"undefined env vars: {sorted(set(missing))}")
    return expanded


@dataclass
class ConversationConfig:
    seed_topic: str = "Go 并发模型的演进"
    max_turns: int = 30
    max_prompt_tokens: int = 60000
    temperature: float = 0.7
    max_tokens: int = 2048
    stream: bool = True


@dataclass
class GroupConfig:
    name: str
    pin_channel_id: int | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ExportConfig:
    formats: list[str] = field(default_factory=lambda: ["json", "markdown", "png"])
    output_dir: str = "results/cache-affinity"


@dataclass
class Config:
    base_url: str
    token: str
    model: str
    conversation: ConversationConfig
    repetitions: int
    groups: list[GroupConfig]
    export: ExportConfig = field(default_factory=ExportConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(_expand_env(text)) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> Config:
        if not d.get("base_url"):
            raise ValueError("base_url is required")
        if not d.get("token"):
            raise ValueError("token is required")
        if not d.get("model"):
            raise ValueError("model is required")

        conv_raw = d.get("conversation") or {}
        conv = ConversationConfig(
            seed_topic=conv_raw.get("seed_topic", ConversationConfig.seed_topic),
            max_turns=int(conv_raw.get("max_turns", 30)),
            max_prompt_tokens=int(conv_raw.get("max_prompt_tokens", 60000)),
            temperature=float(conv_raw.get("temperature", 0.7)),
            max_tokens=int(conv_raw.get("max_tokens", 2048)),
            stream=bool(conv_raw.get("stream", True)),
        )

        groups: list[GroupConfig] = []
        for g in d.get("groups") or []:
            groups.append(GroupConfig(
                name=g["name"],
                pin_channel_id=g.get("pin_channel_id"),
                headers=g.get("headers") or {},
            ))
        if not groups:
            raise ValueError("at least one group is required")

        exp_raw = d.get("export") or {}
        export = ExportConfig(
            formats=exp_raw.get("formats", ["json", "markdown", "png"]),
            output_dir=exp_raw.get("output_dir", "results/cache-affinity"),
        )

        return cls(
            base_url=d["base_url"],
            token=d["token"],
            model=d["model"],
            conversation=conv,
            repetitions=int(d.get("repetitions", 3)),
            groups=groups,
            export=export,
        )

    def validate(self) -> None:
        if self.conversation.max_turns <= 0:
            raise ValueError("conversation.max_turns must be > 0")
        if self.conversation.max_prompt_tokens <= 0:
            raise ValueError("conversation.max_prompt_tokens must be > 0")
        if self.repetitions <= 0:
            raise ValueError("repetitions must be > 0")

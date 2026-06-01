"""Orchestrates cache affinity benchmark: groups x repetitions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from rich.console import Console

from fy_loadtest.client import ChatClient

from .config import Config, GroupConfig
from .conversation import ConversationResult, run_conversation
from .metrics import TurnAggregate, aggregate_runs


@dataclass
class GroupResult:
    name: str
    runs: list[ConversationResult] = field(default_factory=list)
    aggregates: list[TurnAggregate] = field(default_factory=list)


@dataclass
class BenchmarkResult:
    model: str
    base_url: str
    groups: list[GroupResult] = field(default_factory=list)


async def run_benchmark(cfg: Config, *, group_filter: str | None = None, console: Console | None = None) -> BenchmarkResult:
    console = console or Console()
    result = BenchmarkResult(model=cfg.model, base_url=cfg.base_url)

    groups = cfg.groups
    if group_filter:
        groups = [g for g in groups if g.name == group_filter]
        if not groups:
            raise ValueError(f"no group named '{group_filter}', available: {[g.name for g in cfg.groups]}")

    for gi, group in enumerate(groups):
        console.rule(f"[bold blue]组 {gi+1}/{len(groups)}: {group.name}")
        group_result = GroupResult(name=group.name)

        for rep in range(cfg.repetitions):
            console.print(f"  [dim]重复 {rep+1}/{cfg.repetitions}[/dim]")

            headers = _resolve_headers(group)
            async with ChatClient(
                base_url=cfg.base_url,
                token=cfg.token,
                pin_channel_id=group.pin_channel_id,
                extra_headers=headers if headers else None,
            ) as client:
                conv = await run_conversation(
                    client,
                    model=cfg.model,
                    seed_topic=cfg.conversation.seed_topic,
                    max_turns=cfg.conversation.max_turns,
                    max_prompt_tokens=cfg.conversation.max_prompt_tokens,
                    temperature=cfg.conversation.temperature,
                    max_tokens=cfg.conversation.max_tokens,
                    stream=cfg.conversation.stream,
                )
                group_result.runs.append(conv)
                if conv.turns:
                    console.print(f"    完成 {len(conv.turns)} 轮, 最终 cache ratio: {conv.turns[-1].cache_ratio:.1%}")
                else:
                    console.print("    无数据")

        group_result.aggregates = aggregate_runs(group_result.runs)
        result.groups.append(group_result)

    return result


def _resolve_headers(group: GroupConfig) -> dict[str, str]:
    headers: dict[str, str] = {}
    for k, v in group.headers.items():
        if v == "auto":
            headers[k] = uuid.uuid4().hex
        else:
            headers[k] = v
    return headers

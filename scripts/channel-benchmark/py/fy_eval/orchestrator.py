"""Orchestrator — runs test suites per model type and collects results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from rich.console import Console

from .config import Config


class Verdict(str, Enum):
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"


@dataclass
class TestResult:
    test_name: str
    passed: bool
    detail: str = ""
    metrics: dict = field(default_factory=dict)


@dataclass
class ModelResult:
    model_type: str  # text / image / video
    model_name: str
    results: list[TestResult] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        if not self.results:
            return Verdict.PASS
        failed = [r for r in self.results if not r.passed]
        critical = [r for r in failed if r.test_name == "smoke"]
        if critical:
            return Verdict.FAIL
        if failed:
            return Verdict.CONDITIONAL
        return Verdict.PASS

    @property
    def risks(self) -> list[str]:
        return [f"[{r.test_name}] {r.detail}" for r in self.results if not r.passed]


@dataclass
class EvalResult:
    channel_name: str
    model_results: list[ModelResult] = field(default_factory=list)

    @property
    def overall_verdict(self) -> Verdict:
        verdicts = [mr.verdict for mr in self.model_results]
        if Verdict.FAIL in verdicts:
            return Verdict.FAIL
        if Verdict.CONDITIONAL in verdicts:
            return Verdict.CONDITIONAL
        return Verdict.PASS


async def run_eval(cfg: Config, console: Console) -> EvalResult:
    from .runners.text import smoke as text_smoke, load as text_load
    from .runners.image import smoke as img_smoke, load as img_load, safety as img_safety
    from .runners.video import smoke as vid_smoke, load as vid_load

    result = EvalResult(channel_name=cfg.channel.name)

    # Text models
    if cfg.text_models and cfg.text_models.models:
        console.print(f"\n[bold]== 文本模型测试 ({len(cfg.text_models.models)} models) ==[/bold]")
        for model in cfg.text_models.models:
            mr = ModelResult(model_type="text", model_name=model)
            console.print(f"\n  [cyan]{model}[/cyan]")

            if cfg.text_models.tests.smoke:
                console.print("    smoke...", end=" ")
                r = await text_smoke.run(cfg, model)
                mr.results.append(r)
                console.print("[green]PASS[/green]" if r.passed else "[red]FAIL[/red]")

            if cfg.text_models.tests.load and mr.verdict != Verdict.FAIL:
                console.print("    load...", end=" ")
                r = await text_load.run(cfg, model)
                mr.results.append(r)
                _print_result(console, r)

            result.model_results.append(mr)

    # Image models
    if cfg.image_models:
        models = cfg.image_models.models
        if cfg.image_models.auto_probe and not models:
            console.print("\n[bold]== 图片模型探测 ==[/bold]")
            from .runners.image import probe as img_probe
            models = await img_probe.detect(cfg, console)

        if models:
            console.print(f"\n[bold]== 图片模型测试 ({len(models)} models) ==[/bold]")
            for model in models:
                mr = ModelResult(model_type="image", model_name=model)
                console.print(f"\n  [cyan]{model}[/cyan]")

                if cfg.image_models.tests.smoke:
                    console.print("    smoke...", end=" ")
                    r = await img_smoke.run(cfg, model)
                    mr.results.append(r)
                    console.print("[green]PASS[/green]" if r.passed else "[red]FAIL[/red]")

                if cfg.image_models.tests.load and mr.verdict != Verdict.FAIL:
                    console.print("    load...", end=" ")
                    r = await img_load.run(cfg, model)
                    mr.results.append(r)
                    _print_result(console, r)

                if cfg.image_models.tests.safety and mr.verdict != Verdict.FAIL:
                    console.print("    safety...", end=" ")
                    r = await img_safety.run(cfg, model)
                    mr.results.append(r)
                    _print_result(console, r)

                result.model_results.append(mr)

    # Video models
    if cfg.video_models and cfg.video_models.models:
        console.print(f"\n[bold]== 视频模型测试 ({len(cfg.video_models.models)} models) ==[/bold]")
        for model in cfg.video_models.models:
            mr = ModelResult(model_type="video", model_name=model)
            console.print(f"\n  [cyan]{model}[/cyan]")

            if cfg.video_models.tests.smoke:
                console.print("    smoke...", end=" ")
                r = await vid_smoke.run(cfg, model)
                mr.results.append(r)
                console.print("[green]PASS[/green]" if r.passed else "[red]FAIL[/red]")

            if cfg.video_models.tests.load and mr.verdict != Verdict.FAIL:
                console.print("    load...", end=" ")
                r = await vid_load.run(cfg, model)
                mr.results.append(r)
                _print_result(console, r)

            result.model_results.append(mr)

    return result


def _print_result(console: Console, r: TestResult) -> None:
    if r.passed:
        console.print("[green]PASS[/green]")
    else:
        console.print(f"[red]FAIL[/red] {r.detail[:60]}")

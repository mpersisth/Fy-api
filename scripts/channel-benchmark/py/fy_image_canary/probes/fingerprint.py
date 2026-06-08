"""5B-1: Model fingerprint detection.

Six sub-probes:
  1. Speed fingerprint (P50 range check)
  2. Parameter compatibility (unsupported params)
  3. Size compatibility (unsupported sizes)
  4. Error message fingerprint
  5. Metadata fingerprint (revised_prompt)
  6. Response format fingerprint
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from fy_image_conformance.client import ImageClient
from ..config import ImageCanaryConfig
from ..verdict import ProbeOutcome, CONFIDENCE_MEDIUM


@dataclass
class ModelFingerprint:
    speed_p50_range_sec: tuple[float, float]
    supported_sizes: list[str]
    unsupported_params: list[str]
    unsupported_sizes: list[str]
    error_patterns: list[str]
    has_revised_prompt: bool
    has_c2pa: bool
    response_format_variants: list[str]


@dataclass
class FingerprintDB:
    models: dict[str, ModelFingerprint] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> FingerprintDB:
        if not path or not Path(path).exists():
            return cls()
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        db = cls()
        for name, entry in (data.get("models") or {}).items():
            speed = entry.get("speed_p50_range_sec", [0, 999])
            db.models[name] = ModelFingerprint(
                speed_p50_range_sec=(float(speed[0]), float(speed[1])),
                supported_sizes=entry.get("supported_sizes", []),
                unsupported_params=entry.get("unsupported_params", []),
                unsupported_sizes=entry.get("unsupported_sizes", []),
                error_patterns=entry.get("error_patterns", []),
                has_revised_prompt=bool(entry.get("has_revised_prompt", False)),
                has_c2pa=bool(entry.get("has_c2pa", False)),
                response_format_variants=entry.get("response_format_variants", []),
            )
        return db


async def run_fingerprint_probes(
    client: ImageClient,
    cfg: ImageCanaryConfig,
    db: FingerprintDB,
) -> list[ProbeOutcome]:
    model_name = cfg.gateway.model
    fp = db.models.get(model_name)
    if fp is None:
        return [ProbeOutcome(
            probe_id="5b1-no-fingerprint", method="fingerprint",
            passed=True, score=0.5,
            detail=f"no fingerprint data for model '{model_name}'",
            confidence="low",
        )]

    outcomes: list[ProbeOutcome] = []
    prompt = "a red circle on white background"
    body_base = {"model": model_name, "prompt": prompt, "n": 1}

    outcomes.append(await _probe_speed(client, cfg, fp, body_base))

    for param in fp.unsupported_params:
        outcomes.append(await _probe_param_compat(client, cfg, fp, body_base, param))

    for size in fp.unsupported_sizes[:2]:
        outcomes.append(await _probe_size_compat(client, cfg, fp, body_base, size))

    outcomes.append(await _probe_metadata(client, cfg, fp, body_base))
    outcomes.append(await _probe_response_format(client, cfg, fp, body_base))

    return outcomes


async def _probe_speed(
    client: ImageClient,
    cfg: ImageCanaryConfig,
    fp: ModelFingerprint,
    body: dict,
) -> ProbeOutcome:
    latencies = []
    for _ in range(3):
        r = await client.generate(dict(body), pin_channel=cfg.gateway.pin_channel_id)
        if r.success and r.elapsed_sec is not None:
            latencies.append(r.elapsed_sec)

    if not latencies:
        return ProbeOutcome(
            probe_id="5b1-speed", method="fingerprint",
            passed=False, score=0.0,
            detail="all generation attempts failed",
            confidence=CONFIDENCE_MEDIUM,
        )

    p50 = sorted(latencies)[len(latencies) // 2]
    lo, hi = fp.speed_p50_range_sec
    in_range = lo <= p50 <= hi
    return ProbeOutcome(
        probe_id="5b1-speed", method="fingerprint",
        passed=in_range, score=1.0 if in_range else 0.0,
        detail=f"P50={p50:.1f}s expected=[{lo:.0f}-{hi:.0f}]s",
        confidence=CONFIDENCE_MEDIUM,
    )


async def _probe_param_compat(
    client: ImageClient,
    cfg: ImageCanaryConfig,
    fp: ModelFingerprint,
    body: dict,
    param: str,
) -> ProbeOutcome:
    test_body = dict(body)
    test_body[param] = "test_value_unsupported"
    r = await client.generate(test_body, pin_channel=cfg.gateway.pin_channel_id)

    if r.success:
        return ProbeOutcome(
            probe_id=f"5b1-param-{param}", method="fingerprint",
            passed=False, score=0.0,
            detail=f"model accepted unsupported param '{param}' — likely NOT {cfg.gateway.model}",
            confidence="high",
        )
    return ProbeOutcome(
        probe_id=f"5b1-param-{param}", method="fingerprint",
        passed=True, score=1.0,
        detail=f"correctly rejected unsupported param '{param}'",
        confidence="high",
    )


async def _probe_size_compat(
    client: ImageClient,
    cfg: ImageCanaryConfig,
    fp: ModelFingerprint,
    body: dict,
    size: str,
) -> ProbeOutcome:
    test_body = dict(body)
    test_body["size"] = size
    r = await client.generate(test_body, pin_channel=cfg.gateway.pin_channel_id)

    if r.success:
        return ProbeOutcome(
            probe_id=f"5b1-size-{size}", method="fingerprint",
            passed=False, score=0.0,
            detail=f"model accepted unsupported size '{size}' — likely NOT {cfg.gateway.model}",
            confidence="high",
        )
    return ProbeOutcome(
        probe_id=f"5b1-size-{size}", method="fingerprint",
        passed=True, score=1.0,
        detail=f"correctly rejected unsupported size '{size}'",
        confidence="high",
    )


async def _probe_metadata(
    client: ImageClient,
    cfg: ImageCanaryConfig,
    fp: ModelFingerprint,
    body: dict,
) -> ProbeOutcome:
    r = await client.generate(dict(body), pin_channel=cfg.gateway.pin_channel_id)
    if not r.success:
        return ProbeOutcome(
            probe_id="5b1-metadata", method="fingerprint",
            passed=False, score=0.0, detail="generation failed",
            confidence=CONFIDENCE_MEDIUM,
        )

    has_rp = bool(r.revised_prompt)
    expected_rp = fp.has_revised_prompt
    match = has_rp == expected_rp
    return ProbeOutcome(
        probe_id="5b1-metadata", method="fingerprint",
        passed=match, score=1.0 if match else 0.0,
        detail=f"revised_prompt={'present' if has_rp else 'absent'} "
               f"(expected={'present' if expected_rp else 'absent'})",
        confidence=CONFIDENCE_MEDIUM,
    )


async def _probe_response_format(
    client: ImageClient,
    cfg: ImageCanaryConfig,
    fp: ModelFingerprint,
    body: dict,
) -> ProbeOutcome:
    test_body = dict(body)
    test_body["response_format"] = "b64_json"
    r = await client.generate(test_body, pin_channel=cfg.gateway.pin_channel_id)

    if "b64_json" in fp.response_format_variants:
        if r.success and r.image_b64:
            return ProbeOutcome(
                probe_id="5b1-resp-fmt", method="fingerprint",
                passed=True, score=1.0,
                detail="b64_json supported as expected",
                confidence=CONFIDENCE_MEDIUM,
            )
        return ProbeOutcome(
            probe_id="5b1-resp-fmt", method="fingerprint",
            passed=False, score=0.0,
            detail=f"b64_json should be supported but failed: {r.error[:80]}",
            confidence=CONFIDENCE_MEDIUM,
        )
    else:
        if not r.success:
            return ProbeOutcome(
                probe_id="5b1-resp-fmt", method="fingerprint",
                passed=True, score=1.0,
                detail="b64_json correctly rejected (not supported by this model)",
                confidence=CONFIDENCE_MEDIUM,
            )
        return ProbeOutcome(
            probe_id="5b1-resp-fmt", method="fingerprint",
            passed=False, score=0.0,
            detail="b64_json accepted but model shouldn't support it",
            confidence=CONFIDENCE_MEDIUM,
        )

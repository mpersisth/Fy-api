"""Main runner orchestrating 5A and 5B image canary probes."""

from __future__ import annotations

from fy_image_conformance.client import ImageClient

from .config import ImageCanaryConfig
from .verdict import (
    CanaryReport, ProbeOutcome,
    VERDICT_PASS, VERDICT_MISMATCH, VERDICT_INCONCLUSIVE,
    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW,
)
from .runner_5a import run_5a
from .probes.fingerprint import FingerprintDB, run_fingerprint_probes
from .probes.cross_channel import run_cross_channel
from .probes.capability import run_capability_probes


class ImageCanaryRunner:
    def __init__(self, cfg: ImageCanaryConfig):
        self.cfg = cfg
        fp_path = cfg.fingerprint.db_path
        self.fingerprint_db = FingerprintDB.load(fp_path) if fp_path else FingerprintDB()

    async def run_full(self) -> CanaryReport:
        report = CanaryReport(
            mode="full",
            channel_name=self.cfg.gateway.name,
            model=self.cfg.gateway.model,
        )

        async with ImageClient(
            self.cfg.gateway.base_url,
            self.cfg.gateway.user_token,
            timeout=self.cfg.request_timeout_sec,
        ) as gateway_client:

            # 5A: Vendor comparison (optional)
            if self.cfg.vendor:
                async with ImageClient(
                    self.cfg.vendor.base_url,
                    self.cfg.vendor.api_key,
                    timeout=self.cfg.request_timeout_sec,
                ) as vendor_client:
                    results_5a, outcomes_5a = await run_5a(
                        self.cfg, gateway_client, vendor_client)
                    report.outcomes.extend(outcomes_5a)

            # 5B-1: Fingerprint probes
            fp_outcomes = await run_fingerprint_probes(
                gateway_client, self.cfg, self.fingerprint_db)
            report.outcomes.extend(fp_outcomes)

            # 5B-2: Cross-channel comparison
            if self.cfg.additional_channels:
                clients: dict[str, ImageClient] = {
                    self.cfg.gateway.name: gateway_client}
                extra_clients: list[ImageClient] = []
                try:
                    for ac in self.cfg.additional_channels:
                        c = ImageClient(
                            ac.base_url, ac.user_token,
                            timeout=self.cfg.request_timeout_sec)
                        await c.__aenter__()
                        extra_clients.append(c)
                        clients[ac.name] = c

                    all_channels = [self.cfg.gateway] + self.cfg.additional_channels
                    _, xc_outcomes = await run_cross_channel(
                        clients, all_channels, self.cfg.gateway.model,
                        self.cfg.test_prompts[:4],
                        clip_threshold=self.cfg.thresholds.clip_cosine_min,
                    )
                    report.outcomes.extend(xc_outcomes)
                finally:
                    for c in extra_clients:
                        await c.__aexit__(None, None, None)

            # 5B-3: Capability boundary probes
            _, cap_outcomes = await run_capability_probes(gateway_client, self.cfg)
            report.outcomes.extend(cap_outcomes)

        report.combined_verdict, report.combined_confidence = \
            self._compute_combined_verdict(report.outcomes)
        return report

    async def run_vendor_only(self) -> CanaryReport:
        report = CanaryReport(
            mode="vendor_compare",
            channel_name=self.cfg.gateway.name,
            model=self.cfg.gateway.model,
        )
        if not self.cfg.vendor:
            report.combined_verdict = VERDICT_INCONCLUSIVE
            report.combined_confidence = CONFIDENCE_LOW
            return report

        async with ImageClient(
            self.cfg.gateway.base_url,
            self.cfg.gateway.user_token,
            timeout=self.cfg.request_timeout_sec,
        ) as gw_client, ImageClient(
            self.cfg.vendor.base_url,
            self.cfg.vendor.api_key,
            timeout=self.cfg.request_timeout_sec,
        ) as vendor_client:
            _, outcomes = await run_5a(self.cfg, gw_client, vendor_client)
            report.outcomes.extend(outcomes)

        report.combined_verdict, report.combined_confidence = \
            self._compute_combined_verdict(report.outcomes)
        return report

    async def run_fingerprint_only(self) -> CanaryReport:
        report = CanaryReport(
            mode="fingerprint",
            channel_name=self.cfg.gateway.name,
            model=self.cfg.gateway.model,
        )
        async with ImageClient(
            self.cfg.gateway.base_url,
            self.cfg.gateway.user_token,
            timeout=self.cfg.request_timeout_sec,
        ) as client:
            outcomes = await run_fingerprint_probes(
                client, self.cfg, self.fingerprint_db)
            report.outcomes.extend(outcomes)

        report.combined_verdict, report.combined_confidence = \
            self._compute_combined_verdict(report.outcomes)
        return report

    def _compute_combined_verdict(
        self, outcomes: list[ProbeOutcome],
    ) -> tuple[str, str]:
        if not outcomes:
            return VERDICT_INCONCLUSIVE, CONFIDENCE_LOW

        has_hard_fail = False
        has_5a = False
        has_cross_channel = False

        for o in outcomes:
            if o.method in ("clip", "color_histogram", "vlm_comparison"):
                has_5a = True
            if o.method == "cross_channel":
                has_cross_channel = True
            if not o.passed and o.confidence == "high":
                has_hard_fail = True
            # Fingerprint probes for unsupported params/sizes are iron-clad evidence
            if not o.passed and o.method == "fingerprint" and \
                    (o.probe_id.startswith("5b1-param-") or o.probe_id.startswith("5b1-size-")):
                has_hard_fail = True

        if has_hard_fail:
            return VERDICT_MISMATCH, CONFIDENCE_HIGH

        # Count valid probes: exclude capability probes where generation
        # failed or the judge returned an error (score == 0.0 with failure).
        # For capability method, score==0.0 is always an error state — real
        # judge evaluations return >= 0.5 (unparseable fallback) or a true
        # score.  If no valid probes remain, the verdict is inconclusive
        # rather than a false-positive MISMATCH.
        valid_outcomes = [
            o for o in outcomes
            if not (o.method == "capability" and not o.passed and o.score == 0.0)
        ]
        if not valid_outcomes:
            return VERDICT_INCONCLUSIVE, CONFIDENCE_LOW

        total = len(valid_outcomes)
        passed = sum(1 for o in valid_outcomes if o.passed)
        pass_rate = passed / total if total > 0 else 0.0

        if pass_rate >= 0.8:
            if has_5a:
                return VERDICT_PASS, CONFIDENCE_HIGH
            if has_cross_channel:
                return VERDICT_PASS, CONFIDENCE_HIGH
            return VERDICT_PASS, CONFIDENCE_MEDIUM
        elif pass_rate >= 0.5:
            return VERDICT_INCONCLUSIVE, CONFIDENCE_LOW
        else:
            return VERDICT_MISMATCH, CONFIDENCE_MEDIUM

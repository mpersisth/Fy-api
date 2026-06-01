#!/usr/bin/env python3
"""Multi-channel distribution test suite — main entry point.

Usage:
    python -m fy_distribution.run_tests --config config.yaml [--suite SUITE]

Suites:
    all       — run all tests (default)
    weight    — weight distribution only
    priority  — priority fallback only
    affinity  — all affinity tests
    affinity-sticky   — sticky routing only
    affinity-ttl      — TTL behavior only
    affinity-failure  — failure handling only
    affinity-hitrate  — hit rate analysis only
"""

import argparse
import sys
import time

import yaml

from .lib.client import FyApiClient
from .lib.harness import TestHarness
from .recommendations import format_recommendations, generate_recommendations
from .test_affinity import (
    run_affinity_failure_test,
    run_affinity_hit_rate_test,
    run_affinity_sticky_test,
    run_affinity_ttl_test,
)
from .test_priority import run_priority_fallback_test
from .test_weight import run_weight_test


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="fy-api channel distribution tests")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument(
        "--suite",
        default="all",
        choices=[
            "all", "weight", "priority", "affinity",
            "affinity-sticky", "affinity-ttl", "affinity-failure", "affinity-hitrate",
        ],
    )
    args = parser.parse_args()

    config = load_config(args.config)
    client = FyApiClient(
        base_url=config["env"]["base_url"],
        root_token=config["env"]["root_token"],
    )
    harness = TestHarness(client, config)
    test_cfg = config["test"]
    interval = test_cfg.get("request_interval", 0.1)
    log_wait = test_cfg.get("log_wait_seconds", 3)

    print(f"\n{'#'*60}")
    print(f"  fy-distribution test suite")
    print(f"  Target: {config['env']['base_url']}")
    print(f"  Model: {test_cfg['model']} | Group: {test_cfg['group']}")
    print(f"  Suite: {args.suite}")
    print(f"{'#'*60}\n")

    weight_result = None
    priority_result = None
    affinity_sticky = None
    affinity_ttl = None
    affinity_failure = None
    affinity_hitrate = None

    suites = {args.suite} if args.suite != "all" else {
        "weight", "priority", "affinity-sticky",
        "affinity-ttl", "affinity-failure", "affinity-hitrate",
    }
    if "affinity" in suites:
        suites.discard("affinity")
        suites.update({"affinity-sticky", "affinity-ttl", "affinity-failure", "affinity-hitrate"})

    start = time.time()

    if "weight" in suites:
        print("\n[1/6] Running weight distribution test...")
        weight_result = run_weight_test(
            client, harness, num_requests=test_cfg.get("weight_requests", 300),
            interval=interval, log_wait=log_wait,
        )
        print(weight_result.report())

    if "priority" in suites:
        print("\n[2/6] Running priority fallback test...")
        priority_result = run_priority_fallback_test(
            client, harness, num_requests=test_cfg.get("priority_requests", 30),
            interval=interval, log_wait=log_wait,
        )
        print(priority_result.report())

    if "affinity-sticky" in suites:
        print("\n[3/6] Running affinity sticky routing test...")
        affinity_sticky = run_affinity_sticky_test(client, harness, log_wait=log_wait)
        print(affinity_sticky.report())

    if "affinity-ttl" in suites:
        print("\n[4/6] Running affinity TTL test...")
        affinity_ttl = run_affinity_ttl_test(client, harness, short_ttl=10, log_wait=log_wait)
        print(affinity_ttl.report())

    if "affinity-failure" in suites:
        print("\n[5/6] Running affinity failure handling test...")
        affinity_failure = run_affinity_failure_test(client, harness, log_wait=log_wait)
        print(affinity_failure.report())

    if "affinity-hitrate" in suites:
        print("\n[6/6] Running affinity hit rate analysis...")
        affinity_hitrate = run_affinity_hit_rate_test(client, harness, log_wait=log_wait)
        print(affinity_hitrate.report())

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"  Total time: {elapsed:.1f}s")
    print(f"{'='*60}")

    recs = generate_recommendations(
        weight_result=weight_result,
        priority_result=priority_result,
        affinity_sticky_result=affinity_sticky,
        affinity_ttl_result=affinity_ttl,
        affinity_failure_result=affinity_failure,
        affinity_hitrate_result=affinity_hitrate,
    )
    print(format_recommendations(recs))


if __name__ == "__main__":
    main()

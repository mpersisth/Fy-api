#!/usr/bin/env python3
"""
Media billing e2e checks for cn-test/staging environments.

The script uses only public gateway APIs:
  - POST /v1/images/generations
  - POST /v1/video/generations
  - GET  /v1/video/generations/{task_id}
  - GET  /api/log/token

Required env:
  FYAPI_E2E_BASE_URL
  FYAPI_E2E_TOKEN

Optional env:
  FYAPI_E2E_IMAGE_MODEL      default: wan2.6-t2i
  FYAPI_E2E_VIDEO_MODEL      default: wan2.6-i2v
  FYAPI_E2E_R2V_MODEL        default: wan2.6-r2v
  FYAPI_E2E_IMAGE_URL        required for i2v/r2v cases unless --skip-video
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def post_json(client: httpx.Client, path: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post(path, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"{path} HTTP {resp.status_code}: {resp.text[:500]}")
    payload = resp.json()
    if payload.get("error"):
        raise RuntimeError(f"{path} error: {payload['error']}")
    return payload


def fetch_logs(client: httpx.Client) -> list[dict[str, Any]]:
    resp = client.get("/api/log/token")
    if resp.status_code >= 400:
        raise RuntimeError(f"/api/log/token HTTP {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(f"/api/log/token failed: {payload.get('message')}")
    data = payload.get("data") or []
    return [item for item in data if isinstance(item, dict)]


def recent_log(logs: list[dict[str, Any]], model: str, since: int) -> dict[str, Any] | None:
    candidates = [
        item for item in logs
        if item.get("model_name") == model and int(item.get("created_at") or 0) >= since
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.get("created_at") or 0))


def log_other(log: dict[str, Any]) -> dict[str, Any]:
    other = log.get("other")
    if isinstance(other, dict):
        return other
    if isinstance(other, str) and other.strip():
        import json
        try:
            parsed = json.loads(other)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def check_image(client: httpx.Client, model: str) -> CaseResult:
    started = int(time.time()) - 2
    payload = post_json(client, "/v1/images/generations", {
        "model": model,
        "prompt": "simple red square on white background",
        "size": "1024x1024",
        "n": 1,
        "response_format": "url",
    })
    data = payload.get("data") or []
    if not data:
        return CaseResult("image-fixed-price", False, "image response has no data")

    logs = fetch_logs(client)
    log = recent_log(logs, model, started)
    if not log:
        return CaseResult("image-fixed-price", False, f"no recent log for {model}")
    quota = int(log.get("quota") or 0)
    other = log_other(log)
    if quota <= 0:
        return CaseResult("image-fixed-price", False, f"quota={quota}")
    if other.get("is_task") is True:
        return CaseResult("image-fixed-price", False, "image log unexpectedly marked as task")
    return CaseResult("image-fixed-price", True, f"quota={quota}, model_price={other.get('model_price')}")


def submit_video(client: httpx.Client, body: dict[str, Any]) -> str:
    payload = post_json(client, "/v1/video/generations", body)
    task_id = payload.get("id") or payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError(f"video submit response missing task id: {payload}")
    return task_id


def poll_video(client: httpx.Client, task_id: str, timeout_s: int) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        resp = client.get(f"/v1/video/generations/{task_id}")
        if resp.status_code >= 400:
            raise RuntimeError(f"fetch {task_id} HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        if isinstance(payload.get("data"), dict):
            payload = payload["data"]
        last = payload if isinstance(payload, dict) else {}
        status = str(last.get("status") or "").lower()
        if status in {"completed", "succeeded", "success", "failed", "failure"}:
            return last
        time.sleep(10)
    raise TimeoutError(f"task {task_id} not terminal before timeout; last={last}")


def check_video(client: httpx.Client, model: str, image_url: str, *, size: str, seconds: int, timeout_s: int) -> CaseResult:
    started = int(time.time()) - 2
    task_id = submit_video(client, {
        "model": model,
        "prompt": "slow camera push in",
        "input_reference": image_url,
        "size": size,
        "duration": seconds,
    })
    final = poll_video(client, task_id, timeout_s)
    if str(final.get("status") or "").lower() in {"failed", "failure"}:
        return CaseResult(f"video-{model}", False, f"task failed: {final}")

    logs = fetch_logs(client)
    log = recent_log(logs, model, started)
    if not log:
        return CaseResult(f"video-{model}", False, f"no recent log for {model}")
    other = log_other(log)
    quota = int(log.get("quota") or 0)
    if other.get("is_task") is not True:
        return CaseResult(f"video-{model}", False, f"log is not task-shaped: quota={quota}, other={other}")
    if float(other.get("seconds") or 0) <= 0:
        return CaseResult(f"video-{model}", False, f"missing structured seconds: other={other}")
    if quota <= 0:
        return CaseResult(f"video-{model}", False, f"quota={quota}")
    return CaseResult(f"video-{model}", True, f"task_id={task_id}, quota={quota}, other={other}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=env("FYAPI_E2E_BASE_URL"))
    parser.add_argument("--token", default=env("FYAPI_E2E_TOKEN"))
    parser.add_argument("--image-model", default=env("FYAPI_E2E_IMAGE_MODEL", "wan2.6-t2i"))
    parser.add_argument("--video-model", default=env("FYAPI_E2E_VIDEO_MODEL", "wan2.6-i2v"))
    parser.add_argument("--r2v-model", default=env("FYAPI_E2E_R2V_MODEL", "wan2.6-r2v"))
    parser.add_argument("--image-url", default=env("FYAPI_E2E_IMAGE_URL"))
    parser.add_argument("--timeout", type=int, default=int(env("FYAPI_E2E_TIMEOUT", "900")))
    parser.add_argument("--skip-video", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print("cases: image fixed price, i2v task billing, r2v task billing")
        print(f"base_url={args.base_url or '<required>'}")
        print(f"models={args.image_model},{args.video_model},{args.r2v_model}")
        return 0

    if not args.base_url or not args.token:
        print("FYAPI_E2E_BASE_URL and FYAPI_E2E_TOKEN are required", file=sys.stderr)
        return 2

    results: list[CaseResult] = []
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=auth_headers(args.token), timeout=60) as client:
        results.append(check_image(client, args.image_model))
        if not args.skip_video:
            if not args.image_url:
                print("FYAPI_E2E_IMAGE_URL is required unless --skip-video", file=sys.stderr)
                return 2
            results.append(check_video(client, args.video_model, args.image_url, size="720P", seconds=3, timeout_s=args.timeout))
            results.append(check_video(client, args.r2v_model, args.image_url, size="720P", seconds=3, timeout_s=args.timeout))

    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status} {result.name}: {result.detail}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

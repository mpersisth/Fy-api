#!/usr/bin/env python3
# scripts/ops/sync_config.py
#
# 单向同步特定表:国内 Fy-api RDS → 新加坡 Fy-api (通过 admin API)
#
# 同步的表(白名单):
#   - channels     (去掉 key / openai_organization 等凭据字段)
#   - abilities
#   - options      (仅白名单 key,如 ModelRatio 等)
#
# 不同步:users / tokens / logs / quota_records / topups / redemptions / option-secrets
#
# 用法:
#   环境变量:
#     CN_DB_DSN          — 国内 RDS 连接 mysql://user:pass@host:3306/db
#     SG_API_BASE        — https://api.tracenex.sg
#     SG_INTERNAL_TOKEN  — 预共享密钥(见 Fy-api 的 /api/internal/sync endpoint)
#     STATE_FILE         — 持久化 last_sync_time 的文件路径(默认 /tmp/fy_sync_state.json)
#
# 触发方式:
#   - 手动: python3 sync_config.py
#   - 定时: cron / systemd timer / 阿里云函数计算 FC + 定时触发器
#
# 返回码:
#   0  正常
#   1  配置/连接错误
#   2  部分同步失败(SG 返 5xx)

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import pymysql
import requests

# ---- 配置 ----
SYNC_TABLES = {
    "channels": {
        # 哪些字段不同步(敏感 / region-specific)
        "exclude_fields": {"key", "openai_organization", "created_time", "test_time",
                           "response_time", "used_quota", "balance"},
        # 是否支持按 updated_at 增量
        "incremental": True,
    },
    "abilities": {
        "exclude_fields": set(),
        "incremental": False,  # 小表,全量同步
    },
    "options": {
        # 白名单:只同步这些 key 的 options
        "key_whitelist": {
            "ModelRatio", "GroupRatio", "CompletionRatio",
            "CacheRatio", "ModelPrice", "CompletionPrice",
            "GroupGroupRatio", "DefaultGroupRatio",
        },
        "exclude_fields": set(),
        "incremental": False,  # options 表小,全量
    },
}

OPTION_SECRET_PREFIX = ("Session", "Crypto", "Password", "Secret", "Key", "Token")

STATE_FILE = os.environ.get("STATE_FILE", "/tmp/fy_sync_state.json")
HTTP_TIMEOUT = 30
BATCH_SIZE = 200

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sync")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def parse_dsn(dsn: str) -> dict:
    """mysql://user:pass@host:3306/db?charset=utf8mb4"""
    from urllib.parse import urlparse
    u = urlparse(dsn)
    return {
        "host": u.hostname,
        "port": u.port or 3306,
        "user": u.username,
        "password": u.password,
        "database": u.path.lstrip("/"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }


def fetch_channels(conn, since: str | None) -> list[dict]:
    cfg = SYNC_TABLES["channels"]
    with conn.cursor() as cur:
        if cfg["incremental"] and since:
            cur.execute(
                "SELECT * FROM channels WHERE updated_at > %s ORDER BY id",
                (since,),
            )
        else:
            cur.execute("SELECT * FROM channels ORDER BY id")
        rows = cur.fetchall()

    # 脱敏
    for r in rows:
        for field in cfg["exclude_fields"]:
            r.pop(field, None)
    return rows


def fetch_abilities(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM abilities ORDER BY `group`, model, channel_id")
        return cur.fetchall()


def fetch_options(conn) -> list[dict]:
    whitelist = SYNC_TABLES["options"]["key_whitelist"]
    with conn.cursor() as cur:
        placeholders = ",".join(["%s"] * len(whitelist))
        cur.execute(
            f"SELECT `key`, `value` FROM options WHERE `key` IN ({placeholders})",
            list(whitelist),
        )
        rows = cur.fetchall()

    # 再次兜底:排除任何以 secret 前缀开头的 key(双重保险)
    safe = [r for r in rows
            if not any(r["key"].startswith(p) for p in OPTION_SECRET_PREFIX)]
    return safe


def push_to_sg(endpoint: str, payload: dict) -> tuple[bool, str]:
    url = f"{os.environ['SG_API_BASE'].rstrip('/')}{endpoint}"
    headers = {
        "Authorization": f"Bearer {os.environ['SG_INTERNAL_TOKEN']}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        return True, r.text[:200]
    except requests.RequestException as e:
        return False, str(e)


def chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main() -> int:
    # ---- 校验环境变量 ----
    for k in ("CN_DB_DSN", "SG_API_BASE", "SG_INTERNAL_TOKEN"):
        if not os.environ.get(k):
            log.error(f"缺少环境变量: {k}")
            return 1

    state = load_state()
    errors: list[str] = []
    synced: dict[str, int] = {}

    # ---- 连国内 RDS ----
    try:
        conn = pymysql.connect(**parse_dsn(os.environ["CN_DB_DSN"]))
    except pymysql.MySQLError as e:
        log.error(f"国内 RDS 连接失败: {e}")
        return 1

    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # ---- 1. channels ----
        since = state.get("channels_last_sync")
        channels = fetch_channels(conn, since)
        log.info(f"channels: 发现 {len(channels)} 条变更")
        count = 0
        for batch in chunk(channels, BATCH_SIZE):
            ok, msg = push_to_sg("/api/internal/sync/channels", {"channels": batch})
            if ok:
                count += len(batch)
            else:
                errors.append(f"channels batch failed: {msg}")
                log.error(f"channels 推送失败: {msg}")
                break
        synced["channels"] = count
        if count == len(channels):
            state["channels_last_sync"] = now

        # ---- 2. abilities ----
        abilities = fetch_abilities(conn)
        log.info(f"abilities: 全量 {len(abilities)} 条")
        ok, msg = push_to_sg("/api/internal/sync/abilities",
                             {"abilities": abilities, "mode": "replace"})
        if ok:
            synced["abilities"] = len(abilities)
        else:
            errors.append(f"abilities failed: {msg}")

        # ---- 3. options ----
        options = fetch_options(conn)
        log.info(f"options: 白名单 {len(options)} 条")
        ok, msg = push_to_sg("/api/internal/sync/options", {"options": options})
        if ok:
            synced["options"] = len(options)
        else:
            errors.append(f"options failed: {msg}")

    finally:
        conn.close()

    save_state(state)

    log.info(f"同步结果: {synced}")
    if errors:
        for e in errors:
            log.error(e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

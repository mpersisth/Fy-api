#!/usr/bin/env python3
"""
Gross Profit Report — multi-environment financial accounting.

Revenue = logs.quota (exact)
Cost    = quota / group_ratio × cost_factor (per channel per model)

Usage:
  # Generate cost config template
  python gross_profit_report.py --generate-config

  # Run daily report (default: yesterday)
  python gross_profit_report.py --start 2026-06-01 --end 2026-06-01

  # Custom envs/config paths
  python gross_profit_report.py --envs envs.yaml --config channel_costs.yaml \
      --start 2026-06-01 --end 2026-06-01 --output ./report
"""

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

try:
    import yaml
except ImportError:
    yaml = None

QUOTA_PER_UNIT = Decimal("500000")
LOG_TYPE_CONSUME = 2
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _d(v):
    if v is None:
        return Decimal(0)
    return Decimal(str(v))


def _parse_group_ratio(value):
    if value is None:
        return None
    try:
        ratio = Decimal(str(value))
    except Exception:
        return None
    if ratio <= 0:
        return None
    return ratio


def _require_yaml():
    if yaml is None:
        print("Error: pyyaml required. Install: pip install pyyaml", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Environment & DB connection
# ---------------------------------------------------------------------------

def load_envs(envs_path):
    _require_yaml()
    with open(envs_path) as f:
        raw = yaml.safe_load(f)
    return raw.get("environments", {})


def read_dsn_via_ssh(ssh_cmd, env_file, dsn_var):
    cmd = f'{ssh_cmd} "grep -v \'^#\' {env_file} | grep \'^{dsn_var}=\' | head -1"'
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    match = re.match(rf'^{dsn_var}=(.*)', line)
    return match.group(1) if match else None


def remote_mysql_query(ssh_cmd, env_file, dsn_var, sql):
    remote = f"""
set -euo pipefail
ENV_FILE={shlex.quote(env_file)}
DSN=$(grep -v '^#' "$ENV_FILE" | grep '^{dsn_var}=' | head -1 | sed 's/^{dsn_var}=//')
python3 - "$DSN" {shlex.quote(sql)} <<'RPY'
import os, re, subprocess, sys, urllib.parse

dsn = sys.argv[1].strip().strip('"').strip("'")
sql = sys.argv[2]
if dsn.startswith("mysql+pymysql://"):
    u = urllib.parse.urlparse(dsn)
    user = urllib.parse.unquote(u.username or "")
    password = urllib.parse.unquote(u.password or "")
    host = u.hostname or "127.0.0.1"
    port = str(u.port or 3306)
    db = (u.path or "/").lstrip("/")
else:
    m = re.match(r"([^:]+):(.*)@tcp\\(([^:)]+)(?::(\\d+))?\\)/([^?]+)", dsn)
    if not m:
        raise SystemExit("unsupported DSN")
    user, password, host, port, db = m.group(1), m.group(2), m.group(3), m.group(4) or "3306", m.group(5)
env = os.environ.copy()
env["MYSQL_PWD"] = password
cmd = ["mysql", "--batch", "--raw", "--skip-column-names", "-h", host, "-P", port, "-u", user, db, "-e", sql]
subprocess.run(cmd, check=True, env=env)
RPY
""".strip()
    cmd = f"{ssh_cmd} {shlex.quote(remote)}"
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)


def parse_dsn(dsn_str):
    if dsn_str.startswith("mysql+pymysql://"):
        dsn_str = dsn_str[len("mysql+pymysql://"):]
        user_pass, rest = dsn_str.split("@", 1)
        user, password = user_pass.split(":", 1)
        host_port, db = rest.split("/", 1)
        host, port = (host_port.split(":", 1) + ["3306"])[:2]
        return dict(host=host, port=int(port), user=user, password=password, database=db)
    if "@tcp(" in dsn_str:
        user_pass, rest = dsn_str.split("@tcp(", 1)
        user, password = user_pass.split(":", 1)
        host_port, rest = rest.split(")", 1)
        host, port = (host_port.split(":", 1) + ["3306"])[:2]
        db = rest.lstrip("/").split("?")[0]
        return dict(host=host, port=int(port), user=user, password=password, database=db)
    raise ValueError(f"Unrecognized DSN format: {dsn_str[:40]}...")


def connect(dsn_str):
    import pymysql
    kw = parse_dsn(dsn_str)
    kw["charset"] = "utf8mb4"
    kw["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**kw)


# ---------------------------------------------------------------------------
# Cost config
# ---------------------------------------------------------------------------

def load_cost_config(config_path):
    _require_yaml()
    if not os.path.exists(config_path):
        return 1.0, {}
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}
    default_factor = float(raw.get("default_cost_factor", 1.0))
    channels = {}
    for ch_id_raw, ch_data in (raw.get("channels") or {}).items():
        ch_id = int(ch_id_raw)
        if isinstance(ch_data, dict):
            channels[ch_id] = {
                k: float(v) for k, v in ch_data.items()
                if not k.startswith("_") and v is not None
            }
    return default_factor, channels


def get_cost_factor(default_factor, channels, channel_id, model_name):
    ch = channels.get(channel_id, {})
    if model_name in ch:
        return ch[model_name]
    return default_factor


# ---------------------------------------------------------------------------
# Generate config template
# ---------------------------------------------------------------------------

def generate_config(envs, config_path):
    _require_yaml()
    all_channels = {}

    for env_name, env_cfg in envs.items():
        dsn = read_dsn_via_ssh(env_cfg["ssh"], env_cfg["dsn_env_file"], env_cfg["dsn_var"])
        if not dsn:
            print(f"  [{env_name}] Failed to read DSN via SSH, skipping")
            continue
        print(f"  [{env_name}] Connected")
        conn = connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, type, models FROM channels WHERE status = 1 ORDER BY id")
                channels = cur.fetchall()
            for ch in channels:
                ch_id = ch["id"]
                ch_name = ch["name"] or f"channel-{ch_id}"
                models_str = (ch.get("models") or "").strip(",")
                if not models_str:
                    continue
                model_list = sorted(set(m.strip() for m in models_str.split(",") if m.strip()))
                all_channels[ch_id] = {"_name": ch_name, "_env": env_name, "models": model_list}
        finally:
            conn.close()

    with open(config_path, "w") as f:
        f.write("# Channel cost configuration — cost factor per channel per model\n")
        f.write("# cost_factor: 1.0 = official price, 0.8 = 20% discount, 1.1 = 10% premium\n")
        f.write("# Only configure channels/models where factor != 1.0\n\n")
        f.write("default_cost_factor: 1.0\n\n")
        f.write("channels:\n")
        for ch_id in sorted(all_channels.keys()):
            info = all_channels[ch_id]
            f.write(f"\n  {ch_id}:   # {info['_name']} ({info['_env']})\n")
            for model in info["models"]:
                f.write(f"    # {model}: 1.0\n")

    print(f"\nGenerated {config_path}: {len(all_channels)} channels")
    print("Uncomment and edit cost factors for channels where price != official list price.")


# ---------------------------------------------------------------------------
# Log fetching
# ---------------------------------------------------------------------------

def fetch_logs(conn, start_ts, end_ts, batch_size=5000):
    sql = (
        "SELECT l.id, l.user_id, l.username, l.created_at, l.quota, "
        "l.prompt_tokens, l.completion_tokens, l.channel_id, l.model_name, "
        "l.`group`, l.other, c.name AS channel_name "
        "FROM logs l LEFT JOIN channels c ON l.channel_id = c.id "
        "WHERE l.type = %s AND l.created_at >= %s AND l.created_at <= %s "
        "AND l.id > %s ORDER BY l.id LIMIT %s"
    )
    last_id = 0
    while True:
        with conn.cursor() as cur:
            cur.execute(sql, (LOG_TYPE_CONSUME, start_ts, end_ts, last_id, batch_size))
            rows = cur.fetchall()
        if not rows:
            break
        for r in rows:
            yield r
        last_id = rows[-1]["id"]
        if len(rows) < batch_size:
            break


def remote_grouped_rows(env_name, env_cfg, start_ts, end_ts, default_factor, cost_channels):
    sql = f"""
SELECT
  DATE(FROM_UNIXTIME(l.created_at)) AS day,
  l.user_id,
  COALESCE(NULLIF(l.username,''), CONCAT('user-', l.user_id)) AS username,
  l.channel_id,
  COALESCE(NULLIF(c.name,''), CONCAT('ch-', l.channel_id)) AS channel_name,
  l.model_name,
  COUNT(*) AS requests,
  COALESCE(SUM(l.prompt_tokens),0) AS prompt_tokens,
  COALESCE(SUM(l.completion_tokens),0) AS completion_tokens,
  COALESCE(SUM(l.quota),0) AS quota,
  COALESCE(SUM(
    l.quota / IFNULL(
      NULLIF(CAST(JSON_UNQUOTE(JSON_EXTRACT(CASE WHEN JSON_VALID(l.other) THEN l.other ELSE NULL END, '$.group_ratio')) AS DECIMAL(20,8)), 0),
      1
    )
  ),0) AS base_cost_quota,
  COALESCE(SUM(
    CASE
      WHEN JSON_VALID(l.other)
       AND JSON_EXTRACT(l.other, '$.group_ratio') IS NOT NULL
       AND CAST(JSON_UNQUOTE(JSON_EXTRACT(l.other, '$.group_ratio')) AS DECIMAL(20,8)) > 0
      THEN 0 ELSE 1
    END
  ),0) AS group_ratio_missing
FROM logs l
LEFT JOIN channels c ON c.id = l.channel_id
WHERE l.type = {LOG_TYPE_CONSUME}
  AND l.created_at >= {int(start_ts)}
  AND l.created_at <= {int(end_ts)}
  AND l.quota > 0
GROUP BY day,l.user_id,username,l.channel_id,channel_name,l.model_name
ORDER BY day, quota DESC;
""".strip()
    result = remote_mysql_query(env_cfg["ssh"], env_cfg["dsn_env_file"], env_cfg["dsn_var"], sql)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "remote mysql query failed")

    rows_out = []
    warnings = defaultdict(lambda: {"count": 0, "revenue": Decimal(0)})
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 12:
            continue
        (day, user_id, username, channel_id, channel_name, model_name, requests,
         prompt_tokens, completion_tokens, quota, base_cost_quota, group_ratio_missing) = parts
        quota_d = _d(quota)
        base_cost_quota_d = _d(base_cost_quota)
        missing_count = int(Decimal(group_ratio_missing))
        cost_factor = get_cost_factor(default_factor, cost_channels, int(channel_id), model_name)
        revenue_usd = quota_d / QUOTA_PER_UNIT
        cost_usd = base_cost_quota_d / QUOTA_PER_UNIT * _d(cost_factor)
        rows_out.append({
            "env": env_name,
            "date": day,
            "user_id": int(user_id),
            "username": username,
            "channel_id": int(channel_id),
            "channel_name": channel_name,
            "model_name": model_name,
            "requests": int(requests),
            "prompt_tokens": int(Decimal(prompt_tokens)),
            "completion_tokens": int(Decimal(completion_tokens)),
            "quota": quota_d,
            "base_cost_quota": base_cost_quota_d,
            "group_ratio_missing": missing_count > 0,
            "cost_factor": cost_factor,
            "revenue_usd": revenue_usd,
            "cost_usd": cost_usd,
        })
        if missing_count > 0:
            wk = (int(channel_id), channel_name, model_name)
            warnings[wk]["count"] += missing_count
            warnings[wk]["revenue"] += revenue_usd
            warnings[wk]["note"] = "missing or invalid group_ratio; 折扣倍率 marked 缺失"
    return rows_out, dict(warnings)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def ts_to_date(ts):
    tz_sh = timezone(timedelta(hours=8))
    dt = datetime.fromtimestamp(ts, tz=tz_sh)
    return f"{dt.year}/{dt.month}/{dt.day}"


def fmt(d):
    return d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def fmt_ratio(d):
    return str(d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP).normalize())


def process_env(env_name, dsn, start_ts, end_ts, default_factor, cost_channels):
    conn = connect(dsn)
    rows_out = []
    warnings = defaultdict(lambda: {"count": 0, "revenue": Decimal(0)})

    try:
        for row in fetch_logs(conn, start_ts, end_ts):
            quota = int(row["quota"])
            if quota <= 0:
                continue

            other_str = row.get("other") or "{}"
            try:
                other = json.loads(other_str)
            except (json.JSONDecodeError, TypeError):
                other = {}

            group_ratio = _parse_group_ratio(other.get("group_ratio"))
            group_ratio_missing = group_ratio is None
            if group_ratio_missing:
                group_ratio = Decimal("1")

            channel_id = row["channel_id"]
            model_name = row["model_name"]
            cost_factor = get_cost_factor(default_factor, cost_channels, channel_id, model_name)

            revenue_usd = _d(quota) / QUOTA_PER_UNIT
            base_cost_quota = _d(quota) / group_ratio
            cost_usd = base_cost_quota / QUOTA_PER_UNIT * _d(cost_factor)

            rows_out.append({
                "env": env_name,
                "date": ts_to_date(row["created_at"]),
                "user_id": row["user_id"],
                "username": row["username"],
                "channel_id": channel_id,
                "channel_name": row["channel_name"] or f"ch-{channel_id}",
                "model_name": model_name,
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "quota": _d(quota),
                "base_cost_quota": base_cost_quota,
                "group_ratio_missing": group_ratio_missing,
                "cost_factor": cost_factor,
                "revenue_usd": revenue_usd,
                "cost_usd": cost_usd,
            })

            if group_ratio_missing:
                wk = (channel_id, row["channel_name"] or f"ch-{channel_id}", model_name)
                warnings[wk]["count"] += 1
                warnings[wk]["revenue"] += revenue_usd
                warnings[wk]["note"] = "missing or invalid group_ratio; cost used 1.0 fallback"

            if cost_factor == default_factor and default_factor == 1.0:
                wk = (channel_id, row["channel_name"] or f"ch-{channel_id}", model_name)
                warnings[wk]["count"] += 1
                warnings[wk]["revenue"] += revenue_usd
                warnings[wk]["note"] = "using default_cost_factor — verify or configure"
    finally:
        conn.close()

    return rows_out, dict(warnings)


def generate_report(all_rows, output_dir, warnings_all):
    os.makedirs(output_dir, exist_ok=True)

    # --- detail.csv ---
    detail_path = os.path.join(output_dir, "detail.csv")
    with open(detail_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期", "环境", "用户", "渠道ID", "渠道", "模型", "请求数",
                     "输入Tokens", "输出Tokens", "折扣倍率", "收入(USD)", "成本(USD)",
                     "毛利(USD)", "毛利率(%)"])

        # Group by 4 dimensions
        grouped = defaultdict(lambda: {
            "requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "revenue": Decimal(0), "cost": Decimal(0), "quota": Decimal(0),
            "base_cost_quota": Decimal(0), "group_ratio_missing": 0
        })
        for r in all_rows:
            key = (r["env"], r["date"], r["user_id"], r["username"],
                   r["channel_id"], r["channel_name"], r["model_name"])
            grouped[key]["requests"] += int(r.get("requests", 1))
            grouped[key]["prompt_tokens"] += r["prompt_tokens"]
            grouped[key]["completion_tokens"] += r["completion_tokens"]
            grouped[key]["quota"] += r.get("quota", r["revenue_usd"] * QUOTA_PER_UNIT)
            grouped[key]["base_cost_quota"] += r.get("base_cost_quota", r["cost_usd"] * QUOTA_PER_UNIT)
            if r.get("group_ratio_missing"):
                grouped[key]["group_ratio_missing"] += int(r.get("requests", 1))
            grouped[key]["revenue"] += r["revenue_usd"]
            grouped[key]["cost"] += r["cost_usd"]

        for key in sorted(grouped.keys()):
            v = grouped[key]
            rev, cst = fmt(v["revenue"]), fmt(v["cost"])
            if v["group_ratio_missing"] > 0:
                discount_ratio = "缺失"
            elif v["base_cost_quota"] > 0:
                discount_ratio = fmt_ratio(v["quota"] / v["base_cost_quota"])
            else:
                discount_ratio = "缺失"
            profit = rev - cst
            margin = (profit / rev * 100) if rev > 0 else Decimal(0)
            env, date, _user_id, username, channel_id, channel_name, model_name = key
            w.writerow([fmt_report_date(date), env, username, channel_id, channel_name, model_name,
                        v["requests"], v["prompt_tokens"], v["completion_tokens"],
                        discount_ratio, rev, cst, fmt(profit), fmt_pct(margin)])
    print(f"  {detail_path} ({len(grouped)} rows)")

    # --- summary CSVs ---
    _write_summary(output_dir, "by_day.csv", all_rows,
                   key_fn=lambda r: (r["env"], r["date"]),
                   header=["env", "date"])
    _write_summary(output_dir, "by_user.csv", all_rows,
                   key_fn=lambda r: (r["env"], r["date"], r["username"]),
                   header=["env", "date", "username"])
    _write_summary(output_dir, "by_channel.csv", all_rows,
                   key_fn=lambda r: (r["env"], r["date"], r["channel_name"]),
                   header=["env", "date", "channel_name"])
    _write_summary(output_dir, "by_model.csv", all_rows,
                   key_fn=lambda r: (r["env"], r["date"], r["model_name"]),
                   header=["env", "date", "model_name"])

    # --- warnings.csv ---
    warn_path = os.path.join(output_dir, "warnings.csv")
    warn_rows = []
    for (ch_id, ch_name, model), v in sorted(warnings_all.items(), key=lambda x: -x[1]["count"]):
        if v["count"] > 0:
            warn_rows.append([ch_id, ch_name, model, v["count"], fmt(v["revenue"]),
                              v.get("note", "verify report inputs")])
    with open(warn_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["channel_id", "channel_name", "model_name", "request_count",
                     "revenue_usd", "note"])
        for wr in warn_rows:
            w.writerow(wr)
    print(f"  {warn_path} ({len(warn_rows)} rows)")

    # --- Terminal summary ---
    _print_terminal_summary(all_rows)


def _write_summary(output_dir, filename, all_rows, key_fn, header):
    agg = defaultdict(lambda: {"requests": 0, "revenue": Decimal(0), "cost": Decimal(0)})
    for r in all_rows:
        k = key_fn(r)
        agg[k]["requests"] += int(r.get("requests", 1))
        agg[k]["revenue"] += r["revenue_usd"]
        agg[k]["cost"] += r["cost_usd"]

    path = os.path.join(output_dir, filename)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header + ["requests", "revenue_usd", "cost_usd", "profit_usd", "margin_pct"])
        for k in sorted(agg.keys()):
            v = agg[k]
            rev, cst = fmt(v["revenue"]), fmt(v["cost"])
            profit = rev - cst
            margin = (profit / rev * 100) if rev > 0 else Decimal(0)
            w.writerow(list(k) + [v["requests"], rev, cst, fmt(profit),
                                   margin.quantize(Decimal("0.01"))])
    print(f"  {path} ({len(agg)} rows)")


def fmt_pct(d):
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize())


def fmt_report_date(value):
    if isinstance(value, str):
        for fmt_str in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(value, fmt_str)
                return f"{dt.year}/{dt.month}/{dt.day}"
            except ValueError:
                pass
    return value


def _print_terminal_summary(all_rows):
    # By env+date
    by_env = defaultdict(lambda: {"revenue": Decimal(0), "cost": Decimal(0)})
    by_user = defaultdict(lambda: {"revenue": Decimal(0), "cost": Decimal(0)})
    by_channel = defaultdict(lambda: {"revenue": Decimal(0), "cost": Decimal(0)})

    for r in all_rows:
        by_env[r["env"]]["revenue"] += r["revenue_usd"]
        by_env[r["env"]]["cost"] += r["cost_usd"]
        uk = (r["env"], r["username"])
        by_user[uk]["revenue"] += r["revenue_usd"]
        by_user[uk]["cost"] += r["cost_usd"]
        ck = (r["env"], r["channel_name"])
        by_channel[ck]["revenue"] += r["revenue_usd"]
        by_channel[ck]["cost"] += r["cost_usd"]

    total_rev = sum(v["revenue"] for v in by_env.values())
    total_cost = sum(v["cost"] for v in by_env.values())
    total_profit = total_rev - total_cost
    total_margin = (total_profit / total_rev * 100) if total_rev > 0 else Decimal(0)

    print(f"\n{'='*55}")
    print(f"  {'Env':<8} {'Revenue':>10} {'Cost':>10} {'Profit':>10} {'Margin':>8}")
    print(f"  {'-'*46}")
    for env in sorted(by_env.keys()):
        v = by_env[env]
        rev, cst = fmt(v["revenue"]), fmt(v["cost"])
        profit = rev - cst
        margin = (profit / rev * 100) if rev > 0 else Decimal(0)
        print(f"  {env:<8} ${rev:>9.4f} ${cst:>9.4f} ${profit:>9.4f} {margin:>6.1f}%")
    print(f"  {'-'*46}")
    print(f"  {'TOTAL':<8} ${fmt(total_rev):>9.4f} ${fmt(total_cost):>9.4f} "
          f"${fmt(total_profit):>9.4f} {total_margin:>6.1f}%")

    print(f"\n--- Top Users ---")
    top_users = sorted(by_user.items(), key=lambda x: -x[1]["revenue"])[:10]
    for (env, uname), v in top_users:
        rev, cst = fmt(v["revenue"]), fmt(v["cost"])
        print(f"  {uname} ({env}){' '*(20-len(uname)-len(env))} "
              f"rev ${rev:>9.4f}  cost ${cst:>9.4f}  profit ${fmt(rev-cst):>9.4f}")

    print(f"\n--- Top Channels ---")
    top_channels = sorted(by_channel.items(), key=lambda x: -x[1]["revenue"])[:10]
    for (env, ch_name), v in top_channels:
        rev, cst = fmt(v["revenue"]), fmt(v["cost"])
        name_display = ch_name[:16]
        print(f"  {name_display} ({env}){' '*(20-len(name_display)-len(env))} "
              f"rev ${rev:>9.4f}  cost ${cst:>9.4f}  profit ${fmt(rev-cst):>9.4f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Multi-env gross profit report")
    parser.add_argument("--envs", default=os.path.join(SCRIPT_DIR, "envs.yaml"),
                        help="Environment config file (default: envs.yaml)")
    parser.add_argument("--config", default=os.path.join(SCRIPT_DIR, "channel_costs.yaml"),
                        help="Channel cost config (default: channel_costs.yaml)")
    parser.add_argument("--generate-config", action="store_true",
                        help="Generate cost config template from DB, then exit")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: same as start)")
    parser.add_argument("--output", default=os.path.join(os.getcwd(), "gross_profit_report"),
                        help="Output directory")
    args = parser.parse_args()

    _require_yaml()
    envs = load_envs(args.envs)
    if not envs:
        print(f"Error: no environments in {args.envs}", file=sys.stderr)
        sys.exit(1)

    if args.generate_config:
        print("Generating cost config template...")
        generate_config(envs, args.config)
        return

    # Default window: yesterday 17:00 → today 16:59:59 (Asia/Shanghai)
    tz_sh = timezone(timedelta(hours=8))
    now_sh = datetime.now(tz_sh)

    if not args.start and not args.end:
        start_dt = (now_sh - timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
        end_dt = now_sh.replace(hour=16, minute=59, second=59, microsecond=0)
    else:
        if not args.start:
            args.start = (now_sh - timedelta(days=1)).strftime("%Y-%m-%d")
        if not args.end:
            args.end = args.start
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(
            hour=17, minute=0, second=0, tzinfo=tz_sh)
        end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(
            hour=16, minute=59, second=59, tzinfo=tz_sh) + timedelta(days=1)

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    default_factor, cost_channels = load_cost_config(args.config)
    has_config = os.path.exists(args.config)
    if has_config:
        configured = sum(len(v) for v in cost_channels.values())
        print(f"Cost config: {args.config} ({configured} channel/model overrides, default={default_factor})")
    else:
        print(f"No cost config found at {args.config} — using default_cost_factor={default_factor}")

    print(f"Report period: {args.start} to {args.end}\n")

    all_rows = []
    warnings_all = {}

    for env_name, env_cfg in envs.items():
        print(f"[{env_name}] Reading DSN via SSH...")
        dsn = read_dsn_via_ssh(env_cfg["ssh"], env_cfg["dsn_env_file"], env_cfg["dsn_var"])
        if not dsn:
            print(f"[{env_name}] Failed to read DSN — skipping")
            continue
        print(f"[{env_name}] Querying logs...")
        try:
            rows, warns = process_env(env_name, dsn, start_ts, end_ts, default_factor, cost_channels)
        except Exception as err:
            print(f"[{env_name}] Direct DB query failed ({err}); retrying via remote mysql...")
            rows, warns = remote_grouped_rows(env_name, env_cfg, start_ts, end_ts, default_factor, cost_channels)
        all_rows.extend(rows)
        for k, v in warns.items():
            if k in warnings_all:
                warnings_all[k]["count"] += v["count"]
                warnings_all[k]["revenue"] += v["revenue"]
            else:
                warnings_all[k] = v
        print(f"[{env_name}] {len(rows)} rows")

    if not all_rows:
        print("\nNo data found for the given period.")
        return

    print(f"\nWriting reports to {args.output}/")
    generate_report(all_rows, args.output, warnings_all)


if __name__ == "__main__":
    main()

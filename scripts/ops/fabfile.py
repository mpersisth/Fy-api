"""
Fabric tasks for operating TraceNex fleet (CN + SG + anywhere else).

Setup:
    pip install fabric

Usage:
    cd scripts/ops
    fab -l                              # list all tasks
    fab -H cn status                    # status on CN only
    fab -H sg,cn status                 # on both
    fab deploy --tag=v0.9.7             # deploy v0.9.7 to all hosts (default)
    fab -H cn logs --tail=200           # last 200 lines of Fy-api log on CN
    fab -H cn,sg ps                     # container status on both
    fab sync-config                     # trigger CN->SG config sync now

Host aliases are defined in `fabric.yaml` (fabric auto-loads it).
SSH config comes from ~/.ssh/config — add hosts there rather than hardcoding IPs.
"""
import os
from datetime import datetime

from fabric import Connection, task
from fabric import Config
from invoke.exceptions import UnexpectedExit

# ====================================================================
# Host registry — edit when you add a region
# ====================================================================
HOSTS = {
    "cn": {
        "host": "tracenex-cn",          # ~/.ssh/config alias
        "user": "root",
        "region": "cn-hangzhou",
        "registry": "transnext-acr-ee-registry-vpc.cn-hangzhou.cr.aliyuncs.com",
        "namespace": "transnext",
        "repo": "fy-api",
        "env_file": "/opt/fy-api/config/fy-api.env",
        "log_dir": "/opt/fy-api/logs",
        "nginx_conf": "/etc/nginx/conf.d/fy-api.conf",
    },
    "sg": {
        "host": "tracenex-sg",          # ~/.ssh/config alias
        "user": "root",
        "region": "ap-southeast-1",
        "registry": "registry.ap-southeast-1.aliyuncs.com",
        "namespace": "tracenex-sg",     # fill in your SG ACR namespace
        "repo": "fy-api",
        "env_file": "/opt/fy-api/config/fy-api.env",
        "log_dir": "/opt/fy-api/logs",
        "nginx_conf": "/etc/nginx/conf.d/fy-api.conf",
    },
}


def _resolve(ctx):
    """
    Fabric passes a Connection when -H/--hosts is used, else a Context.
    Return either a list of HOSTS entries that match the connection, or
    all hosts if no -H given (for whole-fleet commands like `deploy`).
    """
    if isinstance(ctx, Connection):
        # single host,match by alias in ~/.ssh/config
        for key, info in HOSTS.items():
            if info["host"] == ctx.host or key == ctx.host:
                return [(key, info, ctx)]
        # fall back to the connection as-is with no metadata
        return [("unknown", {}, ctx)]
    # no -H: run on all hosts
    results = []
    for key, info in HOSTS.items():
        conn = Connection(host=info["host"], user=info["user"])
        results.append((key, info, conn))
    return results


def _run(conn, cmd, warn=False, hide=False):
    """Thin wrapper that prints the host prefix so output is disambiguated."""
    prefix = f"[{conn.host}]"
    if not hide:
        print(f"{prefix} $ {cmd}")
    return conn.run(cmd, warn=warn, hide=hide, pty=False)


# ====================================================================
# Health / status
# ====================================================================

@task
def status(ctx):
    """One-page health snapshot: container, nginx, disk, memory."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ({info.get('region', '?')}) ====")
        _run(c, "podman ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'fy-api|tracenex'", warn=True)
        _run(c, "systemctl is-active nginx || true", warn=True)
        _run(c, "df -h /opt/fy-api /var/log/nginx 2>/dev/null | awk 'NR>1'", warn=True)
        _run(c, "free -h | head -2", warn=True)


@task
def ps(ctx):
    """List Fy-api containers."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        _run(c, "podman ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'NAMES|fy-api|tracenex' || true",
             warn=True)


@task
def stats(ctx):
    """Container resource stats (CPU / memory)."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        _run(c, "podman stats --no-stream", warn=True)


# ====================================================================
# Logs
# ====================================================================

@task(help={"tail": "how many lines (default 100)", "grep": "grep pattern"})
def logs(ctx, tail=100, grep=""):
    """Tail Fy-api container stdout."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} (last {tail}) ====")
        cmd = f"podman logs --tail {tail} fy-api-green 2>&1 || podman logs --tail {tail} fy-api-blue 2>&1"
        if grep:
            cmd += f" | grep -iE '{grep}'"
        _run(c, cmd, warn=True)


@task(help={"tail": "how many lines", "grep": "grep pattern"})
def nginx_access(ctx, tail=100, grep=""):
    """Tail Nginx access log."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        cmd = f"tail -n {tail} /var/log/nginx/fy-api-access.log /var/log/nginx/www_tracenex_cn-access.log 2>/dev/null"
        if grep:
            cmd += f" | grep -iE '{grep}'"
        _run(c, cmd, warn=True)


@task(help={"tail": "how many lines"})
def nginx_error(ctx, tail=50):
    """Tail Nginx error log (filtering SSL scanner noise)."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        cmd = (f"tail -n {tail} /var/log/nginx/fy-api-error.log /var/log/nginx/www_tracenex_cn-error.log 2>/dev/null "
               f"| grep -v 'SSL_do_handshake\\|bad key share\\|bad record type' || true")
        _run(c, cmd, warn=True)


@task
def errors(ctx):
    """Summary of [ERR] lines in Fy-api logs, deduped."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        cmd = (r"""podman logs --since 24h fy-api-green 2>&1 | grep '\[ERR\]' """
               r"""| sed -E 's/^\[ERR\] [0-9\/]+ - [0-9:]+ \| [^|]+ \|//' """
               r"""| sort | uniq -c | sort -rn | head -15 || true""")
        _run(c, cmd, warn=True)


# ====================================================================
# Deploy
# ====================================================================

@task(help={
    "tag": "image tag to deploy (e.g. v0.9.7)",
    "only": "comma-separated host keys to deploy to (default: all)",
})
def deploy(ctx, tag, only=""):
    """
    Blue-green deploy `tag` to specified hosts (or all).

    Runs scripts/prod/06-deploy-blue-green.sh on each host with
    the region-appropriate REGISTRY / NAMESPACE / REPO.
    """
    target_keys = [k.strip() for k in only.split(",") if k.strip()] or list(HOSTS.keys())

    for key in target_keys:
        if key not in HOSTS:
            print(f"⚠️  unknown host key: {key}, skip")
            continue
        info = HOSTS[key]
        c = Connection(host=info["host"], user=info["user"])
        print(f"\n==== Deploying {tag} to {key.upper()} ====")

        # Assumes scripts/prod is at ~/Fy-api/scripts/prod on the remote
        cmd = (
            f"cd ~/Fy-api/scripts/prod && "
            f"sudo -E REGISTRY={info['registry']} "
            f"NAMESPACE={info['namespace']} "
            f"REPO={info['repo']} "
            f"./06-deploy-blue-green.sh {tag}"
        )
        try:
            _run(c, cmd)
            print(f"✅ {key.upper()} deployed {tag}")
        except UnexpectedExit as e:
            print(f"❌ {key.upper()} deploy failed: {e}")


@task(help={"tag": "tag to pull (doesn't deploy)"})
def pull(ctx, tag):
    """Pull image to all hosts without swapping — useful for pre-deploy warm."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== Pulling {tag} to {key.upper()} ====")
        image = f"{info['registry']}/{info['namespace']}/{info['repo']}:{tag}"
        _run(c, f"podman pull {image}", warn=True)


@task(help={"tag": "tag to rollback to"})
def rollback(ctx, tag):
    """
    Emergency rollback: blue-green deploy to a specific older tag.

    Same mechanism as `deploy` but named clearly for on-call clarity.
    """
    print(f"⚠️  Rolling back to {tag}")
    deploy(ctx, tag=tag)


# ====================================================================
# Config sync (CN -> SG)
# ====================================================================

@task
def sync_config(ctx):
    """Trigger one CN -> SG config sync now (channels / abilities / options)."""
    cn = HOSTS["cn"]
    c = Connection(host=cn["host"], user=cn["user"])
    print("\n==== Running config sync (CN -> SG) ====")
    _run(c, "python3 ~/Fy-api/scripts/ops/sync_config.py", warn=True)


# ====================================================================
# Certs
# ====================================================================

@task
def certs(ctx):
    """Show TLS cert expiry for each host."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        _run(c, "sudo ls -la /etc/letsencrypt/live/ 2>/dev/null | awk 'NR>1 {print $NF}' | while read d; do "
                "echo \"[$d]\"; "
                "sudo openssl x509 -in /etc/letsencrypt/live/$d/fullchain.pem -noout -subject -issuer -dates 2>/dev/null; "
                "done", warn=True)


@task
def cert_renew(ctx):
    """Force certbot renewal + nginx reload."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        _run(c, "sudo certbot renew --force-renewal", warn=True)
        _run(c, "sudo nginx -t && sudo systemctl reload nginx", warn=True)


# ====================================================================
# DB helpers (runs read-only queries through the container)
# ====================================================================

@task(help={"sql": "SQL to execute (read-only recommended!)"})
def dbq(ctx, sql):
    """
    Execute a SQL query against the Fy-api RDS from within the container.
    Only works if the container has mysql client installed.
    Use with caution — prefer dry-run SELECT for safety.
    """
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        # Extract DSN from env-file, then run the query
        cmd = f"""
        DSN=$(grep '^SQL_DSN=' {info['env_file']} | cut -d= -f2-)
        USER=$(echo $DSN | sed -nE 's|^([^:]+):.*|\\1|p')
        PASS=$(echo $DSN | sed -nE 's|^[^:]+:([^@]+)@.*|\\1|p')
        HOST=$(echo $DSN | sed -nE 's|^[^@]+@tcp\\(([^:]+):.*|\\1|p')
        DB=$(echo $DSN | sed -nE 's|^[^/]+/([^?]+).*|\\1|p')
        mysql -h$HOST -u$USER -p$PASS $DB -e {sql!r} 2>&1 | head -50
        """
        _run(c, cmd, warn=True)


# ====================================================================
# Quick actions
# ====================================================================

@task
def restart_nginx(ctx):
    """systemctl reload nginx (not restart, avoids downtime)."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        _run(c, "sudo nginx -t && sudo systemctl reload nginx", warn=True)


@task
def restart_fyapi(ctx):
    """Restart the active Fy-api container (NOT blue-green — this causes a short outage)."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        _run(c, "podman restart $(podman ps --format '{{.Names}}' | grep -E 'fy-api-(blue|green)' | head -1)",
             warn=True)


@task
def uptime(ctx):
    """System uptime + load."""
    for key, info, c in _resolve(ctx):
        print(f"\n==== {key.upper()} ====")
        _run(c, "uptime && cat /proc/loadavg", warn=True)

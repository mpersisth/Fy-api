"""
Fabric release tasks for TraceNex/Fy-api.

Flow:
    local git push
      -> server git fetch/checkout
      -> server podman build
      -> server podman push to intranet ACR
      -> server blue-green deploy from ACR

Setup:
    pip install -r scripts/ops/requirements.txt

Common usage from the Fy-api repo root:
    fab info
    fab status --target=cn
    fab status --target=sg
    fab bootstrap-system --target=sg
    fab release --target=cn --tag=v0.9.8 --ref=origin/main
    fab deploy --target=cn --tag=v0.9.8
    fab rollback --target=cn --tag=v0.9.7
    fab logs --target=cn --tail=200

Known targets:
    cn: ssh -i ~/.ssh/tracenex_XN.pem -p 58422 root@8.136.146.211
    sg: ssh -i ~/.ssh/AI_tracenex.pem -p 58422 root@47.236.133.70
    legacy: ssh root@8.222.175.17

Override with environment variables when needed:
    FYAPI_TARGET
    FYAPI_HOST, FYAPI_PORT, FYAPI_USER, FYAPI_KEY
    FYAPI_REPO_URL, FYAPI_SRC_DIR, FYAPI_BUILD_DIR
    FYAPI_REGISTRY, FYAPI_NAMESPACE, FYAPI_REPO
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from fabric import Connection, task

TARGETS = {
    "cn": {
        "host": "8.136.146.211",
        "port": 58422,
        "user": "root",
        "key": "~/.ssh/tracenex_XN.pem",
        "app_dir": "/opt/fy-api",
        "src_dir": "/root/Fy-api",
        "build_dir": "/tmp/fy-api-build",
        "registry": "transnext-acr-ee-registry-vpc.cn-hangzhou.cr.aliyuncs.com",
        "namespace": "transnext",
        "repo": "fy-api",
        "repo_url": "git@github.com:seraph0017/Fy-api.git",
    },
    "sg": {
        "host": "47.236.133.70",
        "port": 58422,
        "user": "root",
        "key": "~/.ssh/AI_tracenex.pem",
        "app_dir": "/opt/fy-api",
        "src_dir": "/root/Fy-api",
        "build_dir": "/tmp/fy-api-build",
        "registry": "transnext-acr-ee-sg-registry-vpc.ap-southeast-1.cr.aliyuncs.com",
        "namespace": "ai_transnext",
        "repo": "fy-api",
        "repo_url": "https://github.com/seraph0017/Fy-api.git",
    },
    "legacy": {
        "host": "8.222.175.17",
        "port": 22,
        "user": "root",
        "key": "",
        "app_dir": "/opt/fy-api",
        "src_dir": "/root/Fy-api",
        "build_dir": "/tmp/fy-api-build",
        "registry": "",
        "namespace": "",
        "repo": "fy-api",
        "repo_url": "git@github.com:seraph0017/Fy-api.git",
    },
    "test": {
        "host": "8.156.88.148",
        "port": 58422,
        "user": "root",
        "key": "",
        "app_dir": "/opt/fy-api",
        "src_dir": "/root/Fy-api",
        "build_dir": "/tmp/fy-api-build",
        "registry": "",
        "namespace": "",
        "repo": "fy-api",
        "repo_url": "git@github.com:seraph0017/Fy-api.git",
        "nginx_conf": "/etc/nginx/conf.d/tracenex-test.conf",
        "mem": "6g",
        "cpus": "2",
    },
    "sg-test": {
        "host": "8.222.175.17",
        "port": 22,
        "user": "root",
        "key": "",
        "app_dir": "/opt/fy-api",
        "src_dir": "/root/Fy-api",
        "build_dir": "/tmp/fy-api-build",
        "registry": "",
        "namespace": "",
        "repo": "fy-api",
        "repo_url": "https://github.com/seraph0017/Fy-api.git",
        "nginx_conf": "/etc/nginx/conf.d/fy-api.conf",
        "mem": "6g",
        "cpus": "4",
    },
}

DEFAULT_REPO_URL = "git@github.com:seraph0017/Fy-api.git"
DEFAULT_REF = os.getenv("FYAPI_DEFAULT_REF", "origin/main")
DEPLOY_LOCK = os.getenv("FYAPI_DEPLOY_LOCK", "/tmp/fy-api-deploy.lock")
SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9._/@:+-]+$")


def _validate_arg(name: str, value: str) -> str:
    if not value:
        raise ValueError(f"{name} is required")
    if not SAFE_ARG_RE.match(value):
        raise ValueError(f"unsafe {name}: {value!r}")
    return value


def _q(value: str) -> str:
    return shlex.quote(value)


def _config(target: str | None = None) -> dict[str, object]:
    name = target or os.getenv("FYAPI_TARGET", "cn")
    if name not in TARGETS:
        raise ValueError(f"unknown target {name!r}; expected one of: {', '.join(TARGETS)}")
    cfg = dict(TARGETS[name])
    cfg["name"] = name

    overrides = {
        "host": "FYAPI_HOST",
        "port": "FYAPI_PORT",
        "user": "FYAPI_USER",
        "key": "FYAPI_KEY",
        "app_dir": "FYAPI_APP_DIR",
        "src_dir": "FYAPI_SRC_DIR",
        "build_dir": "FYAPI_BUILD_DIR",
        "registry": "FYAPI_REGISTRY",
        "namespace": "FYAPI_NAMESPACE",
        "repo": "FYAPI_REPO",
        "repo_url": "FYAPI_REPO_URL",
    }
    for key, env_name in overrides.items():
        value = os.getenv(env_name)
        if value:
            cfg[key] = int(value) if key == "port" else value

    cfg["key"] = os.path.expanduser(str(cfg.get("key") or ""))
    cfg["env_file"] = os.getenv("FYAPI_ENV_FILE", f"{cfg['app_dir']}/config/fy-api.env")
    cfg.setdefault("nginx_conf", "/etc/nginx/conf.d/fy-api.conf")
    if os.getenv("FYAPI_NGINX_CONF"):
        cfg["nginx_conf"] = os.getenv("FYAPI_NGINX_CONF")
    return cfg


def _image(cfg: dict[str, object], tag: str) -> str:
    tag = _validate_arg("tag", tag)
    if cfg.get("registry") and cfg.get("namespace"):
        return f"{cfg['registry']}/{cfg['namespace']}/{cfg['repo']}:{tag}"
    return f"{cfg['repo']}:{tag}"


def _connect(cfg: dict[str, object]) -> Connection:
    connect_kwargs = {}
    key = str(cfg.get("key") or "")
    if key:
        connect_kwargs["key_filename"] = key
    return Connection(
        host=str(cfg["host"]),
        user=str(cfg["user"]),
        port=int(cfg["port"]),
        connect_kwargs=connect_kwargs,
    )


def _run(c: Connection, command: str, *, warn: bool = False, hide: bool = False):
    if not hide:
        print(f"[{c.user}@{c.host}:{c.port}] $ {command}")
    return c.run(command, warn=warn, hide=hide, pty=False)


def _ensure_source_checkout(c: Connection, cfg: dict[str, object]):
    src_dir = str(cfg["src_dir"])
    parent = _q(str(Path(src_dir).parent))
    src = _q(src_dir)
    repo = _q(str(cfg.get("repo_url") or DEFAULT_REPO_URL))
    _run(
        c,
        " && ".join(
            [
                f"mkdir -p {parent}",
                f"if [ ! -d {src}/.git ]; then git clone {repo} {src}; fi",
                f"test -d {src}/.git",
            ]
        ),
    )


def _checkout_ref(c: Connection, cfg: dict[str, object], ref: str):
    ref = _validate_arg("ref", ref)
    src = _q(str(cfg["src_dir"]))
    quoted_ref = _q(ref)
    _run(
        c,
        " && ".join(
            [
                f"cd {src}",
                "git fetch origin --tags --prune",
                f"git checkout -f {quoted_ref}",
                f"git reset --hard {quoted_ref}",
                "git rev-parse --short HEAD",
            ]
        ),
    )


@task(help={"target": "target name: cn or sg"})
def info(ctx, target="cn"):
    """Print local Fabric deployment configuration."""
    cfg = _config(target)
    print(f"target:    {cfg['name']}")
    print(f"host:      {cfg['user']}@{cfg['host']}:{cfg['port']}")
    print(f"key:       {cfg['key']}")
    print(f"src:       {cfg['src_dir']}")
    print(f"build_dir: {cfg['build_dir']}")
    print(f"repo_url:  {cfg.get('repo_url') or DEFAULT_REPO_URL}")
    if cfg.get("registry") and cfg.get("namespace"):
        print(f"image:     {cfg['registry']}/{cfg['namespace']}/{cfg['repo']}:<tag>")
    else:
        print("image:     <not configured>")
    print(f"env_file:  {cfg['env_file']}")
    print(f"nginx:     {cfg['nginx_conf']}")


@task(help={"target": "target name: cn or sg"})
def preflight(ctx, target="cn"):
    """Check SSH connectivity and OS basics without requiring app setup."""
    cfg = _config(target)
    key = str(cfg.get("key") or "")
    if key and not Path(key).exists():
        raise FileNotFoundError(f"SSH key not found: {key}")
    c = _connect(cfg)
    _run(c, "hostnamectl || uname -a")
    _run(c, "id && pwd && df -h / | awk 'NR==1 || NR==2'")
    _run(c, "command -v apt-get || command -v dnf || true")


@task(help={"target": "target name: cn or sg"})
def bootstrap_system(ctx, target="sg"):
    """Upload prod scripts and run 01-setup-system.sh on a fresh server."""
    cfg = _config(target)
    c = _connect(cfg)
    scripts_dir = Path(__file__).resolve().parent / "scripts" / "prod"
    if not scripts_dir.is_dir():
        raise FileNotFoundError(f"prod scripts not found: {scripts_dir}")

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "prod-scripts.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(archive), "-C", str(scripts_dir.parent), "prod"],
            check=True,
        )
        remote_archive = "/tmp/fy-api-prod-scripts.tar.gz"
        c.put(str(archive), remote_archive)

    _run(
        c,
        " && ".join(
            [
                "rm -rf /root/prod",
                "tar -xzf /tmp/fy-api-prod-scripts.tar.gz -C /root",
                "chmod +x /root/prod/*.sh",
                "/root/prod/01-setup-system.sh",
            ]
        ),
    )


@task(help={"target": "target name: cn or sg"})
def check(ctx, target="cn"):
    """Check remote prerequisites for deployed Fy-api service."""
    cfg = _config(target)
    key = str(cfg.get("key") or "")
    if key and not Path(key).exists():
        raise FileNotFoundError(f"SSH key not found: {key}")

    c = _connect(cfg)
    _run(c, "command -v git && command -v podman && command -v curl && command -v flock")
    _run(c, f"test -f {_q(str(cfg['env_file']))} && test -f {_q(str(cfg['nginx_conf']))}")
    _run(c, "podman info >/dev/null")
    _run(c, f"test -d {_q(str(cfg['app_dir']))}")


@task(help={"target": "target name: cn or sg", "ref": "git ref to checkout"})
def sync_code(ctx, target="cn", ref=DEFAULT_REF):
    """Fetch and checkout code on the server source directory."""
    cfg = _config(target)
    c = _connect(cfg)
    _ensure_source_checkout(c, cfg)
    _checkout_ref(c, cfg, ref)


@task(
    help={
        "target": "target name: cn or sg",
        "tag": "image tag to build, e.g. v0.9.8",
        "ref": "git ref to checkout before build; defaults to the same value as tag",
        "pull": "pass --pull to podman build",
        "no_cache": "pass --no-cache to podman build",
    }
)
def build(ctx, target="cn", tag="", ref="", pull=True, no_cache=False):
    """Build the Fy-api image on the server."""
    tag = _validate_arg("tag", tag)
    ref = ref or tag
    cfg = _config(target)
    c = _connect(cfg)
    _ensure_source_checkout(c, cfg)
    _checkout_ref(c, cfg, ref)

    flags = []
    if pull:
        flags.append("--pull")
    if no_cache:
        flags.append("--no-cache")

    image = _q(_image(cfg, tag))
    flag_str = " ".join(flags)
    build_dir = _q(str(cfg["build_dir"]))
    src_dir = _q(str(cfg["src_dir"]))
    _run(
        c,
        " && ".join(
            [
                f"rm -rf {build_dir}",
                f"mkdir -p {build_dir}",
                f"cd {src_dir}",
                f"git archive --format=tar HEAD | tar -x -C {build_dir}",
                f"cd {build_dir}",
                f"podman build {flag_str} -t {image} .",
                f"rm -rf {build_dir}",
            ]
        ),
    )


@task(help={"target": "target name: cn or sg", "tag": "image tag to push to ACR"})
def push_image(ctx, target="cn", tag=""):
    """Push a previously built image from the server to ACR."""
    cfg = _config(target)
    c = _connect(cfg)
    _run(c, f"podman push {_q(_image(cfg, tag))}")


@task(help={"target": "target name: cn or sg", "tag": "image tag already present in ACR"})
def deploy(ctx, target="cn", tag=""):
    """Deploy an ACR image with the existing blue-green script."""
    tag = _validate_arg("tag", tag)
    cfg = _config(target)
    c = _connect(cfg)
    deploy_dir = f"{cfg['src_dir']}/scripts/prod"
    has_registry = bool(cfg.get("registry") and cfg.get("namespace"))
    image = _image(cfg, tag)
    env_parts = [
        f"IMAGE={_q(image)}",
        f"REPO={_q(str(cfg['repo']))}",
    ]
    if not has_registry:
        env_parts.append("SKIP_PULL=1")
    if cfg.get("nginx_conf"):
        env_parts.append(f"NGINX_CONF={_q(str(cfg['nginx_conf']))}")
    if cfg.get("mem"):
        env_parts.append(f"MEM={_q(str(cfg['mem']))}")
    if cfg.get("cpus"):
        env_parts.append(f"CPUS={_q(str(cfg['cpus']))}")
    deploy_cmd = (
        f"cd {_q(deploy_dir)} && "
        + " ".join(env_parts)
        + f" ./06-deploy-blue-green.sh {_q(tag)}"
    )
    _run(c, f"flock {_q(DEPLOY_LOCK)} -c {_q(deploy_cmd)}")


@task(
    help={
        "target": "target name: cn or sg",
        "tag": "image tag to build/push/deploy, e.g. v0.9.8",
        "ref": "git ref to checkout; defaults to the same value as tag",
        "skip_build": "skip build step",
        "skip_push": "skip ACR push step",
        "pull": "pass --pull to podman build (default True)",
    }
)
def release(ctx, target="cn", tag="", ref="", skip_build=False, skip_push=False, pull=True):
    """Full release: checkout, build, push to ACR, blue-green deploy, health check."""
    tag = _validate_arg("tag", tag)
    ref = ref or tag
    cfg = _config(target)
    has_registry = bool(cfg.get("registry") and cfg.get("namespace"))
    if not skip_build:
        build(ctx, target=target, tag=tag, ref=ref, pull=pull)
    if not skip_push and has_registry:
        push_image(ctx, target=target, tag=tag)
    deploy(ctx, target=target, tag=tag)
    health(ctx, target=target)


@task(help={"target": "target name: cn or sg", "tag": "older image tag to deploy"})
def rollback(ctx, target="cn", tag=""):
    """Rollback by deploying an older ACR image tag."""
    deploy(ctx, target=target, tag=tag)
    health(ctx, target=target)


@task(help={"target": "target name: cn or sg"})
def status(ctx, target="cn"):
    """Show git ref, containers, nginx status, and disk usage."""
    cfg = _config(target)
    c = _connect(cfg)
    _run(c, f"cd {_q(str(cfg['src_dir']))} && git log -1 --oneline", warn=True)
    _run(c, "podman ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'NAMES|fy-api' || true", warn=True)
    _run(c, "systemctl is-active nginx || true", warn=True)
    _run(c, f"df -h {_q(str(cfg['app_dir']))} /var/log/nginx 2>/dev/null | awk 'NR>1'", warn=True)


@task(help={"target": "target name: cn or sg"})
def health(ctx, target="cn"):
    """Check the active local blue/green API status endpoint."""
    cfg = _config(target)
    c = _connect(cfg)
    _run(
        c,
        "curl -fsS http://127.0.0.1:3001/api/status || "
        "curl -fsS http://127.0.0.1:3002/api/status",
        warn=True,
    )


@task(help={"target": "target name: cn or sg", "tail": "number of container log lines"})
def logs(ctx, target="cn", tail=100):
    """Show logs from the active Fy-api blue/green container."""
    cfg = _config(target)
    c = _connect(cfg)
    tail = int(tail)
    _run(
        c,
        "ACTIVE=$(podman ps --format '{{.Names}}' | grep -E '^fy-api-(blue|green)$' | head -1); "
        f"test -n \"$ACTIVE\" && podman logs --tail {tail} \"$ACTIVE\"",
        warn=True,
    )

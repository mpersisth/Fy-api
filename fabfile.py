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
    fab check
    fab release --tag=v0.9.8 --ref=origin/main
    fab release --tag=v0.9.8              # if v0.9.8 is also a git tag
    fab deploy --tag=v0.9.8              # deploy an image already in ACR
    fab rollback --tag=v0.9.7
    fab status
    fab logs --tail=200

Defaults match:
    ssh -i ~/.ssh/tracenex_XN.pem -p 58422 root@8.136.146.211

Override with environment variables when needed:
    FYAPI_HOST, FYAPI_PORT, FYAPI_USER, FYAPI_KEY
    FYAPI_REPO_URL, FYAPI_SRC_DIR
    FYAPI_REGISTRY, FYAPI_NAMESPACE, FYAPI_REPO
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from fabric import Connection, task

SSH_HOST = os.getenv("FYAPI_HOST", "8.136.146.211")
SSH_PORT = int(os.getenv("FYAPI_PORT", "58422"))
SSH_USER = os.getenv("FYAPI_USER", "root")
SSH_KEY = os.path.expanduser(os.getenv("FYAPI_KEY", "~/.ssh/tracenex_XN.pem"))

APP_DIR = os.getenv("FYAPI_APP_DIR", "/opt/fy-api")
SRC_DIR = os.getenv("FYAPI_SRC_DIR", f"{APP_DIR}/src")
ENV_FILE = os.getenv("FYAPI_ENV_FILE", f"{APP_DIR}/config/fy-api.env")
NGINX_CONF = os.getenv("FYAPI_NGINX_CONF", "/etc/nginx/conf.d/fy-api.conf")
REPO_URL = os.getenv("FYAPI_REPO_URL", "git@github.com:seraph0017/Fy-api.git")
DEFAULT_REF = os.getenv("FYAPI_DEFAULT_REF", "origin/main")

ACR_REGISTRY = os.getenv("FYAPI_REGISTRY", "registry-vpc.cn-hangzhou.aliyuncs.com")
ACR_NAMESPACE = os.getenv("FYAPI_NAMESPACE", "fy-api")
ACR_REPO = os.getenv("FYAPI_REPO", "fy-api")

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


def _image(tag: str) -> str:
    tag = _validate_arg("tag", tag)
    return f"{ACR_REGISTRY}/{ACR_NAMESPACE}/{ACR_REPO}:{tag}"


def _connect() -> Connection:
    connect_kwargs = {}
    if SSH_KEY:
        connect_kwargs["key_filename"] = SSH_KEY
    return Connection(
        host=SSH_HOST,
        user=SSH_USER,
        port=SSH_PORT,
        connect_kwargs=connect_kwargs,
    )


def _run(c: Connection, command: str, *, warn: bool = False, hide: bool = False):
    if not hide:
        print(f"[{c.user}@{c.host}:{c.port}] $ {command}")
    return c.run(command, warn=warn, hide=hide, pty=False)


def _ensure_source_checkout(c: Connection):
    parent = _q(str(Path(SRC_DIR).parent))
    src = _q(SRC_DIR)
    repo = _q(REPO_URL)
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


def _checkout_ref(c: Connection, ref: str):
    ref = _validate_arg("ref", ref)
    src = _q(SRC_DIR)
    quoted_ref = _q(ref)
    _run(
        c,
        " && ".join(
            [
                f"cd {src}",
                "git fetch origin --tags --prune",
                f"git checkout -f {quoted_ref}",
                f"git reset --hard {quoted_ref}",
                "git clean -fdx",
                "git rev-parse --short HEAD",
            ]
        ),
    )


@task
def info(ctx):
    """Print local Fabric deployment configuration."""
    print(f"host:      {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    print(f"key:       {SSH_KEY}")
    print(f"src:       {SRC_DIR}")
    print(f"repo_url:  {REPO_URL}")
    print(f"image:     {ACR_REGISTRY}/{ACR_NAMESPACE}/{ACR_REPO}:<tag>")
    print(f"env_file:  {ENV_FILE}")
    print(f"nginx:     {NGINX_CONF}")


@task
def check(ctx):
    """Check local SSH key and remote prerequisites."""
    if SSH_KEY and not Path(SSH_KEY).exists():
        raise FileNotFoundError(f"SSH key not found: {SSH_KEY}")

    c = _connect()
    _run(c, "command -v git && command -v podman && command -v curl && command -v flock")
    _run(c, f"test -f {_q(ENV_FILE)} && test -f {_q(NGINX_CONF)}")
    _run(c, "podman info >/dev/null")
    _run(c, f"test -d {_q(APP_DIR)}")


@task(help={"ref": "git ref to checkout on server, default: origin/main"})
def sync_code(ctx, ref=DEFAULT_REF):
    """Fetch and checkout code on the server build directory."""
    c = _connect()
    _ensure_source_checkout(c)
    _checkout_ref(c, ref)


@task(
    help={
        "tag": "image tag to build, e.g. v0.9.8",
        "ref": "git ref to checkout before build; defaults to the same value as tag",
        "pull": "pass --pull to podman build",
        "no_cache": "pass --no-cache to podman build",
    }
)
def build(ctx, tag, ref="", pull=True, no_cache=False):
    """Build the Fy-api image on the server."""
    tag = _validate_arg("tag", tag)
    ref = ref or tag

    c = _connect()
    _ensure_source_checkout(c)
    _checkout_ref(c, ref)

    flags = []
    if pull:
        flags.append("--pull")
    if no_cache:
        flags.append("--no-cache")

    image = _q(_image(tag))
    flag_str = " ".join(flags)
    _run(c, f"cd {_q(SRC_DIR)} && podman build {flag_str} -t {image} .")


@task(help={"tag": "image tag to push to ACR"})
def push_image(ctx, tag):
    """Push a previously built image from the server to intranet ACR."""
    c = _connect()
    _run(c, f"podman push {_q(_image(tag))}")


@task(help={"tag": "image tag already present in ACR"})
def deploy(ctx, tag):
    """Deploy an ACR image with the existing blue-green script."""
    tag = _validate_arg("tag", tag)
    c = _connect()
    deploy_dir = f"{SRC_DIR}/scripts/prod"
    deploy_cmd = (
        f"cd {_q(deploy_dir)} && "
        f"REGISTRY={_q(ACR_REGISTRY)} "
        f"NAMESPACE={_q(ACR_NAMESPACE)} "
        f"REPO={_q(ACR_REPO)} "
        f"./06-deploy-blue-green.sh {_q(tag)}"
    )
    _run(c, f"flock {_q(DEPLOY_LOCK)} -c {_q(deploy_cmd)}")


@task(
    help={
        "tag": "image tag to build/push/deploy, e.g. v0.9.8",
        "ref": "git ref to checkout; defaults to the same value as tag",
        "skip_build": "skip build step",
        "skip_push": "skip ACR push step",
    }
)
def release(ctx, tag, ref="", skip_build=False, skip_push=False):
    """Full release: checkout, build, push to ACR, blue-green deploy, health check."""
    tag = _validate_arg("tag", tag)
    ref = ref or tag

    if not skip_build:
        build(ctx, tag=tag, ref=ref)
    if not skip_push:
        push_image(ctx, tag=tag)
    deploy(ctx, tag=tag)
    health(ctx)


@task(help={"tag": "older image tag to deploy"})
def rollback(ctx, tag):
    """Rollback by deploying an older ACR image tag."""
    deploy(ctx, tag=tag)
    health(ctx)


@task
def status(ctx):
    """Show git ref, containers, nginx status, and disk usage."""
    c = _connect()
    _run(c, f"cd {_q(SRC_DIR)} && git log -1 --oneline", warn=True)
    _run(c, "podman ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'NAMES|fy-api' || true", warn=True)
    _run(c, "systemctl is-active nginx || true", warn=True)
    _run(c, f"df -h {_q(APP_DIR)} /var/log/nginx 2>/dev/null | awk 'NR>1'", warn=True)


@task
def health(ctx):
    """Check the active local blue/green API status endpoint."""
    c = _connect()
    _run(
        c,
        "curl -fsS http://127.0.0.1:3001/api/status || "
        "curl -fsS http://127.0.0.1:3002/api/status",
    )


@task(help={"tail": "number of container log lines"})
def logs(ctx, tail=100):
    """Show logs from the active Fy-api blue/green container."""
    c = _connect()
    tail = int(tail)
    _run(
        c,
        "ACTIVE=$(podman ps --format '{{.Names}}' | grep -E '^fy-api-(blue|green)$' | head -1); "
        f"test -n \"$ACTIVE\" && podman logs --tail {tail} \"$ACTIVE\"",
        warn=True,
    )

# Operations scripts

Two related tools live here:

1. **`sync_config.py`** — Single-direction config sync (CN Fy-api RDS → SG Fy-api via HTTP). See [`README-sync.md`](./README.md) above.
2. **`fabfile.py`** — Fabric tasks for operating the entire fleet from your laptop over SSH.

## Fabric quick start

### Install

```bash
# On your laptop (not on the servers)
pip install fabric
# or with pipx for an isolated tool install
pipx install fabric
```

### Prerequisites: SSH config

The fabfile uses host aliases, not IPs. Put these in your `~/.ssh/config`:

```ssh-config
Host tracenex-cn
    HostName 8.136.146.211
    User root
    IdentityFile ~/.ssh/tracenex_cn.pem

Host tracenex-sg
    HostName <your-sg-eip>
    User root
    IdentityFile ~/.ssh/tracenex_sg.pem

# When you add a new region, add here and update fabfile.py HOSTS
Host tracenex-us
    HostName <future-us-eip>
    User root
    IdentityFile ~/.ssh/tracenex_us.pem
```

Verify: `ssh tracenex-cn uptime` should work without password.

### List available tasks

```bash
cd scripts/ops
fab -l
```

### Daily operations

```bash
# Snapshot health of both regions
fab status

# Just CN
fab -H tracenex-cn status

# Just SG
fab -H tracenex-sg status

# Show last 100 lines of Fy-api container log on all hosts
fab logs

# 200 lines on CN only, filter for errors
fab -H tracenex-cn logs --tail=200 --grep='ERR|panic'

# Show recent [ERR] summary on both hosts (deduped)
fab errors

# Container stats (CPU/MEM)
fab stats

# TLS cert expiry
fab certs
```

### Deploy (blue-green, zero downtime)

```bash
# Deploy v0.9.7 to both CN and SG (default)
fab deploy --tag=v0.9.7

# Deploy to CN only (test first, then SG)
fab deploy --tag=v0.9.7 --only=cn

# After CN looks good for 24h, roll to SG
fab deploy --tag=v0.9.7 --only=sg

# Emergency rollback
fab rollback --tag=v0.9.6-tracenex --only=sg

# Pre-warm: pull image without switching
fab pull --tag=v0.9.7
```

### Config sync (CN → SG)

```bash
# Runs the sync script on CN, which posts to SG's /api/internal/sync/*
fab sync-config
```

### Cert renewal

```bash
fab certs                  # check expiry
fab cert-renew             # force renew + nginx reload
```

### Nginx

```bash
fab restart-nginx          # reload (no downtime)
fab -H tracenex-cn nginx-access --tail=500 --grep='429|500'
fab nginx-error --tail=100
```

### Dangerous actions

```bash
# Restart Fy-api container — causes 5-15 second outage!
# Prefer `fab deploy` (blue-green) instead.
fab restart-fyapi --only=cn
```

## Adding a new host (e.g. US / EU)

1. Provision the box, run `scripts/prod/01-07` just like you did for CN/SG
2. Add SSH alias to `~/.ssh/config`
3. Add entry to `HOSTS` dict at top of `fabfile.py`
4. Verify: `fab -H tracenex-us status`

## Why Fabric and not Ansible?

- **Fabric**: Python, one file, 200 lines, pip install, great for < 10 hosts doing SSH commands. That's you.
- **Ansible**: YAML, inventory, playbooks, roles. Overkill at your scale. Consider it at 50+ hosts or when you need declarative state.

## Safety rails built in

- Every command prints `[hostname] $ cmd` prefix so multi-host output is disambiguated
- `deploy` requires `--tag`, no accidental "deploy latest"
- `dbq` explicitly warns against non-SELECT queries
- `restart-fyapi` is clearly marked as causing downtime
- `rollback` is an alias for `deploy` with clearer naming for on-call

## TODO ideas (not built yet)

- `fab backup-db` — snapshot RDS via Aliyun CLI before risky deploys
- `fab slack-notify` — post deploy status to a webhook
- `fab drift-check` — compare channels table between CN and SG to spot sync gaps
- `fab loadtest` — trigger a canary load against a host

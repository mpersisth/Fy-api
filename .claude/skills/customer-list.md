---
name: customer-list
description: Use when the user asks for TraceNex/Fy-api customer lists, client rosters, user quota reports, discount breakdowns, model usage per customer, or channel/supplier mappings per customer. Covers CN and SG production environments.
---

# Customer List Report

## Purpose

Generate a consolidated customer list from CN and SG production environments, showing each external customer's quota usage, model consumption, discount configuration (GroupRatio + GroupGroupRatio), and supplier channel mappings. Output as CSV with one row per customer.

## Internal Accounts (Exclude)

The following accounts are internal and MUST be excluded from reports:

- dongbing
- seraph0017
- TJZ
- Ai短剧平台
- wt
- 刘畅
- AI工具箱

Also exclude:
- Accounts with `role = 100` (superadmin)
- Test accounts (test1–test7, yang, etc.) unless they have meaningful external usage

## SSH Access

| Target | SSH Command |
|--------|-------------|
| CN | `ssh -i ~/.ssh/tracenex_XN.pem -p 58422 root@8.136.146.211` |
| SG | `ssh -i ~/.ssh/AI_tracenex.pem -p 58422 root@47.236.133.70` |

DB credentials are in `/opt/fy-api/config/fy-api.env` as `SQL_DSN` (Go GORM format: `user:pass@tcp(host:port)/db?params`).

## Workflow

### 1. SSH to each environment and parse DB credentials

```bash
DSN=$(grep -E "^SQL_DSN=" /opt/fy-api/config/fy-api.env | sed 's/SQL_DSN=//' | tr -d '"' | tr -d "'")
MYSQL_USER=$(echo "$DSN" | sed -E 's/^([^:]+):.*$/\1/')
MYSQL_PASS=$(echo "$DSN" | sed -E 's/^[^:]+:([^@]+)@.*$/\1/')
MYSQL_HOST=$(echo "$DSN" | sed -E 's/.*@tcp\(([^:)]+).*/\1/')
MYSQL_PORT=$(echo "$DSN" | sed -E 's/.*@tcp\([^:]+:([0-9]+)\).*/\1/')
MYSQL_DB=$(echo "$DSN" | sed -E 's/.*\)\/([^?]+).*/\1/')
```

Never print DSN credentials in chat or logs.

### 2. Query customer data (one row per user)

```sql
SELECT
  u.id,
  u.username,
  IFNULL(u.display_name, '') as display_name,
  u.`group`,
  u.quota,
  u.used_quota,
  ROUND(u.used_quota / 500000, 2) as used_usd,
  ROUND(u.quota / 500000, 2) as total_usd,
  IFNULL(GROUP_CONCAT(DISTINCT l.model_name ORDER BY l.model_name SEPARATOR ' | '), '') as models_used,
  IFNULL(GROUP_CONCAT(DISTINCT CONCAT(c.name, '(', c.id, ')') ORDER BY c.name SEPARATOR ' | '), '') as channels_used
FROM users u
LEFT JOIN logs l ON l.user_id = u.id AND l.type = 2
LEFT JOIN channels c ON l.channel_id = c.id
WHERE u.status = 1 AND u.role != 100
GROUP BY u.id
ORDER BY u.used_quota DESC;
```

### 3. Query discount configuration

```sql
SELECT `key`, value FROM options WHERE `key` IN ('GroupRatio', 'GroupGroupRatio');
```

This returns two JSON structures:
- **GroupRatio**: `{ "groupName": ratio }` — flat multiplier applied to ALL models for users in that group
- **GroupGroupRatio**: `{ "userGroup": { "modelGroup": ratio } }` — per-model-series multiplier, stacked on top of GroupRatio

Final billing formula: `模型定价 × GroupRatio × GroupGroupRatio`

### 4. Match discounts to customers

For each customer:
1. Look up their `group` field
2. Find their GroupRatio (default 1.0 if not listed)
3. Find their GroupGroupRatio entries (per model series)
4. Format as semicolon-separated: `ModelSeries→ratio; ModelSeries→ratio`
5. If GroupRatio ≠ 1.0, prefix with: `组基准GroupRatio→{value}(全模型{value*100}折)`

### 5. Filter and output

Exclude internal accounts and test accounts per the exclusion list above.

### 6. Write CSV

Output path: `docs/customer-list-{YYYY-MM-DD}.csv`

CSV columns:
```
环境,客户ID,客户名称,分组,已用额度(USD),总额度(USD),折扣配置,使用模型,供应商渠道
```

Rules:
- Both environments in one file, distinguished by `环境` column (CN/SG)
- One row per customer
- Discounts: semicolons between model series (e.g. `Claude→0.75; GPT→0.7; Kimi→0.5`)
- Models separated by ` | `
- Channels separated by ` | `
- Wrap fields containing commas/pipes in double quotes
- Sort by used_usd descending within each environment

## Common Pitfalls

- SSH sandbox: if first attempt fails, request permission escalation and retry
- Never print DB credentials in chat
- `used_quota > quota` is valid (overdraft accounts); do not filter these out
- Some accounts have logs but models_used is empty (purged logs); note this
- GroupGroupRatio keys are model-group names (e.g. "Claude", "gpt", "Gemini"), not individual model names
- A user's group field must exactly match a key in GroupGroupRatio for the discount to apply
- Quota conversion: raw integer ÷ 500,000 = USD

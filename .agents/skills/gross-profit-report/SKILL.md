---
name: gross-profit-report
description: Use when the user asks for TraceNex/Fy-api daily spend, consumption, cost, revenue, gross profit, gross margin, channel/user/model breakdowns, or yesterday/today CN/SG production billing reports with screenshot-friendly output.
---

# Gross Profit Report

## Purpose

Produce a screenshot-friendly TraceNex consumption/gross-profit report for CN/SG across date ranges, especially "yesterday and today", with channel + user + model in one table.

## Agent Support

- **Claude Code**: use `Bash`, `Read`, `Edit`, and `git` commands. If the user asks for console output, paste the important report into the chat because users may not see tool stdout.
- **Codex**: use `exec_command`, `apply_patch`, and `git` commands. Do not assume the user sees command output; relay the report in the final/chat response.
- **Discovery/install paths**: the repo copy at `.agents/skills/gross-profit-report` is project-local. For global availability, keep synchronized copies at `~/.codex/skills/gross-profit-report` for Codex and `~/.claude/skills/gross-profit-report` for Claude Code.

## Workflow

1. **Confirm report scope**
   - The default daily window is **yesterday 17:00 → today 17:00 (Asia/Shanghai)**. This aligns with the operational "billing day" used by the team.
   - Example: running on 2026-06-04, the default range is `2026-06-03 17:00:00+08:00` through `2026-06-04 16:59:59+08:00`.
   - Convert the Asia/Shanghai boundaries to exact Unix timestamps before querying. Do not let a UTC default silently shift the window.
   - Confirm the requested environments when explicit; otherwise daily production reports usually cover `cn` and `sg`.
   - Use the combined detail grain unless the user asks otherwise: `日期 / 环境 / 渠道 / 用户 / 模型`.

2. **Read repository context**
   - Read `OVERLAY.md` before changes.
   - Check `git status --short --branch` and preserve unrelated local changes.

3. **Optional: handle report script/PR prerequisites**
   - Only do PR work when the user explicitly asks to merge a report PR, mentions a PR number, or the required script is missing.
   - Inspect a requested PR with `gh pr view <number> --json title,baseRefName,headRefName,files,commits,url`.
   - Check whether the merge commit is already in `HEAD` with `git log --oneline --decorate --max-count=30 --all`.
   - If merging is required, fetch/merge into the requested branch without overwriting unrelated dirty files.

4. **Run the primary report script**
   - Default window: yesterday 17:00 → today 17:00 (Asia/Shanghai). Compute start/end timestamps dynamically on the remote host:
     ```bash
     export TZ=Asia/Shanghai
     START_TS=$(date -d "yesterday 17:00:00" +%s)
     END_TS=$(date -d "today 16:59:59" +%s)
     ```
   - If the user asks for a different date range, convert accordingly and make the exact boundaries visible in the response.

5. **Fallback when local DB connection fails**
   - If PyMySQL direct connection to RDS fails, or the script says it cannot read a DSN / found no data because SSH was sandboxed, rerun with the needed SSH approval and query from each production host over SSH.
   - Use `/opt/fy-api/config/fy-api.env` to read `SQL_DSN` on the remote host. Never print DSN credentials.
   - Prefer remote-host local MySQL execution: SSH to the production host, parse `SQL_DSN` inside the remote shell/Python process, pass the password through `MYSQL_PWD`, run `mysql --batch --raw --skip-column-names`, and return only TSV query results. This avoids local RDS network drops and avoids exposing DB credentials in logs.
   - Query `logs` joined to `channels`, filtered to `logs.type = 2`, `quota > 0`, and the requested date range.
   - Use the same accounting basis:
     - `收入 = logs.quota / 500000`
     - `成本 = logs.quota / group_ratio / 500000`，如果配置了 `channel_costs.yaml`，再乘以渠道/模型成本系数 `cost_factor`
     - `折扣倍率 = SUM(logs.quota) / SUM(logs.quota / group_ratio)`，这是日志 `other.group_ratio` 的聚合有效倍率
     - `毛利 = 收入 - 成本`
     - `毛利率 = 毛利 / 收入 * 100`
   - If `channel_costs.yaml` exists, apply its cost factors; otherwise state that no channel cost factor was applied.

6. **Output format**
   - Use Chinese headers.
   - Put the three required dimensions in one table:
     `日期 / 环境 / 渠道 / 用户 / 模型 / 请求数 / 输入Tokens / 输出Tokens / 收入 / 成本 / 毛利 / 毛利率`
   - CSV output from `scripts/ops/gross_profit_report.py` must match the operations table format exactly for `detail.csv`:
     `日期 / 环境 / 用户 / 渠道ID / 渠道 / 模型 / 请求数 / 输入Tokens / 输出Tokens / 折扣倍率 / 收入(USD) / 成本(USD) / 毛利(USD) / 毛利率(%)`.
   - `折扣倍率` is the actual effective user/group ratio from log `other.group_ratio`, aggregated as `SUM(quota) / SUM(quota / group_ratio)`. It is not the channel/model `cost_factor` from `channel_costs.yaml`.
   - Never temporarily hard-code or fallback `折扣倍率` to `1`. If `group_ratio` is missing or invalid, mark the CSV cell as `缺失` and write the issue to `warnings.csv`; do not disguise unknown data as a valid multiplier.
   - Prefer a Markdown table for screenshot readability. Truncate long channel/model labels if needed, but preserve channel ID.
   - Include summary tables only if useful: by day/env, by channel, by user, by model.
   - Do not rely on tool stdout being visible. Paste the final table in the assistant response.

## Useful Query Shape

```sql
SELECT DATE(FROM_UNIXTIME(l.created_at)) AS day,
       l.user_id,
       COALESCE(NULLIF(l.username,''), CONCAT('user-', l.user_id)) AS username,
       l.channel_id,
       COALESCE(NULLIF(c.name,''), CONCAT('ch-', l.channel_id)) AS channel_name,
       l.model_name,
       COUNT(*) AS requests,
       SUM(l.prompt_tokens) AS prompt_tokens,
       SUM(l.completion_tokens) AS completion_tokens,
       SUM(l.quota) AS quota,
       SUM(l.quota / IFNULL(NULLIF(CAST(JSON_UNQUOTE(JSON_EXTRACT(CASE WHEN JSON_VALID(l.other) THEN l.other ELSE NULL END, '$.group_ratio')) AS DECIMAL(20,8)), 0), 1)) AS base_cost_quota,
       SUM(CASE
             WHEN JSON_VALID(l.other)
              AND JSON_EXTRACT(l.other, '$.group_ratio') IS NOT NULL
              AND CAST(JSON_UNQUOTE(JSON_EXTRACT(l.other, '$.group_ratio')) AS DECIMAL(20,8)) > 0
             THEN 0 ELSE 1
           END) AS group_ratio_missing
FROM logs l
LEFT JOIN channels c ON c.id = l.channel_id
WHERE l.type = 2
  AND l.created_at >= <start_unix_ts_for_local_datetime>
  AND l.created_at <= <end_unix_ts_for_local_datetime>
  AND l.quota > 0
GROUP BY day,l.user_id,username,l.channel_id,channel_name,l.model_name
ORDER BY day, quota DESC;
```

## Common Pitfalls

- PR already merged: report the merge commit instead of re-merging.
- Tool stdout invisibility: paste the report into the response.
- Local RDS access failure: use SSH remote MySQL fallback.
- Sandbox can make SSH DSN reads look like "no data"; request escalation and retry before concluding the period is empty.
- The checked-in script may use UTC date parsing; for "yesterday/today" ops reports, explicitly use Asia/Shanghai timestamps if you run fallback SQL.
- Misaligned screenshot tables: use Markdown tables or shortened labels, not raw fixed-width CJK text.
- Timezone drift: avoid UTC-only interpretation for "yesterday/today" unless the user explicitly asks for UTC.
- Default window is 17:00–17:00 Shanghai, NOT midnight–midnight. When the user says "昨天" without further detail, use yesterday 17:00 → today 17:00.
- Never output a CSV where `折扣倍率` is all `1` unless verified from source logs; check the distribution before finalizing.

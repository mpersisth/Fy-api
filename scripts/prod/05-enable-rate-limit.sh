#!/bin/bash
# scripts/prod/05-enable-rate-limit.sh
# 打开 Model Request RateLimit(热更新,不重启容器)+ 配置分组限流
#
# 前置: 已经首次 setup 过 fyadmin 管理员账号
#
# 用法:
#   ADMIN_USER=fyadmin \
#   ADMIN_PASS='your-admin-password' \
#   API_BASE=http://127.0.0.1:3001 \
#   ./05-enable-rate-limit.sh

set -euo pipefail

log() { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
err() { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

API_BASE="${API_BASE:-http://127.0.0.1:3001}"
ADMIN_USER="${ADMIN_USER:-}"
ADMIN_PASS="${ADMIN_PASS:-}"

[ -n "$ADMIN_USER" ] || err "请设置 ADMIN_USER"
[ -n "$ADMIN_PASS" ] || err "请设置 ADMIN_PASS"

COOKIE=/tmp/fy-admin.cookie
trap 'rm -f "$COOKIE"' EXIT

# ─────────────────────────────────────────────────────────
# 1) 登录拿 session
# ─────────────────────────────────────────────────────────
log "登录管理员 $ADMIN_USER ..."
LOGIN_RESP=$(curl -sSf -c "$COOKIE" -X POST "$API_BASE/api/user/login" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg u "$ADMIN_USER" --arg p "$ADMIN_PASS" '{username:$u,password:$p}')")

echo "$LOGIN_RESP" | jq -e '.success == true' >/dev/null \
  || { echo "$LOGIN_RESP" | jq .; err "登录失败"; }
log "登录成功"

# ─────────────────────────────────────────────────────────
# 2) 辅助函数:改一个 option
# ─────────────────────────────────────────────────────────
set_option() {
  local key="$1"
  local value="$2"
  log "设置 $key = $value"
  curl -sSf -b "$COOKIE" -X PUT "$API_BASE/api/option/" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg k "$key" --arg v "$value" '{key:$k,value:$v}')" \
    | jq -e '.success == true' >/dev/null \
    || err "设置 $key 失败"
}

# ─────────────────────────────────────────────────────────
# 3) 打开模型请求限流(MRRL)
# ─────────────────────────────────────────────────────────
log "=== 打开 Model Request RateLimit ==="
set_option "ModelRequestRateLimitEnabled"         "true"
set_option "ModelRequestRateLimitDurationMinutes" "1"
set_option "ModelRequestRateLimitCount"           "0"       # 总次数,0=不限
set_option "ModelRequestRateLimitSuccessCount"    "1000"    # 每用户默认 1000/min

# ─────────────────────────────────────────────────────────
# 4) 分组配额(核心 — 按客户限流的入口)
# ─────────────────────────────────────────────────────────
log "=== 配置分组限流 ==="
# 格式: {"分组名": [总次数上限, 成功次数上限]}
# 总次数 = 0 表示"总次数不限,只看成功次数"
GROUP_JSON=$(cat <<'EOF'
{
  "default":       [0, 60],
  "trial":         [120, 30],
  "customer_acme": [0, 2000],
  "vip":           [0, 5000],
  "internal":      [0, 0]
}
EOF
)

set_option "ModelRequestRateLimitGroup" "$(echo "$GROUP_JSON" | jq -c .)"

# ─────────────────────────────────────────────────────────
# 5) 查询当前状态
# ─────────────────────────────────────────────────────────
log "=== 当前限流配置 ==="
curl -sSf -b "$COOKIE" "$API_BASE/api/option/" \
  | jq -r '.data[] | select(.key | startswith("ModelRequestRateLimit")) | "\(.key)=\(.value)"'

cat <<MSG

═══════════════════════════════════════════════════════════════
限流已打开 ✅ (不需要重启容器,下一个请求就生效)

分组配额:
  default       [0, 60]      — 默认用户每分钟 60 次成功
  trial         [120, 30]    — 试用用户:总 120 / 成功 30
  customer_acme [0, 2000]    — 客户 ACME 每分钟 2000 次
  vip           [0, 5000]    — VIP 客户每分钟 5000 次
  internal      [0, 0]       — 内部用户不限(0/0 视为不限)

把客户绑到分组:
  1. 后台 → 用户管理 → 编辑 → 分组字段
  2. 或 SQL: UPDATE users SET \`group\`='customer_acme' WHERE email='x@acme.com';

Token 级别二级切分:
  后台 → 令牌管理 → 编辑 → 分组字段(覆盖用户分组)

撞限流测试:
  for i in {1..100}; do
    curl -s -o /dev/null -w "%{http_code} " \\
      -X POST $API_BASE/v1/chat/completions \\
      -H "Authorization: Bearer <token>" \\
      -H "Content-Type: application/json" \\
      -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
  done
  # 超过 60 次后会看到 429

Redis 里看某用户(userId=42)当前窗口用量:
  redis-cli -h <host> -a <pass> LLEN  rateLimit:MRRLS:42
  redis-cli -h <host> -a <pass> TTL   rateLimit:MRRLS:42

详细见 docs/deploy/rate-limiting.md
═══════════════════════════════════════════════════════════════
MSG

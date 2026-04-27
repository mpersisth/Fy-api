#!/bin/bash
# scripts/prod/07-setup-logrotate.sh
# 一键安装/刷新所有 Fy-api 相关 logrotate 规则
#
# 幂等:覆盖已有规则,重复跑无副作用
#
# 涵盖:
#   ① /etc/logrotate.d/fy-api-nginx  — Nginx 主域 + 别名域访问/错误日志
#   ② /etc/logrotate.d/fy-api        — Fy-api 容器落盘日志 (/opt/fy-api/logs/)
#
# 用法: sudo ./07-setup-logrotate.sh

set -euo pipefail

log()  { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
warn() { printf "\033[33m[warn]\033[0m %s\n" "$*"; }
err()  { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "请用 root 或 sudo 执行"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_NGINX="$SCRIPT_DIR/logrotate/fy-api-nginx"
SRC_FYAPI="$SCRIPT_DIR/logrotate/fy-api"

[ -f "$SRC_NGINX" ] || err "缺少 $SRC_NGINX,请 git pull 或检查 scripts/prod/logrotate/"
[ -f "$SRC_FYAPI" ] || err "缺少 $SRC_FYAPI,请 git pull 或检查 scripts/prod/logrotate/"

# ─────────────────────────────────────────────────────────
# 1) 部署规则文件
# ─────────────────────────────────────────────────────────
log "安装 /etc/logrotate.d/fy-api-nginx ..."
install -m 0644 -o root -g root "$SRC_NGINX" /etc/logrotate.d/fy-api-nginx

log "安装 /etc/logrotate.d/fy-api ..."
install -m 0644 -o root -g root "$SRC_FYAPI" /etc/logrotate.d/fy-api

# ─────────────────────────────────────────────────────────
# 2) 语法校验(logrotate -d 是 dry-run)
# ─────────────────────────────────────────────────────────
log "校验 logrotate 规则..."
logrotate -d /etc/logrotate.d/fy-api-nginx > /tmp/lr-nginx.log 2>&1 || {
  cat /tmp/lr-nginx.log
  err "fy-api-nginx 规则校验失败"
}
logrotate -d /etc/logrotate.d/fy-api > /tmp/lr-fyapi.log 2>&1 || {
  cat /tmp/lr-fyapi.log
  err "fy-api 规则校验失败"
}

# ─────────────────────────────────────────────────────────
# 3) 确保 logrotate 定时任务在跑
# ─────────────────────────────────────────────────────────
if systemctl list-timers logrotate.timer --no-pager 2>/dev/null | grep -q logrotate; then
  log "logrotate.timer 正常(systemd)"
elif [ -x /etc/cron.daily/logrotate ]; then
  log "cron.daily/logrotate 正常"
else
  warn "未检测到 logrotate 定时任务!手动启用:"
  warn "  systemctl enable --now logrotate.timer"
  warn "或检查 /etc/cron.daily/logrotate 是否存在且可执行"
fi

# ─────────────────────────────────────────────────────────
# 4) 打印当前日志状态
# ─────────────────────────────────────────────────────────
log "当前日志占用:"
echo "--- Nginx ---"
du -h /var/log/nginx/*.log 2>/dev/null | sort -hr | head -20 || echo "  (无日志文件)"
echo "--- Fy-api 容器落盘 ---"
du -h /opt/fy-api/logs/*.log 2>/dev/null | sort -hr | head -20 || echo "  (无日志文件)"

cat <<MSG

═══════════════════════════════════════════════════════════════
Logrotate 配置完成 ✅

Nginx 日志 (/var/log/nginx/):
  匹配: fy-api-*.log, *_tracenex_*.log
  策略: 每天切,保留 14 天,压缩
  信号: USR1 (nginx reopen)

Fy-api 容器日志 (/opt/fy-api/logs/):
  匹配: oneapi-*.log
  策略: 每天切 或 文件 >500MB 就切,保留 14 天,压缩
  模式: copytruncate (Go 进程不会 reopen fd)

验证切割逻辑(强制执行一次,不管日志多大):
  sudo logrotate -f /etc/logrotate.d/fy-api-nginx
  sudo logrotate -f /etc/logrotate.d/fy-api

查看 logrotate 执行历史:
  sudo cat /var/lib/logrotate/status | grep -E 'nginx|fy-api'

调整保留天数(默认 14):
  编辑 scripts/prod/logrotate/{fy-api,fy-api-nginx}
  改 'rotate 14' 成别的数 → 重跑此脚本

Podman 容器 stdout 日志(另一层,已在 06 脚本 docker run 里设置):
  --log-opt max-size=100m --log-opt max-file=5
  → 每个容器最多 500MB 循环,不需要 logrotate 管
═══════════════════════════════════════════════════════════════
MSG

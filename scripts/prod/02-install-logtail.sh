#!/bin/bash
# scripts/prod/02-install-logtail.sh
# 安装阿里云 SLS Logtail,配置机器组标识
#
# 前置:
#   1. 在 SLS 控制台建好 Project(fy-api-prod)和 4 个 Logstore
#   2. 在 SLS 控制台"机器组管理"建机器组,类型选"用户自定义标识"
#
# 用法: sudo REGION=cn-hangzhou MACHINE_ID=fy-api-prod ./02-install-logtail.sh

set -euo pipefail

log() { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
err() { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "请用 root 或 sudo 执行"

REGION="${REGION:-cn-hangzhou}"
MACHINE_ID="${MACHINE_ID:-fy-api-prod}"
USE_VPC="${USE_VPC:-1}"   # 1 = 用 VPC 内网下载安装脚本,省公网流量

# ─────────────────────────────────────────────────────────
# 1) 下载 Logtail 安装脚本
# ─────────────────────────────────────────────────────────
log "从 $REGION 下载 Logtail 安装脚本..."
if [ "$USE_VPC" = "1" ]; then
  URL="https://logtail-release-$REGION.oss-$REGION-internal.aliyuncs.com/linux64/logtail.sh"
else
  URL="https://logtail-release-$REGION.oss-$REGION.aliyuncs.com/linux64/logtail.sh"
fi

cd /tmp
wget -q "$URL" -O logtail.sh
chmod 755 logtail.sh

# ─────────────────────────────────────────────────────────
# 2) 安装
# ─────────────────────────────────────────────────────────
log "执行安装(此步会联网连接 SLS 服务器)..."
if [ "$USE_VPC" = "1" ]; then
  ./logtail.sh install "${REGION}-vpc"
else
  ./logtail.sh install "$REGION"
fi

# ─────────────────────────────────────────────────────────
# 3) 写入机器组自定义标识
# ─────────────────────────────────────────────────────────
log "写入机器组标识: $MACHINE_ID"
mkdir -p /etc/ilogtail
echo "$MACHINE_ID" > /etc/ilogtail/user_defined_id
log "标识已写入 /etc/ilogtail/user_defined_id"

# ─────────────────────────────────────────────────────────
# 4) 重启 Logtail
# ─────────────────────────────────────────────────────────
log "重启 ilogtaild..."
/etc/init.d/ilogtaild restart || systemctl restart ilogtaild || true
sleep 3

# ─────────────────────────────────────────────────────────
# 5) 状态检查
# ─────────────────────────────────────────────────────────
if systemctl is-active --quiet ilogtaild 2>/dev/null || pgrep -f ilogtail >/dev/null; then
  log "Logtail 运行中 ✓"
else
  err "Logtail 未正常启动,检查 /usr/local/ilogtail/ilogtail.LOG"
fi

cat <<MSG

═══════════════════════════════════════════════════════════════
Logtail 安装完成 ✅

机器组标识: $MACHINE_ID

下一步(在 SLS 控制台做):
  1. 机器组管理 → 创建机器组 → 类型选"用户自定义标识"
     → 自定义标识填: $MACHINE_ID
     → 确认"心跳状态"为 OK
  2. 日志接入(4 个 Logstore 各建一条采集配置):

     Logstore            路径                                 模式
     ------------------  -----------------------------------  ------
     fy-api-app          /opt/fy-api/logs/oneapi-*.log        单行+正则
     fy-api-nginx-access /var/log/nginx/fy-api-access.log     Nginx
     fy-api-nginx-error  /var/log/nginx/fy-api-error.log      极简
     fy-api-consume      /opt/fy-api/logs/oneapi-*.log        带 filter 过滤 "record consume log"

     详细正则和 JSON 字段索引见 docs/deploy/prod-podman-single.md §8.4

  3. 把所有采集配置挂到上面的机器组上

排错:
  tail /usr/local/ilogtail/ilogtail.LOG          # Logtail 自身日志
  cat /etc/ilogtail/user_defined_id              # 确认标识
═══════════════════════════════════════════════════════════════
MSG

#!/bin/bash
# scripts/prod/04-deploy-fyapi.sh
# 首次部署 Fy-api(仅 blue 容器)
#
# 前置:
#   1. 已跑过 01-setup-system.sh
#   2. /opt/fy-api/config/fy-api.env 已填写(权限 600)
#   3. 已经 podman login 阿里云 ACR
#
# 用法: IMAGE_TAG=v0.9.5 ./04-deploy-fyapi.sh

set -euo pipefail

log() { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
err() { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

# ─────────────────────────────────────────────────────────
# 参数
# ─────────────────────────────────────────────────────────
IMAGE_TAG="${IMAGE_TAG:-}"
[ -n "$IMAGE_TAG" ] || err "请设置 IMAGE_TAG,例: IMAGE_TAG=v0.9.5"

REGISTRY="${REGISTRY:-registry-vpc.cn-hangzhou.aliyuncs.com}"
IMAGE="$REGISTRY/fy-api/fy-api:$IMAGE_TAG"

ENV_FILE=/opt/fy-api/config/fy-api.env
LOG_DIR=/opt/fy-api/logs
DATA_DIR=/opt/fy-api/data

[ -f "$ENV_FILE" ] || err "配置文件 $ENV_FILE 不存在,请先创建"
[ "$(stat -c '%a' "$ENV_FILE")" = "600" ] || err "$ENV_FILE 权限必须是 600"

# 16c32g ECS 默认值
MEM="${MEM:-22g}"
CPUS="${CPUS:-12}"
CONTAINER="${CONTAINER:-fy-api-blue}"
HOST_PORT="${HOST_PORT:-3001}"

# ─────────────────────────────────────────────────────────
# 1) 拉镜像
# ─────────────────────────────────────────────────────────
log "拉镜像: $IMAGE"
podman pull "$IMAGE"

# ─────────────────────────────────────────────────────────
# 2) 清理残留容器
# ─────────────────────────────────────────────────────────
if podman ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  log "清理旧的 $CONTAINER 容器..."
  podman stop -t 20 "$CONTAINER" 2>/dev/null || true
  podman rm "$CONTAINER"
fi

# ─────────────────────────────────────────────────────────
# 3) 启动
# ─────────────────────────────────────────────────────────
log "启动容器 $CONTAINER (port 127.0.0.1:$HOST_PORT, mem=$MEM, cpus=$CPUS)"
podman run -d --name "$CONTAINER" \
  --restart=unless-stopped \
  -p "127.0.0.1:$HOST_PORT:3000" \
  -v "$LOG_DIR:/app/logs:Z" \
  -v "$DATA_DIR:/data:Z" \
  --env-file "$ENV_FILE" \
  --ulimit nofile=1048576:1048576 \
  --log-driver=k8s-file \
  --log-opt max-size=100m \
  --log-opt max-file=5 \
  --memory="$MEM" --memory-swap="$MEM" \
  --cpus="$CPUS" \
  "$IMAGE" \
  --log-dir=/app/logs

# ─────────────────────────────────────────────────────────
# 4) 健康检查
# ─────────────────────────────────────────────────────────
log "等待容器健康(最多 60 秒)..."
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$HOST_PORT/api/status" 2>/dev/null | grep -q '"success":true'; then
    log "容器健康 ✓"
    break
  fi
  sleep 1
  if [ "$i" -eq 60 ]; then
    log "--- 最近容器日志 ---"
    podman logs --tail 100 "$CONTAINER"
    err "容器未在 60 秒内通过健康检查"
  fi
done

# ─────────────────────────────────────────────────────────
# 5) 日志落盘验证
# ─────────────────────────────────────────────────────────
log "验证日志落盘..."
sleep 3
if ls "$LOG_DIR"/oneapi-*.log >/dev/null 2>&1; then
  log "日志文件已生成: $(ls -t "$LOG_DIR"/oneapi-*.log | head -1)"
else
  log "⚠️ 日志文件未生成,容器内检查:"
  podman exec "$CONTAINER" ls -la /app/logs/ 2>/dev/null || true
fi

# ─────────────────────────────────────────────────────────
# 6) Redis 连通性验证
# ─────────────────────────────────────────────────────────
log "验证 Redis 连通..."
REDIS_URL=$(grep -E '^REDIS_CONN_STRING=' "$ENV_FILE" | cut -d= -f2-)
if [ -n "$REDIS_URL" ]; then
  # 容器里用 Fy-api 自带的 Go 连接,我们这里用临时 redis 容器 ping 一下
  if podman run --rm redis:7-alpine redis-cli -u "$REDIS_URL" PING 2>/dev/null | grep -q PONG; then
    log "Redis 连通 ✓"
  else
    log "⚠️ Redis ping 失败,检查 REDIS_CONN_STRING、VPC 白名单、密码"
  fi
fi

# ─────────────────────────────────────────────────────────
# 7) logrotate for Fy-api 日志
# ─────────────────────────────────────────────────────────
if [ ! -f /etc/logrotate.d/fy-api ]; then
  log "创建 Fy-api 日志轮转规则..."
  cat | sudo tee /etc/logrotate.d/fy-api > /dev/null <<'EOF'
/opt/fy-api/logs/oneapi-*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    maxsize 500M
}
EOF
fi

# ─────────────────────────────────────────────────────────
# 8) 记录发版
# ─────────────────────────────────────────────────────────
echo "$(date -u +%F-%T) deploy $IMAGE_TAG: $CONTAINER on port $HOST_PORT" \
  >> /opt/fy-api/deploy-log.md

cat <<MSG

═══════════════════════════════════════════════════════════════
首次部署完成 ✅

容器: $CONTAINER
镜像: $IMAGE
端口: 127.0.0.1:$HOST_PORT → container :3000
资源: --memory=$MEM --cpus=$CPUS

下一步:
  1. 首次初始化 root 管理员:
       read -s ROOT_PASS
       curl -s -X POST http://127.0.0.1:$HOST_PORT/api/setup \\
         -H "Content-Type: application/json" \\
         -d '{"username":"fyadmin","password":"'"\$ROOT_PASS"'","confirmPassword":"'"\$ROOT_PASS"'","SelfUseModeEnabled":false,"DemoSiteEnabled":false}'

  2. 在后台打开模型请求限流 + 按分组配额:
       ./05-enable-rate-limit.sh

  3. 下次发版使用蓝绿脚本:
       ./06-deploy-blue-green.sh vX.Y.Z

日志命令:
  podman logs -f $CONTAINER
  tail -f $LOG_DIR/oneapi-*.log
═══════════════════════════════════════════════════════════════
MSG

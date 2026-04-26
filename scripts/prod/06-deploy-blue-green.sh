#!/bin/bash
# scripts/prod/06-deploy-blue-green.sh
# 蓝绿零停机发版
#
# 用法: ./06-deploy-blue-green.sh <new-image-tag>
# 示例: ./06-deploy-blue-green.sh v0.9.6

set -euo pipefail

log()  { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
warn() { printf "\033[33m[warn]\033[0m %s\n" "$*"; }
err()  { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

NEW_TAG="${1:-}"
[ -n "$NEW_TAG" ] || err "用法: $0 <new-image-tag>"

REGISTRY="${REGISTRY:-registry-vpc.cn-hangzhou.aliyuncs.com}"
NAMESPACE="${NAMESPACE:-fy-api}"
REPO="${REPO:-fy-api}"
IMAGE="$REGISTRY/$NAMESPACE/$REPO:$NEW_TAG"

ENV_FILE=/opt/fy-api/config/fy-api.env
LOG_DIR=/opt/fy-api/logs
DATA_DIR=/opt/fy-api/data
NGINX_CONF=/etc/nginx/conf.d/fy-api.conf
MEM="${MEM:-22g}"
CPUS="${CPUS:-12}"

[ -f "$ENV_FILE" ]   || err "$ENV_FILE 不存在"
[ -f "$NGINX_CONF" ] || err "$NGINX_CONF 不存在,请先跑 03-setup-nginx.sh"

# ─────────────────────────────────────────────────────────
# 1) 判断当前活跃
# ─────────────────────────────────────────────────────────
if podman ps --format '{{.Names}}' | grep -qx fy-api-blue; then
  CUR=blue;  CUR_PORT=3001
  NEXT=green; NEXT_PORT=3002
elif podman ps --format '{{.Names}}' | grep -qx fy-api-green; then
  CUR=green; CUR_PORT=3002
  NEXT=blue;  NEXT_PORT=3001
else
  warn "没检测到 fy-api-blue 或 fy-api-green,当作首次部署"
  CUR=none;  CUR_PORT=0
  NEXT=blue;  NEXT_PORT=3001
fi

log "当前活跃:$CUR ($CUR_PORT)  →  准备上线:$NEXT ($NEXT_PORT)  tag=$NEW_TAG"

# ─────────────────────────────────────────────────────────
# 2) 拉镜像
# ─────────────────────────────────────────────────────────
log "拉镜像 $IMAGE ..."
podman pull "$IMAGE"

# ─────────────────────────────────────────────────────────
# 3) 启动 NEXT 容器
# ─────────────────────────────────────────────────────────
log "清理可能残留的 fy-api-$NEXT ..."
podman rm -f "fy-api-$NEXT" 2>/dev/null || true

log "启动 fy-api-$NEXT (port=$NEXT_PORT) ..."
podman run -d --name "fy-api-$NEXT" \
  --restart=unless-stopped \
  -p "127.0.0.1:$NEXT_PORT:3000" \
  -v "$LOG_DIR:/app/logs:Z" \
  -v "$DATA_DIR:/data:Z" \
  --env-file "$ENV_FILE" \
  --ulimit nofile=1048576:1048576 \
  --log-driver=k8s-file \
  --log-opt max-size=100m --log-opt max-file=5 \
  --memory="$MEM" --memory-swap="$MEM" \
  --cpus="$CPUS" \
  "$IMAGE" \
  --log-dir=/app/logs

# ─────────────────────────────────────────────────────────
# 4) 健康检查
# ─────────────────────────────────────────────────────────
log "等 fy-api-$NEXT 健康(最多 60 秒)..."
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$NEXT_PORT/api/status" 2>/dev/null \
        | grep -q '"success":true'; then
    log "fy-api-$NEXT 健康 ✓"
    break
  fi
  sleep 1
  if [ "$i" -eq 60 ]; then
    log "--- 失败,最近日志 ---"
    podman logs --tail 80 "fy-api-$NEXT"
    err "fy-api-$NEXT 未就绪 — 已启动但健康检查失败,回滚:podman rm -f fy-api-$NEXT"
  fi
done

# ─────────────────────────────────────────────────────────
# 5) 切 Nginx upstream
# ─────────────────────────────────────────────────────────
if [ "$CUR" != "none" ]; then
  log "切换 Nginx upstream: $CUR_PORT → $NEXT_PORT ..."
  # 精确替换 upstream server 行,不动其他 :端口 出现的地方(健康检查路径等)
  sudo sed -i -E "s|(server\s+127\.0\.0\.1:)$CUR_PORT(\s)|\1$NEXT_PORT\2|" "$NGINX_CONF"
  sudo nginx -t || err "Nginx 配置校验失败,回滚 sed 改动"
  sudo systemctl reload nginx
  log "Nginx 已切到 $NEXT"
else
  log "首次部署,跳过 Nginx 切换(由 03-setup-nginx.sh 负责)"
fi

# ─────────────────────────────────────────────────────────
# 6) 等旧容器连接排空,再停掉
# ─────────────────────────────────────────────────────────
if [ "$CUR" != "none" ]; then
  log "等 30 秒让 fy-api-$CUR 的连接排空..."
  sleep 30

  log "停旧容器 fy-api-$CUR ..."
  podman stop -t 60 "fy-api-$CUR"
  # 先别 rm,留 4 小时观察;cron 或下次发版再清
  log "旧容器已停但未删除,保留用于紧急回滚。回滚:"
  log "  podman start fy-api-$CUR && sudo sed -i 's|$NEXT_PORT|$CUR_PORT|' $NGINX_CONF && sudo nginx -s reload"
fi

# ─────────────────────────────────────────────────────────
# 7) 记录
# ─────────────────────────────────────────────────────────
echo "$(date -u +%F-%T) deploy $NEW_TAG: $CUR -> $NEXT (port $NEXT_PORT)" \
  >> /opt/fy-api/deploy-log.md

cat <<MSG

═══════════════════════════════════════════════════════════════
蓝绿发版完成 ✅

当前活跃: fy-api-$NEXT (port $NEXT_PORT)
旧容器:   fy-api-$CUR (已停,未删,可回滚)
镜像:     $IMAGE

观察新容器:
  podman logs -f fy-api-$NEXT
  tail -f $LOG_DIR/oneapi-*.log
  curl -sf http://127.0.0.1:$NEXT_PORT/api/status | jq .

如果 30 分钟内一切 OK,清理旧容器:
  podman rm fy-api-$CUR

如果需要回滚:
  podman start fy-api-$CUR
  sudo sed -i -E 's|(server\s+127\.0\.0\.1:)$NEXT_PORT(\s)|\\1$CUR_PORT\\2|' $NGINX_CONF
  sudo nginx -t && sudo systemctl reload nginx
  podman stop -t 20 fy-api-$NEXT
═══════════════════════════════════════════════════════════════
MSG

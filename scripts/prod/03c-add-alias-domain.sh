#!/bin/bash
# scripts/prod/03c-add-alias-domain.sh
# 给一个「别名域名」配 HTTPS,直接反代到 Fy-api 后端(不做 301 跳转)
#
# 典型用法:让 www.tracenex.cn 和 api.tracenex.cn 都能正常提供服务,地址栏不变
#
# 和 03b 的区别:
#   03b  →  www.tracenex.cn  301 跳到 api.tracenex.cn(地址栏会变)
#   03c  →  www.tracenex.cn  直接服务 Fy-api,地址栏保持 www.tracenex.cn
#
# 前置:
#   1. 已跑过 03-setup-nginx.sh (主域名 HTTPS 已就绪,fy_api_backend upstream 已定义)
#   2. ALIAS 域名的 DNS A 记录已指向本机公网 IP
#   3. 安全组已放行 80 + 443
#
# 用法:
#   sudo ALIAS=www.tracenex.cn \
#        EMAIL=seraph0017@hotmail.com \
#        ./03c-add-alias-domain.sh

set -euo pipefail

log() { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
err() { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "请用 root 或 sudo 执行"
[ -n "${ALIAS:-}" ]  || err "请设置 ALIAS,例: ALIAS=www.tracenex.cn"
[ -n "${EMAIL:-}" ]  || err "请设置 EMAIL,例: EMAIL=sre@example.com"

CONF_FILE=/etc/nginx/conf.d/alias-${ALIAS}.conf
REDIRECT_CONF=/etc/nginx/conf.d/redirect-${ALIAS}.conf
CERT_DIR=/etc/letsencrypt/live/$ALIAS
WEBROOT=/var/www/html

mkdir -p "$WEBROOT"

# ─────────────────────────────────────────────────────────
# 0) 如果之前用 03b 把这个域名配成 301 跳转了,先删掉
# ─────────────────────────────────────────────────────────
if [ -f "$REDIRECT_CONF" ]; then
  log "检测到旧的 301 跳转配置 $REDIRECT_CONF,删除..."
  rm -f "$REDIRECT_CONF"
fi

# ─────────────────────────────────────────────────────────
# 1) DNS 自检
# ─────────────────────────────────────────────────────────
log "检查 DNS: $ALIAS ..."
RESOLVED=$(dig +short "$ALIAS" | tail -1 || true)
MY_IP=$(curl -s --max-time 5 https://ifconfig.me || echo "unknown")
if [ -z "$RESOLVED" ]; then
  err "$ALIAS 没有 DNS 解析,先去 DNS 控制台加 A 记录再来"
fi
if [ "$MY_IP" != "unknown" ] && [ "$RESOLVED" != "$MY_IP" ]; then
  log "⚠️ DNS 解析到 $RESOLVED,本机公网 IP 是 $MY_IP,可能还没生效"
fi

# ─────────────────────────────────────────────────────────
# 2) 临时 HTTP 配置用于 ACME challenge
# ─────────────────────────────────────────────────────────
log "步骤 1: 写临时 HTTP 配置用于证书申请..."
cat > "$CONF_FILE" <<EOF
server {
    listen 80;
    server_name $ALIAS;
    location /.well-known/acme-challenge/ { root $WEBROOT; }
    location / { return 404; }
}
EOF
nginx -t && systemctl reload nginx

# ─────────────────────────────────────────────────────────
# 3) 申请证书
# ─────────────────────────────────────────────────────────
if [ -d "$CERT_DIR" ]; then
  log "证书已存在,跳过申请: $CERT_DIR"
else
  log "步骤 2: 通过 certbot 申请 Let's Encrypt 证书 ($ALIAS)..."
  certbot certonly --webroot -w "$WEBROOT" \
    -d "$ALIAS" \
    --email "$EMAIL" \
    --agree-tos --non-interactive \
    || err "证书申请失败,检查: (a) DNS 解析 (b) 80 端口公网可达 (c) ICP 备案"
fi

# ─────────────────────────────────────────────────────────
# 4) 写最终反代配置
#    注意:fy_api_backend upstream 复用 03-setup-nginx.sh 里定义的那一份
# ─────────────────────────────────────────────────────────
log "步骤 3: 写反代配置: $ALIAS → fy_api_backend"

# 日志文件名中的点换成下划线,避免 logrotate 匹配歧义
LOG_SUFFIX=$(echo "$ALIAS" | tr '.' '_')

cat > "$CONF_FILE" <<EOF
# $ALIAS → Fy-api 后端(别名域,与主域同一份服务)
# 生成自: scripts/prod/03c-add-alias-domain.sh

# ─── HTTP → HTTPS ────────────────────────────────
server {
    listen 80;
    server_name $ALIAS;
    # Let's Encrypt renewal 走 ACME challenge
    location /.well-known/acme-challenge/ { root $WEBROOT; }
    # 其他一律 301 到 HTTPS(同一个域名,不换主机头)
    location / { return 301 https://\$host\$request_uri; }
}

# ─── HTTPS ───────────────────────────────────────
server {
    listen 443 ssl http2;
    server_name $ALIAS;

    ssl_certificate     $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         'TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Frame-Options               "SAMEORIGIN" always;
    add_header X-Content-Type-Options        "nosniff" always;
    add_header Referrer-Policy               "strict-origin-when-cross-origin" always;

    # ─── 大小与超时 ─────────────────────────────
    client_max_body_size      16m;
    client_body_timeout       60s;
    client_header_timeout     30s;

    proxy_connect_timeout     30s;
    proxy_send_timeout        900s;
    proxy_read_timeout        900s;
    send_timeout              900s;

    # ─── SSE / 流式必须关缓冲 ─────────────────
    proxy_buffering           off;
    proxy_request_buffering   off;
    proxy_http_version        1.1;
    proxy_set_header Connection "";

    # ─── 真实 IP ──────────────────────────────
    proxy_set_header Host              \$host;
    proxy_set_header X-Real-IP         \$remote_addr;
    proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;

    # ─── 独立日志 ────────────────────────────
    access_log /var/log/nginx/${LOG_SUFFIX}-access.log fy_api_main buffer=32k flush=5s;
    error_log  /var/log/nginx/${LOG_SUFFIX}-error.log warn;

    # ─── 反代到主 upstream(03 脚本里定义)──
    location / {
        proxy_pass http://fy_api_backend;
    }
}
EOF

# ─────────────────────────────────────────────────────────
# 5) 校验 + reload
# ─────────────────────────────────────────────────────────
log "步骤 4: nginx -t 校验..."
nginx -t || err "nginx 配置语法错误"
systemctl reload nginx

# ─────────────────────────────────────────────────────────
# 6) 验证
# ─────────────────────────────────────────────────────────
log "步骤 5: 验证..."
sleep 1
HTTPS_RESP=$(curl -skI "https://$ALIAS/api/status" | head -1 || true)
log "HTTPS /api/status: $HTTPS_RESP"

cat <<MSG

═══════════════════════════════════════════════════════════════
别名域名反代配置完成 ✅

$ALIAS  →  fy_api_backend (与主域共享后端)
证书:     $CERT_DIR
配置文件: $CONF_FILE
日志:     /var/log/nginx/${LOG_SUFFIX}-{access,error}.log

浏览器访问 https://$ALIAS/ :
  - 地址栏保持 $ALIAS(不会跳到别的域名)
  - 响应内容和主域完全一致
  - 请求流量走同一个 Fy-api 容器

验证:
  curl -sI https://$ALIAS/api/status    # 期望 HTTP/2 200
  curl -sI http://$ALIAS/               # 期望 301 → https://$ALIAS/

证书自动续期:
  certbot 的 timer 会同时续 $ALIAS 和主域,无需额外配置。

切回 301 跳转:
  sudo rm $CONF_FILE
  sudo FROM=$ALIAS TO=<主域> EMAIL=$EMAIL ./03b-add-redirect-domain.sh
═══════════════════════════════════════════════════════════════
MSG

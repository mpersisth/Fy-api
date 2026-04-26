#!/bin/bash
# scripts/prod/03-setup-nginx.sh
# 生成 Fy-api 的 Nginx 反向代理配置、申请 Let's Encrypt 证书
#
# 用法: sudo DOMAIN=api.your-domain.com EMAIL=sre@your-domain.com ./03-setup-nginx.sh

set -euo pipefail

log() { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
err() { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "请用 root 或 sudo 执行"
[ -n "${DOMAIN:-}" ]  || err "请设置 DOMAIN,例: DOMAIN=api.example.com"
[ -n "${EMAIL:-}" ]   || err "请设置 EMAIL,例: EMAIL=sre@example.com"

ACTIVE_PORT="${ACTIVE_PORT:-3001}"  # 蓝绿的当前活跃端口,默认 blue=3001

CONF_FILE=/etc/nginx/conf.d/fy-api.conf
CERT_DIR=/etc/letsencrypt/live/$DOMAIN

# ─────────────────────────────────────────────────────────
# 1) 先写一个 HTTP-only 临时配置,让 certbot 能走 HTTP-01 challenge
# ─────────────────────────────────────────────────────────
log "步骤 1: 写临时 HTTP 配置用于证书申请..."
cat > $CONF_FILE <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 404; }
}
EOF
mkdir -p /var/www/html
nginx -t && systemctl reload nginx

# ─────────────────────────────────────────────────────────
# 2) 申请证书
# ─────────────────────────────────────────────────────────
if [ -d "$CERT_DIR" ]; then
  log "证书已存在,跳过申请: $CERT_DIR"
else
  log "步骤 2: 通过 certbot 申请 Let's Encrypt 证书..."
  certbot certonly --webroot -w /var/www/html \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos --non-interactive \
    || err "证书申请失败,检查: (a) DNS 是否解析到本机 (b) 80 端口是否可达公网"
fi

# ─────────────────────────────────────────────────────────
# 3) 写生产 HTTPS + 反代配置(蓝绿 upstream)
# ─────────────────────────────────────────────────────────
log "步骤 3: 写生产 HTTPS 配置,活跃端口=$ACTIVE_PORT"
cat > $CONF_FILE <<EOF
# Fy-api 生产反代配置 — 蓝绿 upstream
# active: $ACTIVE_PORT

# ─── upstream 蓝绿切换靠这里 ──────────────────────
upstream fy_api_backend {
    server 127.0.0.1:$ACTIVE_PORT max_fails=2 fail_timeout=10s;
    keepalive 64;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

# ─── HTTP → HTTPS ────────────────────────────────
server {
    listen 80;
    server_name $DOMAIN;
    # Let's Encrypt renewal 走 ACME challenge
    location /.well-known/acme-challenge/ { root /var/www/html; }
    # 其他一律 301 到 https
    location / { return 301 https://\$host\$request_uri; }
}

# ─── HTTPS ───────────────────────────────────────
server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate     $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         'TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    ssl_session_cache  shared:SSL:10m;
    ssl_session_timeout 10m;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # ─── 大小与超时(关键)──────────────────────
    client_max_body_size      16m;
    client_body_timeout       60s;
    client_header_timeout     30s;

    proxy_connect_timeout     30s;
    # AI 流式响应最多 15 分钟,别再用默认 60 秒
    proxy_send_timeout        900s;
    proxy_read_timeout        900s;
    send_timeout              900s;

    # ─── SSE / 流式必须关闭缓冲 ────────────────
    proxy_buffering           off;
    proxy_request_buffering   off;
    proxy_http_version        1.1;
    proxy_set_header Connection "";

    # ─── 真实 IP 给 Fy-api(限流看) ───────────
    proxy_set_header Host              \$host;
    proxy_set_header X-Real-IP         \$remote_addr;
    proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;

    # ─── 日志(独立,方便 SLS 采集) ───────────
    log_format fy_api_main '\$remote_addr - \$remote_user [\$time_local] "\$request" '
                           '\$status \$body_bytes_sent "\$http_referer" '
                           '"\$http_user_agent" "\$http_x_forwarded_for" '
                           'rt=\$request_time uct="\$upstream_connect_time" '
                           'urt="\$upstream_response_time"';
    access_log /var/log/nginx/fy-api-access.log fy_api_main buffer=32k flush=5s;
    error_log  /var/log/nginx/fy-api-error.log warn;

    # ─── 反代主入口 ───────────────────────────
    location / {
        proxy_pass http://fy_api_backend;
    }

    # ─── Nginx 状态页(内网用,给 Prometheus/运维 ping)────
    location = /nginx-status {
        stub_status on;
        allow 127.0.0.1;
        allow 10.0.0.0/8;
        deny all;
        access_log off;
    }
}
EOF

# ─────────────────────────────────────────────────────────
# 4) 语法校验 + reload
# ─────────────────────────────────────────────────────────
log "步骤 4: nginx -t 校验..."
nginx -t || err "nginx 配置语法错误"
systemctl reload nginx

# ─────────────────────────────────────────────────────────
# 5) 证书自动续期
# ─────────────────────────────────────────────────────────
log "步骤 5: 确保证书自动续期 timer 已启用..."
systemctl enable --now certbot-renew.timer 2>/dev/null || \
  cat > /etc/cron.d/certbot-renew <<'CRONEOF'
0 3 * * * root certbot renew --quiet --post-hook "systemctl reload nginx"
CRONEOF

# ─────────────────────────────────────────────────────────
# 6) Nginx 日志 logrotate(Fy-api 容器日志另行处理)
# ─────────────────────────────────────────────────────────
cat > /etc/logrotate.d/fy-api-nginx <<'EOF'
/var/log/nginx/fy-api-*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    sharedscripts
    postrotate
        [ -f /var/run/nginx.pid ] && kill -USR1 $(cat /var/run/nginx.pid)
    endscript
}
EOF

cat <<MSG

═══════════════════════════════════════════════════════════════
Nginx 配置完成 ✅

域名:       $DOMAIN
证书路径:   $CERT_DIR
upstream:   127.0.0.1:$ACTIVE_PORT (active)

测试:
  curl -I https://$DOMAIN/api/status
  # 期望: HTTP/2 200 + HSTS header

  curl -vI -k https://$DOMAIN/         # 看证书信息
  openssl s_client -servername $DOMAIN -connect $DOMAIN:443 < /dev/null

切换 upstream(蓝绿发版时):
  ACTIVE_PORT=3002 $0              # 切到 green
  # 或手动 sed -i 'upstream fy_api_backend' /etc/nginx/conf.d/fy-api.conf

日志位置:
  /var/log/nginx/fy-api-access.log
  /var/log/nginx/fy-api-error.log

下一步:
  ./04-deploy-fyapi.sh             # 起 Fy-api 容器
═══════════════════════════════════════════════════════════════
MSG

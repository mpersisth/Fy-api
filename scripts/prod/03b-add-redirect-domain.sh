#!/bin/bash
# scripts/prod/03b-add-redirect-domain.sh
# 给一个「次要域名」配 HTTPS,并把所有请求 301 跳到主域名
#
# 典型用法:把 www.tracenex.cn 跳到 api.tracenex.cn
#
# 前置:
#   1. 已跑过 03-setup-nginx.sh (主域名 HTTPS 已就绪)
#   2. FROM 域名的 DNS A 记录已指向本机公网 IP
#   3. 安全组已放行 80 + 443
#
# 用法:
#   sudo FROM=www.tracenex.cn \
#        TO=api.tracenex.cn \
#        EMAIL=seraph0017@hotmail.com \
#        ./03b-add-redirect-domain.sh

set -euo pipefail

log() { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
err() { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "请用 root 或 sudo 执行"
[ -n "${FROM:-}" ]   || err "请设置 FROM,例: FROM=www.tracenex.cn"
[ -n "${TO:-}" ]     || err "请设置 TO,例: TO=api.tracenex.cn"
[ -n "${EMAIL:-}" ]  || err "请设置 EMAIL,例: EMAIL=sre@example.com"

CONF_FILE=/etc/nginx/conf.d/redirect-${FROM}.conf
CERT_DIR=/etc/letsencrypt/live/$FROM
WEBROOT=/var/www/html

mkdir -p "$WEBROOT"

# ─────────────────────────────────────────────────────────
# 1) DNS 自检(非强制,失败只告警)
# ─────────────────────────────────────────────────────────
log "检查 DNS: $FROM ..."
RESOLVED=$(dig +short "$FROM" | tail -1 || true)
MY_IP=$(curl -s --max-time 5 https://ifconfig.me || echo "unknown")
if [ -z "$RESOLVED" ]; then
  err "$FROM 没有 DNS 解析,先去 DNS 控制台加 A 记录再来"
fi
if [ "$MY_IP" != "unknown" ] && [ "$RESOLVED" != "$MY_IP" ]; then
  log "⚠️ DNS 解析到 $RESOLVED,本机公网 IP 是 $MY_IP,可能还没生效"
  log "⚠️ 如果确定 DNS 改过了,等 TTL 再跑;否则 certbot 会失败"
fi

# ─────────────────────────────────────────────────────────
# 2) 先写一个 HTTP-only 临时配置,让 certbot 走 ACME challenge
# ─────────────────────────────────────────────────────────
log "步骤 1: 写临时 HTTP 配置用于证书申请..."
cat > "$CONF_FILE" <<EOF
server {
    listen 80;
    server_name $FROM;
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
  log "步骤 2: 通过 certbot 申请 Let's Encrypt 证书 ($FROM)..."
  certbot certonly --webroot -w "$WEBROOT" \
    -d "$FROM" \
    --email "$EMAIL" \
    --agree-tos --non-interactive \
    || err "证书申请失败,检查: (a) DNS 是否解析到本机 (b) 80 端口公网可达 (c) 备案是否通过"
fi

# ─────────────────────────────────────────────────────────
# 4) 写最终配置:80/443 全部 301 到 TO
# ─────────────────────────────────────────────────────────
log "步骤 3: 写 301 跳转配置: $FROM → $TO"
cat > "$CONF_FILE" <<EOF
# $FROM → $TO (301 永久跳转)
# 生成自: scripts/prod/03b-add-redirect-domain.sh

# ─── HTTP → 跳到 HTTPS 主域 ──────────────────────
server {
    listen 80;
    server_name $FROM;
    # Let's Encrypt renewal 走 ACME challenge
    location /.well-known/acme-challenge/ { root $WEBROOT; }
    # POST/PUT/PATCH/DELETE 用 308 保留 method+body; GET/HEAD 维持 301
    location / {
        if (\$request_method !~ ^(GET|HEAD)\$) { return 308 https://$TO\$request_uri; }
        return 301 https://$TO\$request_uri;
    }
}

# ─── HTTPS → 跳到 HTTPS 主域 ─────────────────────
server {
    listen 443 ssl http2;
    server_name $FROM;

    ssl_certificate     $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         'TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    # POST/PUT/PATCH/DELETE 用 308 保留 method+body; GET/HEAD 维持 301
    if (\$request_method !~ ^(GET|HEAD)$) { return 308 https://$TO\$request_uri; }
    return 301 https://$TO\$request_uri;
}
EOF

# ─────────────────────────────────────────────────────────
# 5) 校验 + reload
# ─────────────────────────────────────────────────────────
log "步骤 4: nginx -t 校验..."
nginx -t || err "nginx 配置语法错误"
systemctl reload nginx

# ─────────────────────────────────────────────────────────
# 6) 验证 301
# ─────────────────────────────────────────────────────────
log "步骤 5: 验证 301 跳转..."
sleep 1
HTTP_RESP=$(curl -sI "http://$FROM/" | head -1 || true)
HTTPS_RESP=$(curl -skI "https://$FROM/" | head -1 || true)
LOC=$(curl -skI "https://$FROM/" | grep -i '^location:' || true)

log "HTTP:  $HTTP_RESP"
log "HTTPS: $HTTPS_RESP"
log "Loc:   $LOC"

cat <<MSG

═══════════════════════════════════════════════════════════════
301 跳转域名配置完成 ✅

$FROM  →  $TO
证书:     $CERT_DIR
配置文件: $CONF_FILE

验证:
  curl -sI  http://$FROM/        # 期望 301 → https://$TO/
  curl -skI https://$FROM/       # 期望 301 → https://$TO/
  curl -sI  https://$FROM/any    # 期望 301 → https://$TO/any

证书自动续期:
  certbot 的 timer 会同时续 $FROM 和主域,无需额外配置。

撤掉跳转(以后要把 $FROM 改成独立站点):
  sudo rm $CONF_FILE
  sudo nginx -t && sudo systemctl reload nginx
  # 然后重新写 server 块,或跑 03-setup-nginx.sh
═══════════════════════════════════════════════════════════════
MSG

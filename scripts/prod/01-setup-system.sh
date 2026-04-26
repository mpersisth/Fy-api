#!/bin/bash
# scripts/prod/01-setup-system.sh
# 生产 ECS 一次性系统初始化:安装包、内核参数、ulimit、目录、防火墙
#
# 运行环境: Alibaba Cloud Linux 3 / RHEL 9 / Rocky Linux 9
# 要求:    root 或 sudo 权限
# 用法:    sudo ./01-setup-system.sh

set -euo pipefail

log() { printf "\033[36m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
err() { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "请用 root 或 sudo 执行"

# ─────────────────────────────────────────────────────────
# 1) 基础包(自动适配 dnf / apt)
# ─────────────────────────────────────────────────────────
log "检测包管理器..."
if command -v dnf >/dev/null; then
  PM=dnf
elif command -v apt-get >/dev/null; then
  PM=apt
else
  err "找不到 dnf 或 apt-get,无法自动装包。请手动安装 podman 等基础包后重跑"
fi
log "使用包管理器: $PM"

if [ "$PM" = "dnf" ]; then
  log "安装基础包 (RHEL/Rocky/Alibaba Cloud Linux)..."
  dnf install -y \
    podman podman-compose \
    nginx certbot python3-certbot-nginx \
    jq htop vim tmux git curl wget \
    logrotate
  # passt 可选 — 没有也能跑(走默认 slirp4netns)
  dnf install -y passt 2>/dev/null && HAS_PASTA=1 || HAS_PASTA=0
else
  log "安装基础包 (Debian/Ubuntu)..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    podman \
    nginx certbot python3-certbot-nginx \
    jq htop vim tmux git curl wget ca-certificates \
    logrotate uidmap slirp4netns
  # passt 在 Ubuntu 22.04 (jammy) 官方源没有,23.10+ / Debian 12+ 才有
  # 装不上就用默认 slirp4netns,单机生产 + Nginx 反代真实 IP 足够
  if apt-get install -y --no-install-recommends passt 2>/dev/null; then
    HAS_PASTA=1
    log "passt 安装成功 → 将使用 pasta 网络栈"
  else
    HAS_PASTA=0
    log "passt 未在当前 apt 源(Ubuntu 22.04 正常),将使用 slirp4netns"
  fi

  # Debian/Ubuntu 的 podman-compose 走 pip
  if ! command -v podman-compose >/dev/null; then
    log "安装 podman-compose (pip3)..."
    apt-get install -y --no-install-recommends python3-pip
    pip3 install --break-system-packages podman-compose 2>/dev/null || \
      pip3 install podman-compose
  fi
fi

log "版本信息:"
podman --version
podman-compose --version 2>/dev/null || log "  podman-compose 未安装成功,稍后再手动装"
nginx -v 2>&1 | head -1

# ─────────────────────────────────────────────────────────
# 2) 内核参数
# ─────────────────────────────────────────────────────────
log "应用内核参数..."
cat > /etc/sysctl.d/99-fyapi.conf <<'EOF'
# TCP 连接池 / 队列
net.ipv4.tcp_max_syn_backlog = 8192
net.core.somaxconn = 8192
net.ipv4.ip_local_port_range = 1024 65535

# TIME_WAIT 复用
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 30

# TCP 缓冲
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216

# BBR
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# 文件句柄
fs.file-max = 2097152

# 虚拟内存
vm.swappiness = 10
vm.overcommit_memory = 1
EOF
sysctl --system >/dev/null

# ─────────────────────────────────────────────────────────
# 3) ulimit
# ─────────────────────────────────────────────────────────
log "设置 ulimit..."
cat > /etc/security/limits.d/99-fyapi.conf <<'EOF'
*    soft  nofile  1048576
*    hard  nofile  1048576
root soft  nofile  1048576
root hard  nofile  1048576
EOF

# ─────────────────────────────────────────────────────────
# 4) 时区
# ─────────────────────────────────────────────────────────
log "设置时区..."
timedatectl set-timezone Asia/Shanghai

# ─────────────────────────────────────────────────────────
# 5) Podman rootless 网络栈
# ─────────────────────────────────────────────────────────
mkdir -p /etc/containers
if [ "${HAS_PASTA:-0}" = "1" ]; then
  log "配置 Podman 网络栈为 pasta(更快 + 保留真实 IP)..."
  cat > /etc/containers/containers.conf <<'EOF'
[network]
default_rootless_network_cmd = "pasta"

[containers]
default_ulimits = [
  "nofile=1048576:1048576"
]
EOF
else
  log "使用默认 slirp4netns 网络栈(单机 + Nginx 反代场景完全够用)..."
  cat > /etc/containers/containers.conf <<'EOF'
[containers]
default_ulimits = [
  "nofile=1048576:1048576"
]
EOF
fi

# ─────────────────────────────────────────────────────────
# 6) 目录结构
# ─────────────────────────────────────────────────────────
log "创建 /opt/fy-api 目录..."
mkdir -p /opt/fy-api/{logs,data,config,backup,scripts}
chmod 755 /opt/fy-api
# config 是敏感目录,后续 .env 放进去
chmod 700 /opt/fy-api/config

# ─────────────────────────────────────────────────────────
# 7) 防火墙(firewalld / ufw / 都没有就靠阿里云安全组)
# ─────────────────────────────────────────────────────────
if systemctl is-active --quiet firewalld 2>/dev/null; then
  log "配置 firewalld..."
  firewall-cmd --permanent --add-port=80/tcp
  firewall-cmd --permanent --add-port=443/tcp
  firewall-cmd --reload
  log "firewalld 已放行 80/443。"
elif command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  log "配置 ufw..."
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw reload
  log "ufw 已放行 80/443。"
else
  log "未检测到启用的 firewalld 或 ufw — 请确认阿里云安全组已放 80/443"
fi

# ─────────────────────────────────────────────────────────
# 8) Nginx 启动
# ─────────────────────────────────────────────────────────
log "启用 Nginx..."
systemctl enable --now nginx
log "Nginx 状态: $(systemctl is-active nginx)"

# ─────────────────────────────────────────────────────────
# 9) 自动快照提醒
# ─────────────────────────────────────────────────────────
cat <<'MSG'

═══════════════════════════════════════════════════════════════
系统初始化完成 ✅

下一步:
  1. 在阿里云控制台为本 ECS 配置"自动快照策略"(每日 03:00,保留 7 天)
  2. 把 /opt/fy-api/config/fy-api.env 从模板复制并填值:
       scp config/fy-api.env.example root@<this-host>:/opt/fy-api/config/fy-api.env
       ssh root@<this-host> "chmod 600 /opt/fy-api/config/fy-api.env"
  3. 运行下一个脚本:
       ./02-install-logtail.sh
       ./03-setup-nginx.sh
       ./04-deploy-fyapi.sh
═══════════════════════════════════════════════════════════════
MSG

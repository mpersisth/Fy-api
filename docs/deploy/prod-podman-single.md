# 生产环境部署 Runbook（单机 Podman + 阿里云托管数据面）

> 读者：运维 / 中小团队创始人
> 场景：生产环境先不上 K8s,用一台阿里云 ECS 跑 Podman,背后接 RDS + Redis 托管实例,走蓝绿发版 + SLS 日志分析
> 预期规模：< 500 RPS,客户数 < 500
> 版本：2026-04-26
>
> 本文档前提:已经读过 [`test-podman.md`](./test-podman.md)(测试机版)。
> 如果业务规模起来,迁 SAE 或 ACK 的路径见 [`prod-ack.md`](./prod-ack.md)。

---

## 目录

1. [为什么选单机 Podman(决策回顾)](#一为什么选单机-podman决策回顾)
2. [部署架构与资源清单](#二部署架构与资源清单)
3. [ECS 初始化](#三ecs-初始化)
4. [阿里云 RDS / Redis 准备](#四阿里云-rds--redis-准备)
5. [镜像构建与推送(ACR)](#五镜像构建与推送acr)
6. [部署 Fy-api 容器](#六部署-fy-api-容器)
7. [日志落盘(关键,本 runbook 的重点)](#七日志落盘关键本-runbook-的重点)
8. [日志接入阿里云 SLS](#八日志接入阿里云-sls)
9. [Nginx + SSL + 对外发布](#九nginx--ssl--对外发布)
10. [蓝绿发版(零停机)](#十蓝绿发版零停机)
11. [备份与容灾](#十一备份与容灾)
12. [安全基线](#十二安全基线)
13. [监控与告警(引用)](#十三监控与告警引用)
14. [常见故障速查](#十四常见故障速查)
15. [升级路径:迁 SAE 或 ACK](#十五升级路径迁-sae-或-ack)

---

## 一、为什么选单机 Podman(决策回顾)

决策过的核心前提:

| 维度 | 单机 Podman | ACK |
|---|---|---|
| 起步月成本 | ¥1,200~1,500 | ¥4,500+ |
| 运维复杂度 | ⭐ | ⭐⭐⭐⭐ |
| 故障面 | 小(1 台 ECS) | 大(K8s 本身) |
| 水平扩展 | 手动升配 | HPA 自动 |
| 多 AZ 容灾 | 主备手动 | 自动 |
| 发版方式 | 蓝绿 / restart | RollingUpdate |
| 适合规模 | < 500 RPS / < 500 付费客户 | 更大 |

**选单机的前提条件**(任一不满足就该升级):

- ✅ 业务规模 < 500 RPS
- ✅ 对可用性要求在 99.5% 以内(单机全年停机约 44h 上限)
- ✅ 团队人手 1-3 人,更关注"快速响应"而非"自动化规模"
- ✅ 想把成本压到最低

---

## 二、部署架构与资源清单

### 2.1 架构图

```
        用户
          │
          ▼
    api.<domain>.com (DNS)
          │
          ▼  HTTPS (443)
┌─────────────────────────────────────────┐
│   阿里云 ECS(1 台,生产主机)            │
│                                         │
│   Nginx (系统进程)  ← TLS 终止         │
│     │ proxy_pass                        │
│     ▼                                   │
│   ┌────── Podman 容器 ──────────────┐   │
│   │                                 │   │
│   │  fy-api-blue  :3001  (active)   │   │
│   │  fy-api-green :3002  (standby)  │   │
│   │                                 │   │
│   └─────────────────────────────────┘   │
│                                         │
│   systemd ilogtaild ──────────────┐     │
│   读 /root/Fy-api/logs/*.log      │     │
└───────────────────────────────────┼─────┘
                                    │
                                    ▼
         ┌──────────────────────────────────┐
         │  阿里云 SLS(日志服务)           │
         │  Project: fy-api-prod            │
         │   ├─ fy-api-app  (应用日志)     │
         │   └─ fy-api-nginx(访问日志)     │
         └──────────────────────────────────┘

         VPC 内网:
         ┌───────────────┐   ┌─────────────┐
         │ RDS MySQL 8.0 │   │ Redis 7 主备 │
         │ 2c4g 100G     │   │ 主备 256MB   │
         └───────────────┘   └─────────────┘
```

### 2.2 资源清单

| 资源 | 规格 | 命名示例 | 月成本 |
|---|---|---|---|
| ECS | `ecs.c7.xlarge` 4c8g,100G ESSD | `fy-api-prod-1` | ¥600 |
| EIP + 公网带宽 | 5 Mbps 按固定 / 按量 | — | ¥100~200 |
| RDS MySQL 8.0 高可用 | 2c4g,100G ESSD PL1,**主备双机** | `rm-xxx-fy-api-prod` | ¥400 |
| R-KVStore Redis 7 主备 | 256MB 标准版 | `r-xxx-fy-api-prod` | ¥60 |
| ACR 个人版(或企业版) | 1 个仓库 | `registry.cn-hangzhou.aliyuncs.com/fy-api/fy-api` | ¥0~100 |
| SLS(日志) | 30GB 查询 + 180 天归档 | `fy-api-prod` | ¥100 |
| OSS(SLS 归档、备份) | 标准存储 50G | `fy-api-backup` | ¥10 |
| 备案域名 | — | `api.<your-domain>.com` | — |
| **月成本合计** | | | **¥1,270~1,470** |

---

## 三、ECS 初始化

### 3.1 创建 ECS

阿里云控制台 → ECS → 创建实例:

- **镜像**:Alibaba Cloud Linux 3 或 Debian 12(下面命令以 Alibaba Cloud Linux 3 为例)
- **网络**:VPC 内,同一 vSwitch 要能和 RDS/Redis 互通
- **安全组**:先只放 22(SSH),443 / 80 稍后开放
- **EIP**:分配一个,带宽按实际需求

### 3.2 系统调优(一次性)

```bash
# 1) 升级基础包
sudo dnf update -y
sudo dnf install -y podman podman-compose passt jq nginx vim htop logrotate tmux

# 2) 内核参数(网络 + 文件句柄)
sudo tee /etc/sysctl.d/99-fyapi.conf > /dev/null <<'EOF'
net.ipv4.tcp_max_syn_backlog = 8192
net.core.somaxconn = 8192
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 30
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
fs.file-max = 2097152
vm.swappiness = 10
EOF
sudo sysctl --system

# 3) 文件句柄上限
sudo tee /etc/security/limits.d/99-fyapi.conf > /dev/null <<'EOF'
*  soft  nofile  1048576
*  hard  nofile  1048576
root soft nofile 1048576
root hard nofile 1048576
EOF

# 4) 时区
sudo timedatectl set-timezone Asia/Shanghai

# 5) Podman 版本确认(要 ≥ 4.x)
podman --version        # 期望 4.x 或 5.x
podman-compose --version

# 6) rootless 网络用 pasta(比 slirp4netns 快 5-10 倍,保留真实 IP)
mkdir -p ~/.config/containers
cat > ~/.config/containers/containers.conf <<'EOF'
[network]
default_rootless_network_cmd = "pasta"
EOF

# 7) 允许用户服务在无 SSH 时继续运行
sudo loginctl enable-linger $USER
```

### 3.3 开防火墙

```bash
# 如果用 firewalld(Alibaba Cloud Linux 默认)
sudo firewall-cmd --permanent --add-port=80/tcp
sudo firewall-cmd --permanent --add-port=443/tcp
sudo firewall-cmd --reload

# 阿里云安全组同步开 80 / 443(控制台操作)
```

### 3.4 目录规范

```bash
sudo mkdir -p /opt/fy-api/{logs,data,config,backup,scripts}
sudo chown -R $USER:$USER /opt/fy-api
cd /opt/fy-api
```

后续所有挂载、脚本都以 `/opt/fy-api/` 为根。

---

## 四、阿里云 RDS / Redis 准备

### 4.1 RDS MySQL

控制台 → RDS → 创建实例:

- 版本:MySQL 8.0
- 系列:**高可用版(主备双机)**
- 规格:`mysql.n4.medium.2c`(2c4g)起步
- 存储:ESSD PL1 100G
- 网络:VPC,和 ECS 同一个 VPC
- 参数组:新建一个 `fy-api-prod-pg`,改:
  - `innodb_buffer_pool_size` = 75% 内存
  - `innodb_flush_log_at_trx_commit` = 1
  - `max_connections` = 500
  - `character_set_server` = utf8mb4
  - `transaction_isolation` = READ-COMMITTED
  - `slow_query_log` = ON / `long_query_time` = 1

**白名单**:把 ECS 的内网 IP 或 vSwitch CIDR 加进去(RDS 控制台 → 数据安全性)。

**建库建账号**:

```sql
-- 在 DMS 或 mysql CLI 里执行
CREATE DATABASE fy_api DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'fy_api_app'@'%' IDENTIFIED BY 'REPLACE_ME_STRONG_PWD';
GRANT ALTER, CREATE, CREATE VIEW, DELETE, DROP, INDEX, INSERT,
      REFERENCES, SELECT, SHOW VIEW, TRIGGER, UPDATE
      ON fy_api.* TO 'fy_api_app'@'%';

-- 只读账号(SLS/BI 用)
CREATE USER 'fy_api_ro'@'%' IDENTIFIED BY 'REPLACE_ME_RO_PWD';
GRANT SELECT ON fy_api.* TO 'fy_api_ro'@'%';

-- Prometheus exporter 后续会用(可选)
CREATE USER 'exporter'@'%' IDENTIFIED BY 'REPLACE_ME_EXPORTER_PWD';
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'%';
FLUSH PRIVILEGES;
```

**(强烈建议)开启专属代理**:RDS 控制台 → 数据库代理 → 开启。之后 Fy-api 连代理域名而非 RDS 直连。理由见 [`prod-ack.md`](./prod-ack.md) §3.5。

### 4.2 R-KVStore Redis

- 版本:Redis 7.0
- 架构:**主备标准版** 256MB 或 1GB 起步
- 网络:同 VPC
- 开密码,关闭公网

白名单:ECS 内网 IP。

### 4.3 连接验证(从 ECS 上测)

```bash
# MySQL
podman run --rm -it mysql:8 \
  mysql -h rm-xxx-proxy.mysql.rds.aliyuncs.com -P 3306 -u fy_api_app -p fy_api \
  -e "SELECT VERSION();"

# Redis
podman run --rm -it redis:7-alpine \
  redis-cli -h r-xxx.redis.rds.aliyuncs.com -a 'REDIS_PASS' PING
# 期望:PONG
```

---

## 五、镜像构建与推送(ACR)

### 5.1 ACR 配置(一次性)

1. 控制台 → 容器镜像服务 → 开通个人版(免费)或企业版
2. 创建命名空间 `fy-api`、仓库 `fy-api`
3. 在**本地或 CI 构建机**上登录:

```bash
podman login registry.cn-hangzhou.aliyuncs.com
# 用户:阿里云账号或 RAM 子账号
# 密码:ACR 访问凭证(非阿里云登录密码)
```

### 5.2 构建推送

```bash
# 本地 / CI 构建机上
cd ~/Projects/apiGateway/Fy-api
VERSION=$(cat VERSION)                        # 比如 v0.9.4
GIT_SHA=$(git rev-parse --short HEAD)         # 比如 a1b2c3d4
IMAGE_BASE=registry.cn-hangzhou.aliyuncs.com/fy-api/fy-api

podman build --platform linux/amd64 \
  -t $IMAGE_BASE:$VERSION \
  -t $IMAGE_BASE:sha-$GIT_SHA \
  .

podman push $IMAGE_BASE:$VERSION
podman push $IMAGE_BASE:sha-$GIT_SHA

# 记录发版
echo "$VERSION (sha-$GIT_SHA) - $(date -u +%F-%T)" >> /opt/fy-api/deploy-log.md
```

⚠️ **严禁用 `:latest` 上生产**,回滚靠 tag 精确定位。

### 5.3 ECS 上拉镜像(内网地址,省钱)

```bash
# 在 ECS 上
podman login registry-vpc.cn-hangzhou.aliyuncs.com    # 注意 -vpc
podman pull registry-vpc.cn-hangzhou.aliyuncs.com/fy-api/fy-api:v0.9.4
```

---

## 六、部署 Fy-api 容器

### 6.1 落地配置文件

```bash
mkdir -p /opt/fy-api/config
```

**`/opt/fy-api/config/fy-api.env`**(敏感文件,权限 600):

```dotenv
# ==== 基础 ====
TZ=Asia/Shanghai
GIN_MODE=release
FRONTEND_BASE_URL=https://api.your-domain.com

# ==== 数据源 ====
SQL_DSN=fy_api_app:REPLACE_ME_STRONG_PWD@tcp(rm-xxx-proxy.mysql.rds.aliyuncs.com:3306)/fy_api?charset=utf8mb4&parseTime=True&loc=Local
REDIS_CONN_STRING=redis://:REPLACE_ME_REDIS_PASS@r-xxx.redis.rds.aliyuncs.com:6379/0

# ==== 会话 / 加密(首次生成后保持不变)====
# openssl rand -hex 32  生成
SESSION_SECRET=REPLACE_ME_64_HEX_CHARS
CRYPTO_SECRET=REPLACE_ME_64_HEX_CHARS

# ==== 性能 ====
GOMAXPROCS=4
GOMEMLIMIT=6500MiB
RELAY_MAX_IDLE_CONNS=5000
RELAY_MAX_IDLE_CONNS_PER_HOST=500
RELAY_TIMEOUT=600
STREAMING_TIMEOUT=300
SQL_MAX_IDLE_CONNS=50
SQL_MAX_OPEN_CONNS=200
MEMORY_CACHE_ENABLED=true
SYNC_FREQUENCY=60
BATCH_UPDATE_ENABLED=true
BATCH_UPDATE_INTERVAL=3

# ==== 日志(关键,详细见 §7)====
ERROR_LOG_ENABLED=true

# ==== 限流(详细见 rate-limiting.md)====
GLOBAL_API_RATE_LIMIT_ENABLE=true
GLOBAL_API_RATE_LIMIT=3000
GLOBAL_API_RATE_LIMIT_DURATION=60
CRITICAL_RATE_LIMIT_ENABLE=true
CRITICAL_RATE_LIMIT=50
CRITICAL_RATE_LIMIT_DURATION=1200

# ==== 节点名(审计日志用)====
NODE_NAME=fy-api-prod-1
```

权限:

```bash
chmod 600 /opt/fy-api/config/fy-api.env
```

### 6.2 首次启动(仅 blue,占 :3001)

```bash
IMAGE=registry-vpc.cn-hangzhou.aliyuncs.com/fy-api/fy-api:v0.9.4

podman run -d --name fy-api-blue \
  --restart=unless-stopped \
  -p 127.0.0.1:3001:3000 \
  -v /opt/fy-api/logs:/app/logs:Z \
  -v /opt/fy-api/data:/data:Z \
  --env-file /opt/fy-api/config/fy-api.env \
  --ulimit nofile=1048576:1048576 \
  --log-driver=k8s-file \
  --log-opt max-size=100m \
  --log-opt max-file=5 \
  --memory=8g --memory-swap=8g \
  --cpus=4 \
  $IMAGE \
  --log-dir=/app/logs           # ← 关键,Fy-api CLI 参数
```

几点要点:
- `127.0.0.1:3001:3000` — 只绑 loopback,外部经 Nginx 反代,**不直接暴露**
- `--log-driver=k8s-file` — 替掉默认 journald(解决你之前 logs/ 空的问题)
- `--memory=8g --cpus=4` — 对齐 `.env` 的 `GOMAXPROCS=4 / GOMEMLIMIT=6500MiB`
- `:Z` — SELinux 自动打标签(Alibaba Cloud Linux 默认开)
- 命令末尾 `--log-dir=/app/logs` — 容器应用层落盘(见 §7)

### 6.3 首次冒烟

```bash
# 容器起来了吗
podman ps
# 期望:STATUS=Up

# 日志文件生成了吗
ls -lh /opt/fy-api/logs/
# 期望:oneapi-YYYYMMDDHHMMSS.log

# 健康
curl -s http://127.0.0.1:3001/api/status | jq .
# 期望:{"success":true, ...}
```

### 6.4 首次管理员初始化

```bash
read -s ROOT_PASS
curl -s -X POST http://127.0.0.1:3001/api/setup \
  -H "Content-Type: application/json" \
  -d "$(cat <<EOF
{
  "username": "fyadmin",
  "password": "$ROOT_PASS",
  "confirmPassword": "$ROOT_PASS",
  "SelfUseModeEnabled": false,
  "DemoSiteEnabled": false
}
EOF
)"
unset ROOT_PASS
```

---

## 七、日志落盘(关键,本 runbook 的重点)

### 7.1 为什么默认不落盘

源码事实(`common/init.go:21`):

```go
LogDir = flag.String("log-dir", "./logs", "specify the log directory")
```

**`LogDir` 是 CLI flag,不是环境变量**。默认 `./logs`,在容器 workdir `/data/logs` 下。
如果你启动命令没带 `--log-dir`,并且挂载又挂到 `/app/logs`,就会"挂载目录是空的"。

### 7.2 正确做法(三件齐备)

1. **容器启动命令末尾追加 `--log-dir=/app/logs`**(§6.2 已经带了)
2. **挂载宿主机目录到 `/app/logs`**:`-v /opt/fy-api/logs:/app/logs:Z`
3. **`ERROR_LOG_ENABLED=true`** — 让错误也进文件

### 7.3 日志文件结构

```bash
ls /opt/fy-api/logs/
# oneapi-20260426101523.log   ← 当前文件
# oneapi-20260426221010.log   ← 每次容器重启都会新开一个
```

单文件内容示例:

```
[GIN] 2026/04/26 - 10:15:23 | 200 | 412.3ms | 1.2.3.4 | POST /v1/chat/completions
[INFO] 2026/04/26 - 10:15:23 | 20260426... | record consume log: userId=2, params={"channel_id":2,...}
panic: runtime error: ...
  goroutine 1 [running]:
  ...
```

### 7.4 日志轮转(必做,否则迟早打爆磁盘)

**方式 A:系统 logrotate(推荐)**

```bash
sudo tee /etc/logrotate.d/fy-api > /dev/null <<'EOF'
/opt/fy-api/logs/oneapi-*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate           # 不改变 fd,Fy-api 继续往同一个 inode 写
    maxsize 500M
    su root root
}
EOF

# 验证配置
sudo logrotate -d /etc/logrotate.d/fy-api
# 手动执行一次
sudo logrotate -f /etc/logrotate.d/fy-api
```

**方式 B:每日重启换新文件**(不推荐,流式有损失)

```bash
# crontab -e
0 3 * * * podman restart fy-api-blue && find /opt/fy-api/logs -name 'oneapi-*.log' -mtime +14 -delete
```

### 7.5 同时保留容器层日志(双保险)

即使应用层落盘,容器 stdout/stderr 也有价值(包含镜像 entrypoint 启动前的信息、panic 栈等)。`--log-driver=k8s-file` 会写到:

```
~/.local/share/containers/storage/overlay-containers/<cid>/userdata/ctr.log
```

也可以用:

```bash
podman logs --since 1h fy-api-blue > /tmp/blue-last-hour.log
```

### 7.6 验证清单

```bash
# 容器内能看到
podman exec fy-api-blue ls -l /app/logs
# 期望:oneapi-*.log

# 宿主机同步能看到
ls -lh /opt/fy-api/logs/

# 内容是同一文件(inode 一致)
podman exec fy-api-blue stat /app/logs/oneapi-*.log | grep Inode
stat /opt/fy-api/logs/oneapi-*.log | grep Inode
# 两侧 Inode 应该一样(挂载穿透)

# logrotate 规则生效
sudo logrotate -d /etc/logrotate.d/fy-api 2>&1 | grep -i fy-api
```

---

## 八、日志接入阿里云 SLS

### 8.1 选 SLS 的理由

- 你已经在阿里云生态,Logtail 5 分钟跑通
- SQL + 机器学习查询,上手友好
- 按量付费,100G 以内月费 ~¥300
- 钉钉/邮件/短信告警集成好

### 8.2 建 Project 和 Logstore

阿里云控制台 → 日志服务 SLS:

| 对象 | 名称 | 用途 | 保留 |
|---|---|---|---|
| Project | `fy-api-prod` | 项目容器 | — |
| Logstore | `fy-api-app` | Fy-api 应用日志(`oneapi-*.log`) | 30 天 |
| Logstore | `fy-api-nginx-access` | Nginx 访问日志 | 30 天 |
| Logstore | `fy-api-nginx-error` | Nginx 错误日志 | 30 天 |
| Logstore | `fy-api-consume` | 计费日志(record consume log),结构化单独存 | 180 天 |

**为什么把 consume 单独拆**:这类日志保留久、查询多,按 user_id 聚合算账用,跟应用日志查询模式完全不同,拆开便于建索引和审计。

### 8.3 ECS 上装 Logtail

```bash
# 1) 下载 Logtail(替换 region)
REGION=cn-hangzhou
wget -q "https://logtail-release-$REGION.oss-$REGION-internal.aliyuncs.com/linux64/logtail.sh" -O logtail.sh
sudo chmod 755 logtail.sh

# 2) 安装(内网模式,跑在 VPC 内不走公网)
sudo ./logtail.sh install $REGION-vpc

# 3) 检查
sudo systemctl status ilogtaild

# 4) 查看日志
sudo tail -f /usr/local/ilogtail/ilogtail.LOG
```

### 8.4 SLS 控制台:建机器组 + 采集配置

**Step 1:机器组**

SLS 控制台 → 项目 `fy-api-prod` → 机器组 → 创建:

- 名称:`ecs-prod`
- 标识类型:**用户自定义标识**
- 自定义标识:`fy-api-prod`
- 在 ECS 上写入标识:
  ```bash
  sudo mkdir -p /etc/ilogtail
  echo "fy-api-prod" | sudo tee /etc/ilogtail/user_defined_id
  sudo /etc/init.d/ilogtaild restart
  ```

**Step 2:采集配置(Fy-api 应用日志)**

SLS 控制台 → Logstore `fy-api-app` → 接入数据 → 文本日志 → 下一步:

| 字段 | 值 |
|---|---|
| 配置名称 | `fy-api-app-collector` |
| 日志路径 | `/opt/fy-api/logs` |
| 日志文件名 | `oneapi-*.log` |
| 采集模式 | **单行 - 正则模式**(下方给正则) |
| 多行起始正则 | `^\[(INFO\|WARN\|ERROR\|GIN\|DEBUG\|FATAL)\]` |

**正则提取**(提取 Fy-api 日志里的结构字段):

```
^\[(?<level>\w+)\]\s+(?<ts>[\d/]+\s+-\s+[\d:]+)(?:\s+\|\s+(?<request_id>\S+))?(?:\s+\|\s+(?<message>.*))?$
```

对"计费日志"这种关键行,再加一条**索引**让 SLS 可以 JSON 展开 `params` 字段(控制台 → 索引管理 → JSON 字段):

```
params.channel_id: long
params.prompt_tokens: long
params.completion_tokens: long
params.cache_tokens: long
params.model_name: text
params.quota: long
params.user_id: long
```

**Step 3:把机器组挂上这个配置** — 应用即可。

**Step 4:验证** — 往 Fy-api 发一个请求,2-5 分钟内 SLS Logstore 查询页面就能看到数据。

### 8.5 SLS 查询示例(实战)

进 Logstore → 查询分析:

```sql
-- 1) 近 1 小时每个客户消耗排行
* AND "record consume log"
| select
    regexp_extract(__raw_log__, 'userId=(\d+)', 1) AS user_id,
    sum(cast(json_extract_scalar(__raw_log__, '$.quota') AS bigint)) AS total_quota,
    count(*) AS call_count
from log
group by user_id
order by total_quota desc
limit 20

-- 2) 每个模型的缓存命中率
* AND "record consume log"
| select
    json_extract_scalar(__raw_log__, '$.model_name') AS model,
    100.0 * sum(case when cast(json_extract_scalar(__raw_log__, '$.cache_tokens') AS bigint) > 0 then 1 else 0 end) / count(*) AS hit_pct,
    count(*) AS total
from log
group by model
order by total desc

-- 3) 最近的 panic
level:FATAL OR panic OR "runtime error"
| select __time__, __raw_log__
order by __time__ desc
limit 50

-- 4) 429 限流命中分布
"您已达到请求数限制"
| select date_trunc('minute', __time__) AS min, count(*) AS cnt
from log
group by min
order by min
```

### 8.6 告警规则(最少 5 条)

SLS 告警中心 → 新建:

| 告警 | 查询 | 触发条件 | 通知 |
|---|---|---|---|
| **Fatal / panic** | `panic OR "runtime error" OR FATAL` | 1min ≥ 1 条 | 钉钉 P0 + 短信 |
| **5xx 错误率** | HTTP GIN 日志里 status 字段 | 5min > 10 条 | 钉钉 P1 |
| **限流命中** | `"您已达到请求数限制"` | 5min > 10 条 | 邮件(容量规划告警) |
| **客户消耗突增** | 单用户 5min quota 同比 > 300% | 满足即告 | 钉钉 P2(可能刷量) |
| **上游错误** | `upstream AND (error OR failed)` | 5min > 20 条 | 钉钉 P1 |

**钉钉 webhook 配置**:

1. 钉钉群 → 智能群助手 → 添加机器人 → 自定义 webhook
2. 安全设置选"自定义关键词",填 `Fy-api`
3. 把 webhook URL 填到 SLS 告警的"通知"选项

### 8.7 SLS 数据长期归档到 OSS(省费)

SLS 控制台 → Logstore → 数据加工 → OSS 投递:

- Bucket:`fy-api-backup`
- 路径:`sls-archive/%Y%m%d/`
- 压缩:Snappy
- 策略:热数据 SLS 存 30 天,冷数据 OSS 归档 180 天

查询冷数据用 DLA 或 Data Lake Formation,按次计费。

---

## 九、Nginx + SSL + 对外发布

### 9.1 安装 certbot + 申请证书

```bash
sudo dnf install -y certbot python3-certbot-nginx
sudo certbot certonly --standalone -d api.your-domain.com \
  --email sre@your-domain.com --agree-tos --non-interactive
# 证书在 /etc/letsencrypt/live/api.your-domain.com/
```

### 9.2 Nginx 配置(蓝绿 upstream)

```bash
sudo tee /etc/nginx/conf.d/fy-api.conf > /dev/null <<'EOF'
upstream fy_api_backend {
    # 起步时只有 blue,蓝绿发版切换这里
    server 127.0.0.1:3001 max_fails=2 fail_timeout=10s;
    # server 127.0.0.1:3002 backup;   # green 作为备份
    keepalive 64;
}

server {
    listen 80;
    server_name api.your-domain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/api.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.your-domain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         'TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:ECDHE-ECDSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    # 流式 AI 响应可能长,给足超时
    client_max_body_size      16m;
    proxy_connect_timeout     30s;
    proxy_send_timeout        900s;
    proxy_read_timeout        900s;

    # SSE / 流式必须关闭 buffering
    proxy_buffering           off;
    proxy_request_buffering   off;
    proxy_http_version        1.1;
    proxy_set_header Connection "";

    # 真实 IP 给 Fy-api 看(限流用)
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # 日志(独立,方便 SLS 分析)
    access_log /var/log/nginx/fy-api-access.log combined buffer=32k flush=5s;
    error_log  /var/log/nginx/fy-api-error.log warn;

    location / {
        proxy_pass http://fy_api_backend;
    }

    location = /nginx-status {
        stub_status on;
        allow 127.0.0.1;
        deny all;
    }
}
EOF

sudo nginx -t && sudo systemctl reload nginx
sudo systemctl enable nginx
```

### 9.3 证书自动续期

```bash
sudo systemctl enable --now certbot-renew.timer
# 或 cron
echo '0 3 * * * certbot renew --quiet --post-hook "systemctl reload nginx"' | sudo tee -a /etc/crontab
```

### 9.4 Nginx 日志也接 SLS

新建 Logstore `fy-api-nginx-access` 的采集配置:

- 日志路径:`/var/log/nginx/fy-api-access.log`
- 采集模式:正则
- 多行起始正则:`^\d+\.\d+\.\d+\.\d+`
- 字段:`remote_addr`、`time`、`method`、`path`、`status`、`body_size`、`referer`、`ua`、`duration`

---

## 十、蓝绿发版(零停机)

### 10.1 原理

两个容器各占一个端口:

```
┌─ blue  3001 :active
│
└─ green 3002 :standby  → 发版时拉新版,测通后 Nginx 切到 green
```

Nginx 改 `upstream` 指向即可,切换过程 < 1 秒。

### 10.2 发版脚本

```bash
cat > /opt/fy-api/scripts/deploy.sh <<'BASH'
#!/bin/bash
# Usage: ./deploy.sh <new-image-tag>
# Example: ./deploy.sh v0.9.5
set -euo pipefail

NEW_TAG="${1:-}"
[ -z "$NEW_TAG" ] && { echo "usage: $0 <tag>"; exit 1; }

IMAGE=registry-vpc.cn-hangzhou.aliyuncs.com/fy-api/fy-api:$NEW_TAG

# 判断当前活跃颜色
ACTIVE=$(curl -s http://127.0.0.1/nginx-status 2>/dev/null; : )
if podman ps --format '{{.Names}}' | grep -q fy-api-blue; then
  CUR=blue; NEXT=green; NEXT_PORT=3002; CUR_PORT=3001
else
  CUR=green; NEXT=blue; NEXT_PORT=3001; CUR_PORT=3002
fi
echo ">> 当前活跃: $CUR  准备上线: $NEXT ($NEW_TAG)"

# 1) 拉新镜像
podman pull "$IMAGE"

# 2) 停掉残留的 NEXT 容器(如果存在)
podman rm -f "fy-api-$NEXT" 2>/dev/null || true

# 3) 起 NEXT 容器
podman run -d --name "fy-api-$NEXT" \
  --restart=unless-stopped \
  -p "127.0.0.1:$NEXT_PORT:3000" \
  -v /opt/fy-api/logs:/app/logs:Z \
  -v /opt/fy-api/data:/data:Z \
  --env-file /opt/fy-api/config/fy-api.env \
  --ulimit nofile=1048576:1048576 \
  --log-driver=k8s-file --log-opt max-size=100m --log-opt max-file=5 \
  --memory=8g --cpus=4 \
  "$IMAGE" \
  --log-dir=/app/logs

# 4) 健康检查(最多等 60 秒)
echo ">> 等待 $NEXT 健康..."
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$NEXT_PORT/api/status" | grep -q '"success":true'; then
    echo ">> $NEXT 已就绪"; break
  fi
  sleep 1
  [ "$i" -eq 60 ] && { echo "!! $NEXT 未就绪,回滚"; podman rm -f fy-api-$NEXT; exit 2; }
done

# 5) Nginx 切换 upstream
sudo sed -i "s|127.0.0.1:$CUR_PORT|127.0.0.1:$NEXT_PORT|" /etc/nginx/conf.d/fy-api.conf
sudo nginx -t && sudo systemctl reload nginx
echo ">> Nginx 已切到 $NEXT"

# 6) 等现有连接排空,再停旧容器
echo ">> 等 30s 让旧容器连接排空..."
sleep 30
podman stop -t 30 "fy-api-$CUR"
podman rm "fy-api-$CUR"

# 7) 记录
echo "$(date -u +%F-%T) deploy $NEW_TAG: $CUR -> $NEXT" \
  >> /opt/fy-api/deploy-log.md
echo ">> 完成:活跃容器 = fy-api-$NEXT"
BASH

chmod +x /opt/fy-api/scripts/deploy.sh
```

### 10.3 回滚

```bash
# 方式 1:指定旧版本 tag 再跑一次 deploy.sh
/opt/fy-api/scripts/deploy.sh v0.9.4    # 回滚到前一个版本

# 方式 2:如果 NEXT 容器还在(没被 rm),改 Nginx 切回来
# 但按上面脚本 step 6 旧容器会被删,建议保留 4 小时观察再删:
# 注释掉脚本里的 "podman rm" 一行
```

---

## 十一、备份与容灾

### 11.1 RDS

- 自动备份:RDS 控制台默认已开,每日全量 + binlog;**保留时间改到 30 天**
- 跨地域备份:开启(杭州 → 上海),防机房级故障
- 每季度做一次**恢复演练**,步骤见 [`../Phase3-DB-migration-runbook.md`](../Phase3-DB-migration-runbook.md)

### 11.2 Redis

数据非持久,挂了就自动重建限流计数。不需要备份。

### 11.3 配置与镜像

- `/opt/fy-api/config/fy-api.env` — **每次改动手动 copy 一份到 OSS**:
  ```bash
  # 加入 cron
  0 2 * * * aliyun oss cp /opt/fy-api/config/fy-api.env oss://fy-api-backup/config/$(date +\%F)/
  ```
- ACR 镜像:保留所有 prod tag 至少 90 天

### 11.4 ECS 本身

- 云盘快照:每日 03:00 自动,保留 7 天
- 关键变更前手动打快照(打补丁、改 nginx.conf)

---

## 十二、安全基线

### 12.1 网络

- 安全组只开 22 / 80 / 443
- 22 端口仅白名单 IP(你的办公出口 + 堡垒机)
- Fy-api 容器只绑 `127.0.0.1:3001`,不直接对外

### 12.2 凭据

- `.env` 权限 600,属主 root
- RDS / Redis 密码每 90 天轮换(改完 `.env` 后蓝绿发版生效,不用 downtime)
- `SESSION_SECRET` / `CRYPTO_SECRET` **一旦生效别再改**

### 12.3 镜像扫描

```bash
# 拉下来扫一遍(ACR 企业版自带)
podman image trust set --type signedBy --pubkeysfile acr.pub <registry>
# 或用 trivy / grype
trivy image registry-vpc.cn-hangzhou.aliyuncs.com/fy-api/fy-api:v0.9.4
```

### 12.4 审计日志

- Fy-api `record consume log` 已记录每次 API 调用的 userId + 计费
- Nginx access log 记录所有 HTTP 请求
- 两者都进 SLS 180 天归档

### 12.5 Fail2ban(可选)

```bash
sudo dnf install -y fail2ban
# 为 SSH 和 Nginx 各建一条规则,超过 5 次失败封 10 分钟
```

---

## 十三、监控与告警(引用)

Fy-api 本身没有原生 `/metrics`(见 [`observability.md`](./observability.md) §3.1)。

**监控分工**:

| 层面 | 工具 | 目的 |
|---|---|---|
| 业务指标(429 / 5xx / 客户消耗) | **SLS 告警** | 本文 §8.6 |
| HTTP 可用性 / 延迟 / 证书过期 | Prometheus + Blackbox | [`observability.md`](./observability.md) §3 |
| 宿主机 / 容器资源 | Prometheus + node/cAdvisor | 同上 |
| RDS / Redis 健康 | Prometheus + exporter | 同上,或云监控 |

**阿里云云监控**(免费):控制台 → 云监控 → 一键打开 ECS / RDS / Redis 的 CPU、内存、磁盘、QPS 基础指标告警,5 分钟搞定。

等业务起量了再按 [`observability.md`](./observability.md) 上一整套 Prometheus 栈。

---

## 十四、常见故障速查

| 症状 | 诊断 | 修复 |
|---|---|---|
| `/opt/fy-api/logs/` 是空的 | 启动命令忘了 `--log-dir=/app/logs` | 按 §6.2 重启容器 |
| 宿主机目录有旧文件但不更新 | 挂载路径不对或容器在往 `/data/logs` 写 | `podman inspect fy-api-blue \| grep Mounts -A 5` |
| SLS 里没数据 | Logtail 权限 / 机器组标识错 | `sudo tail /usr/local/ilogtail/ilogtail.LOG`,确认机器组标识 |
| 502 Bad Gateway | 容器挂了 / 端口变了 | `podman ps`;看 `/var/log/nginx/fy-api-error.log` |
| 蓝绿切换后 504 | `proxy_read_timeout` 太小,流式被截 | §9.2 的 900s 要保留 |
| 容器频繁 OOM | `GOMEMLIMIT` > `--memory` | `GOMEMLIMIT` ≈ `memory × 0.85` |
| 限流全落一个 IP | slirp4netns SNAT | 确认 §3.2 配了 pasta |
| 多用户 session 串 | 两台机都跑但 Redis 没共享 | 确认 `REDIS_CONN_STRING` 指向同一实例 |
| Gemini 永不命中缓存 | preview 模型不支持隐式缓存 | 换 `gemini-2.5-flash` 稳定版,前缀 ≥ 4096 tokens |
| `panic: dial tcp` | RDS 白名单没加 ECS | 加 ECS 内网 IP / vSwitch CIDR |

---

## 十五、升级路径:迁 SAE 或 ACK

触发条件(任一满足就该评估升级):

- 峰值 RPS > 500 持续 2 周
- 单机 CPU / 内存持续 > 70%
- 付费客户要求 SLA ≥ 99.9%
- 想要多 AZ 容灾
- 要做 A/B / 金丝雀

### 15.1 迁 SAE(最省事)

镜像是同一个,改几个部署参数就行,**2-4 小时能完成**:

1. 把 `.env` 变量迁到 SAE 的"环境变量"配置
2. 挂载配置替换为 NAS(日志打 stdout 让 SAE 托管采集)
3. 把 Nginx 逻辑上的域名 → SAE 的 SLB 地址
4. DNS 切换
5. 原单机保留 7 天作为回退

### 15.2 迁 ACK

按 [`prod-ack.md`](./prod-ack.md) 整篇执行。镜像不变,部署清单全部重写。
估算工作量 3-5 天(含新环境验证和灰度切流)。

---

## 附录 A:上线 Checklist

**基础设施**:

- [ ] ECS 内核参数 + ulimit 已应用
- [ ] Podman 版本 ≥ 4.x,pasta 网络栈已切换
- [ ] `/opt/fy-api/` 目录结构齐
- [ ] 防火墙 / 安全组 80/443 已开

**数据层**:

- [ ] RDS 参数组调优
- [ ] RDS 白名单 + 账号
- [ ] RDS 专属代理已开(或理解后果)
- [ ] RDS 自动备份 30 天 + 跨地域
- [ ] Redis 实例 + 白名单 + 密码

**应用**:

- [ ] `.env` 权限 600,SESSION/CRYPTO secret 不再改
- [ ] 镜像 tag 固定(非 latest)
- [ ] 容器启动命令末尾 `--log-dir=/app/logs`
- [ ] `/opt/fy-api/logs/` 有当前日志文件

**日志**:

- [ ] logrotate 规则已建并 dry-run 通过
- [ ] SLS Project + 4 个 Logstore 已建
- [ ] Logtail 已装 + 机器组标识写入
- [ ] 采集配置应用到机器组,SLS 查询页能看到新数据
- [ ] 5 条告警规则已建 + 钉钉 webhook 测试通过

**对外**:

- [ ] Nginx 配置应用,`nginx -t` 通过
- [ ] HTTPS 证书签发成功,TLS 测试 A 级以上
- [ ] 蓝绿脚本 `/opt/fy-api/scripts/deploy.sh` 跑通一次回滚演练

**安全**:

- [ ] 云盘快照每日自动
- [ ] 云监控基础告警已开
- [ ] 安全组最小化(只开必要端口)
- [ ] `.env` 已备份到 OSS

---

## 附录 B:一键启动模板(供 copy)

```bash
# 见 /opt/fy-api/scripts/start-blue.sh (你自己维护一份)
cat > /opt/fy-api/scripts/start-blue.sh <<'BASH'
#!/bin/bash
set -euo pipefail
IMAGE="${1:-registry-vpc.cn-hangzhou.aliyuncs.com/fy-api/fy-api:v0.9.4}"

podman run -d --name fy-api-blue \
  --restart=unless-stopped \
  -p 127.0.0.1:3001:3000 \
  -v /opt/fy-api/logs:/app/logs:Z \
  -v /opt/fy-api/data:/data:Z \
  --env-file /opt/fy-api/config/fy-api.env \
  --ulimit nofile=1048576:1048576 \
  --log-driver=k8s-file --log-opt max-size=100m --log-opt max-file=5 \
  --memory=8g --cpus=4 \
  "$IMAGE" \
  --log-dir=/app/logs
BASH
chmod +x /opt/fy-api/scripts/start-blue.sh
```

---

## 附录 C:相关文档

- 测试环境:[`test-podman.md`](./test-podman.md)
- ACK 生产版:[`prod-ack.md`](./prod-ack.md)
- 限流开关与按客户限流:[`rate-limiting.md`](./rate-limiting.md)
- 日志 / SLS / Prometheus 详细:[`observability.md`](./observability.md)
- 监控栈配置:[`monitoring/`](./monitoring/)
- DB 迁移:[`../Phase3-DB-migration-runbook.md`](../Phase3-DB-migration-runbook.md)
- 上游同步:[`../Monthly-upstream-sync-runbook.md`](../Monthly-upstream-sync-runbook.md)

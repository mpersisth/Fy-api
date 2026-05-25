# TraceNex 私有化部署指南

本文档面向需要在自有服务器上独立部署 TraceNex API 网关的客户。按照以下步骤操作即可完成部署。

## 前置要求

### 硬件要求

| 规格 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 4 核 | 8 核+ |
| 内存 | 8 GB | 16 GB+ |
| 磁盘 | 40 GB SSD | 100 GB SSD |
| 网络 | 能访问上游 AI 服务商 API | 同左 |

### 软件要求

- 操作系统：Linux（推荐 Debian 12 / Ubuntu 22.04 / CentOS 8+）
- Docker 20.10+ 或 Podman 4.0+
- Docker Compose v2（如使用 Docker 方式部署）

### 需要提前准备

1. **Docker 镜像**：请联系我方技术人员获取镜像仓库地址和登录凭据
2. **域名**：准备一个域名，并将 DNS A 记录指向服务器公网 IP（HTTPS 证书由部署脚本自动申请）
3. **数据库**：可使用内置 PostgreSQL/MySQL，也可对接已有数据库实例

---

## 客户需要提供 / 确认的信息

在开始部署前，请确认以下信息：

| 项目 | 说明 | 示例 |
|------|------|------|
| **服务器公网 IP** | 域名 A 记录需指向此 IP | `203.0.113.10` |
| **域名** | 用于对外提供 API 服务的域名 | `api.yourcompany.com` |
| **邮箱** | 用于 Let's Encrypt 证书注册（接收过期提醒） | `ops@yourcompany.com` |
| **数据库方案** | 使用内置 PostgreSQL，还是对接已有 MySQL/PostgreSQL | 见下方说明 |
| **数据库连接信息**（如对接已有） | 地址、端口、用户名、密码、库名 | `rm-xxx.mysql.rds.aliyuncs.com:3306` |
| **Redis 方案** | 使用内置 Redis，还是对接已有实例 | 见下方说明 |
| **Redis 连接信息**（如对接已有） | 地址、端口、密码 | `r-xxx.redis.rds.aliyuncs.com:6379` |

以下信息由部署过程自动生成，无需客户准备：

- `SESSION_SECRET` / `CRYPTO_SECRET`：部署时在服务器上生成
- HTTPS 证书：由 Let's Encrypt 自动签发和续期
- 管理员账号：首次启动后创建

---

## 方式一：Docker Compose 部署（推荐）

适合快速部署、单机场景。

### 第 1 步：创建工作目录

```bash
mkdir -p /opt/tracenex && cd /opt/tracenex
```

### 第 2 步：创建 docker-compose.yml

```yaml
version: '3.4'

services:
  tracenex:
    image: <镜像地址，请联系技术人员获取>
    container_name: tracenex
    restart: always
    command: --log-dir /app/logs
    ports:
      - "127.0.0.1:3000:3000"
    volumes:
      - ./data:/data
      - ./logs:/app/logs
    environment:
      - SQL_DSN=postgresql://root:123456@postgres:5432/tracenex
      - REDIS_CONN_STRING=redis://:123456@redis:6379
      - SESSION_SECRET=<用 openssl rand -hex 32 生成>
      - CRYPTO_SECRET=<用 openssl rand -hex 32 生成>
      - FRONTEND_BASE_URL=https://你的域名
      - NODE_NAME=tracenex-node-1
      - TZ=Asia/Shanghai
      - MEMORY_CACHE_ENABLED=true
      - BATCH_UPDATE_ENABLED=true
      - SYNC_FREQUENCY=60
      - ERROR_LOG_ENABLED=true
    depends_on:
      - redis
      - postgres
    networks:
      - tracenex-network

  redis:
    image: redis:7
    container_name: tracenex-redis
    restart: always
    command: ["redis-server", "--requirepass", "123456"]
    networks:
      - tracenex-network

  postgres:
    image: postgres:15
    container_name: tracenex-postgres
    restart: always
    environment:
      POSTGRES_USER: root
      POSTGRES_PASSWORD: "123456"
      POSTGRES_DB: tracenex
    volumes:
      - pg_data:/var/lib/postgresql/data
    networks:
      - tracenex-network

volumes:
  pg_data:

networks:
  tracenex-network:
    driver: bridge
```

> **重要**：上述配置中的 `123456` 为示例密码，部署前务必修改为强密码。

### 第 3 步：生成安全密钥

```bash
# 生成 SESSION_SECRET
openssl rand -hex 32

# 生成 CRYPTO_SECRET
openssl rand -hex 32
```

将生成的两个密钥分别填入 `docker-compose.yml` 中对应的环境变量。

### 第 4 步：登录镜像仓库并启动服务

```bash
# 登录镜像仓库（地址和凭据请联系技术人员获取）
docker login <镜像仓库地址>

# 启动所有服务
cd /opt/tracenex
docker compose up -d

# 查看运行状态
docker compose ps
```

> **下一步**：服务启动后，请先完成下方「配置 Nginx 反向代理与 HTTPS」章节，再继续第 5 步。

### 第 5 步：配置 Nginx 与 HTTPS

参见下方「配置 Nginx 反向代理与 HTTPS」章节完成域名和证书配置。完成后通过 `https://你的域名` 访问服务。

### 第 6 步：初始化管理员账号

服务启动后，执行以下命令创建管理员（请替换密码）：

```bash
curl -X POST http://127.0.0.1:3000/api/setup \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "你的管理员密码",
    "confirmPassword": "你的管理员密码",
    "SelfUseModeEnabled": false,
    "DemoSiteEnabled": false
  }'
```

### 第 7 步：验证部署

```bash
# 检查服务健康状态
curl http://127.0.0.1:3000/api/status

# 预期返回：{"message":"","success":true,"data":{"start_time":...}}
```

验证通过后，打开浏览器访问 `https://你的域名`，使用第 6 步创建的管理员账号登录。

### 第 8 步：添加渠道

登录管理后台后，进入「渠道」页面，添加上游 AI 服务商的 API Key：

1. 点击「添加新的渠道」
2. 选择类型（如 OpenAI、Claude、Gemini 等）
3. 填入对应的 API Key
4. 选择该渠道支持的模型
5. 点击「提交」

添加完成后，系统即可通过 OpenAI 兼容接口转发请求。

---

## 方式二：使用 MySQL 替代 PostgreSQL

如需使用已有的 MySQL 数据库，修改 `docker-compose.yml`：

1. 删除 `postgres` 服务和 `pg_data` 卷
2. 将 `SQL_DSN` 改为 MySQL 格式：

```bash
SQL_DSN=用户名:密码@tcp(数据库地址:3306)/数据库名?charset=utf8mb4&parseTime=True&loc=Local
```

MySQL 版本要求：≥ 5.7.8。

如使用外部 PostgreSQL，格式为：

```bash
SQL_DSN=postgresql://用户名:密码@数据库地址:5432/数据库名
```

---

## 方式三：对接已有数据库和 Redis

如果您已有 MySQL/PostgreSQL 和 Redis 实例，可以只部署应用容器：

```yaml
version: '3.4'

services:
  tracenex:
    image: <镜像地址，请联系技术人员获取>
    container_name: tracenex
    restart: always
    command: --log-dir /app/logs
    ports:
      - "127.0.0.1:3000:3000"
    volumes:
      - ./data:/data
      - ./logs:/app/logs
    environment:
      - SQL_DSN=用户名:密码@tcp(你的数据库地址:3306)/tracenex?charset=utf8mb4&parseTime=True&loc=Local
      - REDIS_CONN_STRING=redis://:密码@你的Redis地址:6379/0
      - SESSION_SECRET=<openssl rand -hex 32>
      - CRYPTO_SECRET=<openssl rand -hex 32>
      - FRONTEND_BASE_URL=https://你的域名
      - NODE_NAME=tracenex-node-1
      - TZ=Asia/Shanghai
      - MEMORY_CACHE_ENABLED=true
      - BATCH_UPDATE_ENABLED=true
      - SYNC_FREQUENCY=60
      - ERROR_LOG_ENABLED=true
```

> 首次启动时，应用会自动创建所需的数据库表（AutoMigrate），无需手动导入 SQL。

---

## 配置 Nginx 反向代理与 HTTPS

Nginx 提供 HTTPS 终止和域名访问，是生产部署的必要组件。

### 安装 Nginx

```bash
# Debian/Ubuntu
apt-get install -y nginx certbot python3-certbot-nginx

# CentOS/RHEL
yum install -y nginx certbot python3-certbot-nginx
```

### 配置反向代理

创建 `/etc/nginx/conf.d/tracenex.conf`：

```nginx
server {
    listen 80;
    server_name 你的域名;

    client_max_body_size 64m;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE/流式响应支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
```

### 申请 HTTPS 证书

```bash
# 确保域名 DNS 已解析到本机 IP，然后执行：
certbot --nginx -d 你的域名 --email 你的邮箱 --agree-tos --non-interactive

# 验证 Nginx 配置
nginx -t && systemctl reload nginx

# 验证证书自动续期
certbot renew --dry-run
```

证书有效期 90 天，certbot 会自动续期。

---

## 性能调优

根据服务器规格调整以下环境变量：

| 环境变量 | 4c8g | 8c16g | 16c32g |
|----------|------|-------|--------|
| `GOMAXPROCS` | 3 | 6 | 12 |
| `GOMEMLIMIT` | 5000MiB | 10000MiB | 20000MiB |
| `SQL_MAX_IDLE_CONNS` | 30 | 50 | 100 |
| `SQL_MAX_OPEN_CONNS` | 100 | 200 | 500 |
| `RELAY_TIMEOUT` | 600 | 600 | 600 |
| `STREAMING_TIMEOUT` | 300 | 300 | 300 |

在 `docker-compose.yml` 的 `environment` 中添加即可。

---

## 升级版本

```bash
cd /opt/tracenex

# 1. 拉取新镜像（版本号由技术人员提供）
docker compose pull

# 2. 重启服务（数据不会丢失）
docker compose up -d

# 3. 验证
curl http://127.0.0.1:3000/api/status
```

升级过程中会有短暂中断（约 5-10 秒）。如需零停机升级，请联系技术人员协助配置蓝绿部署。

---

## 数据备份

### 数据库备份

**PostgreSQL（内置）：**

```bash
docker exec tracenex-postgres pg_dump -U root tracenex > backup_$(date +%F).sql
```

**MySQL（外部）：**

```bash
mysqldump -h 数据库地址 -u 用户名 -p 数据库名 > backup_$(date +%F).sql
```

### 应用数据备份

```bash
# 备份上传文件和本地数据
tar -czf tracenex-data_$(date +%F).tar.gz /opt/tracenex/data/
```

建议设置定时任务每日备份。

---

## 常见问题

### Q: 启动后无法访问页面

1. 检查容器是否正常运行：`docker compose ps`
2. 检查端口是否被占用：`ss -tlnp | grep 3000`
3. 检查防火墙是否放行 3000 端口
4. 查看容器日志：`docker compose logs tracenex`

### Q: 数据库连接失败

1. 确认数据库地址、端口、用户名、密码正确
2. 确认数据库服务已启动
3. 如使用云数据库，确认安全组/白名单已放行服务器 IP
4. 检查 `SQL_DSN` 格式是否正确

### Q: Redis 连接失败

1. 确认 Redis 地址和密码正确
2. 确认 Redis 服务已启动
3. 如使用云 Redis，确认内网访问已开通
4. 不配置 Redis 时系统仍可运行，但缓存和限流功能不可用

### Q: 流式响应中断或超时

在环境变量中增大超时时间：

```bash
STREAMING_TIMEOUT=600
RELAY_TIMEOUT=600
```

### Q: 如何查看日志

```bash
# 容器标准输出
docker compose logs -f tracenex

# 应用落盘日志
ls /opt/tracenex/logs/
tail -f /opt/tracenex/logs/oneapi-*.log
```

---

## 环境变量完整参考

| 变量名 | 必填 | 说明 | 示例 |
|--------|------|------|------|
| `SQL_DSN` | 是 | 数据库连接串 | 见上文 |
| `REDIS_CONN_STRING` | 推荐 | Redis 连接串 | `redis://:pass@host:6379/0` |
| `SESSION_SECRET` | 是 | 会话加密密钥（64 位 hex） | `openssl rand -hex 32` |
| `CRYPTO_SECRET` | 是 | 数据加密密钥（64 位 hex） | `openssl rand -hex 32` |
| `FRONTEND_BASE_URL` | 是 | 前端访问地址 | `https://api.example.com` |
| `NODE_NAME` | 推荐 | 节点标识 | `tracenex-node-1` |
| `TZ` | 否 | 时区 | `Asia/Shanghai` |
| `PORT` | 否 | 监听端口（默认 3000） | `3000` |
| `MEMORY_CACHE_ENABLED` | 推荐 | 启用内存缓存 | `true` |
| `BATCH_UPDATE_ENABLED` | 推荐 | 启用批量写入 | `true` |
| `SYNC_FREQUENCY` | 否 | 配置同步间隔（秒） | `60` |
| `STREAMING_TIMEOUT` | 否 | 流式超时（秒） | `300` |
| `RELAY_TIMEOUT` | 否 | 请求总超时（秒） | `600` |
| `ERROR_LOG_ENABLED` | 否 | 记录错误日志 | `true` |

---

## 技术支持

如部署过程中遇到问题，请联系技术人员获取支持：

- 镜像地址和访问凭据
- 版本升级通知
- 蓝绿部署/零停机升级方案
- 多节点高可用架构咨询

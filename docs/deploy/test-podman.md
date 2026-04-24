# 测试环境部署 Runbook（Podman）

> 读者：负责测试环境的工程师
> 目标：从零到 Fy-api 在测试服务器上跑起来、能供 QA 点击、持续 14 天以上稳定运行
> 数据库：**连远端阿里云 RDS**（与生产同构，验证 schema 兼容）
> 运行时：**Podman**（rootless，生产级的兼容测试；本地可用 Docker Desktop / OrbStack 等效验证）

---

## 一、前置准备（一次性）

### 1.1 服务器基线

| 项 | 要求 |
|----|------|
| OS | 任意主流 Linux（Rocky / RHEL 9、Ubuntu 22.04+、Debian 12+、Fedora 40+） |
| 架构 | x86_64（若是 arm64 服务器，后续构建命令加 `--platform linux/arm64`） |
| 内存 | ≥ 8 GB（构建阶段 vite 吃内存，≤ 4 GB 可能 OOM） |
| 磁盘 | ≥ 30 GB 可用（镜像约 200 MB，但构建缓存和 RDS 导出副本会占空间） |
| 网络 | 出站可访问：`docker.io`、`registry.access.redhat.com`、`github.com`、目标 RDS 实例 |

### 1.2 安装 Podman

**RHEL / Rocky / Fedora：**
```bash
sudo dnf install -y podman podman-compose
```

**Ubuntu / Debian：**
```bash
sudo apt-get update
sudo apt-get install -y podman podman-compose
```

**版本校验**（要求 ≥ 4.x，建议 5.x）：
```bash
podman --version          # 期望：podman version 5.x
podman-compose --version  # 期望：podman-compose version 1.5+
```

### 1.3 Rootless 下的网络与用户映射

podman 默认 rootless，对测试环境有 3 个**一次性配置**：

```bash
# ① 允许普通用户跑容器（Fedora / RHEL 默认已就绪，Ubuntu/Debian 可能需要）
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80  # 若要用 80/443 端口
sudo systemctl --user enable --now podman.socket       # rootless socket

# ② 增加 UID/GID 映射范围（SLIRP / pasta 需要）
grep "^$USER:" /etc/subuid /etc/subgid
# 若无输出：
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $USER
podman system migrate  # 应用 subuid 变化

# ③ SELinux 系统（RHEL/CentOS/Fedora）需要在 volume 挂载时加 :Z
getenforce  # Enforcing / Permissive / Disabled，前两种情况容器挂载需加 :Z
```

### 1.4 RDS 实例准备

与运维确认以下条目并记录（**写到 `.env.test` 里，不要提交 git**）：

| 字段 | 示例值 | 说明 |
|------|--------|------|
| `DB_HOST` | `rm-xxxxxxx.mysql.rds.aliyuncs.com` | 阿里云 RDS 内网 endpoint（若服务器在同 VPC）或公网 endpoint + 白名单 |
| `DB_PORT` | `3306`（MySQL）/ `5432`（PostgreSQL） | — |
| `DB_NAME` | `fy_api_test` | **独立库**，不要与 prod 混用 |
| `DB_USER` | `fy_api_app` | 最小权限账号：`CREATE TABLE / ALTER TABLE / SELECT / INSERT / UPDATE / DELETE / INDEX` |
| `DB_PASSWORD` | — | 长随机字符串 |
| `DB_SSL` | `true` / `false` | 阿里云公网连接建议开启 |

**连接性提前验证**（跳过这步后面启动容器会 panic）：
```bash
# MySQL
mysql -h$DB_HOST -P$DB_PORT -u$DB_USER -p$DB_PASSWORD $DB_NAME -e "SELECT 1;"

# PostgreSQL
PGPASSWORD=$DB_PASSWORD psql -h$DB_HOST -p$DB_PORT -U$DB_USER -d$DB_NAME -c "SELECT 1;"
```

**确认 RDS 白名单**包含测试服务器的出口 IP（运维配置）。

---

## 二、部署文件落地

### 2.1 代码同步

```bash
cd ~
git clone git@github.com:seraph0017/Fy-api.git
cd Fy-api
git log -1 --format="%h %s"    # 记录当前 commit，便于回滚
```

> **注意**：`upstream` 远程指向只读的 `QuantumNous/new-api`。**从不 push 到 upstream**。每月同步按 [`../Monthly-upstream-sync-runbook.md`](../Monthly-upstream-sync-runbook.md) 执行。

### 2.2 写测试环境 compose 文件

在 `~/Fy-api/` 新建 `compose.test.yml`（这个文件已加 `.gitignore`，每台服务器可以有不同配置）：

```yaml
# compose.test.yml — Fy-api 测试环境（podman + 远端 RDS + 可选本地 redis）
services:
  fy-api:
    image: fy-api:local
    container_name: fy-api
    restart: unless-stopped
    ports:
      - "3000:3000"
    volumes:
      # :Z 仅 SELinux 系统需要；Ubuntu/Debian 去掉 :Z
      - ./data:/data:Z
      - ./logs:/app/logs:Z
    env_file:
      - ./.env.test
    environment:
      - TZ=Asia/Shanghai
      - ERROR_LOG_ENABLED=true
      - NODE_NAME=fy-api-test-1
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3000/api/status | grep -o '\"success\":[[:space:]]*true' || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # 可选：本地 redis 做缓存 + rate limit（如果测试环境不配阿里云 redis）
  redis:
    image: redis:7-alpine
    container_name: fy-api-redis
    restart: unless-stopped
    volumes:
      - ./redis-data:/data:Z
    command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
```

### 2.3 写 `.env.test`（敏感配置，永远不进 git）

```bash
cat > .env.test <<'EOF'
# ========== 数据库（阿里云 RDS）==========
# MySQL 示例：
SQL_DSN=fy_api_app:YOUR_PASSWORD_HERE@tcp(rm-xxxxxxx.mysql.rds.aliyuncs.com:3306)/fy_api_test?charset=utf8mb4&parseTime=True&loc=Local&tls=skip-verify
# PostgreSQL 示例（注释掉上面一行，启用下面一行）：
# SQL_DSN=postgres://fy_api_app:YOUR_PASSWORD_HERE@rm-xxxxxxx.pg.rds.aliyuncs.com:5432/fy_api_test?sslmode=require

# ========== Redis ==========
REDIS_CONN_STRING=redis://redis:6379

# ========== 会话加密密钥 ==========
# 首次部署时生成；两台及以上节点必须一致
# openssl rand -hex 32
SESSION_SECRET=CHANGE_ME_64_HEX_CHARS

# 令牌 / 渠道密钥 AES 加密密钥
CRYPTO_SECRET=CHANGE_ME_64_HEX_CHARS

# ========== 其他 ==========
GLOBAL_WEB_RATE_LIMIT_NUM=240          # /api/* 每分钟请求上限
GLOBAL_WEB_RATE_LIMIT_DURATION=60
FRONTEND_BASE_URL=http://<test-server-fqdn>:3000
EOF

chmod 600 .env.test   # 防误读
```

**密钥生成一次性命令**：
```bash
# SESSION_SECRET 和 CRYPTO_SECRET 必须是 32 字节十六进制
echo "SESSION_SECRET=$(openssl rand -hex 32)"
echo "CRYPTO_SECRET=$(openssl rand -hex 32)"
```

> **安全提示**：`.env.test` 文件一旦泄露等价于漏泄整个网关数据库。建议：①设置文件权限 600；② 不要在 chat/IM/wiki 截图；③ 同步 `SESSION_SECRET`/`CRYPTO_SECRET` 时走加密通道（1Password、Bitwarden、Vault）。

---

## 三、镜像构建

### 3.1 服务器直接构建（推荐）

```bash
cd ~/Fy-api

# Dockerfile 是多阶段：bun(frontend) → go(backend) → debian(runtime)
# 首次构建 5-15 分钟（主要在 bun install + vite build + golang module download）
podman build -t fy-api:local .

# 查看镜像
podman images | grep fy-api
# 预期：localhost/fy-api   local   xxxxxxx   <1 min ago   ~190 MB
```

**构建常见坑**：

| 症状 | 原因 | 对策 |
|------|------|------|
| `bun install` 超时 | docker.io 拉包慢 | `podman build --network=host .` 走宿主出口 |
| `go mod download` 卡住 | Go 模块镜像不可达 | Dockerfile 里加 `ENV GOPROXY=https://goproxy.cn,direct`（一次性修改不 commit） |
| `vite build` 被 kill / exit 137 | 内存不足 | 给服务器加内存；或改 `package.json` 的 `build` 脚本加 `NODE_OPTIONS=--max_old_space_size=4096` |
| 架构不对 | arm64 服务器跑默认 amd64 | 加 `--platform linux/arm64` |
| 依赖 hash 过期 | Dockerfile 里 `@sha256:` pin 过期 | 短期：注释掉 `@sha256:` 后缀；长期：PR 升级 Dockerfile |

### 3.2 本地构建 → 服务器导入（网络差时备选）

```bash
# 本地
podman build --platform linux/amd64 -t fy-api:local .
podman save fy-api:local -o /tmp/fy-api.tar

# 传输
scp /tmp/fy-api.tar user@test-server:/tmp/

# 服务器
podman load -i /tmp/fy-api.tar
rm /tmp/fy-api.tar
```

---

## 四、首次启动

```bash
cd ~/Fy-api
mkdir -p data logs redis-data    # :Z 挂载前预建，避免 root 拥有

podman-compose -f compose.test.yml up -d
# 或 podman 4.4+ 的内置插件：
# podman compose -f compose.test.yml up -d

# 监控启动（Ctrl+C 退出）
podman logs -f fy-api
```

**期望启动日志**：
```
[SYS] xxxx/xx/xx | using MySQL (or PostgreSQL) as database
[SYS] xxxx/xx/xx | database migration started
[SYS] xxxx/xx/xx | i18n initialized with languages: zh-CN, zh-TW, en
[SYS] xxxx/xx/xx | Fy-api ... started
  ➜  Fy-api  ready in xxx ms
```

**启动失败排障**：

```bash
# 容器状态
podman ps -a | grep fy-api

# 完整日志
podman logs --tail 200 fy-api

# 健康检查状态
podman inspect fy-api | jq '.[0].State.Health'

# 容器 exec 进去查配置
podman exec -it fy-api sh
```

常见失败原因：

| 日志关键字 | 含义 | 对策 |
|-----------|------|------|
| `dial tcp ...: i/o timeout` | RDS 不可达 | 检查 RDS 白名单、安全组、`SQL_DSN` 格式 |
| `Access denied for user` | 账号密码错 | 对照 RDS 控制台账号密码，注意特殊字符需 URL encode |
| `database does not exist` | 库名不对或未创建 | RDS 控制台先 CREATE DATABASE |
| `SESSION_SECRET must be set` | 首次启动要求提供 | 检查 `.env.test` 是否生效 |
| 容器一直 `starting` 不 `healthy` | 应用启动 > 30s | 正常，首次 AutoMigrate 耗时；30s 后再看 |

---

## 五、冒烟验证

部署完成 30 秒后跑下面所有命令，任意一项失败就别告诉 QA 可测：

```bash
HOST=http://localhost:3000  # 或 http://<test-fqdn>

# 1. 健康检查
curl -sf $HOST/api/status | grep -q '"success":true' && echo "✅ status OK" || echo "❌ status FAIL"

# 2. 品牌词
curl -s $HOST/api/status | grep -o '"system_name":"[^"]*"'
# 期望：{"system_name":"Fy-api"}

# 3. Request-ID 响应头
curl -sI $HOST/api/status | grep -i "X-Oneapi-Request-Id"
# 期望：X-Oneapi-Request-Id: 2026......

# 4. 前端 HTML
curl -s $HOST/ | grep -oE "<title>[^<]*</title>"
# 期望：<title>Fy-api</title>

# 5. 内嵌产品文档
curl -s -o /dev/null -w "HTTP %{http_code}\n" $HOST/product-docs/Fy-api.md
# 期望：HTTP 200

# 6. Setup 状态
curl -s $HOST/api/setup | python3 -m json.tool
# 期望看到 "success":true
```

---

## 六、首次初始化 root 管理员

Fy-api 首次启动时没有 root 用户，必须通过 `/api/setup` 接口建立：

```bash
# 准备账号密码，至少 8 位
read -s ROOT_PASS
curl -s -X POST $HOST/api/setup \
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
# 期望：{"message":"系统初始化成功","success":true}

unset ROOT_PASS
```

现在浏览器 `http://<test-fqdn>:3000` 可以用 `fyadmin` 登录了。

---

## 七、回归测试清单（Fy-api overlay 验收）

| 功能 | 路径 | 通过条件 |
|------|------|----------|
| 品牌词 | `/api/status` JSON | `system_name:"Fy-api"` |
| 页面 title | 任意页 | `<title>Fy-api</title>` |
| Favicon | `/favicon.ico` | 200，内容非空 |
| Logo | `/new_logo.png` | 200 |
| 登录按钮排序 | `/login` 浏览器 | 邮箱/用户名登录按钮在最上方；"没有账户？注册"**始终**显示 |
| 产品文档 | `/docs` 浏览器 | 渲染"Fy-api 说明手册"，18 张截图全部加载 |
| 管理后台 CSV 导出 | 用量日志页面 | 顶部有"导出 CSV"按钮，点击下载 UTF-8+BOM 文件 |
| 后端 CSV API | `GET /api/log/export?type=0` | 带 cookie + `New-Api-User: 1` header，返回 200 CSV，**表头含 `request_id` 列** |
| i18n 切换 | 管理后台右上角 | zh-CN / zh-TW / en / ja / ru / fr / vi 七种语言，品牌词均为 Fy-api |

验证 CSV 导出端到端（admin cookie 已登录）：
```bash
# 登录拿 cookie
curl -c /tmp/fy-cookies.txt -X POST $HOST/api/user/login \
  -H "Content-Type: application/json" \
  -d '{"username":"fyadmin","password":"'"$ROOT_PASS"'"}'

# 导出
curl -b /tmp/fy-cookies.txt -H "New-Api-User: 1" \
  -o /tmp/logs.csv $HOST/api/log/export?type=0
file /tmp/logs.csv              # 应为 Unicode text, UTF-8 (with BOM)
head -1 /tmp/logs.csv           # 表头
head -1 /tmp/logs.csv | tr ',' '\n' | grep -n request_id   # 应在第 14 列
```

---

## 八、日常运维

### 8.1 查看日志

```bash
# 容器 stdout
podman logs --tail 200 --since 1h fy-api

# 应用日志（ERROR_LOG_ENABLED=true 时会写 /app/logs/）
ls -lh logs/
tail -f logs/error.log
```

### 8.2 发版 / 更新代码

```bash
cd ~/Fy-api
git pull origin main

# 重建镜像（利用 bun/go 层缓存，通常 1-3 分钟）
podman build -t fy-api:local .

# 滚动重启
podman-compose -f compose.test.yml up -d --force-recreate fy-api

# 查状态
podman ps
podman logs --tail 50 fy-api
```

### 8.3 资源监控

```bash
# 容器资源
podman stats --no-stream fy-api fy-api-redis

# 磁盘
du -sh data/ logs/ redis-data/
df -h

# 宿主机
top -p $(pgrep -f "new-api")
```

### 8.4 停止 / 启动 / 重启

```bash
# 停
podman-compose -f compose.test.yml down

# 完全清空（保留 DB 数据在 RDS，本地只清容器/镜像缓存）
podman-compose -f compose.test.yml down
podman rmi fy-api:local
rm -rf data/ logs/ redis-data/

# 重新拉起
podman-compose -f compose.test.yml up -d
```

### 8.5 配合 systemd 开机自启（可选）

```bash
# 生成 systemd unit（用户级）
mkdir -p ~/.config/systemd/user
podman generate systemd --name fy-api --files --new
mv container-fy-api.service ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now container-fy-api.service

# 允许用户服务在无 ssh 会话时继续运行
sudo loginctl enable-linger $USER
```

---

## 九、常见故障速查

| 症状 | 可能原因 | 诊断 | 修复 |
|------|---------|------|------|
| 浏览器打不开 | 防火墙未放行 3000 | `ss -tlnp \| grep 3000`、`curl localhost:3000` | 放行 3000 或加 nginx 反代 |
| `502 Bad Gateway` | 容器 crash | `podman ps` / `podman logs fy-api` | 按日志修，见 §4 排障表 |
| 页面加载但 `/api/*` 500 | DB 连接断 | `podman logs fy-api \| grep -i "dial\|error"` | 查 RDS 状态、白名单、`SQL_DSN` |
| 登录时 `session invalid` | SESSION_SECRET 被改 | 比对 `.env.test` 与当前 `env` | 保持 secret 不变；已改需清浏览器 cookie |
| 多节点部署用户频繁被踢 | Redis 未共享 | 检查 REDIS_CONN_STRING | 所有节点连同一个 Redis |
| `/docs` 页面 301 循环 | 物理目录与路由冲突（已修） | `curl -I $HOST/docs` | 升级到最新代码（含 commit `7ffcc5c4`） |
| CSV 下载乱码 | BOM 丢失 / 非 UTF-8 打开 | `xxd logs.csv \| head -1` | 正确响应第一字节是 `ef bb bf`；Excel 选"数据→从文本" |
| podman build 频繁 OOM | VM 内存不足（仅 mac/Docker Desktop） | — | 加内存或用 orbstack |

---

## 十、停用环境（退役）

```bash
cd ~/Fy-api
podman-compose -f compose.test.yml down
rm -rf data/ logs/ redis-data/

# RDS 测试库
# 通知 DBA：DROP DATABASE fy_api_test;

# 本地镜像 + systemd service
podman rmi fy-api:local
systemctl --user disable --now container-fy-api.service 2>/dev/null
rm -f ~/.config/systemd/user/container-fy-api.service
systemctl --user daemon-reload

# 代码仓库
rm -rf ~/Fy-api
```

---

## 附录 A：最小可用 `.env.test` 字段清单

| 字段 | 必填 | 说明 |
|------|:---:|------|
| `SQL_DSN` | ✅ | 远端 RDS 连接串 |
| `SESSION_SECRET` | ✅ | 会话加密 64 位 hex |
| `CRYPTO_SECRET` | ✅ | 令牌/渠道密钥加密 64 位 hex |
| `REDIS_CONN_STRING` | 🟡 | 单节点可不配；**多节点必配** |
| `FRONTEND_BASE_URL` | 🟡 | OAuth 回调、邮件里的链接会引用；不配默认本机 |
| `GLOBAL_WEB_RATE_LIMIT_NUM` | ⚪ | 默认 240/min，够用 |
| `TZ` | ⚪ | 默认 `Asia/Shanghai` |

完整环境变量见上游文档：<https://docs.newapi.pro/zh/docs/installation/config-maintenance/environment-variables>

## 附录 B：与生产的差异点

| 维度 | 测试（本 runbook） | 生产（ACK） |
|------|--------------------|-------------|
| 运行时 | 单机 Podman | ACK Deployment + HPA |
| 实例数 | 1 | 2-N（HPA 控制） |
| 入口 | 直接 port 3000 或 nginx 反代 | Nginx Ingress + cert-manager |
| 镜像源 | 本地 build 或 `podman save/load` | ACR 运维手动 push |
| Redis | 本地 container 或阿里云 Redis | 阿里云 Redis（生产规格） |
| 日志 | 容器 stdout + 本地文件 | stdout 聚合到 SLS/ELK |
| 监控 | 无或简单 `podman stats` | Prometheus + 阿里云 ARMS |

生产部署见 [`prod-ack.md`](./prod-ack.md)。

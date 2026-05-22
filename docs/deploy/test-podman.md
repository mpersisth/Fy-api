# 测试环境部署 Runbook（Podman）

> 读者：负责测试环境的工程师
> 目标：从零到 TraceNex 在测试服务器上跑起来、能供 QA 点击、持续 14 天以上稳定运行
> 数据库：**连远端阿里云 RDS**（与生产同构，验证 schema 兼容）
> 运行时：**Podman**（rootless，生产级的兼容测试；本地可用 Docker Desktop / OrbStack 等效验证）

---

## 一、前置准备（一次性）

### 1.0 阿里云低成本测试环境清单

测试环境优先目标是 QA 联调、回归、验证 RDS schema 与 Redis/session 行为，不按生产压测规格采购。

| 资源 | 最低可用 | 推荐低配 | 备注 |
|------|----------|----------|------|
| ECS | `2c4g` / 40-80G ESSD | `4c8g` / 80G ESSD | `2c4g` 可跑单容器；如果在服务器上构建镜像或同时跑 TraceNexBiz，建议 `4c8g` |
| EIP | 1-5 Mbps 按量 | 5 Mbps 按量 | 仅供 QA 访问；大文件/流式压测再临时升带宽 |
| RDS MySQL 8.0 | 1c2g / 40G | 1c2g 或 2c4g / 50-100G | 独立测试库，禁止复用 prod |
| Redis/Tair | 可不配 | 256-512MB | 单节点 smoke 可不配；多节点/session/rate-limit 联调必须配 |
| ACR | 个人版/基础私有仓库 | 基础私有仓库 | 测试镜像也必须打明确版本 tag |
| SLS | 可选，7 天保留 | 7-15 天保留 | 需要排查跨服务 trace 时开启 |

省钱做法：前端静态资源直接由 Nginx 服务或临时本地构建，不在测试机上长跑 4 个 dev server；ECS 只跑 `fy-api` 容器和 Nginx。

### 1.1 服务器基线

| 项 | 要求 |
|----|------|
| OS | 任意主流 Linux（Rocky / RHEL 9、Ubuntu 22.04+、Debian 12+、Fedora 40+） |
| 架构 | x86_64（若是 arm64 服务器，后续构建命令加 `--platform linux/arm64`） |
| 内存 | 运行 ≥ 4 GB；服务器本机构建建议 ≥ 8 GB（构建阶段 vite 吃内存，≤ 4 GB 可能 OOM） |
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
cd TraceNex
git log -1 --format="%h %s"    # 记录当前 commit，便于回滚
```

> **注意**：`upstream` 远程指向只读的 `QuantumNous/new-api`。**从不 push 到 upstream**。每周一按工作区 `apiGateway/docs/Weekly-upstream-sync-runbook.md` 执行同步（注意这份 runbook 在工作区根目录的 docs/ 下，不在 Fy-api/docs/ 里）。

### 2.2 写测试环境 compose 文件

在 `~/TraceNex/` 新建 `compose.test.yml`（这个文件已加 `.gitignore`，每台服务器可以有不同配置）：

```yaml
# compose.test.yml — TraceNex 测试环境（podman + 远端 RDS，无 Redis）
# 单节点测试环境不需要 Redis——TraceNex 会自动用内存缓存。
# 多节点 / staging 验证多 pod 会话时，Redis 建议走阿里云 Redis 实例而不是本地容器
# （podman-compose 对 network_mode: service:xxx 的支持不稳定，跨容器 DNS 也偶发）。
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
```

> **什么时候需要 Redis？**
> - 要跑 2+ pod 验证多实例会话（否则每个 pod 自己的内存缓存各干各的）
> - 压测 rate limit 的跨节点一致性
>
> **怎么加 Redis？** 走阿里云 Redis 实例，`.env.test` 里 `REDIS_CONN_STRING=redis://:<pass>@r-xxx.redis.rds.aliyuncs.com:6379` 即可；本地 podman 容器里跑 Redis 不推荐（podman-compose 的 `network_mode: service:redis` 不稳定，跨容器 DNS 也常失灵）。

### 2.3 写 `.env.test`（敏感配置，永远不进 git）

```bash
cat > .env.test <<'EOF'
# ========== 数据库（阿里云 RDS）==========
# MySQL 示例：
# 【重要】阿里云 RDS 默认未开启 SSL，不要加 tls 参数。
# 若 RDS 控制台里开了 SSL，改为 tls=true 并提供 CA 证书（见上游文档 TLS 章节）。
SQL_DSN=fy_api_app:YOUR_PASSWORD_HERE@tcp(rm-xxxxxxx.mysql.rds.aliyuncs.com:3306)/fy_api_test?charset=utf8mb4&parseTime=True&loc=Local
# PostgreSQL 示例（注释掉上面一行，启用下面一行）：
# SQL_DSN=postgres://fy_api_app:YOUR_PASSWORD_HERE@rm-xxxxxxx.pg.rds.aliyuncs.com:5432/fy_api_test?sslmode=require

# ========== Redis（可选）==========
# 单节点测试不配 Redis，TraceNex 自动用内存缓存。
# 多节点或需要跨 pod 会话共享时，才配阿里云 Redis 实例的内网地址：
# REDIS_CONN_STRING=redis://:password@r-xxxxxxx.redis.rds.aliyuncs.com:6379

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
cd ~/TraceNex

# Dockerfile 是多阶段：bun(frontend) → go(backend) → debian(runtime)
# 首次构建 5-15 分钟（主要在 bun install + vite build + golang module download）
podman build -t fy-api:local .

# 查看镜像。测试/发版镜像也必须打明确版本 tag，不能只用 latest。
podman images | grep fy-api
# 预期：localhost/fy-api   local   xxxxxxx   <1 min ago   ~190 MB
```

如镜像会推到 ACR，按工作区版本规则打 tag，例如：

```bash
VERSION=1.2.1-tracenex
ACR=registry-vpc.cn-hangzhou.aliyuncs.com/fy-api/fy-api
podman tag fy-api:local ${ACR}:${VERSION}
podman push ${ACR}:${VERSION}
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
cd ~/TraceNex
mkdir -p data logs                      # :Z 挂载前预建，避免 root 拥有

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
[SYS] xxxx/xx/xx | TraceNex ... started
  ➜  TraceNex  ready in xxx ms
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
| `TLS requested but server does not support TLS` | DSN 带 `tls=` 但 RDS 未启 SSL | DSN 去掉 `&tls=...`；或 RDS 控制台开启 SSL 并用正确参数 |
| `Access denied for user 'xxx'@'%' to database 'yyy'` | 阿里云 RDS 账号未授权该库 | RDS 控制台 → 账号管理 → 修改权限 → 把库授给账号（读写级别） |
| `Redis ping test failed: ... lookup redis on ... no such host` | 跨容器 DNS 不通 | compose 里用 `network_mode: service:redis` + `REDIS_CONN_STRING=redis://localhost:6379` |
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
# 期望：{"system_name":"TraceNex"}

# 3. Request-ID 响应头
curl -sI $HOST/api/status | grep -i "X-Oneapi-Request-Id"
# 期望：X-Oneapi-Request-Id: 2026......

# 4. 前端 HTML
curl -s $HOST/ | grep -oE "<title>[^<]*</title>"
# 期望：<title>TraceNex</title>

# 5. 内嵌产品文档
curl -s -o /dev/null -w "HTTP %{http_code}\n" $HOST/product-docs/TraceNex.md
# 期望：HTTP 200

# 6. Setup 状态
curl -s $HOST/api/setup | python3 -m json.tool
# 期望看到 "success":true
```

---

## 六、首次初始化 root 管理员

TraceNex 首次启动时没有 root 用户，必须通过 `/api/setup` 接口建立：

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

## 七、回归测试清单（TraceNex overlay 验收）

| 功能 | 路径 | 通过条件 |
|------|------|----------|
| 品牌词 | `/api/status` JSON | `system_name:"TraceNex"` |
| 页面 title | 任意页 | `<title>TraceNex</title>` |
| Favicon | `/favicon.ico` | 200，内容非空 |
| Logo | `/new_logo.png` | 200 |
| 登录按钮排序 | `/login` 浏览器 | 邮箱/用户名登录按钮在最上方；"没有账户？注册"**始终**显示 |
| 产品文档 | `/docs` 浏览器 | 渲染"TraceNex 说明手册"，18 张截图全部加载 |
| 管理后台 CSV 导出 | 用量日志页面 | 顶部有"导出 CSV"按钮，点击下载 UTF-8+BOM 文件 |
| 后端 CSV API | `GET /api/log/export?type=0` | 带 cookie + `New-Api-User: 1` header，返回 200 CSV，**表头含 `request_id` 列** |
| i18n 切换 | 管理后台右上角 | zh-CN / zh-TW / en / ja / ru / fr / vi 七种语言，品牌词均为 TraceNex |

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
cd ~/TraceNex
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
du -sh data/ logs/
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
rm -rf data/ logs/

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

## 九、性能优化（测试环境提速）

> Podman 不是比 Docker 慢,是默认配置偏保守。按下面顺序改,**单机 RPS 通常能提升 3-10 倍**。
> 生产的完整性能手册见 [`prod-ack.md`](./prod-ack.md) §九;本节只列"测试环境上手就能用"的那部分。

### 9.1 优化优先级(按收益降序)

| # | 优化点 | 典型症状 | 提速幅度 |
|---|-------|---------|---------|
| 1 | **Podman 网络栈** 换成 pasta 或 rootful + `--net=host` | IP 限流全落一个 IP / localhost 吞吐 < 100Mbps | 3-10× |
| 2 | **TraceNex 连接池 / 批量写** 打开并调大 | p99 高 / MySQL CPU 飙升 | 2-5× |
| 3 | **数据库别用 SQLite**(连测试也换 MySQL) | 并发 >20 就卡 | 5-20× |
| 4 | **Linux 内核** TCP / 文件句柄调优 | 大量 TIME_WAIT / `too many open files` | 1.5× |

### 9.2 Podman 网络栈(收益最大)

**现象**:rootless Podman 默认用 `slirp4netns`(用户态 TCP 栈),有两个坏毛病:

1. 吞吐只有宿主网卡的 1/5 左右
2. **不保留真实客户端 IP** — TraceNex 的 IP 限流会把所有请求记到同一个 SNAT 地址,撞 `GLOBAL_API_RATE_LIMIT` 墙

三个方案任选:

**方案 A — 换 pasta(rootless 推荐,兼顾安全和性能)**

```bash
# 安装 pasta(passt 项目的一部分)
sudo apt install -y passt        # Debian/Ubuntu
sudo dnf install -y passt         # RHEL/Alibaba Cloud Linux

# 让 Podman 默认用 pasta 取代 slirp4netns
mkdir -p ~/.config/containers
cat > ~/.config/containers/containers.conf <<'EOF'
[network]
default_rootless_network_cmd = "pasta"
EOF

# 重建容器
podman-compose -f compose.test.yml down
podman-compose -f compose.test.yml up -d
```

**方案 B — 切 rootful(最省事,性能最好,测试环境无安全洁癖时首选)**

```bash
# 以 root 身份跑
sudo podman-compose -f compose.test.yml up -d
# 或加到 systemd 里用 system 级而非 user 级 unit
```

**方案 C — `--net=host`(测试机专用,跳过 NAT)**

编辑 `compose.test.yml`:

```yaml
services:
  fy-api:
    network_mode: host     # 跳过整个网络栈,直接复用宿主机
    # 此时 ports: 段会被忽略,由 TraceNex 的 PORT 环境变量控制
    environment:
      - PORT=3000
```

**选哪个**:

- QA 点击主要靠自己 → **方案 B** 最快上手
- 需要保留真实 IP 做限流验收 → **方案 A**
- 单机压测想榨性能 → **方案 C**

### 9.3 TraceNex 环境变量加速(复制进 `.env.test`)

测试机上把下面这些加到 `.env.test`,是 `prod-ack.md` §6.2 里生产级配置的"测试机缩小版":

```dotenv
# ── Go 运行时 ──────────────────────────────────────────
# 告诉 Go 容器里实际能用多少 CPU(数值 = podman run --cpus 或宿主机核数)
GOMAXPROCS=4
# 软内存上限,比容器内存低 ~15% 防 OOM
GOMEMLIMIT=3500MiB

# ── 上游转发连接池(TraceNex → OpenAI/Gemini/Kimi 的 keep-alive)──
# 默认 500/100 偏小,测试机调大几乎无副作用
RELAY_MAX_IDLE_CONNS=5000
RELAY_MAX_IDLE_CONNS_PER_HOST=500
# 非流式 120 够;流式(Kimi/Claude 长回答)建议 600
RELAY_TIMEOUT=600
STREAMING_TIMEOUT=300

# ── DB 连接池 ──────────────────────────────────────────
SQL_MAX_IDLE_CONNS=50
SQL_MAX_OPEN_CONNS=300

# ── 内存缓存 + 批量写(这两个不开会打满 DB)───────────
MEMORY_CACHE_ENABLED=true
SYNC_FREQUENCY=60              # 每 60s 从 DB 刷一次配置到内存
BATCH_UPDATE_ENABLED=true
BATCH_UPDATE_INTERVAL=3        # 3s 一次批量写 usage/quota

# ── 日志 ───────────────────────────────────────────────
# 关掉错误体打印,测试稳定后日志量明显降低
ERROR_LOG_ENABLED=false
# GIN 生产模式,避免每请求打一条框架日志
GIN_MODE=release
```

改完 `podman-compose up -d --force-recreate fy-api` 重启一次生效。

### 9.4 日志驱动换掉 journald

Podman 默认 `--log-driver=journald`,高并发下 journald 会成为瓶颈,而且你已经见过 `~/TraceNex/logs/` 为空的现象 — 日志全进了 journal,不落盘。

在 `compose.test.yml` 里给 TraceNex 服务加:

```yaml
services:
  fy-api:
    logging:
      driver: k8s-file            # 或 json-file
      options:
        max-size: "100m"
        max-file: "5"
```

或命令行 `--log-driver=k8s-file --log-opt max-size=100m --log-opt max-file=5`。

### 9.5 如果还在用 SQLite,换掉

TraceNex 每个请求至少 3 次写(usage、log、quota),SQLite 单写锁直接被打穿。
测试环境也建议连 RDS(跟生产同构,参考 §二的 `SQL_DSN`)。

连不到 RDS 的本地场景,加个 MySQL sidecar:

```bash
# 起本地 MySQL(仅测试,生产走 RDS)
podman run -d --name fy-api-mysql \
  -e MYSQL_ROOT_PASSWORD='test-pwd' \
  -e MYSQL_DATABASE=newapi \
  -v fy-api-mysql-data:/var/lib/mysql \
  -p 3306:3306 \
  mysql:8.4

# 在 .env.test 里把 SQL_DSN 改成
# SQL_DSN=root:test-pwd@tcp(localhost:3306)/newapi?parseTime=true&charset=utf8mb4
```

### 9.6 宿主机 Linux 内核(一次性,持久化)

```bash
cat | sudo tee /etc/sysctl.d/99-fyapi.conf <<'EOF'
# 连接队列 / 端口范围
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

# BBR 拥塞控制(Kernel ≥ 4.9)
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# 文件句柄
fs.file-max = 2097152
EOF

sudo sysctl --system

# 给 Podman 容器更高的 nofile 上限
podman run --ulimit nofile=1048576:1048576 ...
# 或在 compose 里:
#   ulimits:
#     nofile: { soft: 1048576, hard: 1048576 }
```

### 9.7 压测验证(改完回归一下)

```bash
# 装 hey(简易 HTTP 压测)
go install github.com/rakyll/hey@latest

# 你的 TraceNex token
export KEY=sk-xxxxxxxxxxxxxxxx

# 200 并发、共 2000 请求
hey -n 2000 -c 200 \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -m POST \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}' \
    http://localhost:3000/v1/chat/completions
```

重点看三个指标:

- **Requests/sec** — 优化前后对比
- **99% in ... secs** — 尾延迟,通常 >5s 就是 DB 慢查询或 GC
- **Error distribution** — 非 200 > 0 时调大 `SQL_MAX_OPEN_CONNS` 或降 `-c`

### 9.8 测试机典型档位参考

| 测试机规格 | `GOMAXPROCS` | `GOMEMLIMIT` | `SQL_MAX_OPEN` | `RELAY_MAX_IDLE` | 目标 RPS |
|---|---|---|---|---|---|
| 2c / 4g | 2 | 3500MiB | 150 | 2000 | 100-300 |
| **4c / 8g(推荐)** | **4** | **7000MiB** | **300** | **5000** | **500-1500** |
| 8c / 16g | 8 | 14000MiB | 500 | 8000 | 2000-5000 |

> 实际 RPS 很大程度取决于上游模型响应时间,**流式接口通常 < 500 RPS**(单请求本身占用 30-300 秒)。

### 9.9 排障速查

| 现象 | 最可能原因 | 怎么看 |
|------|-----------|-------|
| 容器 CPU 打满、宿主机看不出高负载 | `GOMAXPROCS` 没限,Go 在宿主机 N 核上抢调度 | `podman exec fy-api cat /proc/1/status \| grep Cpus_allowed_list` |
| 大量 TIME_WAIT | 上游 keep-alive 没生效 | `ss -ant \| awk '{print $1}' \| sort \| uniq -c` |
| TraceNex 日志 `too many connections` | `SQL_MAX_OPEN_CONNS` < DB 真实连接用量 | MySQL 里 `SHOW STATUS LIKE 'Threads_connected';` |
| 偶发 502 | 连接池命中上限或上游 TLS 握手失败 | `podman logs fy-api \| grep -i upstream` |
| IP 限流全落同一 IP | rootless slirp4netns SNAT | 换 pasta(§9.2 方案 A) |
| logs/ 目录空,但 journal 里有内容 | 默认日志驱动是 journald | 换 `k8s-file`(§9.4) |

---

## 十、常见故障速查

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

## 十一、停用环境（退役）

```bash
cd ~/TraceNex
podman-compose -f compose.test.yml down
rm -rf data/ logs/

# RDS 测试库
# 通知 DBA：DROP DATABASE fy_api_test;

# 本地镜像 + systemd service
podman rmi fy-api:local
systemctl --user disable --now container-fy-api.service 2>/dev/null
rm -f ~/.config/systemd/user/container-fy-api.service
systemctl --user daemon-reload

# 代码仓库
rm -rf ~/TraceNex
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

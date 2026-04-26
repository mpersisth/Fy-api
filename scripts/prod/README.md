# 生产单机部署脚本集

> 把 `prod-podman-single.md` runbook 的所有步骤写成了**可直接 scp 到 ECS 执行的 bash 脚本**。
> 按数字顺序依次执行即可。

## 前置准备清单

开跑脚本之前,这些阿里云侧资源必须就绪:

- [ ] ECS 16c32g,内网能访问 RDS/Redis/ACR
- [ ] **阿里云 RDS MySQL 8.0**:已建库、白名单开 ECS、专属代理(可选)
- [ ] **阿里云 R-KVStore Redis**:已开好、内网地址和密码就绪(你已完成)
- [ ] **阿里云 ACR**:镜像 `registry.cn-hangzhou.aliyuncs.com/fy-api/fy-api:vX.Y.Z` 已 push
- [ ] **阿里云 SLS**:Project `fy-api-prod` + 4 个 Logstore 已建(`fy-api-app`、`fy-api-nginx-access`、`fy-api-nginx-error`、`fy-api-consume`)
- [ ] **备案域名**:DNS A 记录已指向 ECS 的 EIP
- [ ] 在本机(笔记本)执行 `openssl rand -hex 32` 两次,作为 `SESSION_SECRET` / `CRYPTO_SECRET`

## 步骤

```bash
# ──────────────────────────────────────────
# Step 0:把脚本和配置模板打包到 ECS
# ──────────────────────────────────────────
# 在本地
cd ~/Projects/apiGateway/Fy-api
scp -r scripts/prod config/fy-api.env.example \
    root@<ECS-IP>:/root/

# ──────────────────────────────────────────
# Step 1:登录 ECS,系统初始化
# ──────────────────────────────────────────
ssh root@<ECS-IP>
cd /root/prod
chmod +x *.sh
./01-setup-system.sh

# ──────────────────────────────────────────
# Step 2:写真实的 .env(填 RDS/Redis/SECRET)
# ──────────────────────────────────────────
cp /root/fy-api.env.example /opt/fy-api/config/fy-api.env
vi /opt/fy-api/config/fy-api.env
# 改:
#   SQL_DSN              → RDS fy_api_app 账号密码 + 代理地址
#   REDIS_CONN_STRING    → 你的阿里云 Redis 内网地址 + 密码
#   SESSION_SECRET       → openssl rand -hex 32(本地生成)
#   CRYPTO_SECRET        → 同上
#   FRONTEND_BASE_URL    → https://api.your-domain.com
#   NODE_NAME            → fy-api-prod-1
chmod 600 /opt/fy-api/config/fy-api.env

# ──────────────────────────────────────────
# Step 3:装 SLS Logtail(日志接入阿里云)
# ──────────────────────────────────────────
REGION=cn-hangzhou MACHINE_ID=fy-api-prod ./02-install-logtail.sh
# 执行后按脚本末尾指示,去 SLS 控制台:
#   1) 新建机器组,类型选"用户自定义标识",填 "fy-api-prod"
#   2) 给 4 个 Logstore 各建采集配置,挂到该机器组

# ──────────────────────────────────────────
# Step 4:配置 Nginx + Let's Encrypt 证书
# ──────────────────────────────────────────
DOMAIN=api.your-domain.com EMAIL=sre@your-domain.com ./03-setup-nginx.sh

# ──────────────────────────────────────────
# Step 5:登录 ACR 并首次部署 Fy-api 容器
# ──────────────────────────────────────────
podman login registry-vpc.cn-hangzhou.aliyuncs.com
IMAGE_TAG=v0.9.5 ./04-deploy-fyapi.sh

# ──────────────────────────────────────────
# Step 6:初始化管理员(一次性)
# ──────────────────────────────────────────
read -s ROOT_PASS
curl -s -X POST http://127.0.0.1:3001/api/setup \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"fyadmin\",\"password\":\"$ROOT_PASS\",\"confirmPassword\":\"$ROOT_PASS\",\"SelfUseModeEnabled\":false,\"DemoSiteEnabled\":false}"
unset ROOT_PASS

# ──────────────────────────────────────────
# Step 7:打开模型请求限流(按客户分组,热更新,不用重启)
# ──────────────────────────────────────────
ADMIN_USER=fyadmin \
ADMIN_PASS='your-admin-password' \
API_BASE=http://127.0.0.1:3001 \
./05-enable-rate-limit.sh

# ──────────────────────────────────────────
# 以后发版(蓝绿,零停机)
# ──────────────────────────────────────────
./06-deploy-blue-green.sh v0.9.6
```

## 脚本一览

| # | 文件 | 作用 | 执行时机 |
|---|---|---|---|
| 01 | `01-setup-system.sh` | 内核参数 / ulimit / 装 Podman+Nginx+logrotate / pasta 网络 / 目录 / 防火墙 | 新 ECS 首次 |
| 02 | `02-install-logtail.sh` | 装 SLS Logtail,写机器组标识 | 新 ECS 首次 |
| 03 | `03-setup-nginx.sh` | 生成 Nginx 反代配置 + 申请 Let's Encrypt + 证书续期 | 新域名首次 |
| 03b | `03b-add-redirect-domain.sh` | 给次要域名配 HTTPS + 301 跳到主域(如 www → api) | 每个新增跳转域名 |
| 04 | `04-deploy-fyapi.sh` | 拉 ACR 镜像首次启动 `fy-api-blue` | 首次部署 |
| 05 | `05-enable-rate-limit.sh` | 通过 API 打开 Model Request RateLimit + 配分组 JSON | 首次/改分组 |
| 06 | `06-deploy-blue-green.sh` | 蓝绿发版,零停机切版本 | 每次发版 |

## 关键设计

- **凭据全部走环境变量或命令行参数**,脚本本身不含秘密,可以 commit
- **幂等**:脚本可重复跑,不会因为某一步已完成而崩溃
- **显式失败**:每一步失败都有明显红字提示 + 下一步指示
- **日志落盘必验证**:04 脚本会检查 `/opt/fy-api/logs/` 是否真的有文件
- **Redis 连通必验证**:04 脚本会从容器外 ping 一次托管 Redis

## 回滚

每个脚本执行前,建议:

```bash
# 创建 ECS 云盘快照
# 阿里云控制台 → ECS → 云盘 → 创建快照

# 记录当前 Nginx 配置
sudo cp /etc/nginx/conf.d/fy-api.conf /etc/nginx/conf.d/fy-api.conf.bak.$(date +%F)

# 记录当前容器镜像 tag
podman ps --format '{{.Names}} {{.Image}}'
```

蓝绿发版的回滚见 `06-deploy-blue-green.sh` 末尾的指示,一条命令切回旧容器。

## 关联文档

- 完整 runbook:[`../../docs/deploy/prod-podman-single.md`](../../docs/deploy/prod-podman-single.md)
- 限流详解:[`../../docs/deploy/rate-limiting.md`](../../docs/deploy/rate-limiting.md)
- 日志 + SLS + Prometheus:[`../../docs/deploy/observability.md`](../../docs/deploy/observability.md)
- 环境变量模板:[`../../config/fy-api.env.example`](../../config/fy-api.env.example)

# 限流开启与配置指南

> 读者：运维 / SRE / 运营管理员
> 目标：搞清楚 Fy-api 的 7 套限流,**哪几个可以后台点点不重启就开,哪几个必须改启动配置才能开**,以及核心的"按客户分组"限流怎么落地。
> 最近更新：2026-04-26

---

## 一分钟版速查

| 需求 | 要重启容器吗? | 怎么做 |
|---|:---:|---|
| **开启 / 关闭 "按客户/按分组" AI 调用限流** | ❌ 不用 | 后台 → 运营设置 → 模型请求速率限制 → 勾选启用 → 保存 |
| 动态调整某分组配额 | ❌ 不用 | 同上,改分组 JSON |
| 把 Global API / Critical / Search / Web / Upload 限流调严/调松/关掉 | ✅ 要重启 | 改环境变量 → `podman restart fy-api` 或 `kubectl rollout restart` |
| 关闭邮件验证码频率限制 | 🚫 改不了 | 代码硬编码,要动源码 |

**一句话总结**:**只有 1 套能热更新(Model Request RateLimit,就是我们用来"按客户限流"的那套)**。其余 6 套的开关和阈值都在 `common/init.go` 里只读一次环境变量,改了必须重启。

---

## 二、Fy-api 七套限流一览

全部限流中间件都定义在 `middleware/rate-limit.go`、`middleware/model-rate-limit.go`、`middleware/email-verification-rate-limit.go`。

| # | 限流名 | 热更新? | 作用范围 | 默认 | 开关环境变量 |
|---|---|:---:|---|---|---|
| 1 | **Model Request RateLimit**(AI 调用按用户/分组) | ✅ **是** | `/v1/*` + `/v1beta/*` 模型调用 | 关 | 无 env,后台点开 |
| 2 | Global API RateLimit | ❌ 否 | `/api/*`(按 IP) | 180 / 180s | `GLOBAL_API_RATE_LIMIT_ENABLE` |
| 3 | Global Web RateLimit | ❌ 否 | Web 前端资源 | 60 / 180s | `GLOBAL_WEB_RATE_LIMIT_ENABLE` |
| 4 | Critical RateLimit | ❌ 否 | 登录/注册/支付/取 key 等敏感端点 | 20 / 20min | `CRITICAL_RATE_LIMIT_ENABLE` |
| 5 | Search RateLimit | ❌ 否 | 搜索端点(按用户) | 10 / 60s | `SEARCH_RATE_LIMIT_ENABLE` |
| 6 | Upload / Download RateLimit | ❌ 否 | 代码已写但**未挂路由**,等于未启用 | 10 / 60s | 无 |
| 7 | Email Verification RateLimit | 🚫 不可改 | `/api/verification`(发验证码) | 10 / 小时 + 30s 间隔 | 无,**硬编码** |

---

## 三、🟢 热更新:开启 Model Request RateLimit(最常用)

这是你要做"按客户限流"时实际用到的那套。**不用重启容器,后台保存即生效**。

### 3.1 源码路径(验证热更新行为)

```
model/option.go:318-319     — options 写入时直接改内存变量
setting/rate_limit.go       — 被保护的变量存储
middleware/model-rate-limit.go — 每个请求读取变量并判断
```

这三个文件保证:后台保存 → 数据库 options 表写入 → 内存变量更新 → 下一个请求立即生效。

### 3.2 后台 UI 操作(推荐)

1. 管理员登录 → **运营设置** → **模型请求速率限制** 板块
2. 勾选 **启用模型请求速率限制**
3. 填四个字段:

| 字段 | 含义 | 推荐值 |
|---|---|---|
| 时间窗口(分钟) | 滑动窗口长度,最小 1 分钟 | `1` |
| 总请求数限制 | 含失败次数,`0` = 不限 | `0` |
| 成功请求数限制 | 只统计 HTTP < 400 | `1000` |
| 分组限流配置(JSON) | 按用户分组配独立配额 | 见下 |

4. **保存** — 下一个请求就按新规则生效

### 3.3 分组 JSON 格式(核心)

```json
{
  "default":        [120,  60],
  "customer_acme":  [2000, 1000],
  "customer_beta":  [600,  300],
  "vip":            [5000, 3000],
  "internal":       [0,    10000],
  "trial":          [60,   30]
}
```

- **key** = 用户分组名(或 token 分组名)
- **value** = `[总次数上限, 成功次数上限]`,单位都是"每时间窗口"
- **优先级**:token 分组 > 用户分组 > 全局默认

### 3.4 用 curl / API 一键开(不进后台)

```bash
BASE=https://your-fy-api-domain
ADMIN_USER=root
ADMIN_PASS='your-admin-password'

# 1) 管理员登录(拿 session cookie)
curl -c /tmp/fy.cookie -X POST "$BASE/api/user/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}"

# 2) 启用限流
curl -b /tmp/fy.cookie -X PUT "$BASE/api/option/" \
  -H "Content-Type: application/json" \
  -d '{"key":"ModelRequestRateLimitEnabled","value":"true"}'

# 3) 设置窗口长度(分钟)
curl -b /tmp/fy.cookie -X PUT "$BASE/api/option/" \
  -H "Content-Type: application/json" \
  -d '{"key":"ModelRequestRateLimitDurationMinutes","value":"1"}'

# 4) 设全局默认成功次数上限
curl -b /tmp/fy.cookie -X PUT "$BASE/api/option/" \
  -H "Content-Type: application/json" \
  -d '{"key":"ModelRequestRateLimitSuccessCount","value":"1000"}'

# 5) 设全局默认总次数上限(0=不限)
curl -b /tmp/fy.cookie -X PUT "$BASE/api/option/" \
  -H "Content-Type: application/json" \
  -d '{"key":"ModelRequestRateLimitCount","value":"0"}'

# 6) 分组配额 JSON (注意内层引号要转义)
curl -b /tmp/fy.cookie -X PUT "$BASE/api/option/" \
  -H "Content-Type: application/json" \
  -d '{"key":"ModelRequestRateLimitGroup","value":"{\"default\":[120,60],\"customer_acme\":[2000,1000],\"vip\":[5000,3000]}"}'
```

### 3.5 直接改数据库(应急用)

```sql
UPDATE options SET value='true' WHERE `key`='ModelRequestRateLimitEnabled';
UPDATE options SET value='1'    WHERE `key`='ModelRequestRateLimitDurationMinutes';
UPDATE options SET value='0'    WHERE `key`='ModelRequestRateLimitCount';
UPDATE options SET value='1000' WHERE `key`='ModelRequestRateLimitSuccessCount';
UPDATE options
SET value = '{"default":[120,60],"customer_acme":[2000,1000]}'
WHERE `key` = 'ModelRequestRateLimitGroup';
```

⚠️ **注意**:直接改 options 表时 Fy-api 需要**轮询重读配置**才能看到,默认每 `SYNC_FREQUENCY`(60 秒)同步一次。如果想立即生效,用上面 §3.4 的 API 接口,它会同时改 DB 和内存。

### 3.6 把客户绑到分组

```sql
-- 单个客户
UPDATE users SET `group`='customer_acme' WHERE email='someone@acme.com';

-- 批量
UPDATE users SET `group`='vip'
WHERE email IN ('a@example.com','b@example.com','c@example.com');

-- 同一客户下不同 token 做二级切分(prod 高配、test 低配)
UPDATE tokens SET `group`='customer_acme_prod' WHERE id IN (101,102);
UPDATE tokens SET `group`='customer_acme_test' WHERE id IN (103,104);
-- 然后在分组 JSON 里给两个 group 各配一份
```

对应的也可以在后台 UI 完成:**用户管理 → 编辑用户 → 分组**,以及 **令牌管理 → 编辑令牌 → 分组**。

### 3.7 验证限流生效

```bash
# 快速撞一下客户 VIP token 的限流墙
for i in {1..30}; do
  curl -s -o /dev/null -w "%{http_code} " \
    -X POST "$BASE/v1/chat/completions" \
    -H "Authorization: Bearer <customer_acme 的 token>" \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
done
echo

# 当累计超过该分组 successCount 后,应该看到一排 429
# 返回 body 示例:
# {"error":{"message":"您已达到请求数限制:1分钟内最多请求1000次","type":"...","code":"..."}}
```

Redis 里直接看窗口用量(假设某客户的 userId=42):

```bash
redis-cli -h <host> LLEN  rateLimit:MRRLS:42     # 已累计成功次数
redis-cli -h <host> TTL   rateLimit:MRRLS:42     # 剩余窗口秒数
redis-cli -h <host> LRANGE rateLimit:MRRLS:42 0 -1
```

---

## 四、🔴 必须重启:开启其他 6 套限流

这 6 套的开关/阈值都在容器启动时从环境变量读一次,之后不能热更新。

### 4.1 可调参数一览

```bash
# ── Global API RateLimit (按 IP 保护 /api/*)──────────────────────
GLOBAL_API_RATE_LIMIT_ENABLE=true        # 默认 true
GLOBAL_API_RATE_LIMIT=180                # 窗口内次数
GLOBAL_API_RATE_LIMIT_DURATION=180       # 窗口秒数

# ── Global Web RateLimit (保护前端资源)─────────────────────────
GLOBAL_WEB_RATE_LIMIT_ENABLE=true
GLOBAL_WEB_RATE_LIMIT=60
GLOBAL_WEB_RATE_LIMIT_DURATION=180

# ── Critical RateLimit (登录/注册/支付等敏感端点)──────────────
CRITICAL_RATE_LIMIT_ENABLE=true
CRITICAL_RATE_LIMIT=20
CRITICAL_RATE_LIMIT_DURATION=1200        # 20*60s

# ── Search RateLimit (按用户 ID 保护搜索端点)─────────────────
SEARCH_RATE_LIMIT_ENABLE=true
SEARCH_RATE_LIMIT=10
SEARCH_RATE_LIMIT_DURATION=60
```

### 4.2 本机 Podman 操作步骤

编辑 `.env.test`(或 `compose.test.yml` 的 `environment:` 段):

```dotenv
# 放宽 API 限流(测试机抓包/压测场景)
GLOBAL_API_RATE_LIMIT_ENABLE=true
GLOBAL_API_RATE_LIMIT=3000
GLOBAL_API_RATE_LIMIT_DURATION=60

# 把 Critical 调严一点,防止撞库
CRITICAL_RATE_LIMIT_ENABLE=true
CRITICAL_RATE_LIMIT=10
CRITICAL_RATE_LIMIT_DURATION=600
```

重启容器让改动生效:

```bash
cd ~/Fy-api
podman-compose -f compose.test.yml up -d --force-recreate fy-api
# 或者
podman restart fy-api
```

确认:

```bash
podman exec fy-api env | grep -E 'RATE_LIMIT'
```

### 4.3 线上 ACK 操作步骤

这些参数已经放在 `deploy/k8s/10-configmap.yaml`(见 `prod-ack.md` §6.2)。

```bash
# 1) 改 ConfigMap
kubectl -n fy-api edit configmap fy-api-config
# 或
kubectl -n fy-api apply -f deploy/k8s/10-configmap.yaml

# 2) 触发 Pod 滚动重启(envFrom ConfigMap 改了需要 rollout)
kubectl -n fy-api rollout restart deploy/fy-api

# 3) 观察
kubectl -n fy-api rollout status deploy/fy-api --timeout=5m
```

> 改 ConfigMap 本身不会自动重启 Pod(跟 Secret 不同),必须显式 `rollout restart`。

### 4.4 想全关掉某套限流

把对应 `_ENABLE` 设为 `false`,重启即可:

```dotenv
GLOBAL_API_RATE_LIMIT_ENABLE=false
GLOBAL_WEB_RATE_LIMIT_ENABLE=false
CRITICAL_RATE_LIMIT_ENABLE=false
SEARCH_RATE_LIMIT_ENABLE=false
```

⚠️ **生产强烈建议不要关 Critical RateLimit** — 它是防暴力破解登录/支付的主要屏障。

---

## 五、🚫 改不了(硬编码)

### 5.1 Email Verification RateLimit

保护 `/api/verification`(发邮箱验证码),硬编码 **10 次/小时 + 两次间隔至少 30 秒**。定义在
`middleware/email-verification-rate-limit.go`,**没有环境变量开关**。

想调只能改源码 + 重新构建镜像:

```go
// middleware/email-verification-rate-limit.go 原文(供参考)
// 这里的 10 和 3600 是硬编码
if !inMemoryRateLimiter.Request(key, 10, 3600) { ... }
```

### 5.2 Upload / Download RateLimit

代码已写好但 **路由没有引用**,等于未启用。想用必须改源码挂到对应上传/下载路由。

---

## 六、决策树:我该改哪个?

```
需求:限流某类请求
│
├─ 是 AI 调用(/v1/* 或 /v1beta/*)吗?
│   ├─ 是 → 用 Model Request RateLimit(热更新,按用户分组)
│   │        → 本文 §三
│   └─ 否 ↓
│
├─ 是整个 /api/* 按 IP 粗粒度保护吗?
│   └─ 是 → Global API RateLimit(改 env,要重启)
│            → 本文 §4
│
├─ 是登录/注册/支付等敏感端点吗?
│   └─ 是 → Critical RateLimit(改 env,要重启)
│            → 本文 §4
│
├─ 是搜索端点吗?
│   └─ 是 → Search RateLimit(改 env,要重启)
│
├─ 是发邮件验证码吗?
│   └─ 是 → 改源码(本文 §5.1)
│
└─ 是按 IP/模型/渠道/并发数 限 AI 调用?
    └─ 现有代码不支持,需要二次开发 middleware/model-rate-limit.go
```

---

## 七、常见问题 FAQ

### Q1:我开了 Model Request RateLimit,但发现不生效?

可能原因(按概率排序):

1. **分组没配对**:确认该用户的 `users.group` 字段确实指向某个在 `ModelRequestRateLimitGroup` JSON 里的 key
2. **Redis 没连上**:没 Redis 时 Fy-api 退化为进程内存计数,**多副本不共享**,单副本看起来也可能像"没限住";执行 `podman exec fy-api env \| grep REDIS_CONN_STRING` 确认
3. **配额是 `[0, 0]`**:在代码里 `totalCount=0` 被当成"不限",所以 VIP 想要"无限"可以用这个
4. **token 级分组覆盖了**:token 上如果单独设了 group,会覆盖用户分组,查 `tokens.group`

### Q2:Redis 没开,我改限流还生效吗?

生效,但是**每个 Pod 单独计数**,达不到"全局共享配额"。线上多副本部署必须配 Redis(`REDIS_CONN_STRING`)。

### Q3:滑动窗口是"严格滚动"还是"固定窗口"?

Fy-api 实现的是**近似滑动窗口**:Redis 里用 List 存每次请求时间戳,查询时对比最旧和当前时间差。边界附近有小抖动(几秒级),对业务无感。

### Q4:命中限流时的响应是什么?

HTTP **429 Too Many Requests**,body 是 OpenAI 兼容错误对象:

```json
{
  "error": {
    "message": "您已达到请求数限制:1分钟内最多请求1000次",
    "type": "rate_limit_exceeded",
    "code": "rate_limit_exceeded"
  }
}
```

### Q5:某个 VIP 客户想彻底不限,但全局又想保留默认值怎么办?

给 VIP 建个专属分组,配额两个都是 0(即"不限"):

```json
{
  "default":  [120, 60],
  "vip_none": [0, 0]
}
```

再把该 VIP 的 `users.group='vip_none'`。

### Q6:改完 ModelRequestRateLimitGroup 后,旧的窗口计数会自动清吗?

不会。已经进入 Redis 的计数会按**原配额**消耗直到窗口结束(最长 `DurationMinutes` 分钟),新窗口开始时按新配额算。

如果需要立刻清掉某用户的计数(比如刚刚升了 VIP):

```bash
redis-cli -h <host> DEL rateLimit:MRRLS:42
redis-cli -h <host> DEL rateLimit:42          # 总次数令牌桶
```

---

## 八、上线 Checklist(按客户限流)

- [ ] 确认已配 Redis(`REDIS_CONN_STRING`),否则多副本共享失效
- [ ] 在后台打开 `ModelRequestRateLimitEnabled`
- [ ] 配好 `ModelRequestRateLimitGroup` JSON,至少包含 `default`
- [ ] 每个客户的用户 `group` 字段已指向 JSON 里存在的 key
- [ ] 用一个目标分组的 token 跑一次 §3.7 的撞限流脚本,确认 429
- [ ] 监控:在 SLS / 日志里加 `429` 告警规则
- [ ] 回滚预案:关闭 `ModelRequestRateLimitEnabled` 即可撤销(一键)

---

## 九、相关文档

- 部署与其他环境变量:[`test-podman.md`](./test-podman.md)、[`prod-ack.md`](./prod-ack.md)
- 完整环境变量清单:<https://docs.newapi.pro/zh/docs/installation/config-maintenance/environment-variables>
- 按客户分组限流的代码实现:`middleware/model-rate-limit.go`、`setting/rate_limit.go`

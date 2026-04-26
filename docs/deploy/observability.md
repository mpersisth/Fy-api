# Fy-api 日志与监控 Runbook

> 读者:运维 / SRE
> 目标:解决三件事 — (1) 日志落盘 (2) 日志接入 SLS 分析 (3) Prometheus 监控告警
> 版本:2026-04-26

---

## 摘要(先看)

| 需求 | 解决方案 | 涉及文件 |
|---|---|---|
| **日志不落盘** | 给容器加 `--log-dir=/app/logs` **Fy-api 自己的 CLI 参数**,挂载 logs 目录出来 | 本文 §1 |
| **日志分析** | 宿主机装 Logtail → SLS;结构化解析 `record consume log` | 本文 §2 |
| **Prometheus 监控告警** | 装一套 Prometheus + Grafana + Exporter 黑盒栈;Fy-api 没原生 `/metrics`,只能做容器/DB/HTTP 层监控 | 本文 §3,配套 `monitoring/` 目录 |

Fy-api 源码没暴露 `/metrics` 端点,**业务指标的告警(429、5xx 占比、客户消耗)建议走 SLS,不走 Prometheus**。Prometheus 负责基础设施层。两者互补。

---

## 一、日志落盘

### 1.1 为什么你现在看不到日志

三个原因叠加(按我们诊断过的):

1. **容器 ENTRYPOINT 是 `/new-api`,没传 `--log-dir`**
   - Fy-api 的 LogDir 是**命令行 flag**,不是环境变量(见 `common/init.go:21`)
   - 不传就走默认 `./logs`,在容器工作目录 `/data/logs` 下
2. **`~/Fy-api/logs` 挂载路径不对**
   - 挂载的是宿主机的 `logs/`,但容器并没有把日志写到这个位置
3. **日志驱动是 journald**
   - podman 容器层的 stdout 被 journald 吞了,你在宿主 `logs/` 自然看不到

### 1.2 修法:启动命令追加 `--log-dir`

```bash
# 停掉旧容器
podman stop fy-api && podman rm fy-api

# 重新起,两个关键点:
#   1) 在镜像后面追加 --log-dir=/app/logs(这是 Fy-api 的 CLI 参数)
#   2) 把宿主机 logs 目录挂到容器 /app/logs
podman run -d --name fy-api \
  --restart=unless-stopped \
  -p 3000:3000 \
  -v /root/Fy-api/logs:/app/logs:Z \
  -v /root/Fy-api/data:/data:Z \
  -e SQL_DSN="..." \
  -e REDIS_CONN_STRING="..." \
  -e ERROR_LOG_ENABLED=true \
  -e TZ=Asia/Shanghai \
  --log-driver=k8s-file \
  --log-opt max-size=100m \
  --log-opt max-file=5 \
  <镜像名> \
  --log-dir=/app/logs        # ← Fy-api 自己的参数,镜像名之后
```

### 1.3 验证

```bash
# 容器内
podman exec fy-api ls -l /app/logs
# 期望看到 oneapi-20260426xxxxxx.log

# 宿主机
ls -lh /root/Fy-api/logs/
tail -f /root/Fy-api/logs/oneapi-*.log
```

文件内容(实测样本):

```
[GIN] 2026/04/26 - 10:15:23 | 200 | 412.3ms | 1.2.3.4 | POST /v1/chat/completions
[INFO] 2026/04/26 - 10:15:23 | 20260426... | record consume log: userId=2, params={"channel_id":2, ...}
```

### 1.4 日志轮转(避免打爆磁盘)

Fy-api 不自己轮转。启动后文件会一直涨。两种办法:

**A. 宿主机 logrotate(推荐)**

```bash
sudo tee /etc/logrotate.d/fy-api > /dev/null <<'EOF'
/root/Fy-api/logs/oneapi-*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate           # 不动 fd,Fy-api 继续写
    maxsize 500M
}
EOF

sudo logrotate -d /etc/logrotate.d/fy-api   # dry-run 检查
```

**B. 让 Fy-api 每次启动换新文件**

Fy-api 的日志名带时间戳,`podman restart fy-api` 就会新建一个新文件。配合 cron:

```cron
0 0 * * * podman restart fy-api && find /root/Fy-api/logs -name 'oneapi-*.log' -mtime +14 -delete
```

---

## 二、日志接入 SLS

### 2.1 为什么选 SLS(对比 Loki / ES / ELK)

| 方案 | 上手 | 查询 | 100GB 月成本 | 场景 |
|---|---|---|---|---|
| **SLS** | 5min | SQL + 可视化 | ~¥300 | **已在阿里云,首选** |
| Grafana Loki | 30min | 仅 LogQL | 自建 / ~¥200 | 已用 Grafana |
| 自建 ES | 数小时 | 强 | 自建贵,托管 ¥800+ | 超大规模 |

**我的建议:直接 SLS**,特别是你已经在阿里云生态。

### 2.2 三步接入

#### Step 1:SLS 控制台建 Project + Logstore

- **Project**: `fy-api-prod`(按环境隔离)
- **Logstore 1**: `fy-api-app` — 所有应用日志
- **Logstore 2**: `fy-api-consume` — 计费日志(单独存方便分析)
- 保留策略:30 天查询 + OSS 归档 180 天

#### Step 2:ECS 上装 Logtail

```bash
wget -q https://logtail-release-cn-hangzhou.oss-cn-hangzhou-internal.aliyuncs.com/linux64/logtail.sh -O logtail.sh
sudo chmod 755 logtail.sh
# 替换 cn-hangzhou 为你的 region
sudo ./logtail.sh install cn-hangzhou
sudo systemctl status ilogtaild
```

#### Step 3:SLS 控制台建采集配置

- **数据源**:文本日志
- **日志路径**:`/root/Fy-api/logs/  **/oneapi-*.log`
- **模式**:先用"极简模式"跑通,后面加解析

对 `record consume log` 这种结构化日志,加一条**正则提取**:

```regex
\[(?<level>\w+)\]\s+(?<ts>\S+\s+-\s+\S+)\s+\|\s+(?<request_id>\S+)\s+\|\s+record\s+consume\s+log:\s+userId=(?<user_id>\d+),\s+params=(?<params>\{.*\})
```

抽出 `level / ts / request_id / user_id / params`,SLS 里就能:

```sql
-- 近 1 小时每个客户消耗排行
* AND "record consume log"
| select user_id,
         sum(cast(json_extract_scalar(params,'$.quota') as bigint)) AS total,
         count(*) AS calls
  from log
  group by user_id
  order by total desc
  limit 20

-- 各模型缓存命中率
* | select json_extract_scalar(params,'$.model_name') as model,
          sum(case when cast(json_extract_scalar(params,'$.other.cache_tokens') as bigint) > 0 then 1 else 0 end) * 100.0 / count(*) as hit_pct,
          count(*) as total
   group by model
   order by total desc
```

### 2.3 告警(替代 Prometheus 缺失的业务维度)

SLS 控制台 → 告警中心,建这 5 条:

| 告警 | 查询 | 阈值 | 接收人 |
|---|---|---|---|
| 5xx 错误率 | `* \| select count(*) from log where status_code >= 500` | 5min > 10 条 | 钉钉 P1 |
| panic | `panic OR goroutine OR "runtime error"` | 1min ≥ 1 条 | 钉钉 P0 + 短信 |
| 限流命中 | `"您已达到请求数限制"` | 5min > 10 条 | 邮件 |
| 客户消耗暴增 | (按 user_id 统计 sum(quota) 同比) | > 200% 或 < 30% | 钉钉 P2 |
| 上游失败率 | `* AND upstream AND error` | 5min > 20 条 | 钉钉 P1 |

---

## 三、Prometheus 监控与告警

### 3.1 重要前提 — Fy-api 没原生 `/metrics`

看源码:`middleware/stats.go` 只在内部做 `activeConnections` 计数,没暴露 HTTP 端点。`go.mod` 里虽有 `prometheus/client_golang`,但没被主程序使用。

所以 Prometheus 采集范围只能是**黑盒三层**:

| 层 | exporter | 覆盖指标 |
|---|---|---|
| HTTP | Blackbox Exporter | uptime、延迟、证书天数 |
| 容器/主机 | node_exporter + cAdvisor | CPU/内存/磁盘/网络/容器资源 |
| 数据 | mysqld_exporter + redis_exporter | MySQL QPS/慢查询/连接,Redis OPS/mem |

**业务层指标(429 次数、分客户消耗、缓存命中率等)靠 SLS,不走 Prometheus**。两条腿各管一段。

### 3.2 完整栈一键起(本目录 `monitoring/`)

```bash
cd /root/Fy-api/monitoring/

# 1) 先让 Fy-api 加入同一个网络,便于 Prometheus 抓取
podman network create fy-api-net 2>/dev/null || true
podman network connect fy-api-net fy-api

# 2) 改配置里占位符
#    - prometheus.yml 里的 your-domain.com
#    - compose.monitoring.yml 里的 RDS/Redis 地址和密码
#    - alertmanager.yml 里的钉钉 webhook

# 3) 起栈
podman-compose -f compose.monitoring.yml up -d

# 4) 检查
curl -sf http://localhost:9090/-/healthy && echo "Prometheus OK"
curl -sf http://localhost:3001/api/health && echo "Grafana OK"
curl -sf http://localhost:9093/-/healthy && echo "Alertmanager OK"
curl -sf http://localhost:9115/                      # Blackbox
curl -sf http://localhost:9104/metrics | head -5     # MySQL exporter
curl -sf http://localhost:9121/metrics | head -5     # Redis exporter
```

#### 目录结构

```
monitoring/
├── compose.monitoring.yml       # 整套栈的 compose
├── prometheus.yml               # 采集目标配置
├── alerts.yml                   # 告警规则(3 组 15 条)
├── alertmanager.yml             # 告警分发(钉钉/短信)
├── blackbox.yml                 # 黑盒探测模块
└── grafana-datasources.yml      # Grafana 数据源自动注册
```

### 3.3 栈说明

| 服务 | 端口 | 作用 |
|---|---|---|
| Prometheus | 9090 | 时序 DB + 采集器,保留 30d |
| Alertmanager | 9093 | 告警分发去重 |
| Grafana | **3001** | 可视化(避开 Fy-api 的 3000) |
| Node Exporter | 9100(host) | 宿主机指标,host 网络 |
| cAdvisor | 8080 | 每个容器资源 |
| Blackbox Exporter | 9115 | Fy-api 的 `/api/status` 和 TLS 证书 |
| mysqld-exporter | 9104 | RDS MySQL |
| redis-exporter | 9121 | 云 Redis |

### 3.4 RDS 这边要给 exporter 开个只读账号

```sql
-- 给 mysqld-exporter 一个最小权限账号
CREATE USER 'exporter'@'%' IDENTIFIED BY 'STRONG_PWD';
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'%';
FLUSH PRIVILEGES;
```

把 `compose.monitoring.yml` 里 `DATA_SOURCE_NAME` 改成这个账号。

### 3.5 告警规则速览(`alerts.yml`)

已经写好 3 组共 15 条(完整见文件):

**A. 可用性**
- `FyApiDown` — 2 分钟不可达
- `FyApiHighLatency` — 响应 > 2s 持续 5min
- `TLSCertExpiringSoon` — 证书 < 15 天到期

**B. 资源**
- `NodeHighCPU` / `NodeHighMemory` / `NodeDiskAlmostFull` — 宿主机
- `FyApiContainerHighCPU` / `FyApiContainerOOMApproaching` — 容器
- `ContainerRestarted` — 15min 内重启过

**C. 数据**
- `MySQLHighConnections` — 连接用量 > 70%
- `MySQLSlowQueriesHigh` — 慢查询 > 5/s
- `MySQLDown` / `RedisDown`
- `RedisHighMemory` — > 80%

### 3.6 Grafana 面板(推荐导入)

登录 `http://<server>:3001`(admin / 你在 compose 里设的密码),**Dashboards → Import** 这几个官方 ID:

| 面板 | ID | 说明 |
|---|---|---|
| Node Exporter Full | **1860** | 宿主机一览 |
| cAdvisor Container | **14282** | 容器资源 |
| MySQL Overview | **7362** | MySQL 监控 |
| Redis Dashboard | **763** | Redis 监控 |
| Blackbox Exporter | **7587** | HTTP 探测 |

5 个面板 5 分钟搞定。之后有自定义需求再手画。

### 3.7 告警通道(钉钉示例)

1. 钉钉群 → 设置 → 智能群助手 → 添加机器人 → **自定义 webhook**
2. 安全设置选"自定义关键词",填 `Fy-api`(告警消息里必须含这个词)
3. 拿到 webhook URL,填进 `alertmanager.yml` 的 `YOUR_DINGTALK_TOKEN`

测试:

```bash
curl -X POST "http://localhost:9093/api/v2/alerts" \
  -H "Content-Type: application/json" \
  -d '[{"labels":{"alertname":"TestAlert","severity":"warning","instance":"Fy-api-test"},
       "annotations":{"summary":"this is a Fy-api test alert"}}]'
```

钉钉群里应该能看到消息。

---

## 四、SLS 和 Prometheus 分工

| 关注点 | 工具 | 原因 |
|---|---|---|
| 宿主机/容器 CPU、内存、磁盘、网络 | **Prometheus** | 指标是时序数字,适合 TSDB |
| HTTP 可用性 / 延迟 / 证书到期 | **Prometheus**(Blackbox) | 同上 |
| MySQL / Redis 健康 | **Prometheus** | 有成熟 exporter |
| 单条请求全链路跟踪 | **SLS** | 日志带 request_id,按 ID 串联 |
| 某个客户的消耗 / 缓存命中 | **SLS** | 需要解析 params JSON 做聚合 |
| 429 限流、5xx、panic 关键字 | **SLS** | 日志语义搜索 |
| 上游(OpenAI/Gemini)失败率 | **SLS** | 日志里 upstream error 关键字 |

**两者不重复,是互补的**。同一个告警可以两边都发(去重交给 Alertmanager + SLS 告警抑制)。

---

## 五、上线 Checklist

### 日志落盘
- [ ] `podman exec fy-api ls -l /app/logs` 有 `oneapi-*.log`
- [ ] 宿主机 `/root/Fy-api/logs/` 能看到同名文件
- [ ] 装了 logrotate 或 cron 轮转
- [ ] 磁盘用量监控已配(见 Prometheus `NodeDiskAlmostFull`)

### SLS
- [ ] Logtail 进程跑着:`systemctl status ilogtaild`
- [ ] SLS 控制台 `fy-api-app` Logstore 有数据(左侧查询 `*`)
- [ ] 正则解析规则生效(有 `user_id / params` 字段)
- [ ] 5 条告警规则已建 + 钉钉通知测试通过

### Prometheus
- [ ] 所有 exporter 健康:`curl :9100 :8080 :9115 :9104 :9121`
- [ ] `http://prom:9090/targets` 全部 `UP`
- [ ] `http://prom:9090/alerts` 看到规则加载,无 `FIRING`
- [ ] Grafana 数据源通,面板有数据
- [ ] Alertmanager 测试告警到钉钉

---

## 六、故障排查速查

| 现象 | 原因 | 修复 |
|---|---|---|
| 容器 `/app/logs` 空 | 没传 `--log-dir=/app/logs` | 按 §1.2 重启 |
| 宿主机目录空但容器有 | 挂载路径不匹配 | `podman inspect fy-api \| grep Mounts -A 5` |
| SLS 无数据 | Logtail 没权限读 / 路径写错 | `sudo tail /usr/local/ilogtail/ilogtail.LOG` |
| Prometheus target DOWN | exporter 不通 / 防火墙 | `curl target:9xxx/metrics` |
| cAdvisor 找不到容器 | Podman 存储路径不同 | 挂载 `/var/lib/containers` 而非 `/var/lib/docker` |
| MySQL exporter 报 auth 错 | RDS 白名单没加 ECS | RDS 控制台 → 白名单 → 加 ECS IP |
| 告警不通 | 钉钉关键词没中 | 消息体必须含 `Fy-api`;检查 `alertmanager.yml` |

---

## 七、相关文档

- 部署主文档:[`test-podman.md`](./test-podman.md)、[`prod-ack.md`](./prod-ack.md)
- 限流开关:[`rate-limiting.md`](./rate-limiting.md)
- 配套配置文件:[`monitoring/`](./monitoring/)

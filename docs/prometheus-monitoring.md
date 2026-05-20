# Prometheus Monitoring for Fy-api

本文档说明如何为 TraceNex (Fy-api) 启用 Prometheus 监控，并接入 Grafana 可视化和 AlertManager 告警。

## 1. 启用 Prometheus Metrics

在 Fy-api 的运行环境中设置环境变量：

```bash
PROMETHEUS_METRICS=1
```

启用后，Fy-api 会：
- 在所有 relay 请求 (`/v1/*`, `/v1beta/*`) 上自动采集指标
- 暴露 `/metrics` 端点供 Prometheus 抓取

未设置此变量时，监控完全禁用，零开销。

## 2. 暴露的指标

### 文本/通用模型

| 指标名 | 类型 | Labels | 说明 |
|--------|------|--------|------|
| `fy_relay_requests_total` | Counter | model, channel_id, status_code, endpoint_type, is_stream | 请求总数 |
| `fy_relay_errors_total` | Counter | model, channel_id, error_type | 错误总数 |
| `fy_relay_duration_seconds` | Histogram | model, channel_id, endpoint_type | 端到端延迟 |
| `fy_relay_ttft_seconds` | Histogram | model, channel_id | 首字延迟 (TTFT)，仅流式 |
| `fy_relay_retries_total` | Counter | model, endpoint_type | 重试次数 |

### 图片生成

| 指标名 | 类型 | Labels | 说明 |
|--------|------|--------|------|
| `fy_image_duration_seconds` | Histogram | model, channel_id | 图片生成耗时 |

### Label 说明

| Label | 值域 | 说明 |
|-------|------|------|
| `model` | 模型名 (如 `claude-opus-4-6`) | 用户请求的原始模型名 |
| `channel_id` | 数字 (如 `30`) | 最终使用的渠道 ID |
| `status_code` | HTTP 状态码 (如 `200`, `429`) | 返回给客户端的状态码 |
| `endpoint_type` | `chat`, `image`, `audio`, `embedding`, `other` | 请求类型 |
| `is_stream` | `true`, `false` | 是否流式请求 |
| `error_type` | `rate_limited`, `auth_error`, `client_error`, `upstream_unavailable`, `server_error` | 错误分类 |

## 3. 部署 Prometheus

### docker-compose 方式（推荐）

在服务器上创建 `monitoring/docker-compose.yml`：

```yaml
version: "3.8"
services:
  prometheus:
    image: prom/prometheus:v2.53.0
    ports:
      - "127.0.0.1:9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - ./alert-rules.yml:/etc/prometheus/alert-rules.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=30d'
    restart: unless-stopped

  grafana:
    image: grafana/grafana:11.1.0
    ports:
      - "127.0.0.1:3001:3000"
    volumes:
      - grafana_data:/var/lib/grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=changeme
    restart: unless-stopped

  alertmanager:
    image: prom/alertmanager:v0.27.0
    ports:
      - "127.0.0.1:9093:9093"
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml
    restart: unless-stopped

volumes:
  prometheus_data:
  grafana_data:
```

### prometheus.yml

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "alert-rules.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  - job_name: "fy-api"
    static_configs:
      - targets: ["host.docker.internal:3000"]
        labels:
          instance: "cn"
```

## 4. 告警规则

创建 `monitoring/alert-rules.yml`：

```yaml
groups:
  - name: fy-api-relay
    rules:
      - alert: HighTTFT
        expr: histogram_quantile(0.95, rate(fy_relay_ttft_seconds_bucket[5m])) > 10
        for: 3m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.model }} ch#{{ $labels.channel_id }} TTFT p95 > 10s"

      - alert: ChannelHighErrorRate
        expr: |
          rate(fy_relay_errors_total[5m])
          / rate(fy_relay_requests_total[5m]) > 0.1
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "ch#{{ $labels.channel_id }} {{ $labels.model }} error > 10%"

      - alert: ChannelRateLimited
        expr: rate(fy_relay_errors_total{error_type="rate_limited"}[5m]) > 0.5
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "ch#{{ $labels.channel_id }} 被限流"

      - alert: ImageGenSlow
        expr: histogram_quantile(0.95, rate(fy_image_duration_seconds_bucket[10m])) > 300
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "图片生成 p95 > 5min: {{ $labels.model }}"

      - alert: HighRetryRate
        expr: rate(fy_relay_retries_total[5m]) > 1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.model }} 重试率过高"
```

## 5. AlertManager 配置

创建 `monitoring/alertmanager.yml`（钉钉/飞书 webhook 示例）：

```yaml
global:
  resolve_timeout: 5m

route:
  receiver: "default"
  group_by: ["alertname", "channel_id"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: "default"
    webhook_configs:
      # 钉钉机器人 webhook（需配合 prometheus-webhook-dingtalk）
      - url: "http://dingtalk-webhook:8060/dingtalk/ops/send"
        send_resolved: true
```

## 6. Grafana 配置

1. 访问 `http://<server>:3001`，用 admin/changeme 登录
2. 添加 Prometheus 数据源：URL = `http://prometheus:9090`
3. 导入或创建 Dashboard

### 常用 PromQL 查询

```promql
# 各渠道 QPS
sum(rate(fy_relay_requests_total[5m])) by (channel_id)

# 各模型 TTFT p95
histogram_quantile(0.95, sum(rate(fy_relay_ttft_seconds_bucket[5m])) by (le, model))

# 各渠道错误率
sum(rate(fy_relay_errors_total[5m])) by (channel_id)
/ sum(rate(fy_relay_requests_total[5m])) by (channel_id)

# 端到端延迟 p95 (按渠道)
histogram_quantile(0.95, sum(rate(fy_relay_duration_seconds_bucket[5m])) by (le, channel_id))

# 图片生成 p95 延迟
histogram_quantile(0.95, sum(rate(fy_image_duration_seconds_bucket[5m])) by (le, model))

# 429 限流次数 (按渠道)
sum(rate(fy_relay_errors_total{error_type="rate_limited"}[5m])) by (channel_id)

# 重试率
sum(rate(fy_relay_retries_total[5m])) by (model)
```

## 7. 安全注意事项

- `/metrics` 端点默认无鉴权，通过网络策略限制访问（仅允许 Prometheus IP）
- 在 Nginx 反向代理中屏蔽 `/metrics` 对外暴露：
  ```nginx
  location /metrics {
      allow 127.0.0.1;
      deny all;
  }
  ```
- Grafana 和 Prometheus 端口绑定 127.0.0.1，通过 SSH tunnel 或 VPN 访问

## 8. 基数控制

当模型数量增长时，注意监控 Prometheus 的 series 数：

```promql
# 检查 fy_* 指标的 series 数
count({__name__=~"fy_.*"})
```

如果超过 50K series，考虑：
1. 在 `normalizeModelLabel()` 中合并模型版本号（如 `gpt-4o-2024-11-20` → `gpt-4o`）
2. 减少 histogram bucket 数量
3. 对低流量渠道不记录 histogram

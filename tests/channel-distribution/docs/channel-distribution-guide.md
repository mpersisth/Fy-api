# 渠道分流配置指南

> 基于 2026-06-01 在 test 环境 (`api-test.tracenex.cn`) 的全量测试结果，覆盖 14 个场景，全部通过。

---

## 一、核心概念速查

| 概念 | 作用 | 配置位置 |
|------|------|----------|
| **优先级 (Priority)** | 决定「先用哪批渠道」 | 渠道管理 → 编辑渠道 → 优先级 |
| **权重 (Weight)** | 决定「同优先级渠道之间怎么分流」 | 渠道管理 → 编辑渠道 → 权重 |
| **渠道亲和性 (Channel Affinity)** | 让同一用户/会话的请求「粘」在同一渠道 | 系统设置 → 渠道亲和性设置 |

三者的执行顺序：**亲和性缓存命中 → 跳过选择，直接用缓存渠道**；未命中 → 按优先级分层 → 同层内按权重随机选择。

---

## 二、优先级配置

### 2.1 工作原理

- 数值越大 = 优先级越高（priority=1000 优先于 priority=500）
- 系统只会从**当前最高优先级的渠道组**中选渠道
- 当最高优先级的渠道全部不可用时，自动降级到下一优先级

### 2.2 测试验证结果

| 场景 | 结果 |
|------|------|
| 三个优先级 (1000/500/100) 全部在线 | 100% 流量到 priority=1000 |
| 禁用 priority=1000 | 100% 流量自动切到 priority=500 |
| 再禁用 priority=500 | 100% 流量自动切到 priority=100 |

降级是即时生效的，无需等待或手动切换。

### 2.3 最佳实践

```
推荐分层方案（以 deepseek-v4-flash 为例）：

priority=1000  主力渠道（成本低、延迟低的首选供应商）
priority=500   备用渠道（成本略高，作为降级兜底）
priority=100   应急渠道（最贵但最稳定，仅在前两层全挂时启用）
```

**关键规则：**

1. **不同供应商之间用优先级隔离**，不要把不同质量/成本的渠道放在同一优先级
2. **同一供应商的多个 key 用同一优先级**，配合权重做负载均衡
3. 优先级数值建议留足间隔（100/500/1000），方便日后插入中间层
4. **priority=0 且 weight=0 是「均匀分配」的特殊语义**，不建议生产使用

> ⚠️ 注意：新建渠道后需约 1-2 秒生效（系统需刷新渠道缓存）。通过后台修改渠道状态（启用/禁用）会立即触发缓存刷新。

---

## 三、权重配置

### 3.1 工作原理

- 仅在**同一优先级层内**生效
- 流量按权重比例分配：weight=50/30/20 → 50%/30%/20%
- weight=0 有特殊含义：所有 weight=0 的渠道会被等权重处理

### 3.2 测试验证结果

| 渠道 | 配置权重 | 期望比例 | 实际比例 (200次请求) |
|------|---------|---------|---------------------|
| weight-A | 50 | 50.0% | 50.0% |
| weight-B | 30 | 30.0% | 30.0% |
| weight-C | 20 | 20.0% | 20.0% |

卡方检验 p=1.000，分布完全符合预期。

### 3.3 最佳实践

```
场景一：同供应商多 key 负载均衡
  3 个 key，相同质量 → 每个 weight=100（等权重，各 33%）

场景二：新渠道灰度引入
  老渠道 weight=90，新渠道 weight=10 → 先切 10% 观察
  稳定后调整为 weight=50/50

场景三：成本优化（同优先级内）
  便宜渠道 weight=70，贵渠道 weight=30
  → 日常 70% 走便宜渠道，30% 走贵渠道做冗余
```

**关键规则：**

1. **权重比例才重要，绝对值不重要**：weight=50/30/20 和 weight=500/300/200 效果相同
2. **至少 200 次请求才能看到统计收敛**：少量请求的分布偏差是正常的
3. 不建议用极端比例（如 99:1），低权重渠道几乎不会被命中，不如直接降优先级

---

## 四、渠道亲和性配置（重点）

### 4.1 什么是渠道亲和性

渠道亲和性让「同一个用户/会话」的连续请求都路由到**同一个上游渠道**。这对以下场景至关重要：

- **Codex CLI / Claude CLI**：同一编码会话需要保持上下文一致
- **长对话**：避免中途切换渠道导致上下文丢失
- **Prompt Cache**：只有同一渠道才能命中上游的 prompt cache

### 4.2 测试验证结果

| 场景 | 指标 | 结果 |
|------|------|------|
| 同一 key 连续 20 次请求 | 粘性 | 100% 命中同一渠道 |
| 同一 key 连续 30 次长会话 | 命中率 | 100% |
| 10 个用户各 5 次（模拟回访） | 命中率 | 100% |
| TTL 内的请求 | 粘性 | ✅ 保持 |
| TTL 过期后 | 粘性 | ✅ 释放 |
| 亲和渠道宕机 + SkipRetry=true | 行为 | 3/3 请求失败（符合预期） |
| 亲和渠道宕机 + SkipRetry=false | 行为 | 3/3 自动降级成功 |
| SwitchOnSuccess=true | 行为 | 缓存自动切到成功渠道 |

### 4.3 配置项详解

系统设置中的渠道亲和性设置是一个 JSON 配置，包含以下字段：

#### 全局设置

| 字段 | 含义 | 推荐值 |
|------|------|--------|
| `enabled` | 是否启用 | `true` |
| `switch_on_success` | 降级后是否更新缓存到新渠道 | `true`（强烈推荐） |
| `max_entries` | 缓存最大条目数 | `100000` |
| `default_ttl_seconds` | 默认缓存过期时间 | 见下方推荐 |

#### 规则 (rules) 配置

每条规则定义「什么请求需要亲和」以及「用什么做粘性 key」：

| 字段 | 含义 | 说明 |
|------|------|------|
| `name` | 规则名称 | 用于监控和日志 |
| `model_regex` | 模型匹配正则 | 如 `["^gpt-.*$"]` 匹配所有 GPT 模型 |
| `path_regex` | 路径匹配正则 | 如 `["/v1/chat/completions"]` |
| `user_agent_include` | UA 子串匹配 | 如 `["codex"]`，空表示匹配所有 |
| `key_sources` | 粘性 key 来源 | 见下方详解 |
| `ttl_seconds` | 本规则的 TTL | 0 表示用全局 default_ttl |
| `skip_retry_on_failure` | 亲和渠道不可用时是否跳过重试 | 见下方场景分析 |
| `include_rule_name` | 缓存 key 是否包含规则名 | `true`（避免不同规则互相冲突） |
| `include_model_name` | 缓存 key 是否包含模型名 | 按需，见下方 |
| `include_using_group` | 缓存 key 是否包含分组名 | 多分组环境建议 `true` |

#### key_sources 详解

`key_sources` 定义了「用什么信息作为粘性 key」，决定了哪些请求被视为「同一用户/会话」：

| type | 用途 | 示例 |
|------|------|------|
| `request_header` | 从 HTTP 请求头提取 | `{"type": "request_header", "key": "X-Session-Id"}` |
| `gjson` | 从请求 body 的 JSON 中提取 | `{"type": "gjson", "path": "metadata.user_id"}` |
| `context` | 从内部上下文提取 | `{"type": "context", "key": "token_id"}` |

### 4.4 推荐配置方案

#### 方案 A：Codex CLI + Claude CLI（当前默认配置）

```json
{
  "enabled": true,
  "switch_on_success": true,
  "max_entries": 100000,
  "default_ttl_seconds": 3600,
  "rules": [
    {
      "name": "codex cli trace",
      "model_regex": ["^gpt-.*$"],
      "path_regex": ["/v1/responses"],
      "key_sources": [{"type": "gjson", "path": "prompt_cache_key"}],
      "ttl_seconds": 0,
      "skip_retry_on_failure": true,
      "include_rule_name": true,
      "include_using_group": true
    },
    {
      "name": "claude cli trace",
      "model_regex": ["^claude-.*$"],
      "path_regex": ["/v1/messages"],
      "key_sources": [{"type": "gjson", "path": "metadata.user_id"}],
      "ttl_seconds": 0,
      "skip_retry_on_failure": true,
      "include_rule_name": true,
      "include_using_group": true
    }
  ]
}
```

**适用场景：** 以 AI 编码助手为主要客户群体，需要保证 prompt cache 命中率。

#### 方案 B：通用 Chat 场景

```json
{
  "enabled": true,
  "switch_on_success": true,
  "max_entries": 100000,
  "default_ttl_seconds": 1800,
  "rules": [
    {
      "name": "chat session",
      "model_regex": [".*"],
      "path_regex": ["/v1/chat/completions"],
      "key_sources": [{"type": "context", "key": "token_id"}],
      "ttl_seconds": 1800,
      "skip_retry_on_failure": false,
      "include_rule_name": true,
      "include_model_name": true,
      "include_using_group": true
    }
  ]
}
```

**适用场景：** 通用 API 网关，多种模型和客户端混合使用。`skip_retry_on_failure=false` 保证可用性优先。

#### 方案 C：混合模式（推荐）

```json
{
  "enabled": true,
  "switch_on_success": true,
  "max_entries": 100000,
  "default_ttl_seconds": 3600,
  "rules": [
    {
      "name": "codex cli trace",
      "model_regex": ["^gpt-.*$"],
      "path_regex": ["/v1/responses"],
      "key_sources": [{"type": "gjson", "path": "prompt_cache_key"}],
      "ttl_seconds": 0,
      "skip_retry_on_failure": true,
      "include_rule_name": true,
      "include_using_group": true
    },
    {
      "name": "claude cli trace",
      "model_regex": ["^claude-.*$"],
      "path_regex": ["/v1/messages"],
      "key_sources": [{"type": "gjson", "path": "metadata.user_id"}],
      "ttl_seconds": 0,
      "skip_retry_on_failure": true,
      "include_rule_name": true,
      "include_using_group": true
    },
    {
      "name": "general chat",
      "model_regex": [".*"],
      "path_regex": ["/v1/chat/completions"],
      "key_sources": [{"type": "context", "key": "token_id"}],
      "ttl_seconds": 1800,
      "skip_retry_on_failure": false,
      "include_rule_name": true,
      "include_model_name": true,
      "include_using_group": true
    }
  ]
}
```

**适用场景：** 同时服务编码助手和通用 Chat 用户。规则按顺序匹配，CLI 流量命中前两条规则（强一致性），其他流量走第三条（可用性优先）。

### 4.5 关键决策：skip_retry_on_failure

这是最重要的配置决策，直接影响用户体验：

| 设置 | 行为 | 适用场景 |
|------|------|----------|
| `true` | 亲和渠道不可用时**直接报错** | Codex/Claude CLI（宁可报错也不换渠道，因为换渠道会丢失 prompt cache） |
| `false` | 亲和渠道不可用时**自动降级到其他渠道** | 通用 Chat（用户感知不到渠道切换，可用性优先） |

**决策依据：**
- 客户是否依赖上游的 prompt cache？→ 是 → `true`
- 客户能否接受偶尔的请求失败？→ 否 → `false`
- 该模型只有一个上游渠道？→ 无所谓，不影响

### 4.6 TTL 调优建议

| TTL | 适用场景 | 权衡 |
|-----|---------|------|
| 300s (5min) | 短会话、API 调用型场景 | 缓存条目释放快，节省内存 |
| 1800s (30min) | 通用 Chat 对话 | 覆盖大多数对话长度 |
| 3600s (1h) | 编码助手、长会话 | 保证长时间编码会话的一致性 |
| 7200s+ (2h+) | 特殊场景 | 慎用，缓存条目积压可能影响负载均衡效果 |

**实测发现：** TTL 内的命中率接近 100%，TTL 过期后立即释放。建议根据实际会话时长设置，不要过度激进。

---

## 五、配置组合示例

### 示例 1：两个 DeepSeek 渠道，一主一备

```
渠道 A（sophnet）:   priority=1000, weight=100
渠道 B（腾讯hub）:   priority=500,  weight=100
亲和性: 不需要（DeepSeek 无 prompt cache）
```

→ 日常 100% 走渠道 A，A 挂了自动切到 B。

### 示例 2：三个 GPT-4o 渠道，按比例分流

```
渠道 A（Azure East）:  priority=1000, weight=50
渠道 B（Azure West）:  priority=1000, weight=30
渠道 C（OpenAI 直连）: priority=1000, weight=20
亲和性: 建议开启，用 token_id 做 key，TTL=30min
```

→ 流量按 50:30:20 分配，同一用户在 30 分钟内粘在同一渠道。

### 示例 3：Claude 编码助手 + 通用 Chat 混合

```
Claude 渠道 A:  priority=1000, weight=60
Claude 渠道 B:  priority=1000, weight=40
GPT 渠道 C:    priority=1000, weight=100
备用渠道 D:     priority=500,  weight=100

亲和性: 方案 C（混合模式）
  - claude cli trace: skip_retry=true, TTL=1h
  - general chat: skip_retry=false, TTL=30min
```

→ Claude CLI 用户粘在同一渠道（保 prompt cache），Chat 用户可用性优先。

---

## 六、运维检查清单

### 日常检查

- [ ] 查看亲和性缓存统计：`GET /api/option/channel_affinity_cache`，关注 `total` 和各规则的命中数
- [ ] 确认渠道状态（状态=1 启用，状态=2 禁用）
- [ ] 观察日志中的 `channel` 字段分布是否符合预期

### 新增渠道时

1. 创建渠道，设置好 priority 和 weight
2. **等待 1-2 秒**让渠道缓存刷新（或手动编辑一次渠道触发刷新）
3. 用少量请求验证流量是否到达新渠道
4. 逐步调整权重到目标比例

### 渠道故障时

1. 在后台禁用故障渠道（状态设为 2）→ 流量自动切到同优先级其他渠道或降级
2. 如果亲和性缓存指向了故障渠道：
   - `skip_retry=false` → 自动降级，无需操作
   - `skip_retry=true` → 需要清除亲和性缓存：`DELETE /api/option/channel_affinity_cache?all=true`
3. 渠道恢复后重新启用，流量会自动恢复

### 调整亲和性配置时

1. 在系统设置中修改亲和性规则
2. 清除缓存：`DELETE /api/option/channel_affinity_cache?all=true`
3. 等待 1 秒后新规则生效

---

## 七、FAQ

**Q: 修改权重后多久生效？**
A: 修改渠道权重后会立即触发缓存刷新，下一次请求即按新权重分配。但如果开启了亲和性，已缓存的用户仍会粘在原渠道，直到 TTL 过期。

**Q: 同一模型有多个优先级的渠道，亲和性怎么处理？**
A: 亲和性缓存的是具体渠道 ID。如果亲和渠道被禁用且 `skip_retry=false`，会在其他可用渠道中选一个；如果 `switch_on_success=true`，缓存会更新到新渠道。

**Q: max_entries 设多少合适？**
A: 每个条目约占几十字节内存。10 万条 ≈ 几 MB 内存，对服务无感知压力。除非 DAU 超过 10 万，否则保持默认 100000 即可。

**Q: 亲和性会影响负载均衡吗？**
A: 会。亲和性会让流量「粘」在某些渠道上，导致短期内的流量分布不完全均匀。但在用户量足够多时（>100 并发用户），整体分布仍趋向权重比例。TTL 过长会加剧不均衡。

**Q: 为什么新建的渠道没有立即收到流量？**
A: fy-api 的渠道路由有 60 秒缓存周期。新建渠道后，可以通过「编辑」一次该渠道（无需修改任何字段，直接保存）来触发缓存刷新。

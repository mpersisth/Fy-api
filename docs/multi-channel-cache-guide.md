# 多渠道混流与 Prompt Cache 优化指南

本文档梳理 new-api 多渠道混流场景下的缓存命中机制、注意事项和优化策略。

---

## 1. 核心机制

### 1.1 API 网关是无状态代理

- 客户端每次请求带**完整对话历史**（messages 数组），切渠道不会丢"上下文"
- 所谓"1M 上下文"指模型单次请求能接受的最大输入 token 数
- 对话越长，请求体越大，每轮都在重复发送之前所有内容

### 1.2 Prompt Cache 缓存的是什么

**缓存的是输入计算的中间状态（KV cache），不是输出结果。**

| | 传统缓存（CDN/Redis） | 大模型 Prompt Cache |
|--|--|--|
| 缓存什么 | 最终结果（response） | 输入处理的中间状态（KV cache） |
| 命中后 | 直接返回相同结果 | 跳过输入计算，但仍然重新生成输出 |
| 输出是否相同 | 是 | 否（每次独立生成） |

### 1.3 缓存作用域

**Prompt cache 是 per-upstream-API-key 的，不是 per-user 的。**

- 同一个渠道（同一个 key）下所有用户共享缓存池
- 切渠道 = 换 key = 之前积累的缓存全部失效

### 1.4 多用户共享缓存

同一渠道下，不同用户如果共享相同的 system prompt 前缀：
- 用户 A 的请求创建了 system prompt 的缓存
- 用户 B 发相同 system prompt → 命中用户 A 建立的缓存
- 共享部分仅限于前缀匹配的那段，各自的对话历史不共享

---

## 2. 各模型 Prompt Cache 机制对比

### 2.1 海外模型

| | Claude (Anthropic) | OpenAI (GPT-4o/o1) | Gemini | DeepSeek |
|--|--|--|--|--|
| **触发方式** | 显式标记 `cache_control` | 全自动 | 显式创建 `CachedContent` | 全自动 |
| **最小前缀** | 1024 tokens | 1024 tokens | 32,768 tokens | 64 tokens |
| **缓存粒度** | 精确前缀匹配 | 精确前缀匹配 | 整个 CachedContent 对象 | 精确前缀匹配 |
| **TTL** | 5min / 1h（付费） | 5-10min（不可控） | 用户指定（1h 起） | 几分钟（不公开） |
| **缓存作用域** | per-API-key | per-organization | per-project | per-API-key |
| **cache read 折扣** | 90% off | 50% off | 75% off | 90% off（硬盘）/ 100% off（内存） |
| **cache write 加价** | 25% | 无 | 按时长收费 | 无 |
| **可观测性** | `cache_read_input_tokens` | `cached_tokens` | `cachedContentTokenCount` | `prompt_cache_hit_tokens` |
| **显式标记** | 支持（必须） | 不支持 | 支持（必须） | 不支持 |

### 2.2 国内模型

| | DeepSeek | Kimi (Moonshot) | GLM (智谱) | 通义千问 (Qwen) |
|--|--|--|--|--|
| **触发方式** | 全自动 | 全自动 | 全自动 | 全自动（需开启） |
| **最小前缀** | 64 tokens | 1024 tokens | 1024 tokens | 1024 tokens |
| **TTL** | 几分钟（不公开） | ~5 min | 不公开 | 不公开 |
| **缓存作用域** | per-API-key | per-API-key | per-API-key | per-API-key |
| **cache read 折扣** | 90%~100% off | 有折扣（不透明） | 无公开折扣 | 无公开折扣 |
| **cache write 加价** | 无 | 无 | 无 | 无 |
| **可观测性** | `prompt_cache_hit_tokens` | `cached_tokens` | 无明确字段 | 无明确字段 |

### 2.3 同厂商不同模型的缓存支持

**规律：只有文本生成/对话/推理类模型支持 Prompt Cache，图片/音频/Embedding 不支持。**

| 厂商 | 支持缓存的模型 | 不支持的模型 |
|------|---------------|-------------|
| OpenAI | GPT-4o/4.1/4-turbo, o1/o3/o4-mini | GPT-Image, DALL-E, Whisper, TTS, Embedding |
| Anthropic | 全系列 Claude 文本模型 | — |
| DeepSeek | V3 (deepseek-chat), R1 (deepseek-reasoner), Coder | — |
| Google | Gemini 2.5/2.0/1.5 Pro/Flash | Imagen, Embedding |
| Moonshot | moonshot-v1-*, kimi-latest | — |
| 智谱 | GLM-4-Plus/Air/Flash | CogView (图片) |
| 阿里 | qwen-max/plus/turbo | wanx (图片), cosyvoice (语音) |

**注意：Reasoning 模型（o1/o3/R1）的 reasoning tokens 本身不被缓存，只有 input 前缀被缓存。**

---

## 3. 缓存命中的详细条件

### 3.1 Claude

```
命中 = 同 API key
     ∧ 消息序列从头开始逐 block 精确匹配
     ∧ 匹配到带 cache_control 标记的 block 末尾
     ∧ 该缓存在 TTL 内未过期
     ∧ 前缀 ≥ 1024 tokens
```

**匹配规则：**
- 匹配单位是 content block（不是 message），从第一个 system block 开始
- `cache_control` 标记是"断点"——告诉 API "缓存到这里为止"
- 可以设置多个断点，形成嵌套缓存层
- 任何一个 byte 不同（包括空格、换行）就算不匹配

**典型缓存层结构：**
```
┌─────────────────────────────────────────────┐
│ system prompt (2K tokens)    [cache_control] │ ← 所有用户共享
├─────────────────────────────────────────────┤
│ few-shot examples (5K tokens) [cache_control]│ ← 同应用共享
├─────────────────────────────────────────────┤
│ 用户前 N 轮对话 (30K tokens) [cache_control] │ ← 单用户多轮共享
├─────────────────────────────────────────────┤
│ 当前轮输入 (500 tokens)                      │ ← 不缓存，每次重新计算
└─────────────────────────────────────────────┘
```

### 3.2 OpenAI

```
命中 = 同 organization
     ∧ 消息序列从头开始逐 token 精确匹配
     ∧ 前缀 ≥ 1024 tokens
     ∧ 自动缓存未过期（5-10 min，不可控）
```

与 Claude 的区别：无需显式标记、无法控制断点和 TTL、折扣只有 50%。

### 3.3 DeepSeek

```
命中 = 同 API key
     ∧ 消息序列从头开始逐 token 精确匹配
     ∧ 前缀 ≥ 64 tokens（门槛极低）
     ∧ 缓存未过期
```

分两层：内存缓存命中完全免费，硬盘缓存命中按 10% 计费。

### 3.4 Gemini

```
命中 = 请求中引用了有效的 cachedContent name
     ∧ 该 CachedContent 对象未过期
     ∧ 前缀 ≥ 32,768 tokens
```

不是隐式前缀匹配，而是显式引用预创建的缓存对象。不受渠道切换影响（只要同 project）。

---

## 4. 混流的代价

### 4.1 切渠道真正丢失的 vs 不丢失的

| 丢失的 | 影响 |
|--------|------|
| Prompt cache | 公共前缀变全价计费 |
| Gemini 显式 Context Cache | 引用失效 |
| Rate limit 额度 | 换了一个额度池 |

| 不丢失的 | 原因 |
|----------|------|
| 对话上下文 | 客户端每次都带完整 messages |
| 模型能力 | 同模型不同 key，推理能力一样 |
| 输出质量 | 不受渠道切换影响 |

### 4.2 成本影响量化（单次请求，50K context）

| 模型 | Input 单价 | 缓存命中 | 缓存未命中 | 差异倍数 |
|------|-----------|----------|------------|----------|
| Claude Sonnet 4 | ¥21/M | ¥0.20 | ¥1.05 | 5.2x |
| Claude Opus 4 | ¥105/M | ¥0.99 | ¥5.25 | 5.3x |
| DeepSeek V3 | ¥2/M | ¥0.019 | ¥0.10 | 5.3x |
| DeepSeek R1 | ¥4/M | ¥0.038 | ¥0.20 | 5.3x |
| GPT-4o | ¥17.5/M | ¥0.48 | ¥0.875 | 1.8x |
| Gemini 2.5 Pro | ¥8.75/M | ¥0.14 | ¥0.44 | 3.1x |
| Kimi 128k | ¥60/M | ~¥1.0 | ¥3.0 | ~3x |

**结论：DeepSeek 和 Claude 对缓存命中最敏感（90%+ 折扣），混流优化优先级最高。**

---

## 5. 渠道选择与亲和性机制

### 5.1 new-api 渠道选择流程

```
1. Token 绑定渠道？ → 直接使用
2. 亲和性缓存命中？ → 使用缓存的渠道
3. 加权随机选择 → 按 priority 分层，同层按 weight 随机
```

重试机制：retry=0 用最高优先级层，retry=1 降到次优先级层。

### 5.2 亲和性配置

配置入口：管理后台 → 系统设置 → `channel_affinity_setting`

**核心配置项：**

| 配置项 | 说明 | 推荐值 |
|--------|------|--------|
| `enabled` | 全局开关 | `true` |
| `switch_on_success` | 重试成功后是否更新亲和缓存 | `false` |
| `max_entries` | 缓存条目上限 | 100,000 |
| `default_ttl_seconds` | 默认过期时间 | 7200 |

**每条规则可配：**

| 字段 | 说明 |
|------|------|
| `model_regex` | 匹配模型名 |
| `path_regex` | 匹配请求路径 |
| `user_agent_include` | 匹配 User-Agent |
| `key_sources` | 亲和 key 提取源（header / gjson / context） |
| `ttl_seconds` | 单独覆盖 TTL |
| `skip_retry_on_failure` | 亲和渠道不可用时是否跳过重试 |

### 5.3 内置默认规则

| 规则 | 匹配 | Key 来源 | 行为 |
|------|------|----------|------|
| `claude cli trace` | `^claude-.*$` + `/v1/messages` | `metadata.user_id` | `skip_retry=true` |
| `codex cli trace` | `^gpt-.*$` + `/v1/responses` | `prompt_cache_key` | `skip_retry=true` |

### 5.4 亲和性失效 = 缓存断裂的场景

1. TTL 到期 → 下次请求随机选渠道
2. 渠道被禁用且 `skip_retry_on_failure=false` → fallback 到别的渠道
3. `switch_on_success=true` + 偶然重试成功 → 亲和性指向新渠道
4. 渠道 weight/priority 调整 → 新请求可能分配到不同渠道

---

## 6. 优化策略

### 6.1 公式

```
缓存命中率 = f(亲和性命中率 × 渠道集中度 × 前缀稳定性)
```

### 6.2 策略一：扩展亲和性规则

推荐配置（覆盖主流场景）：

```json
{
  "enabled": true,
  "switch_on_success": false,
  "max_entries": 100000,
  "default_ttl_seconds": 7200,
  "rules": [
    {
      "name": "codex cli trace",
      "model_regex": ["^gpt-.*$"],
      "path_regex": ["/v1/responses"],
      "key_sources": [{"type": "gjson", "path": "prompt_cache_key"}],
      "ttl_seconds": 7200,
      "skip_retry_on_failure": true,
      "include_using_group": true,
      "include_rule_name": true
    },
    {
      "name": "claude cli trace",
      "model_regex": ["^claude-.*$"],
      "path_regex": ["/v1/messages"],
      "key_sources": [{"type": "gjson", "path": "metadata.user_id"}],
      "ttl_seconds": 7200,
      "skip_retry_on_failure": true,
      "include_using_group": true,
      "include_rule_name": true
    },
    {
      "name": "cursor session",
      "model_regex": ["^claude-.*$", "^gpt-.*$", "^deepseek-.*$"],
      "path_regex": ["/v1/chat/completions", "/v1/messages"],
      "user_agent_include": ["Cursor", "cursor"],
      "key_sources": [
        {"type": "request_header", "key": "x-cursor-session-id"},
        {"type": "request_header", "key": "x-request-id"}
      ],
      "ttl_seconds": 7200,
      "skip_retry_on_failure": true,
      "include_using_group": true,
      "include_model_name": true,
      "include_rule_name": true
    },
    {
      "name": "generic long conversation",
      "model_regex": ["^claude-.*$", "^gpt-4.*$", "^o[1-9].*$", "^deepseek-.*$"],
      "path_regex": ["/v1/chat/completions", "/v1/messages"],
      "key_sources": [
        {"type": "request_header", "key": "x-session-id"},
        {"type": "context_int", "key": "id"}
      ],
      "ttl_seconds": 3600,
      "skip_retry_on_failure": false,
      "include_using_group": true,
      "include_model_name": true,
      "include_rule_name": true
    }
  ]
}
```

### 6.3 策略二：渠道分池

```
Group: "ide"（长对话专用）
  ├── channel-claude-1 (priority: 10, weight: 100)  ← 少量高限额 key
  └── channel-claude-2 (priority: 10, weight: 100)

Group: "api"（短请求分散）
  ├── channel-claude-3 (priority: 5, weight: 50)
  ├── channel-claude-4 (priority: 5, weight: 50)
  └── channel-claude-5 (priority: 5, weight: 50)
```

- IDE/CLI 用户 → `ide` group，集中路由，缓存命中率高
- API 短请求 → `api` group，多渠道分散，换吞吐量

### 6.4 策略三：System Prompt 标准化

- 公共 system prompt 放前面，个性化内容放后面
- 不要在 system prompt 中包含时间戳、随机 ID 等动态内容
- Claude 场景：在 system prompt 末尾标记 `cache_control`

### 6.5 策略四：减少同池渠道数量

**渠道多 ≠ 更好。** 对缓存敏感的场景，渠道越少、用户越集中，缓存命中率越高。

向上游申请提高单 key 的 TPM/RPM，而不是靠多 key 分流。

### 6.6 混流策略优先级（按模型）

| 优先级 | 模型 | 理由 | 建议 |
|--------|------|------|------|
| **最高** | DeepSeek | 折扣最大（免费~10%），门槛最低（64 tokens） | 强亲和性 + 单渠道集中 |
| **高** | Claude | 90% 折扣 + 支持显式标记 | 强亲和性 + 注入 cache_control |
| **中** | Gemini | 75% 折扣，显式引用不受渠道切换影响 | 确保同 project 即可 |
| **中低** | GPT-4o | 50% 折扣，自动管理 | 亲和性可选 |
| **低** | Kimi / GLM / Qwen | 缓存机制不透明 | 按可用性优先 |

---

## 7. 监控与排查

### 7.1 Prometheus 指标

| 指标 | 说明 |
|------|------|
| `fy_relay_prompt_tokens_total` | 输入 token 总数 |
| `fy_relay_cached_tokens_total` | 缓存命中 token 总数 |
| `fy_relay_cache_creation_tokens_total` | 缓存创建 token 总数 |
| `fy_affinity_lookups_total` | 亲和性查询次数（label: outcome=hit/miss） |
| `fy_affinity_active_entries` | 活跃亲和性缓存条目数 |

### 7.2 关键 PromQL

```promql
# 缓存命中率（按渠道）
sum(rate(fy_relay_cached_tokens_total[5m])) by (channel_id)
/ sum(rate(fy_relay_prompt_tokens_total[5m])) by (channel_id)

# 亲和性命中率
sum(rate(fy_affinity_lookups_total{outcome="hit"}[5m]))
/ sum(rate(fy_affinity_lookups_total[5m]))
```

### 7.3 告警规则

| 告警 | 条件 | 含义 |
|------|------|------|
| LowCacheHitRate | 缓存命中率 < 20% 持续 10min | 亲和性失效或渠道切换频繁 |
| LowAffinityHitRate | 亲和性命中率 < 50% 持续 10min | TTL 过短或规则未覆盖 |
| AffinityEntriesHigh | 活跃条目 > 80K | 接近上限，需扩容或缩短 TTL |

### 7.4 缓存未命中排查

| 现象 | 可能原因 | 排查方式 |
|------|----------|----------|
| `cached_tokens` 始终为 0 | 未标记 cache_control / 前缀 < 阈值 | 检查请求体 |
| 缓存率突然下降 | 亲和性 TTL 到期 / 渠道禁用 | 看 `fy_affinity_lookups_total{outcome="miss"}` |
| 同用户缓存率波动 | system prompt 含动态内容 | 检查是否有时间戳/随机 ID |
| 不同用户间不共享 | 路由到不同渠道 | 按 channel_id 看分布 |
| OpenAI 缓存率低 | 自动 TTL 不可控（5-10 min） | 短时间集中发送验证 |

---

## 8. new-api 中各模型缓存字段映射

```
DeepSeek:  usage.prompt_cache_hit_tokens       → dto.Usage.PromptCacheHitTokens
Claude:    usage.cache_read_input_tokens       → dto.Usage.PromptTokensDetails.CachedTokens
OpenAI:    usage.prompt_tokens_details.cached_tokens → dto.Usage.PromptTokensDetails.CachedTokens
Kimi:      usage.cached_tokens (私有字段)       → dto.Usage.PromptCacheHitTokens
Gemini:    usageMetadata.cachedContentTokenCount → dto.Usage.PromptTokensDetails.CachedTokens
GLM:       无明确字段
```

这些字段最终汇入 `fy_relay_cached_tokens_total` 指标，可在 Grafana 中按 model 维度对比各家实际缓存命中率。

---

## 9. 上下文压缩与缓存的关系

### 9.1 谁在做压缩

| 层面 | 谁做的 | 机制 |
|------|--------|------|
| **客户端** | Claude Code / Cursor / 自研应用 | 本地截断或摘要旧消息，减少发送量 |
| **上游 API** | OpenAI `/v1/responses/compact` | 服务端压缩，返回精简后的上下文 |
| **网关 (new-api)** | 不做 | 纯透传，不修改请求体 |

new-api 是无状态代理，不做任何上下文压缩。代码中的 `relay/channel/openai/relay_responses_compact.go` 只是透传 OpenAI 的 compaction API。

### 9.2 核心矛盾：压缩 = 改变前缀 = 缓存失效

```
第 10 轮（未压缩）：
[system][msg1][msg2]...[msg9][msg10]
 ↑ 前 9 轮完全匹配上一次请求 → 缓存命中

第 10 轮（压缩了前 5 轮）：
[system][summary_of_1-5][msg6]...[msg9][msg10]
 ↑ summary 和原始 msg1-5 不同 → 缓存全部失效
```

### 9.3 各客户端的压缩行为

| 客户端 | 触发时机 | 压缩方式 | 对亲和性影响 | 对缓存影响 |
|--------|----------|----------|-------------|-----------|
| Claude Code | 接近 context window 上限 | 摘要旧消息，保留最近几轮 | 无（user_id 不变） | 失效一次，后续恢复 |
| Cursor | 超长对话 | 截断旧消息 | 无（session_id 不变） | 同上 |
| OpenAI Codex CLI | 主动调用 compact | 服务端压缩 | 无（prompt_cache_key 可能变） | 同上 |

### 9.4 压缩时机的成本权衡

```
不压缩（持续增长）：
  每轮增量成本 ≈ 新增 tokens × 全价 + 历史 tokens × 缓存价
  总成本随轮次线性增长，但增速被缓存折扣压低

压缩触发时：
  一次性代价 = 压缩后 context 大小 × 全价（缓存失效）
  后续恢复 = 第二次请求起重新命中缓存
```

### 9.5 最优策略：分段缓存 + 延迟压缩

**Claude 分段缓存（推荐）：**

```
[system prompt]           [cache_control] ← 永远命中（所有请求共享）
[压缩后的历史摘要]         [cache_control] ← 压缩后重建，后续命中
[最近 3-5 轮完整对话]     [cache_control] ← 每轮增量更新
[当前轮输入]                              ← 不缓存
```

即使压缩了中间部分，system prompt 的缓存仍然有效。

**实际建议：**

| 策略 | 适用场景 |
|------|----------|
| 尽量不压缩 | context < 50% window |
| 延迟压缩 | 等到 70-80% window 再触发 |
| 分段 cache_control | Claude 场景，保护外层缓存 |
| 亲和性 TTL > 压缩周期 | 避免"压缩 + 切渠道"同时发生 |

### 9.6 最坏情况

**亲和性 TTL 到期 + 上下文压缩同时发生 → 新 key + 新前缀 = 完全从零建立缓存。**

避免方法：亲和性 TTL 设为压缩周期的 2 倍以上（如客户端每 2 小时压缩一次，TTL 至少 4 小时）。

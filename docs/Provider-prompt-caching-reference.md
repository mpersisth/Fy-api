# 各厂商 Prompt Caching 机制参考

> 最后更新：2026-06-01
>
> 本文档梳理 TraceNex 网关涉及的各主流 AI 厂商的 prompt/context caching 机制，
> 包括触发方式、计费规则、返回字段、以及网关侧需要注意的兼容性问题。

---

## 一、总览对比

### 触发方式

| 厂商 | 缓存类型 | 客户端是否需要操作 | 最低 token 要求 |
|------|---------|------------------|----------------|
| **Anthropic Claude** | 显式标注 | 必须在 content block 上加 `cache_control` | 1,024（Sonnet）/ 4,096（Opus、Haiku 4.5） |
| **OpenAI** | 自动 | 无需操作 | 1,024 |
| **DeepSeek** | 自动 | 无需操作 | 64 |
| **Google Gemini** | 显式创建 + 隐式自动 | 显式需通过 Caching API 预创建；隐式自动 | 2,048（显式） |
| **智谱 (ZhiPu)** | 隐式自动 + 显式标注 | 隐式无需操作；显式需加 `cache_control` | 512 |
| **Moonshot (Kimi)** | 自动 | 无需操作 | — |

### 计费

| 厂商 | 缓存读取折扣 | 缓存写入加价 | 说明 |
|------|------------|------------|------|
| **Claude** | 0.1x（省 90%） | 1.25x（5min）/ 2x（1h） | 双 TTL 档位 |
| **OpenAI** | 0.1x ~ 0.5x | 无 | 因模型而异（GPT-5 系列 0.1x，GPT-4o 系列 0.5x，GPT-4.1 系列 0.25x） |
| **DeepSeek** | 0.1x（省 90%） | 无 | 磁盘缓存，存储免费 |
| **Gemini** | 0.1x（省 90%） | 无（显式有存储时长费） | 显式缓存按存储时长计费（每百万 token/小时） |
| **智谱** | 0.2x ~ 0.25x | 1.25x（显式） | 隐式 0.2x-0.25x，显式创建 1.25x |
| **Moonshot** | 0.25x（省 75%） | 无 | 自动缓存 |

---

## 二、各厂商详解

### 1. Anthropic Claude

**官方文档**: https://platform.claude.com/docs/en/build-with-claude/prompt-caching

#### 触发方式

Claude 的 prompt caching 需要客户端**显式标注** `cache_control`。在需要缓存的 content block 上添加：

```json
{
  "type": "text",
  "text": "这里是很长的 system prompt...",
  "cache_control": { "type": "ephemeral" }
}
```

不加标注的请求不会触发缓存创建，即使 prompt 完全一样。

#### TTL（缓存有效期）

- **5 分钟**（默认）：`"cache_control": {"type": "ephemeral"}`
- **1 小时**：`"cache_control": {"type": "ephemeral", "ttl": "1h"}`
- 每次缓存命中会重置 TTL 倒计时

#### 返回字段

Claude 的 usage 字段和 OpenAI 不同，缓存信息在顶层 `usage` 中：

```json
{
  "usage": {
    "input_tokens": 100,
    "cache_read_input_tokens": 50000,
    "cache_creation_input_tokens": 20000,
    "output_tokens": 500,
    "cache_creation": {
      "ephemeral_5m_input_tokens": 20000,
      "ephemeral_1h_input_tokens": 0
    }
  }
}
```

- `cache_read_input_tokens` — 缓存命中的 token 数
- `cache_creation_input_tokens` — 缓存写入的 token 数
- `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` — 按 TTL 分档

#### 网关注意事项

- **OAI→Claude 转换路径**（`/v1/chat/completions` → Claude channel）：当前代码 `RequestOpenAI2ClaudeMessage` 在构造 `ClaudeMediaMessage` 时**未转发** `CacheControl` 字段（`relay/channel/claude/relay-claude.go:389`）。通过 OpenAI 格式调用 Claude 的用户无法使用 prompt caching。（上游 issue: [QuantumNous/new-api#4240](https://github.com/QuantumNous/new-api/issues/4240)，截至 2026-06-01 仍 OPEN）
- **Claude 原生协议**（`/v1/messages`）：`ClaudeMessage.Content` 是 `any` 类型，JSON roundtrip 能保留 `cache_control`，正常工作。
- **AWS Bedrock**：`cache_control.scope` 子字段会被 Bedrock 拒绝，网关会自动剥离（`relay/channel/aws/bedrock_content_filter.go`）。
- **流式响应**：AWS Bedrock 的 `message_delta` 事件可能缺少 cache 字段，网关有 `patchClaudeMessageDeltaUsageData` 补丁。
- Claude Code CLI 会自动在 system prompt 和长对话上加 `cache_control`，所以 CLI 用户天然有缓存。普通客户端一般不会加。

---

### 2. OpenAI

**官方文档**: https://developers.openai.com/api/docs/guides/prompt-caching

#### 触发方式

完全**自动**，无需任何代码修改。API 自动对 1,024 token 以上的 prompt 前缀进行缓存，以 128 token 为增量单位。可缓存内容包括：messages、images、audio、tool definitions、structured output schemas。

可选参数 `prompt_cache_key` 可用于影响路由，提高多请求间的缓存命中率。

#### TTL

- 通常 5-10 分钟无访问后自动清除
- 低峰期可能保留最长 1 小时
- **Extended cache retention**（扩展保留）：通过 GPU 本地存储离线缓存，最长可达 24 小时

#### 返回字段

```json
{
  "usage": {
    "prompt_tokens": 1000,
    "completion_tokens": 200,
    "prompt_tokens_details": {
      "cached_tokens": 800
    }
  }
}
```

字段位置标准，无特殊处理。

#### 网关注意事项

- 返回字段已在标准位置，网关直接解析 `prompt_tokens_details.cached_tokens`。
- 特殊兼容：llama.cpp 兼容服务器使用非标准字段 `timings.cache_n`，网关有 `extractLlamaCachedTokensFromBody` 处理。

---

### 3. DeepSeek

**官方文档**: https://api-docs.deepseek.com/guides/kv_cache

#### 触发方式

完全**自动**，默认对所有用户开启。基于磁盘的 Context Caching 技术，自动检测请求间的公共前缀并缓存。64 token 为最小缓存粒度。

缓存在以下位置自动创建：
- 用户输入的末尾
- 模型输出的末尾
- 多个请求的公共前缀
- 长输入/输出中的固定 token 间隔点

#### 返回字段

DeepSeek 使用**非标准字段名**，在 `usage` 顶层：

```json
{
  "usage": {
    "prompt_tokens": 1000,
    "prompt_cache_hit_tokens": 800,
    "prompt_cache_miss_tokens": 200,
    "completion_tokens": 500
  }
}
```

- `prompt_cache_hit_tokens` — 缓存命中（不在 `prompt_tokens_details` 里！）
- `prompt_cache_miss_tokens` — 缓存未命中

#### 网关注意事项

- 网关在 `applyUsagePostProcessing`（`relay/channel/openai/relay-openai.go:595`）中做了归一化：将 `prompt_cache_hit_tokens` 复制到标准 `PromptTokensDetails.CachedTokens`。
- 缓存命中率公式：`cache_hit_rate = prompt_cache_hit_tokens / prompt_tokens`

---

### 4. Google Gemini

**官方文档**: https://ai.google.dev/gemini-api/docs/caching

#### 触发方式

两种模式：

1. **显式缓存**（Explicit Caching）：需要通过独立的 Caching API 预先创建缓存对象，拿到资源名后在请求中通过 `cachedContent` 引用：

```json
{
  "cachedContent": "cachedContents/abc123",
  "contents": [{"role": "user", "parts": [{"text": "基于上面的文档回答..."}]}]
}
```

2. **隐式缓存**（Implicit Caching）：自动启用，无需配置，系统自动检测可缓存前缀。但不保证命中，且无存储费用。

#### TTL

显式缓存默认 TTL 可自定义，支持通过 API 更新 `ttl` 或 `expire_time`。最小 2,048 token 才能创建缓存。

#### 返回字段

```json
{
  "usageMetadata": {
    "promptTokenCount": 1000,
    "cachedContentTokenCount": 800,
    "candidatesTokenCount": 200
  }
}
```

- `cachedContentTokenCount` — 缓存命中 token 数

#### 网关注意事项

- 网关在 `relay-gemini.go:1026` 将 `CachedContentTokenCount` 映射到 `PromptTokensDetails.CachedTokens`。
- 无缓存创建 token 的跟踪（不计写入成本）。
- 显式缓存需要客户端额外调用 Caching API，网关不代理这个操作。

---

### 5. 智谱 (ZhiPu / GLM)

**官方文档**: https://docs.bigmodel.cn/cn/guide/models/text/glm-5.1

#### 触发方式

1. **隐式缓存**（自动）：默认开启，自动识别公共前缀。最少 512 token。
2. **显式缓存**：在 content 上加 `cache_control` 标记（同 Claude 语法）：

```json
{ "cache_control": { "type": "ephemeral" } }
```

单次请求最多 4 个缓存标记，每个标记向前回溯最多 20 个 content 块。

#### 返回字段

字段位置不固定，可能出现在：
- `usage.prompt_tokens_details.cached_tokens`（标准位置）
- `usage.input_tokens_details.cached_tokens`（备选位置）

#### 网关注意事项

- `applyUsagePostProcessing` 中有 `ChannelTypeZhipu_v4` 分支（`relay-openai.go`），按优先级从三个位置尝试提取 cached_tokens。
- 通过阿里云百炼部署的智谱模型，隐式缓存折扣为 0.2x；智谱直连为 0.25x。

---

### 6. Moonshot (Kimi)

**官方文档**: https://platform.kimi.ai/docs/api/chat

#### 触发方式

完全**自动**，无需操作。系统自动检测相同前缀并缓存。

#### 返回字段

Moonshot 的 `cached_tokens` 在**非标准位置**——最后一个流式 chunk 的 `choices[].usage` 中，而非顶层 `usage`：

```json
{
  "choices": [{
    "usage": {
      "cached_tokens": 800
    }
  }]
}
```

#### 网关注意事项

- `applyUsagePostProcessing` 中有 `ChannelTypeMoonshot` 分支，使用 `extractMoonshotCachedTokensFromBody` 从 `choices[].usage.cached_tokens` 提取。
- 已知问题：interleaved thinking 模式下 cached_tokens 可能异常下降。

---

## 三、网关侧缓存 token 归一化流程

所有 provider 的缓存 token 最终都归一到 `dto.Usage` 的统一字段：

```
┌──────────────────────┐     ┌──────────────────────────────────┐
│  Claude              │     │  usage.PromptTokensDetails       │
│  cache_read_input_   │────▶│    .CachedTokens        (读取)   │
│  tokens              │     │    .CachedCreationTokens (写入)   │
│  cache_creation_     │     │  usage.ClaudeCacheCreation5m/1h  │
│  input_tokens        │     │    Tokens                (分档)   │
├──────────────────────┤     ├──────────────────────────────────┤
│  OpenAI              │     │                                  │
│  prompt_tokens_      │────▶│  usage.PromptTokensDetails       │
│  details.cached_     │     │    .CachedTokens                 │
│  tokens              │     │  （直接解析，无需转换）              │
├──────────────────────┤     ├──────────────────────────────────┤
│  DeepSeek            │     │                                  │
│  prompt_cache_hit_   │────▶│  usage.PromptTokensDetails       │
│  tokens              │     │    .CachedTokens                 │
│  （顶层非标准字段）     │     │  （applyUsagePostProcessing 归一） │
├──────────────────────┤     ├──────────────────────────────────┤
│  Gemini              │     │                                  │
│  usageMetadata.      │────▶│  usage.PromptTokensDetails       │
│  cachedContent-      │     │    .CachedTokens                 │
│  TokenCount          │     │  （buildUsageFromGeminiMetadata） │
├──────────────────────┤     ├──────────────────────────────────┤
│  ZhiPu               │     │                                  │
│  多个备选位置          │────▶│  usage.PromptTokensDetails       │
│                      │     │    .CachedTokens                 │
│                      │     │  （三级 fallback 提取）             │
├──────────────────────┤     ├──────────────────────────────────┤
│  Moonshot            │     │                                  │
│  choices[].usage.    │────▶│  usage.PromptTokensDetails       │
│  cached_tokens       │     │    .CachedTokens                 │
│  （非标准位置）         │     │  （extractMoonshot... 提取）      │
└──────────────────────┘     └──────────────────────────────────┘
```

计费层（`service/text_quota.go`）统一使用归一后的字段：
- `CachedTokens` × `CacheRatio` = 缓存读取计费
- `CachedCreationTokens` × `CacheCreationRatio` = 缓存写入计费（仅 Claude）

---

## 四、已知问题与待修复项

| 问题 | 状态 | 影响 |
|------|------|------|
| OAI→Claude 转换丢失 `cache_control`（`relay-claude.go:389`） | 待修复 | 通过 `/v1/chat/completions` 调用 Claude 的用户无法使用缓存 |
| 上游 issue [#4240](https://github.com/QuantumNous/new-api/issues/4240) | OPEN | 同上，上游也未修复 |
| Moonshot interleaved thinking 下 cached_tokens 异常 | 上游问题 | Moonshot 侧问题，网关无法修复 |

---

## 五、最佳实践建议

1. **Claude 用户**：确保客户端发送 `cache_control` 标注，或使用 Claude Code CLI（自动添加）。长 system prompt 尤其建议缓存——省 90% 输入费用。
2. **DeepSeek / OpenAI 用户**：无需额外操作，保持 prompt 前缀稳定即可自动受益。
3. **Gemini 用户**：大文档场景建议使用显式缓存 API 预创建，小场景可依赖隐式缓存。
4. **通用优化**：把不变的内容（system prompt、few-shot 示例、工具定义）放在 prompt 开头，把变化的内容（用户输入、对话最新轮次）放在末尾。

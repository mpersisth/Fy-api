# Cache Affinity Benchmark — Runbook

## 目标

验证 Fy-api 网关的 channel affinity（渠道亲和性）策略是否有效保证多轮对话的 prefix caching 生效。通过自动生成的多轮对话，对比不同亲和性配置下的 `cached_tokens / prompt_tokens` 比例曲线。

## Prerequisites

1. **Python 3.11+** 已安装，虚拟环境已激活
2. **Fy-api 网关** 已运行且可访问
3. **sk-... 测试 token** 有足够 quota（预估：30轮 × 3次 × 4组 ≈ 360 次请求）
4. **目标模型**（如 `deepseek-chat`）已配置至少 2 个渠道
5. 安装依赖：
```bash
cd scripts/channel-benchmark/py
pip install -e .
```

## 四组对比设计

| 组 | 含义 | 预期 cache ratio |
|----|------|-----------------|
| single_channel | 指定单渠道（baseline） | 高（provider 缓存生效） |
| affinity_header | 多渠道 + 按 X-Session-Id 路由 | 应接近 single_channel |
| affinity_token | 多渠道 + 按 user token 路由 | 应接近 single_channel |
| no_affinity | 多渠道 + 无亲和性 | 低（请求散到不同渠道） |

## Step 1: 配置网关 Affinity Rules

在 Fy-api 管理后台 → 运营设置 → 渠道亲和性，添加以下规则：

```json
{
  "enabled": true,
  "switch_on_success": true,
  "max_entries": 100000,
  "default_ttl_seconds": 3600,
  "rules": [
    {
      "name": "cache-bench-session",
      "model_regex": ["^deepseek-.*$"],
      "path_regex": ["/v1/chat/completions"],
      "key_sources": [
        { "type": "request_header", "key": "X-Session-Id" }
      ],
      "ttl_seconds": 3600,
      "include_using_group": true,
      "include_rule_name": true
    }
  ]
}
```

**组 C（affinity_token）的规则：**

如果要测试按 user token 路由，需要额外添加一条规则：

```json
{
  "name": "cache-bench-token",
  "model_regex": ["^deepseek-.*$"],
  "path_regex": ["/v1/chat/completions"],
  "key_sources": [
    { "type": "context_int", "key": "id" }
  ],
  "ttl_seconds": 3600,
  "include_using_group": true,
  "include_rule_name": true
}
```

> **注意：** `context_int` + `key: "id"` 取的是 Gin context 中的 user ID，即同一个 sk-token 对应的用户 ID。这意味着同一用户的所有请求都会路由到同一渠道。

## Step 2: 准备测试配置

```bash
cd scripts/channel-benchmark/py
cp cache-affinity.example.yaml cache-affinity.local.yaml
```

编辑 `cache-affinity.local.yaml`，填入实际值：

```yaml
base_url: "https://api-test.tracenex.cn"   # 你的网关地址
token: "sk-xxxxxxxx"                        # 测试用 token
model: "deepseek-chat"                      # 要测试的模型

conversation:
  seed_topic: "Go 并发模型的演进"            # 对话起始话题
  max_turns: 30                             # 最大轮次
  max_prompt_tokens: 60000                  # prompt token 上限（~80% context window）
  temperature: 0.7
  max_tokens: 2048
  stream: true

repetitions: 3                              # 每组跑 3 次取平均

groups:
  - name: "single_channel"
    pin_channel_id: 6                       # 改成你的实际渠道 ID

  - name: "affinity_header"
    headers:
      X-Session-Id: "auto"                  # 自动生成 UUID

  - name: "affinity_token"
    # 无额外配置

  - name: "no_affinity"
    # 无额外配置

export:
  formats: ["json", "markdown", "png"]
  output_dir: "results/cache-affinity"
```

## Step 3: 验证配置

```bash
python -m fy_cache_affinity run cache-affinity.local.yaml --dry-run
```

应输出配置摘要并正常退出，不发送任何请求。

## Step 4: 清空 Affinity 缓存

每次跑测试前，清空网关的 affinity 缓存，避免上一轮残留影响结果：

```bash
# 通过管理 API 清空
curl -X DELETE -H "Authorization: $ADMIN_TOKEN" \
  "$BASE_URL/api/channel-affinity/cache"
```

## Step 5: 逐组执行

建议按以下顺序执行，因为不同组对网关配置有不同要求：

```bash
# ── 组 A: 单渠道（不需要 affinity 规则）──
python -m fy_cache_affinity run cache-affinity.local.yaml --group single_channel

# ── 组 B: affinity by header ──
# 确保网关已添加 cache-bench-session 规则
python -m fy_cache_affinity run cache-affinity.local.yaml --group affinity_header

# ── 组 C: affinity by token ──
# 确保网关已添加 cache-bench-token 规则
python -m fy_cache_affinity run cache-affinity.local.yaml --group affinity_token

# ── 组 D: 无亲和性 ──
# 删除上面两条规则，或设置 enabled: false
python -m fy_cache_affinity run cache-affinity.local.yaml --group no_affinity
```

也可以一次性跑所有组（前提是网关配置已就绪且组 D 不需要关闭 affinity）：

```bash
python -m fy_cache_affinity run cache-affinity.local.yaml
```

## Step 6: 查看结果

```bash
ls results/cache-affinity/
# raw_2026-05-25_14-30-00.json       ← 原始数据（每轮每次的完整记录）
# comparison_2026-05-25_14-30-00.md  ← Markdown 对比表
# curve_2026-05-25_14-30-00.png      ← 曲线图
```

**曲线图解读：**
- X 轴：轮次（上方副轴标注累计 prompt tokens）
- Y 轴：cache hit ratio (0-100%)
- 4 条线 + 误差带（3 次的 min/max）
- 预期：single_channel 和 affinity_header/affinity_token 三条线重合且逐轮上升，no_affinity 明显低于其他三组

## 注意事项

1. **组间间隔** — 组与组之间建议等待 2-3 分钟，让 provider 端缓存自然过期，避免跨组污染
2. **渠道健康** — 跑测试前先用 Go smoke tool 确认所有渠道可用
3. **费用预估** — 30轮 × 3次 × 4组 = 360 次请求，每次消耗数千 tokens，总计约 200-500 万 tokens
4. **DeepSeek 缓存特性** — DeepSeek 的 prefix caching 是自动的，无需显式创建 cache 对象；缓存粒度为 64 tokens 对齐

## Troubleshooting

**Q: 组 D 的 cache ratio 不为 0？**

多渠道权重配置下可能恰好连续命中同一渠道。解决：增加渠道数量或降低单渠道权重。

**Q: 组 B/C 的 cache ratio 明显低于组 A？**

1. 检查 affinity rule 是否正确匹配 — 在 Fy-api 日志中搜索 `channel_affinity` 确认路由决策
2. 检查 rule 的 `model_regex` 和 `path_regex` 是否匹配你的模型和路径
3. 确认 affinity 缓存未过期（TTL 默认 3600s，30 轮对话通常在 10-20 分钟内完成）

**Q: 所有组的 cache ratio 都是 0？**

Provider 可能不支持 prefix caching，或者返回的 usage 中不包含 `prompt_tokens_details.cached_tokens`。检查：
```bash
# 手动发两次相同请求，看第二次是否有 cached_tokens
curl -X POST "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"hello"}],"max_tokens":10,"stream_options":{"include_usage":true},"stream":true}'
```

**Q: 对话中途报错中断？**

脚本会记录已完成的轮次数据。重新跑该组即可（每次生成新对话，不影响结果）。

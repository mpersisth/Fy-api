# Cache Affinity Benchmark 设计文档

## 目标

验证 Fy-api 网关在多渠道配置下，channel affinity（渠道亲和性）策略是否有效保证多轮对话的 prefix caching 生效。通过 `cached_tokens / prompt_tokens` 比例作为观测指标，对比不同亲和性配置下的缓存命中曲线。

## 测试方法

自动生成多轮对话：发送 seed 问题 → 模型回答 → 脚本让模型基于上文自动追问 → 循环，直到 context window 接近满或达到最大轮次。每轮记录 cache 指标。

## 对比组

| 组 | 配置 | 脚本行为 | 预期 |
|----|------|---------|------|
| A: single_channel | 指定 `pin_channel_id` | 用 client 的 channel pin | cache 100% 生效（baseline） |
| B: affinity_header | 网关配置 rule by `X-Session-Id` | 每个对话带固定 UUID header | 应接近 A |
| C: affinity_token | 网关配置 rule by user token | 同一 sk-token，无额外 header | 应接近 A |
| D: no_affinity | 网关无匹配规则或 affinity OFF | 正常请求，不带亲和标识 | cache 断裂 |

## 执行参数

- 单用户串行执行
- 每组 3 次独立对话（不同 seed），取平均
- 对话深度：跑到 prompt_tokens 达上限或最大轮次
- 同一个 seed 问题用于所有 4 组，确保对比公平

## 模块结构

```
scripts/channel-benchmark/py/
└── fy_cache_affinity/
    ├── __init__.py
    ├── __main__.py        # CLI 入口: python -m fy_cache_affinity run config.yaml
    ├── config.py          # YAML 配置解析 + 校验
    ├── conversation.py    # 多轮对话生成器（自动追问逻辑）
    ├── runner.py          # 串行执行 groups × repetitions
    ├── metrics.py         # 逐轮 cache ratio 计算 + 聚合
    └── report.py          # 对比表格 + 曲线图输出
```

复用 `fy_loadtest.client.ChatClient`（已支持 `cached_tokens` 解析）。需扩展 client 支持自定义 request headers。

## YAML 配置格式

```yaml
base_url: "http://localhost:3000"
token: "sk-test-xxx"
model: "deepseek-chat"

conversation:
  seed_topic: "Go 并发模型的演进"
  max_turns: 30
  max_prompt_tokens: 60000
  temperature: 0.7
  max_tokens: 2048
  stream: true

repetitions: 3

groups:
  - name: "single_channel"
    pin_channel_id: 6

  - name: "affinity_header"
    headers:
      X-Session-Id: "auto"  # "auto" = 每个对话自动生成 UUID

  - name: "affinity_token"
    # 无额外配置，依赖网关按 token 路由

  - name: "no_affinity"
    pin_channel_id: null
    headers: {}
    # 确保不匹配任何 affinity rule
```

## 多轮对话生成逻辑

1. **Turn 0（seed）：** 发送 `[{"role": "user", "content": seed_question}]`
2. **Turn N（追问）：** 在历史末尾追加一条 system 指令：`"基于以上对话内容，提出一个相关的深入问题，只输出问题本身"`，获取模型生成的问题
3. **Turn N+1：** 把生成的问题作为新的 user message 追加到历史，发送完整历史获取回答
4. **终止：** `prompt_tokens >= max_prompt_tokens` 或 `turn >= max_turns`

每轮记录：
- `turn_number`
- `cumulative_prompt_tokens`（本轮请求的 prompt_tokens）
- `cached_tokens`
- `cache_ratio = cached_tokens / prompt_tokens`
- `ttft_ms`
- `e2e_ms`

## 输出格式

### 1. JSON 原始数据

```json
{
  "config": { "model": "deepseek-chat", "max_turns": 30, ... },
  "groups": [
    {
      "name": "single_channel",
      "runs": [
        {
          "seed": "...",
          "turns": [
            { "turn": 1, "prompt_tokens": 52, "cached_tokens": 0, "cache_ratio": 0.0, "ttft_ms": 320, "e2e_ms": 1200 },
            { "turn": 2, "prompt_tokens": 180, "cached_tokens": 52, "cache_ratio": 0.289, ... }
          ]
        }
      ]
    }
  ]
}
```

### 2. Markdown 对比表

按轮次展示 4 组的平均 cache ratio，附累计 token 数。

### 3. 曲线图（PNG/PDF）

- X 轴：轮次（副轴标注累计 tokens）
- Y 轴：cache_ratio (0-100%)
- 4 条线（每组一条）+ 误差带（3 次的 min/max）
- 使用 matplotlib 生成

## 对网关的配置要求

测试前需确保 Fy-api 网关有如下配置：

**组 B（affinity_header）需要的 affinity rule：**

```json
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
```

**组 C（affinity_token）需要的 affinity rule：**

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

**组 D（no_affinity）：** 删除上述规则或设置 `enabled: false`。

脚本不会自动修改网关配置 — 需要手动或通过管理 API 切换。

## 依赖

- Python 3.11+
- httpx（已有，复用 fy_loadtest）
- matplotlib（曲线图）
- PyYAML（配置解析）
- 现有 `fy_loadtest.client.ChatClient`（需扩展支持自定义 headers）

## Runbook — 使用说明

### 前置条件

1. Python 3.11+ 已安装，项目虚拟环境已激活
2. Fy-api 网关已运行且可访问
3. 有一个可用的 `sk-...` 测试 token（有足够 quota）
4. 目标模型（如 `deepseek-chat`）已配置至少 2 个渠道

### 第一步：配置网关 affinity rules

在 Fy-api 管理后台或通过 option API，添加测试所需的 affinity rules：

```json
{
  "enabled": true,
  "rules": [
    {
      "name": "cache-bench-session",
      "model_regex": ["^deepseek-.*$"],
      "path_regex": ["/v1/chat/completions"],
      "key_sources": [{ "type": "request_header", "key": "X-Session-Id" }],
      "ttl_seconds": 3600,
      "include_using_group": true,
      "include_rule_name": true
    },
    {
      "name": "cache-bench-token",
      "model_regex": ["^deepseek-.*$"],
      "path_regex": ["/v1/chat/completions"],
      "key_sources": [{ "type": "context_int", "key": "id" }],
      "ttl_seconds": 3600,
      "include_using_group": true,
      "include_rule_name": true
    }
  ]
}
```

### 第二步：编写测试配置

```bash
cd scripts/channel-benchmark/py
cp cache-affinity.example.yaml cache-affinity.local.yaml
# 编辑 cache-affinity.local.yaml，填入实际的 base_url, token, model, pin_channel_id
```

### 第三步：逐组执行

由于不同组需要不同的网关 affinity 配置，建议按以下顺序执行：

```bash
# 1. 跑组 A（单渠道，不需要 affinity 配置）
python -m fy_cache_affinity run cache-affinity.local.yaml --group single_channel

# 2. 配置网关 affinity rules（添加 cache-bench-session + cache-bench-token）
#    然后跑组 B 和 C
python -m fy_cache_affinity run cache-affinity.local.yaml --group affinity_header
python -m fy_cache_affinity run cache-affinity.local.yaml --group affinity_token

# 3. 关闭 affinity 或删除规则，跑组 D
python -m fy_cache_affinity run cache-affinity.local.yaml --group no_affinity

# 4. 全部跑完后生成对比报告
python -m fy_cache_affinity report cache-affinity.local.yaml
```

也可以一次性跑所有组（前提是网关配置已就绪）：

```bash
python -m fy_cache_affinity run cache-affinity.local.yaml --all
```

### 第四步：查看结果

```bash
# 结果输出到 results/cache-affinity/ 目录
ls results/cache-affinity/
# ├── raw_2026-05-25_14-30-00.json      # 原始数据
# ├── comparison_2026-05-25_14-30-00.md  # 对比表格
# └── curve_2026-05-25_14-30-00.png     # 曲线图
```

### 注意事项

1. **测试前清空 affinity 缓存** — 避免上一轮测试的缓存影响结果：
   通过管理 API `DELETE /api/channel-affinity/cache` 清空
2. **确保渠道健康** — 跑测试前先用 Go smoke tool 确认所有渠道可用
3. **预估费用** — 30 轮 × 3 次 × 4 组 = 360 次请求，每次可能消耗数千 tokens
4. **时间间隔** — 组与组之间建议间隔 2-3 分钟，让 provider 端缓存自然过期，避免跨组污染

### 常见问题

**Q: 组 D 的 cache ratio 不为 0？**
A: 可能是多渠道权重配置下恰好连续命中了同一渠道。增加渠道数量或降低单渠道权重可以让效果更明显。

**Q: 组 B/C 的 cache ratio 明显低于组 A？**
A: 检查 affinity rule 是否正确匹配。可以在 Fy-api 日志中搜索 `channel_affinity` 关键字确认路由决策。

**Q: 对话中途报错中断？**
A: 脚本会记录已完成的轮次数据。可以用 `--resume` 从断点继续（如果实现了），或者重新跑该组。

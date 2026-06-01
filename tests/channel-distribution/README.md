# channel-distribution — 多渠道流量分发测试套件

验证 fy-api 的多渠道分发策略：优先级、权重、亲和性。

这不是压测工具，而是**功能验证/集成测试**，用于确认分发逻辑在各种配置下的正确性，并基于测试数据输出配置优化建议。

## 快速开始

```bash
cd tests/channel-distribution

# 安装依赖
pip install httpx pyyaml scipy

# 配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入 base_url、root_token、upstream_api_key

# 从项目根目录运行全部测试
python -m tests.channel-distribution --config tests/channel-distribution/config.yaml

# 或者从 tests/channel-distribution/ 目录运行
python -m run_tests --config config.yaml

# 运行单个测试套件
python -m run_tests --config config.yaml --suite weight
python -m run_tests --config config.yaml --suite priority
python -m run_tests --config config.yaml --suite affinity
python -m run_tests --config config.yaml --suite affinity-sticky
python -m run_tests --config config.yaml --suite affinity-failure
python -m run_tests --config config.yaml --suite affinity-hitrate
```

## 前置条件

- 一个 **root 权限的 admin token**（用于创建渠道、修改配置、查询日志）
- 一个有效的 **上游 API key**（渠道创建时使用，实际消耗 quota）
- test 环境可达

## 测试套件

| Suite | 测试内容 | 耗时估算 |
|-------|---------|---------|
| `weight` | 权重分布验证（卡方检验） | ~60s |
| `priority` | 优先级降级（禁用高优渠道后流量切换） | ~30s |
| `affinity-sticky` | 亲和性粘连路由 | ~30s |
| `affinity-ttl` | TTL 过期行为 | ~20s + TTL等待 |
| `affinity-failure` | SkipRetry / SwitchOnSuccess | ~60s |
| `affinity-hitrate` | 命中率分析（长会话/冷启动/回头用户） | ~90s |

## 脚本行为

1. **自动创建**测试渠道和令牌
2. **发送请求**并收集 `X-Oneapi-Request-Id`
3. **查询日志** API 获取每个请求命中的渠道
4. **统计分析**并输出结果
5. **自动清理**测试渠道和令牌
6. **输出建议**基于测试数据的配置优化建议

## 亲和性测试覆盖维度

- 规则匹配：ModelRegex / PathRegex / UserAgentInclude
- Key 提取：request_header / gjson body path
- 缓存 Key 作用域：IncludeRuleName / IncludeModelName / IncludeUsingGroup
- TTL：规则级 vs 默认 / 过期行为
- 故障处理：SkipRetryOnFailure / SwitchOnSuccess
- 命中率：长会话 / 冷启动 / 回头用户模式

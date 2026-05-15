# 渠道 Benchmark 执行手册

## 一、目标

对比 N 个上游渠道在 **延迟、吞吐、质量、协议合规** 四个维度的表现，输出一份 PDF 报告供决策。

## 二、前置条件

| 项目 | 要求 |
|------|------|
| Python | 3.11.x（`PYENV_VERSION=3.11.9`），3.14 有兼容问题 |
| 依赖 | `pip install -e .`（fy-loadtest/fy-quality/fy-conformance/fy-canary） |
| PDF 依赖 | `pip install reportlab matplotlib numpy` |
| 网关 token | 需要 **admin token**（`sk-...`），用于 `X-Fy-Channel` pin 渠道 |
| 网络 | 本机能访问网关（如 `https://www.tracenex.cn`） |
| fab 日志 | 需要 SSH key 到目标服务器（可选，缺失则跳过） |

## 三、执行流程

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ 1. 配置文件  │ ──▶ │ 2. 跑测试    │ ──▶ │ 3. 生成报告  │ ──▶ │ 4. 检查输出  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

### Step 1: 准备配置

编辑 `loadtest.local.yaml` / `quality.local.yaml` / `conformance.local.yaml`：

- **channels**: 要对比的渠道 ID + 名称
- **models**: 要测试的模型列表（必须在渠道中已配置）
- **concurrency_levels**: loadtest 并发级别，建议 `[1, 5, 10, 20, 50, 100, 200, 500, 1000, 2000]`

### Step 2: 执行测试（按顺序）

```bash
cd scripts/channel-benchmark/py
export PYENV_VERSION=3.11.9

# 2a. Go smoke test（快，2分钟）
cd ../go && go run . -config benchmark.local.yaml && cd ../py

# 2b. Loadtest（慢，取决于并发级别数 × 模型数）
fy-loadtest -c loadtest.local.yaml

# 2c. Quality（中等，取决于题目数 × judge 数）
fy-quality -c quality.local.yaml

# 2d. Conformance（中等，224 用例 × 模型数 × 渠道数）
# 多渠道需要用 matrix 脚本或逐个跑
bash /tmp/run_conformance_matrix.sh

# 2e. Fab 日志（可选）
conda run -n fy-api-deploy fab logs --target=cn --tail=300 > /tmp/cn-logs.txt
```

### Step 3: 生成报告

```bash
python generate_combined_report.py
# 输出到 reports/combined-report-YYYYMMDD-HHMMSS.pdf
```

### Step 4: 检查

打开 PDF 确认：
- 封面信息正确
- TL;DR 结论页红色高亮可读
- 各表格无溢出/乱码
- 图表渲染正常

## 四、重点关注

### 4.1 Loadtest 并发度选择

- **目标是找到拐点**：成功率下降 / 延迟陡增 / RPS 不再增长
- 从低到高逐步加：`[1, 5, 10, 20, 50, 100, 200, 500, 1000, 2000]`
- 如果 2000 还没拐点，继续加 `[5000, 10000]`
- 每级至少 20 请求，timeout 设 300s（大模型响应慢）

### 4.2 文件名碰撞问题

`fy-loadtest` 多模型同时跑会产生同秒时间戳文件名碰撞（后写覆盖前写）。

**解决方案**：逐模型单独跑，中间 `sleep 2`，或跑完后手动合并 JSON。

### 4.3 Quality 跳过项

- `no embedding client configured` = 未配置 embedding 的评分器（translation/paraphrase）
- 这些不算失败，报告中标记为「跳过」
- 如需启用：配置 embedding model 到 quality.local.yaml

### 4.4 Conformance 多渠道

`fy-conformance` 只支持单 `pin_channel_id`，多渠道需要：
- 用 matrix shell 脚本逐个跑（见 `/tmp/run_conformance_matrix.sh` 模板）
- 或为每个 (channel, model) 组合生成临时 yaml

### 4.5 CJK 字体

PDF 中文渲染依赖系统字体：
- macOS: `/System/Library/Fonts/PingFang.ttc` 或 `STHeiti Medium.ttc`
- Linux: 需安装 `wqy-microhei` 或 `noto-cjk`
- 如果出现 ■ 方块：检查 `_CJK_PATHS` 列表是否覆盖当前系统

### 4.6 报告数据路径

`generate_combined_report.py` 顶部的 `LT_FILES` / `QA_JSON` / `CF_FILES` / `GO_JSON` 是硬编码路径。
每次跑完新测试后需要**手动更新这些路径**指向最新结果文件。

## 五、注意事项

1. **真实计费**：所有测试消耗真实 quota，高并发 × 多模型 = 大量 token 消耗
2. **不要在高峰期跑**：loadtest 高并发会占用渠道配额，影响其他用户
3. **Admin token 安全**：yaml 中的 token 已在 .gitignore，不要提交
4. **结果文件已 gitignore**：`loadtest-results/`、`quality-results/`、`conformance-results/`、`reports/` 均不入库
5. **合并数据**：如果分多次跑（如先跑低并发再跑高并发），用 Python 脚本合并 JSON 的 `channels[].levels[]`

## 六、输出目录结构

```
scripts/channel-benchmark/py/
├── reports/                    # PDF 报告输出（gitignored）
│   └── combined-report-*.pdf
├── loadtest-results/           # loadtest JSON/CSV/MD（gitignored）
├── quality-results/            # quality JSON（gitignored）
├── conformance-results/        # conformance JSON（gitignored）
├── generate_combined_report.py # 报告生成器
├── RUNBOOK.md                  # 本文件
└── *.local.yaml                # 本地配置（gitignored）
```

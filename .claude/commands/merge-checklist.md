# Merge Checklist — 分支合并前置检查

当开发新功能分支或准备合并 PR 时，执行以下检查清单。对每一项给出 ✅/❌/⚠️ 判定和简要说明。

## 使用场景

- 新建功能分支时：检查 1-2 项
- PR 合并前：全部检查

## 检查清单

### 1. 分支策略合规

- [ ] 分支命名是否符合规范（`feature/xxx` 或 `bugfix/xxx`）
- [ ] PR 目标分支是否正确（feature/bugfix → `develop`；仅 hotfix → `main`）
- [ ] 如果目标分支错误，提示修改命令：`gh pr edit <number> --base develop`

### 2. 代码 Review

- [ ] 是否有至少一次 code review（人工或 AI）
- [ ] 关键逻辑是否有单元测试覆盖
- [ ] 是否有明显的安全问题（注入、泄露、硬编码密钥）

### 3. 上游冲突风险评估

- [ ] 修改的文件是否在 upstream/main 近期有活跃提交（`git log upstream/main -- <files>`）
- [ ] 是否修改了上游活跃区域的文件（参考 OVERLAY.md 冲突风险标注）
- [ ] 如果是新文件，冲突风险低；如果修改已有上游文件，评估冲突概率
- [ ] OVERLAY.md 是否已更新（新增/修改 overlay 条目）
- [ ] 是否有 `// Fy-api overlay:` 或 `{/* Fy-api overlay: */}` 注释标记

### 4. 上游 Issue 调研

- [ ] 在 upstream repo 搜索相关 issue：`gh search issues "<关键词>" --repo QuantumNous/new-api`
- [ ] 是否有上游已实现或计划实现的类似功能（如果有，我们的 overlay 可能被取代）
- [ ] 如果上游有相关 issue，记录编号并评估影响

### 5. 业界最佳实践对照

- [ ] 实现方案是否符合该领域的通用最佳实践
- [ ] 是否有更简单/更标准的替代方案
- [ ] 数据存储/传输是否遵循"单一真相源"原则
- [ ] 是否引入了不必要的复杂度

### 6. 数据库兼容性（如涉及后端）

- [ ] 是否同时兼容 SQLite / MySQL / PostgreSQL
- [ ] 是否使用了 GORM 抽象而非原生 SQL
- [ ] 迁移是否可逆

### 7. 国际化（如涉及前端）

- [ ] 新增的用户可见文本是否使用了 `t('中文key')` 包裹
- [ ] 是否避免了在 i18n key 中使用品牌词

## 输出格式

```
## PR #<number> 合并检查报告

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 分支策略 | ✅/❌ | ... |
| 2 | 代码 Review | ✅/❌ | ... |
| 3 | 上游冲突风险 | ✅/⚠️/❌ | ... |
| 4 | 上游 Issue | ✅/⚠️ | ... |
| 5 | 最佳实践 | ✅/⚠️ | ... |
| 6 | DB 兼容性 | ✅/N/A | ... |
| 7 | 国际化 | ✅/N/A | ... |

**结论**：可合并 / 需修改后合并 / 阻塞
**阻塞项**（如有）：...
**建议**（如有）：...
```

## 执行步骤

1. 获取 PR 信息：`gh pr view <number> --json title,body,state,headRefName,baseRefName,files`
2. 获取 diff：`gh pr diff <number>`
3. 检查上游活跃度：`git log upstream/main -- <modified_files>` (如果 upstream 可达)
4. 搜索上游 issue：`gh search issues "<feature关键词>" --repo QuantumNous/new-api`
5. 检查 OVERLAY.md 是否更新
6. 逐项评估并输出报告

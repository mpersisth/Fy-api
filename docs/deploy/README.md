# Fy-api 部署文档

本目录收录 Fy-api 在各环境下的部署 runbook，读者为**内部工程师**（运维、后端、SRE）。所有文档遵循"每一步都可复制粘贴"的原则。

## 环境一览

| 环境 | 容器运行时 | 数据库 | 缓存 | 对外 | 镜像源 | 文档 |
|------|:---:|------|------|------|:---:|------|
| **本地开发** | 任意 | SQLite | 无 | localhost | 本地 build | [`local-dev.md`](./local-dev.md) |
| **测试环境**（staging） | Podman + compose | 阿里云 RDS（与生产同构） | — | SSH port-forward / 内网 | 本地 build，`podman save` 传输 | [`test-podman.md`](./test-podman.md) |
| **正式环境 · 单机**（起步推荐） | Podman + 蓝绿 | 阿里云 RDS | 阿里云 Redis | Nginx + Let's Encrypt | ACR 拉内网镜像 | [`prod-podman-single.md`](./prod-podman-single.md) |
| **正式环境 · K8s**（规模化） | ACK (Deployment + HPA) | 阿里云 RDS | 阿里云 Redis | Nginx Ingress + cert-manager | ACR（运维手动 push） | [`prod-ack.md`](./prod-ack.md) |

## 运营专题

| 专题 | 场景 | 文档 |
|------|------|------|
| **限流开关与按客户限流** | 打开/关闭各套限流，按用户分组配独立配额，区分哪些能热更新、哪些必须重启 | [`rate-limiting.md`](./rate-limiting.md) |
| **日志落盘 + SLS + Prometheus** | 日志不落盘的根因与修复，SLS 接入，Prometheus 三层监控栈与告警规则 | [`observability.md`](./observability.md) + [`monitoring/`](./monitoring/) |

## 读前提醒

1. **SystemName = Fy-api**，但 Go module path 保持 `github.com/QuantumNous/new-api`。所有配置文件里遇到 `new-api` 字样都是历史遗留，**不要改**。
2. 数据库 schema 差异对 Fy-api 相对上游仅有 overlay 增量字段。所有环境统一用 GORM `AutoMigrate`，首次启动自动建表。
3. 如果从 TraceNex 或老版 new-api 升级部署，**先读 [`../Phase3-DB-migration-runbook.md`](../Phase3-DB-migration-runbook.md)** 做迁移演练。
4. 发现部署问题在本目录提 issue / 更新文档。留下时间戳便于追溯。

## 版本约定

- 镜像 tag 规则：`v<VERSION>`（如 `v0.9.3`）+ `latest` + `sha-<git-short>`。
- CI 拉取策略：`IfNotPresent`；需要强制拉新时用 `rollout restart`，不要滥用 `:latest`+`Always`。
- 数据迁移：不走独立脚本，靠 GORM `AutoMigrate`，但**每次发版前阅读 `git diff model/` 确认新增字段**。

## 紧急联系

- 值班：见 `OVERLAY.md` 顶部（如未填写，在 git log 里找主要 committer）
- 上游 issue tracker：<https://github.com/QuantumNous/new-api/issues>

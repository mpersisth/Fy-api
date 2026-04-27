# 本地开发环境

> 读者：后端 / 前端 / 全栈工程师
> 目标：最快把 TraceNex 跑在自己电脑上做开发

有两种模式，按场景选：

| 场景 | 推荐方式 |
|------|---------|
| 调代码、改 Go / React | **源码模式**（§1） |
| 只想看看能不能跑 / 对齐测试环境 | **容器模式**（§2） |

---

## 一、源码模式（hot reload）

### 1.1 依赖

| 工具 | 版本 | 检测命令 |
|------|------|---------|
| Go | 1.25.1 或更高 | `go version` |
| Bun | latest | `bun --version` |
| SQLite | 系统自带 | — |

### 1.2 启动

```bash
cd ~/Projects/apiGateway/TraceNex

# 后端
go mod tidy
go run main.go
# 默认 :3000，无 SQL_DSN 时自动用 SQLite（本地文件 ./data/fy-api.db）
```

前端 dev server 单独开一个终端：
```bash
cd web
bun install
bun run dev
# vite dev server 默认 :5173，已配置代理把 /api 转发到 :3000
```

开发时用 `http://localhost:5173`（前端 hot reload），生产打包后用 `:3000`（Go 服务 embed 静态资源）。

### 1.3 常用命令（详见 `CLAUDE.md`）

```bash
# 后端测试
go test ./...                  # 全部
go test ./... -race            # 带 race 检测
go test -cover ./service/...   # 带覆盖率

# 前端 lint
cd web
bun run lint      # prettier
bun run eslint
bun run i18n:lint

# 全栈一次性 build
make all
```

---

## 二、容器模式（贴近生产）

如果你本地装了 podman / Docker Desktop / OrbStack：

```bash
cd ~/Projects/apiGateway/TraceNex

# 构建镜像（首次 5-15 分钟）
podman build -t fy-api:local .
# 或 docker build -t fy-api:local .

# 最小 compose（SQLite，无外部依赖）
cat > compose.local.yml <<'EOF'
services:
  fy-api:
    image: fy-api:local
    container_name: fy-api
    ports: ["3000:3000"]
    volumes:
      - ./data:/data
      - ./logs:/app/logs
    environment:
      - TZ=Asia/Shanghai
EOF

mkdir -p data logs
podman-compose -f compose.local.yml up -d
# 或 docker-compose -f compose.local.yml up -d
```

完整的本地 podman 部署、回归测试、排障见上级目录的 `docs/本地Podman部署测试报告-20260424.md` 和 [`test-podman.md`](./test-podman.md)。

---

## 三、默认账号

首次启动时数据库为空，**必须通过 `/api/setup` 建 root**：

```bash
curl -X POST http://localhost:3000/api/setup \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"test123456","confirmPassword":"test123456","SelfUseModeEnabled":false,"DemoSiteEnabled":false}'
```

之后 `http://localhost:3000` 用 `admin` / `test123456` 登录。

---

## 四、常见小坑

| 现象 | 原因 | 对策 |
|------|------|------|
| `go: module github.com/QuantumNous/new-api: found...` | Go module path 是故意保留的 | **不要改**，见 `CLAUDE.md` |
| 前端改了但页面不更新 | Vite HMR 被浏览器缓存 | Cmd+Shift+R 或无痕模式 |
| SQLite `database is locked` | 同时跑了两个后端 | 检查 `lsof -i :3000` |
| 注册 / 登录页样式错乱 | 未 `bun run build` 就直接访问 :3000 | 开 dev server 用 :5173，或先 build |

---

## 五、环境与文档

| 场景 | 文档 |
|------|------|
| 调代码 | 你现在看的这个文件 |
| 部署测试 | [`test-podman.md`](./test-podman.md) |
| 部署生产 | [`prod-ack.md`](./prod-ack.md) |
| 数据迁移 | [`../Phase3-DB-migration-runbook.md`](../Phase3-DB-migration-runbook.md) |
| 架构与规则 | `../../CLAUDE.md` |
| Overlay 定制清单 | `../../OVERLAY.md` |

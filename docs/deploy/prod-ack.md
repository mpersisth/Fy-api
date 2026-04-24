# 正式环境部署 Runbook（阿里云 ACK）

> 读者：运维 / SRE
> 目标：Fy-api 跑在阿里云 ACK 集群上，Deployment + HPA + Nginx Ingress + cert-manager 完整生产栈
> 依赖：阿里云 RDS + Redis + OSS + ACR + ACK + 备案域名

---

## 一、前置准备与架构

### 1.1 整体拓扑

```
                       ┌──── DNS (阿里云 / Cloudflare) ────┐
                       │   api.<your-domain>.com          │
                       └──────────────┬──────────────────┘
                                      │ A / CNAME
                                      ▼
  ┌────────────────── 阿里云 ACK ─────────────────────┐
  │                                                  │
  │   Nginx Ingress Controller                       │
  │        │                                         │
  │        │   TLS 终止（cert-manager 管证书）       │
  │        ▼                                         │
  │   Service: fy-api (ClusterIP :3000)              │
  │        │                                         │
  │        ▼                                         │
  │   Deployment: fy-api (2-10 replicas, HPA)        │
  │    │    │    │                                   │
  │    │    │    └─────┐                             │
  │    ▼    ▼          ▼                             │
  │  pod  pod         pod                            │
  │                                                  │
  └──────────────────┬────────────────┬──────────────┘
                     │                │
        ┌────────────┘                └─────────┐
        ▼                                       ▼
  Aliyun RDS                              Aliyun Redis
  (MySQL 8 / PG 15)                       (集群版，多 AZ)
                                               
        ▲
        │
  Aliyun OSS（日志归档、可选）
  Aliyun ACR（镜像仓库）
```

### 1.2 资源清单（申请 + 命名约定）

| 资源 | 规格建议 | 命名示例 | 谁负责 |
|------|---------|---------|:---:|
| ACK 集群 | 3 节点 Master，N 节点 Worker（8C16G 起） | `fy-api-prod-cluster` | 运维 |
| RDS MySQL 或 PostgreSQL | 双节点高可用，8C16G，100GB SSD | `rm-xxxxxxxx-prod` | DBA |
| Redis 集群 | 标准双副本，2GB 起步 | `r-xxxxxxxx-prod` | DBA |
| ACR 企业版 | 1 个命名空间 `fy-api` | `registry.cn-hangzhou.aliyuncs.com/fy-api` | 运维 |
| OSS 存储桶 | 标准类型 | `fy-api-prod-logs`、`fy-api-prod-uploads` | 运维 |
| ACK LoadBalancer | 通过 Nginx Ingress 创建，1 Mbps 起 | — | 自动 |
| 备案域名 | — | `api.<your-domain>.com` | 法务/业务 |

### 1.3 命名空间与工具链

```bash
# kubectl 上下文
kubectl config get-contexts
kubectl config use-context fy-api-prod

# 创建命名空间
kubectl create namespace fy-api

# 验证连接
kubectl get nodes
kubectl get ns fy-api
```

必备本地工具：`kubectl`、`helm`、`podman` 或 `docker`、`jq`、`yq`、（可选）`kustomize`。

---

## 二、ACK 集群基础设施（一次性）

### 2.1 安装 Nginx Ingress Controller

ACK 通常已预装。验证：
```bash
kubectl -n kube-system get pods | grep ingress-nginx
kubectl -n kube-system get svc nginx-ingress-lb  # type=LoadBalancer，EXTERNAL-IP 必须是阿里云 SLB IP
```

若未安装：
```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  -n kube-system \
  --set controller.service.annotations."service\.beta\.kubernetes\.io/alibaba-cloud-loadbalancer-spec"=slb.s1.small \
  --set controller.service.type=LoadBalancer
```

### 2.2 安装 cert-manager

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.2/cert-manager.yaml
kubectl -n cert-manager get pods  # 等全部 Running
```

创建 Let's Encrypt ClusterIssuer（用 HTTP-01 校验；DNS-01 更复杂，按需扩展）：

```yaml
# infra/cert-issuer.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: sre@<your-domain>.com          # 证书过期前收提醒的邮箱
    privateKeySecretRef:
      name: letsencrypt-prod-account-key
    solvers:
      - http01:
          ingress:
            class: nginx
```

```bash
kubectl apply -f infra/cert-issuer.yaml
kubectl get clusterissuer letsencrypt-prod
```

### 2.3 DNS 解析

在 DNS 控制台为 `api.<your-domain>.com` 添加 A 记录，指向 Nginx Ingress SLB 的 EXTERNAL-IP（`kubectl -n kube-system get svc nginx-ingress-lb -o wide`）。

生效后验证：
```bash
dig +short api.<your-domain>.com
# 返回的 IP 应是 SLB EXTERNAL-IP
```

---

## 三、RDS 准备（DBA 执行）

### 3.1 建库建账号

```sql
-- MySQL
CREATE DATABASE fy_api DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'fy_api_app'@'%' IDENTIFIED BY '<LONG_RANDOM_PASSWORD>';
GRANT ALTER, CREATE, CREATE VIEW, DELETE, DROP, INDEX, INSERT,
      REFERENCES, SELECT, SHOW VIEW, TRIGGER, UPDATE
      ON fy_api.* TO 'fy_api_app'@'%';
FLUSH PRIVILEGES;

-- PostgreSQL
CREATE DATABASE fy_api;
CREATE USER fy_api_app WITH PASSWORD '<LONG_RANDOM_PASSWORD>';
GRANT ALL PRIVILEGES ON DATABASE fy_api TO fy_api_app;
\c fy_api
GRANT ALL ON SCHEMA public TO fy_api_app;
```

### 3.2 白名单

RDS 控制台 → 数据安全性 → 添加 ACK Worker 节点的**交换机 CIDR**，**或** NAT 网关的出口 IP。保持最小集合。

### 3.3 连接验证（从 ACK 内）

```bash
kubectl -n fy-api run --rm -it db-check --image=mysql:8 --restart=Never -- \
  mysql -hrm-xxxxxxx.mysql.rds.aliyuncs.com -P3306 -u fy_api_app -p fy_api -e "SELECT VERSION();"
```

---

## 四、Redis 准备

阿里云 Redis → 开通 → 设置白名单（同上）→ 创建账号 + 密码 → 记录内网地址。

验证：
```bash
kubectl -n fy-api run --rm -it redis-check --image=redis:7-alpine --restart=Never -- \
  redis-cli -h r-xxxxxxx.redis.rds.aliyuncs.com -a '<REDIS_PASS>' PING
# 期望：PONG
```

---

## 五、ACR 镜像仓库

### 5.1 一次性：在 ACK 中配置 ACR pull 凭证

运维登录 ACR 控制台 → 创建用户（或使用 RAM 子账号）→ 记录 `user / password`。

```bash
# 创建 Docker Registry Secret
kubectl -n fy-api create secret docker-registry acr-credentials \
  --docker-server=registry.cn-hangzhou.aliyuncs.com \
  --docker-username=<ACR_USER> \
  --docker-password=<ACR_PASSWORD> \
  --docker-email=sre@<your-domain>.com

# 默认 ServiceAccount 自动拉取凭证（省去每个 pod 声明）
kubectl -n fy-api patch serviceaccount default \
  -p '{"imagePullSecrets":[{"name":"acr-credentials"}]}'
```

### 5.2 本地构建 + push 到 ACR（运维每次发版）

```bash
# 登录 ACR
podman login registry.cn-hangzhou.aliyuncs.com
# 用户名：阿里云账号 / ACR 子账号
# 密码：ACR 凭证密码

# 构建（注意：ACK 节点多为 linux/amd64）
cd ~/Fy-api
VERSION=$(cat VERSION)                           # 如 v0.9.3
GIT_SHA=$(git rev-parse --short HEAD)           # 如 7ffcc5c4
IMAGE_BASE=registry.cn-hangzhou.aliyuncs.com/fy-api/fy-api

podman build --platform linux/amd64 \
  -t $IMAGE_BASE:$VERSION \
  -t $IMAGE_BASE:sha-$GIT_SHA \
  -t $IMAGE_BASE:latest \
  .

# Push 三个 tag
podman push $IMAGE_BASE:$VERSION
podman push $IMAGE_BASE:sha-$GIT_SHA
podman push $IMAGE_BASE:latest

# 记录本次发版
cat >> ~/Fy-api/deploy-log.md <<EOF
## $VERSION (sha-$GIT_SHA) - $(date -u +"%Y-%m-%dT%H:%M:%SZ")
- build by: $(whoami)@$(hostname)
- pushed: $IMAGE_BASE:$VERSION
EOF
```

**严禁直接在生产用 `:latest`，必须用 `:v<VERSION>` 或 `:sha-<git-short>` 保证可回滚。**

---

## 六、Kubernetes Manifests

在 `~/Fy-api/deploy/k8s/` 下放下列文件（这些目录已进 `.gitignore`，写个人版本；推荐每个团队维护自己的 GitOps 仓库）：

### 6.1 命名空间与 Secret

```yaml
# deploy/k8s/00-namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: fy-api
  labels:
    environment: production
```

**创建 Secret**（不入库，用 sealed-secrets / external-secrets 管理敏感数据；这里给最朴素版本）：

```bash
kubectl -n fy-api create secret generic fy-api-secrets \
  --from-literal=SQL_DSN='fy_api_app:<PASS>@tcp(rm-xxxxxxx.mysql.rds.aliyuncs.com:3306)/fy_api?charset=utf8mb4&parseTime=True&loc=Local&tls=skip-verify' \
  --from-literal=REDIS_CONN_STRING='redis://:<REDIS_PASS>@r-xxxxxxx.redis.rds.aliyuncs.com:6379' \
  --from-literal=SESSION_SECRET="$(openssl rand -hex 32)" \
  --from-literal=CRYPTO_SECRET="$(openssl rand -hex 32)"
```

**密钥一次定下不要改**（改了所有用户 session / 加密数据作废）。

### 6.2 ConfigMap（非敏感配置）

```yaml
# deploy/k8s/10-configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: fy-api-config
  namespace: fy-api
data:
  TZ: "Asia/Shanghai"
  ERROR_LOG_ENABLED: "true"
  GLOBAL_WEB_RATE_LIMIT_NUM: "240"
  GLOBAL_WEB_RATE_LIMIT_DURATION: "60"
  FRONTEND_BASE_URL: "https://api.<your-domain>.com"
  # Fy-api 自定义，可调
  # MaxLogExportItems 是 Go 常量（50000），不走 env
```

### 6.3 Deployment + Service + HPA

```yaml
# deploy/k8s/20-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fy-api
  namespace: fy-api
  labels:
    app: fy-api
spec:
  replicas: 2                                    # 初始 2 个副本，HPA 会调整
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0                          # 发版期间保持至少 replicas 个可用
  selector:
    matchLabels:
      app: fy-api
  template:
    metadata:
      labels:
        app: fy-api
      annotations:
        # 让 Prometheus 能抓（如果装了）
        prometheus.io/scrape: "true"
        prometheus.io/port: "3000"
    spec:
      # 打散到不同节点，单节点宕机不全灭
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                topologyKey: kubernetes.io/hostname
                labelSelector:
                  matchLabels:
                    app: fy-api
      containers:
        - name: fy-api
          image: registry.cn-hangzhou.aliyuncs.com/fy-api/fy-api:v0.9.3  # ← 发版时改这里，切勿 latest
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 3000
          envFrom:
            - configMapRef:
                name: fy-api-config
            - secretRef:
                name: fy-api-secrets
          env:
            # 多副本必填：让每个 pod 自己报身份
            - name: NODE_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
          # 持久化挂载：OSS-FS 或 NAS；首次部署可先用 emptyDir，后期切 NAS
          volumeMounts:
            - name: data
              mountPath: /data
            - name: logs
              mountPath: /app/logs
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
          readinessProbe:
            httpGet:
              path: /api/status
              port: http
            initialDelaySeconds: 15
            periodSeconds: 10
            timeoutSeconds: 3
            successThreshold: 1
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /api/status
              port: http
            initialDelaySeconds: 60
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 5
          lifecycle:
            preStop:
              exec:
                # 滚动升级时先让 Ingress 摘除流量
                command: ["/bin/sh", "-c", "sleep 10"]
      volumes:
        - name: data
          emptyDir: {}                 # TODO: 切 NAS PVC
        - name: logs
          emptyDir: {}                 # TODO: 切 NAS PVC 或 sidecar 发 SLS
      terminationGracePeriodSeconds: 45
---
apiVersion: v1
kind: Service
metadata:
  name: fy-api
  namespace: fy-api
  labels:
    app: fy-api
spec:
  type: ClusterIP
  selector:
    app: fy-api
  ports:
    - name: http
      port: 3000
      targetPort: http
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: fy-api
  namespace: fy-api
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: fy-api
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 75
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300      # 避免抖动
      policies:
        - type: Percent
          value: 50
          periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 2
          periodSeconds: 30
```

### 6.4 Ingress + Certificate

```yaml
# deploy/k8s/30-ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: fy-api
  namespace: fy-api
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "10m"   # 允许较大的 prompt / 上传
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"  # 流式响应可能长
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
    # 开启 SSE 流式支持
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - api.<your-domain>.com
      secretName: fy-api-tls             # cert-manager 自动签发
  rules:
    - host: api.<your-domain>.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: fy-api
                port:
                  number: 3000
```

### 6.5 PodDisruptionBudget（可用性保障）

```yaml
# deploy/k8s/40-pdb.yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: fy-api
  namespace: fy-api
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: fy-api
```

### 6.6 NetworkPolicy（可选，收紧出入口）

```yaml
# deploy/k8s/50-networkpolicy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: fy-api-allow
  namespace: fy-api
spec:
  podSelector:
    matchLabels:
      app: fy-api
  policyTypes: [Ingress, Egress]
  ingress:
    # 允许 ingress-nginx 访问
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - { protocol: TCP, port: 3000 }
  egress:
    # DNS
    - to:
        - namespaceSelector: {}
      ports:
        - { protocol: UDP, port: 53 }
    # RDS / Redis / HTTPS 出站
    - {}
```

---

## 七、首次部署

```bash
cd ~/Fy-api/deploy/k8s

# 按顺序 apply
kubectl apply -f 00-namespace.yaml
# Secret 用命令创建过，不进文件
kubectl apply -f 10-configmap.yaml
kubectl apply -f 20-deployment.yaml
kubectl apply -f 30-ingress.yaml
kubectl apply -f 40-pdb.yaml
# NetworkPolicy 按需
# kubectl apply -f 50-networkpolicy.yaml

# 监控启动
kubectl -n fy-api rollout status deploy/fy-api --timeout=300s
kubectl -n fy-api get pods -w
kubectl -n fy-api logs -f deploy/fy-api
```

### 7.1 证书就绪

```bash
# 签证书可能要 30-60 秒
kubectl -n fy-api get certificate
# 期望：fy-api-tls  True  fy-api-tls  <age>

kubectl -n fy-api describe certificate fy-api-tls
# 如果一直 Ready=False，看 events；多半是 DNS 未解析或防火墙挡 HTTP-01
```

### 7.2 首次初始化 root 管理员

```bash
read -s ROOT_PASS
curl -s -X POST https://api.<your-domain>.com/api/setup \
  -H "Content-Type: application/json" \
  -d "$(cat <<EOF
{
  "username": "fyadmin",
  "password": "$ROOT_PASS",
  "confirmPassword": "$ROOT_PASS",
  "SelfUseModeEnabled": false,
  "DemoSiteEnabled": false
}
EOF
)"
unset ROOT_PASS
```

### 7.3 冒烟

```bash
H=https://api.<your-domain>.com

curl -sf $H/api/status | grep -q '"success":true' && echo "✅ status"
curl -sI $H/api/status | grep -q "X-Oneapi-Request-Id" && echo "✅ request-id"
curl -s $H/ | grep -q "<title>Fy-api</title>" && echo "✅ brand"
curl -sI $H/ | grep -qi "strict-transport-security" && echo "✅ HSTS"
```

---

## 八、发版流程

**前置：代码已合并 main 并打 tag `v0.9.x`**

```bash
# 1. 本地 build + push（§5.2）
export VERSION=v0.9.4
cd ~/Fy-api
git fetch --tags
git checkout $VERSION

IMAGE=registry.cn-hangzhou.aliyuncs.com/fy-api/fy-api:$VERSION
podman build --platform linux/amd64 -t $IMAGE .
podman push $IMAGE

# 2. 修改 Deployment 的 image tag
kubectl -n fy-api set image deploy/fy-api fy-api=$IMAGE --record
# 或者：编辑 20-deployment.yaml 改 image，再 apply
# kubectl apply -f 20-deployment.yaml

# 3. 等待滚动完成
kubectl -n fy-api rollout status deploy/fy-api --timeout=300s

# 4. 冒烟（§7.3）

# 5. 记录发版 log
echo "$VERSION deployed at $(date -u)" >> ~/Fy-api/deploy-log.md
```

### 8.1 灰度（按 Pod 比例）

发版前可以在非高峰先上一部分副本对比，用 `kubectl patch` 手动拆分：

```bash
# 把 2 副本拆成 1 个旧 + 1 个新
# 简化做法：直接 set image，让滚动升级自己灰度（maxSurge:1 maxUnavailable:0 保障）
```

更精细的灰度需 Argo Rollouts（本 runbook 不覆盖）。

### 8.2 回滚

```bash
kubectl -n fy-api rollout undo deploy/fy-api
kubectl -n fy-api rollout status deploy/fy-api

# 回到指定 revision
kubectl -n fy-api rollout history deploy/fy-api
kubectl -n fy-api rollout undo deploy/fy-api --to-revision=3
```

**注意**：GORM `AutoMigrate` 不支持 DDL 回滚。如果新版本加了字段，老版本 pod 启动时不会删字段但能正常运行（schema 是超集）。破坏性变更前打快照。

---

## 九、日常运维

### 9.1 查看状态

```bash
kubectl -n fy-api get all
kubectl -n fy-api top pods
kubectl -n fy-api get hpa fy-api
kubectl -n fy-api describe deploy fy-api
```

### 9.2 日志

```bash
# 实时
kubectl -n fy-api logs -f deploy/fy-api

# 某个 pod
kubectl -n fy-api logs -f fy-api-xxxxx-yyyyy

# 聚合 errors
kubectl -n fy-api logs deploy/fy-api --tail=1000 | grep -i "error\|panic\|fatal"
```

长期建议：sidecar + SLS / Loki，按 `request_id` 链路查询。

### 9.3 数据库日常

```bash
# 连 RDS 执行 DML 务必走 DBA 审核
# 临时 debug 可以 port-forward 从本地连：
kubectl -n fy-api run tmp-mysql --rm -it --image=mysql:8 --restart=Never -- \
  mysql -h$RDS_HOST -uadmin -p fy_api
```

### 9.4 备份与容灾

- **RDS**：阿里云 RDS 自动备份，默认保留 7 天。建议拉长到 30 天 + 每日异地备份。
- **Redis**：Redis 数据非持久（只做缓存），丢了自动重建。
- **应用代码**：git 仓库 + ACR 镜像。`sha-<git>` tag 保留 180 天。
- **配置**：Secret 和 ConfigMap 推荐走 GitOps（ArgoCD / Flux），改动可审计。

### 9.5 HPA 观察

```bash
kubectl -n fy-api get hpa fy-api -w
# NAME    REFERENCE          TARGETS         MINPODS  MAXPODS  REPLICAS
# fy-api  Deployment/fy-api  45%/70%, 30%/75%  2       10       2

# 压测触发扩容
kubectl -n fy-api run loadgen --rm -it --image=busybox:latest --restart=Never -- \
  sh -c "while true; do wget -q -O- http://fy-api:3000/api/status; done"
```

### 9.6 证书续期

cert-manager 到期前 30 天自动续签。手动验证：
```bash
kubectl -n fy-api get certificate fy-api-tls
kubectl -n fy-api describe certificate fy-api-tls | grep -E "Not Before|Not After|Renewal Time"
```

---

## 十、故障速查

| 症状 | 诊断 | 常见原因 | 修复 |
|------|------|---------|------|
| pod 一直 `CrashLoopBackOff` | `kubectl logs --previous` | DSN 错 / Secret 未挂 / RDS 不可达 | 修 Secret，重启 |
| pod `Pending` | `kubectl describe pod` | 节点资源不足 / `imagePullBackOff` | 加节点 / 修 image tag |
| Ingress 502 | `kubectl -n fy-api get ep fy-api` | Service 没有 endpoint | 检查 pod labels 和 Service selector 对齐 |
| 域名访问 504 | — | 后端响应慢（Gemini 长流式） | 调 ingress annotation `proxy-read-timeout`；看 pod CPU 是否打满 |
| 证书一直 `Not Ready` | `kubectl describe certificate fy-api-tls` | DNS 没解析到 SLB / HTTP-01 Port 80 被挡 | 先解析生效再等重试 |
| HPA 不扩容 | `kubectl describe hpa fy-api` | metrics-server 没装 / target 设置过高 | 安装 metrics-server，验证 `kubectl top pods` 有数值 |
| 多个 pod 用户频繁被踢 | — | SESSION_SECRET 各 pod 不一致 / 未用 Redis 会话存储 | 检查 Secret、检查 `REDIS_CONN_STRING` |
| HTTPS 但 mixed content | 浏览器 DevTools | `FRONTEND_BASE_URL` 配成了 http | ConfigMap 改为 `https://`，重启 |

---

## 十一、停用（退役）

```bash
kubectl -n fy-api delete ingress fy-api
kubectl -n fy-api delete hpa fy-api
kubectl -n fy-api delete deployment fy-api
kubectl -n fy-api delete service fy-api
kubectl -n fy-api delete pdb fy-api
kubectl -n fy-api delete configmap fy-api-config
kubectl -n fy-api delete secret fy-api-secrets acr-credentials
kubectl delete namespace fy-api

# 清理 ACR 镜像（按版本保留策略）
# 清理 RDS 库（DBA 确认）
# 清理 DNS 记录
```

---

## 附录 A：生产与测试的差异

| 维度 | 测试 | 生产 |
|------|------|------|
| 实例数 | 1 | 2-10（HPA） |
| DB | RDS 测试库 | RDS 生产库（高可用） |
| Redis | 可选 | 必选（多 pod 会话共享） |
| 镜像源 | 本地 | ACR |
| 对外 | 可选 TLS | 必须 TLS + HSTS |
| 日志 | 本地文件 | SLS / Loki |
| 备份 | 无 | RDS 自动 + 异地 |
| 发版审批 | 无 | Code Review + 运维审批 |

---

## 附录 B：一次 dry-run 所有 manifest

```bash
cd ~/Fy-api/deploy/k8s
for f in 00-namespace.yaml 10-configmap.yaml 20-deployment.yaml \
         30-ingress.yaml 40-pdb.yaml; do
  echo "--- $f ---"
  kubectl apply --dry-run=client -f $f
done
```

## 附录 C：敏感字段清单（发版前复核）

- [ ] `fy-api-secrets.SQL_DSN`（包含 RDS 密码）
- [ ] `fy-api-secrets.REDIS_CONN_STRING`（包含 Redis 密码）
- [ ] `fy-api-secrets.SESSION_SECRET`（**不要变，变了踢用户**）
- [ ] `fy-api-secrets.CRYPTO_SECRET`（**不要变，变了令牌不可解**）
- [ ] `acr-credentials`（ACR pull 密码）
- [ ] `letsencrypt-prod-account-key`（cert-manager 自维护）

## 附录 D：相关文档

- 测试环境：[`test-podman.md`](./test-podman.md)
- DB 迁移（老部署升级）：[`../Phase3-DB-migration-runbook.md`](../Phase3-DB-migration-runbook.md)
- 回归清单：[`../Phase5-Regression-checklist.md`](../Phase5-Regression-checklist.md)
- 上游同步：[`../Monthly-upstream-sync-runbook.md`](../Monthly-upstream-sync-runbook.md)
- 上游官方文档（环境变量、API 参考）：<https://docs.newapi.pro/zh/docs>

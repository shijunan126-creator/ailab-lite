# platform/deploy

应用 k8s 清单和镜像定义。部署顺序、配置、模型目录、观测和备份见 [部署模块](../../docs/modules/deploy.md)；这里只说明这个目录的清单结构。

## 清单

`base/` 按资源组拆分：`apps/`（API、Agent、Broker、Worker）、`database/`（PostgreSQL）、`objects/`（对象 PVC）、`queue/`（Redis）、`scanner/`（ClamAV）、`search/`（SearXNG）、`crawler/`（抓取器和出口代理）、`gateway/`（公共网关）和 `network.yaml`。每组都带自己的 `kustomization.yaml`，可以单独 apply；升级应用镜像不会重启数据库、重建 PVC 或重建 Portal。

```bash
kubectl kustomize platform/deploy/overlays/dev
kubectl kustomize platform/deploy/overlays/lite
kubectl apply -k platform/deploy/overlays/lite
```

手动首次部署时，先 `kubectl apply -k deploy/overlays/lite` 创建 sandbox namespace 和 RuntimeClass；只 apply 本目录的 app overlay 不会创建它们。

`overlays/dev` 保留 PostgreSQL 16 和独立的 4 GiB 数据库 PVC，对象 PVC 为 20Gi。`overlays/lite` 删除 PostgreSQL，改用 `platform-data` 上的 SQLite；Broker 使用 `hostNetwork` 访问节点 loopback 的 OpenSandbox `:8090`，公共网关使用 NodePort `30080`，对象 PVC 为 40Gi。两者都不是多副本部署：`ReadWriteOnce` 卷和 SQLite 都限制在单节点。

根目录的 `deploy/overlays/lite` 是 sandbox namespace 和 `gvisor` RuntimeClass，不是应用清单；`deploy.sh` 会先应用它，再应用本目录的 lite overlay。NodePort 只暴露 `platform-public`，它只转发 `/v1/`、`/healthz`、`/readyz`；不要把 `platform-api`、Broker 或 `/internal` 直接暴露。

## 镜像

| 镜像 | 构建文件 | 说明 |
|---|---|---|
| `ailab-platform:lite` | `platform/deploy/Dockerfile` | API、Agent、Broker、Worker 共用一个镜像 |
| `ailab-portal` | `platform/deploy/portal.Dockerfile` | Next.js standalone；需要独立 Portal 镜像时使用 |
| `ailab-crawler:lite` | `images/crawler/Dockerfile` | 无头 Chromium 和 Crawl4AI |
| `harbor.ailab.local/sandbox/sbx-office:lite` | `images/sbx-office/Dockerfile` | gVisor 下的办公沙箱 |

平台、Portal 和沙箱镜像从仓库根构建；crawler 以 `images/crawler` 为上下文。`deploy.sh` 负责构建、导入 k3s containerd 并核对导入的配置 digest。`imagePullPolicy: Never` 的清单只能使用已导入的镜像，未导入时 Pod 不会自动拉取。

## 运行约束

- `kubectl kustomize platform/deploy/overlays/{dev,lite}` 必须成功；旧版 kustomize 处理 lite overlay 顶层的 `$patch: delete` 可能崩溃。
- OpenSandbox 的 CRD/controller 必须在应用 lite overlay 前安装；`deploy.sh` 会检查 `batchsandboxes.sandbox.opensandbox.io`。
- 节点必须先安装完整 gVisor 发行包并重启 k3s，使 containerd 读取 `runsc` 的 `ConfigPath`；`k3s/check-node.py` 检查 4 GiB overlay 上限、`net-raw=false`、单 IPv4 Pod CIDR 和 ICMP 规则。
- lite 的 Broker 用 `hostNetwork` 监听节点；宿主防火墙必须把 8081 和 8090 限制在节点/集群内，不能暴露到公网。
- `kubectl delete namespace platform`（以及 `deploy.sh down`）会删除 PVC 和资料；普通升级用上面的资源组 apply，不要用它。

## 验证

清单和部署脚本的静态测试在 `platform/tests/test_web_deployment.py`、`platform/tests/test_deploy_review.py`；节点/镜像基座脚本在 `tests/t0_base.sh`、`tests/t1_image.sh`。完整运行方式见 [部署模块](../../docs/modules/deploy.md) 和 [测试模块](../../docs/modules/testing.md)。

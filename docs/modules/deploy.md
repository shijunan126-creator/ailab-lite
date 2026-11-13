# 部署

平台部署在一台已安装 Docker 与 k3s 的机器上。本模块负责 `deploy.sh`、k3s 节点、k8s 清单、镜像、配置、模型目录、观测和备份。服务内部的业务行为见 [Agent](agent.md)、[API](api.md)、[文件](files.md)、[沙箱](sandbox.md)；Portal 见 [Portal](portal.md)。

## 代码地图

| 要改的东西 | 位置 |
|---|---|
| 一键部署、状态、拆除 | `deploy.sh::preflight`、`::build_images`、`::deploy_platform`、`::start_portal`、`::smoke_test`、`::cmd_status`、`::cmd_down` |
| 平台服务与 Pod 限额 | `platform/deploy/base/apps/services.yaml` |
| 清单入口 | `platform/deploy/base/kustomization.yaml`、`platform/deploy/overlays/lite/kustomization.yaml`、`platform/deploy/overlays/dev/kustomization.yaml` |
| 沙箱 namespace、RuntimeClass、节点 | `deploy/base/sandbox/namespace.yaml`、`deploy/overlays/lite/runtimeclass.yaml`、`k3s/install-runsc.sh`、`k3s/check-node.py`、`k3s/config.yaml` |
| 平台镜像 | `platform/deploy/Dockerfile` |
| Portal 镜像 | `platform/deploy/portal.Dockerfile` |
| 沙箱镜像 | `images/sbx-office/Dockerfile` |
| 抓取器镜像 | `images/crawler/Dockerfile` |
| 配置模型与安全门禁 | `platform/ailab_platform/config.py::Settings`、`::validate_secrets` |
| 模型目录与思考档位 | `platform/ailab_platform/catalog.py::load_catalog`、`::parse_reasoning`、`::reasoning_path`、`platform/models.toml`、`platform/reasoning.toml` |
| 目录校验与发布 | `platform/scripts/make_catalog.py`、`platform/scripts/k8s_models.sh`、`platform/scripts/k8s_secrets.sh` |
| 请求 ID、日志、指标 | `platform/ailab_platform/obs.py::RequestContextMiddleware`、`platform/ailab_platform/metrics.py::metrics_response`、`platform/deploy/observability/` |
| 备份与恢复 | `platform/ailab_platform/ops/backup.py::create`、`::verify`、`::restore` |
| 基座验收脚本 | `tests/t0_base.sh`、`tests/t1_image.sh`、`tests/lib.sh` |

## 部署组成

仓库口径的五个服务是 Portal、API、Agent、Broker、Worker：

| 组件 | 运行方式 | 职责 |
|---|---|---|
| Portal | lite 下由 `deploy.sh` 以 `next start` 启动；也可用 `portal.Dockerfile` 构建独立镜像 | 网页界面和同源代理，只转发 `/v1/*` |
| API | `ailab-platform:lite`，Deployment | 唯一写业务数据库、授权、配额、账本 |
| Agent | 同一镜像，Deployment | 模型循环、工具调用；不直接写业务库 |
| Broker | 同一镜像，Deployment；lite 用 `hostNetwork` | 沙箱租约、exec、文件注入和交付 |
| Worker | 同一镜像，Deployment | 扫描、解析、派生资源和预览 |

`platform-crawler`（无头 Chromium）和 `platform-egress`（出口代理）是受控联网的附属进程，不是上面的五个业务服务。依赖包括：

| 依赖 | lite/dev 实现 | 备注 |
|---|---|---|
| 数据库 | lite 用 SQLite `/data/platform.db`；dev 用 `platform-postgres` | API 是唯一写入方；迁移由 `platform/ailab_platform/migrate.py` 管理 |
| 队列和锁 | `platform-redis` | Redis 不持久化；消息由 outbox 重建，见 ADR 0001 |
| 对象存储 | `platform-data` PVC 上的本地目录；可切 S3 | 资料库、工作区、派生资源都走同一存储接口 |
| 扫描器 | `platform-clamav` | `AILAB_SCANNER_BACKEND=clamav`；扫描失败时上传保持 pending |
| 沙箱运行时 | OpenSandbox 1.1.1 + gVisor `RuntimeClass` | 节点侧 `runsc`；见 [ADR 0007](../architecture/adr/0007-controlled-public-fetch.md) |
| 私有搜索 | `platform-searxng` | 只有 Agent 可以进入；不对外暴露 |

镜像：平台、Portal 和沙箱从仓库根构建，crawler 以 `images/crawler` 为上下文；`deploy.sh::build_images` 负责构建、导入 containerd 和核对 digest。

## 资源预算

目标是 2 vCPU / 约 8 GB 跑完整栈。Pod 限额来自清单，不是容量承诺：

| 工作负载 | request（CPU / 内存） | limit（CPU / 内存） |
|---|---|---|
| `platform-api` | 100m / 128Mi | 500m / 384Mi |
| `platform-broker` | 100m / 128Mi | 500m / 384Mi |
| `platform-agent` | 50m / 96Mi | 300m / 256Mi |
| `platform-worker` | 50m / 128Mi | 300m / 384Mi |
| `platform-public` | 10m / 16Mi | 100m / 48Mi |
| `platform-redis` | 50m / 64Mi | 200m / 128Mi |
| `platform-clamav` | 100m / 1536Mi | 500m / 2Gi |
| `platform-searxng` | 100m / 128Mi | 500m / 512Mi |
| `platform-crawler` | 100m / 256Mi | 1 / 1Gi |
| `platform-egress` | 20m / 48Mi | 300m / 128Mi |
| `platform-postgres`（仅 dev） | 50m / 128Mi | 400m / 384Mi |

lite 的应用与基础设施 request 合计约 0.68 CPU / 2.5 GiB（不含仅 dev 的 PostgreSQL）；lite 沙箱默认 `0.75` CPU / 1.5 GiB，namespace 还限制 1 个 Pod、每个容器 5 GiB ephemeral-storage（request 64 MiB）。`runsc` 的根 overlay 另由节点限制为 4 GiB。k3s 自身保留 500m CPU / 1536 MiB 内存，并在可用内存低于 500 MiB 时驱逐。把沙箱和 Portal 算进去后，2 vCPU / 8 GB 的预算已经接近上限；调整任何一个 Pod 或并发前先重算整栈预算。

存储：lite 的对象与 SQLite 共用 `platform-data` 40Gi PVC；dev 为 20Gi，并另给 PostgreSQL 4Gi。ClamAV 签名卷 1Gi。`AILAB_PROVISIONED_STORAGE_BYTES` 必须等于实际对象容量；当前 lite 设为 40 GiB。API 只在“用户数 × 资料库配额”超过该值时限告警，不阻止启动。

## 关键流程

### 一次完整部署
1. `preflight` 检查 root、`docker`、`k3s`/`kubectl`、Ready 节点、kubeconfig、内存下限和 Portal 端口；`k3s` 与 `kubectl` 由 `platform/scripts/_common.sh` 选择。
2. `resolve_models` 选择 `models.toml` 或 `models.local.toml`，校验目录并确定默认模型的 Key 环境变量；自定义端点会先做一次真实的 chat completions 探测。
3. `build_images` 构建沙箱、平台和 crawler 镜像，`docker save` 后导入 k3s containerd，并用配置 digest 或 manifest digest 核对确实导入了刚构建的镜像。
4. `deploy_platform` 检查 OpenSandbox CRD 和节点 `runsc` 配置，应用根目录 `deploy/overlays/lite` 的 sandbox namespace，启动 `osb_up.sh`（`OSB_RUNTIME=kubernetes`），创建 Secret、模型 ConfigMap 和可选注册/embedding 配置，再应用 `platform/deploy/overlays/lite`。
5. 已存在的部署中，`apply` 没有改动的四个服务会 `rollout restart` 以加载同一 tag 下的新代码；随后等待 8 个 Deployment 就绪，再等 API 和 Broker 的 readiness。
6. `start_portal` 在 `package-lock.json` 变化时执行 `npm ci`，之后 `npm run build`、`next start`；`smoke_test` 检查默认令牌、Portal 代理、登录 Cookie、模型列表、内部路由 404 和离线模型 Run，`--smoke-real` 才跑真实模型。

`./deploy.sh --dry-run` 只打印将要执行的步骤，不需要集群；`status` 查 Pod、四个健康端点和模型列表；`down` 删除整个 `platform` namespace，包括 PVC、Secret 和模型 ConfigMap，只保留镜像、OpenSandbox server 和仓库里的 `platform/models.toml`；重建会生成新的凭据。

常用 `up` 参数（完整列表以 `deploy.sh` 的参数解析和源码注释为准）：

| 场景 | 参数 |
|---|---|
| 接一个 OpenAI 兼容模型 | `--model-base-url URL --model-name NAME [--model-key KEY] [--model-label 显示名] [--model-no-tools]`；不带 base URL 的 `--model-key` 是给目录默认模型用的 |
| 接整份模型目录 | `--models-file FILE`；会复制成 `platform/models.local.toml`，`--models-file platform/models.toml` 回到仓库目录 |
| 注册 | `--registration open/invite/closed`，`invite` 需要 `--invite-code` |
| 历史语义召回 | `--embedding URL 或 off --embedding-model NAME [--embedding-key KEY] [--embedding-min-similarity X] [--embedding-query-prefix TEXT]`；启用后会把会话文本发送到该 embedding 端点 |
| 部署范围 | `--skip-build`、`--no-portal`、`--listen ADDR`、`--smoke-real`、`--dry-run` |

### 只改配置或模型
`sudo ./deploy.sh --skip-build` 复用已导入的平台和 crawler 镜像，重新生成 Secret、模型 ConfigMap 和配置后 apply；沙箱镜像若已过期只会警告并保留旧镜像。改代码、改沙箱镜像不要用这个参数。

### 发布模型目录
本机开发把 `models.toml`/`models.local.toml` 放在 `platform/`，重启 API 和 Agent。k3s 用 `bash platform/scripts/k8s_models.sh` 把目录和旁边的 `reasoning.toml` 发布成 `platform-models` ConfigMap，并重启 API、Agent；`NO_RESTART=1` 只发布不重启。首次部署时，`deploy.sh` 会把默认模型的 Key 写进 `platform-secrets`，再由 `k8s_secrets.sh` 投影到只挂给 API/Agent 的 `platform-model-secrets`。给已有部署增加第二个模型 Key 时，先把 `AILAB_KEY_<NAME>` 按 Secret 运维方式加入 `platform-secrets`（不要把值写进命令行历史），再运行 `k8s_secrets.sh` 重建 `platform-model-secrets`。

## 不变量

- Portal 的公共入口只有 `platform-public`；它只放行 `/v1/`、`/healthz`、`/readyz`，`/internal`、`/metrics`、`/docs` 没有 NodePort。钉住它的测试：`platform/tests/test_web_deployment.py::test_agent_search_reaches_a_private_service_through_the_base_policy`。
- 每个 Deployment 只拿到自己的 identity Secret 和公共 verifier；模型 Key 只给 API/Agent，OpenSandbox Key 只给 Broker，账号密码和邀请码只给 API。
- 服务启动统一经过 `Settings::validate_secrets`。`AILAB_PROFILE=secure` 才会执行完整门禁；`platform/deploy/base/config.yaml` 当前显式写 `AILAB_PROFILE=dev`，所以 k3s 清单本身不是安全上线声明。
- API、Agent、Broker、Worker 共用一个 `ailab-platform:lite` 镜像和 tag，后端代码变更必然一起更新这四个 Deployment；不能只替换其中一个 Python 服务的镜像。
- lite 的 `platform-data` 是 `ReadWriteOnce`，由 API/Broker/Worker 共用；它把整套服务约束在一个节点上，不构成多副本或高可用。
- `deploy.sh down` 会删除 namespace 和 PVC；需要保留数据时只能对资源组单独 apply，不能把它当升级命令。
- `kubectl apply` 失败必须中止部署，不能继续冒烟或报告成功。钉住它的测试：`platform/tests/test_deploy_review.py::test_failed_manifest_application_stops_deployment`、`::test_image_import_compares_configuration_ids_not_manifest_digests`。
- `AILAB_PROVISIONED_STORAGE_BYTES` 是告警阈值，不是写入配额；真正的对象容量由 PVC/S3 和生命周期回收决定。

## 怎么扩展

### 加一个配置项
1. 在 `platform/ailab_platform/config.py::Settings` 加字段，用 `Field` 限制范围，枚举用 `Literal`；环境变量名自动是 `AILAB_<FIELD>`。
2. 跨字段约束放在 `model_validator`，单值格式放在 `field_validator`。不要用没有校验的 `str` 承载安全边界。
3. 如果默认值在共享部署不安全，在 `Settings::validate_secrets` 的 `secure` 分支加拒绝条件；四个服务的 lifespan 都会调用它。
4. 非敏感默认值进 `platform/deploy/base/config.yaml`，敏感值进 `platform/scripts/k8s_secrets.sh` 或对应 Secret；确认目标 Deployment 的 `envFrom`/`env` 真的能读到。
5. 加测试：配置校验用 `platform/tests/test_service_identity.py` 或对应模块测试；改变部署行为时加 `platform/tests/test_deploy_review.py` 的脚本级断言。

### 加或换一个模型
1. 在 `platform/models.toml`（或本机的 `models.local.toml`）加一个 `[[models]]`，填 `id`、`name`、`provider`、`base_url`；OpenAI 兼容接口用 `provider = "openai"`，`upstream_model` 不填时发送 `id`。
2. 需要 Key 时填 `api_key_env = "AILAB_KEY_..."`，把值放进 `platform/.env` 或 k8s Secret，绝不写进目录。`secure` 下公开模型主机还要 `AILAB_ALLOW_EXTERNAL_MODELS=true`，否则启动门禁拒绝。
3. 思考模型按上游字段配置：要求回传 `reasoning_content` 的设 `echo_reasoning = true`；上游把思考混在正文时设 `think_tag`，模板预先打开思考块时再加 `think_prefilled = true`；需要关闭思考的 quiet 调用才设 `disable_reasoning_for_quiet`。
4. 提供可选思考档位时，在 `platform/reasoning.toml` 定义 profile，并在模型上写 `reasoning = "<profile>"`；每个 level 的 `body` 只能追加请求字段，不能覆盖 `model`、`messages`、`tools` 等请求骨架。
5. 本机重启 API/Agent；k3s 执行 `bash platform/scripts/k8s_models.sh`。用 `python platform/scripts/make_catalog.py check <目录>`、`GET /v1/models` 和一次真实 Run 验证；`default = true` 最多一个，没有则使用第一个可用模型。

### 加一个依赖服务或镜像
1. 在 `platform/deploy/base/` 新建资源组目录，加入 Deployment/Service/ConfigMap/Secret/PVC，并把目录加进 `platform/deploy/base/kustomization.yaml`；资源组也可以单独 `kubectl apply -k`。
2. 补 Ingress NetworkPolicy：默认拒绝未声明的入站；只给真正需要的调用方开端口。需要出网的基础设施单独加 egress policy，不要把出口权限开给所有 Pod。
3. 设置 request/limit、readiness/liveness、`securityContext` 和非 root 用户；有持久数据的加 PVC 和容量预算。同步更新 [资源预算](#资源预算) 和整栈内存合计。
4. 本地镜像用 `imagePullPolicy: Never` 时必须先导入 containerd；在 `deploy.sh::build_images` 增加构建/导入，并像现有镜像一样核对 digest。外部镜像固定 tag 或 digest，不要依赖浮动 `latest`。
5. 需要等待就绪的服务加进 `deploy.sh::deploy_platform` 的 rollout 列表和 summary；否则部署成功检查会漏掉它。需要配置的加到 `platform-config` 或专用 Secret。
6. 静态网络/清单测试参照 `platform/tests/test_web_deployment.py`；脚本行为测试参照 `platform/tests/test_deploy_review.py`。

### 只更新变化的组件
- 只改配置、Key 或模型目录：`sudo ./deploy.sh --skip-build`；不重建镜像，但会重新创建 Secret/ConfigMap 并重启四个平台服务。
- 只改 Portal：Portal 与后端镜像分离，可以只重建/替换 Portal 进程或 `portal.Dockerfile` 镜像，复用平台和 Office 镜像；完整 `deploy.sh` 仍会 apply 平台清单，严格只动 Portal 时走 Portal 自身的 `npm ci && npm run build && next start`。
- 改平台后端：重建 `ailab-platform:lite` 并导入；API/Agent/Broker/Worker 共用该镜像，四个 Deployment 一起滚动。`--no-portal` 可跳过 Portal。
- 改沙箱技能或镜像：`images/sbx-office/skills` 同时打进平台镜像和沙箱镜像；必须重建两者，不能只换沙箱。`deploy.sh::build_images` 用内容哈希标签判断沙箱是否变化。
- 用 Docker 层缓存复用未变化的依赖层；不要用随版本变化的 `COPY` 路径或重建未变化镜像来“保证更新”。Portal 依赖只在 `package-lock.json` 哈希变化时重装。

## 配置

所有设置由 `platform/ailab_platform/config.py::Settings` 读取，前缀 `AILAB_`，默认从 `platform/.env` 或 `AILAB_ENV_FILE` 指定的文件加载。完整字段以代码为准，常改类别如下：

| 类别 | 代表变量 | 说明 |
|---|---|---|
| 运行与身份 | `AILAB_ENV`、`AILAB_PROFILE`、`AILAB_SERVICE_NAME`、`AILAB_SERVICE_CREDENTIALS` | profile、服务身份和启动门禁 |
| 数据面 | `AILAB_DATABASE_URL`、`AILAB_REDIS_URL`、`AILAB_STORAGE_BACKEND`、`AILAB_STORAGE_ROOT`、`AILAB_S3_*` | SQLite/PostgreSQL、Redis、Local/S3 |
| 模型与额度 | `AILAB_MODELS_FILE`、`AILAB_LLM_TIMEOUT_SECONDS`、`AILAB_LLM_MAX_RETRIES`、`AILAB_DAILY_TOKEN_LIMIT`、`AILAB_MONTHLY_TOKEN_LIMIT` | 推理超时、重试和用户额度 |
| 沙箱 | `AILAB_SANDBOX_PROVIDER`、`AILAB_SANDBOX_CPU`、`AILAB_SANDBOX_MEMORY_BYTES`、`AILAB_OSB_*` | 沙箱容量、租约和 OpenSandbox |
| 文件与扫描 | `AILAB_SCANNER_BACKEND`、`AILAB_CLAMD_ADDRESS`、`AILAB_MAX_UPLOAD_BYTES`、`AILAB_MAX_PARSE_BYTES` | 扫描器、上传和解析上限 |
| 联网 | `AILAB_WEB_SEARCH_PROVIDER`、`AILAB_SEARXNG_URL`、`AILAB_CRAWLER_URL`、`AILAB_WEB_*` | 搜索、网页读取、下载预算 |
| 生命周期与观测 | `AILAB_GC_*`、`AILAB_*_RETENTION_DAYS`、`AILAB_LOG_FORMAT`、`AILAB_OTEL_*` | 清理、日志和追踪 |

### `AILAB_PROFILE=secure`
`profile` 未显式设置时，`env=test` 为 `test`，`dev/smoke/k3s` 为 `dev`，其他环境为 `secure`。k3s 清单显式写 `AILAB_PROFILE=dev`，要上线必须主动改为 `secure` 并准备真实依赖。

`secure` 下 `Settings::validate_secrets` 拒绝：默认 `dev_token`/`service_token`/`osb_api_key`；mock 沙箱；stub 扫描器；非空旧式共享 `AILAB_SERVICE_TOKEN`；缺失或不足 24 字符的本服务 credential；不匹配的 verifier；未声明 api/agent/broker/worker/ops 五个身份；未设置邀请码的 open/invite 注册；未验收的 OpenSandbox Kubernetes 隔离；过短的 crawler token；以及默认不允许的外部模型主机。检查在四个服务的 lifespan 中执行，失败直接阻止启动。

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_PROFILE` | 选择 `dev`/`test`/`secure` 门禁 | 由 `AILAB_ENV` 推导 |
| `AILAB_MODELS_FILE` | 替代模型目录路径 | `platform/models.toml` 或 `models.local.toml` |
| `AILAB_DATABASE_URL` | 数据库连接 | `platform/var/` 下的 SQLite |
| `AILAB_REDIS_URL` | 队列与锁 | `redis://127.0.0.1:6379/0` |
| `AILAB_STORAGE_BACKEND` | `local` 或 `s3` | `local` |
| `AILAB_SANDBOX_PROVIDER` | `mock`/`docker`/`opensandbox` | `mock` |
| `AILAB_ALLOW_EXTERNAL_MODELS` | secure 下允许公网模型主机 | `false` |

### `models.toml` 与 `reasoning.toml`
目录字段分四组：展示与计量（`id`、`name`、`description`、`weight`、`default`、`enabled`）；路由（`provider`、`base_url`、`upstream_model`、`api_key_env`、`fallback`、`session_header`）；容量（`context_tokens`、`max_output_tokens`、`capabilities` 或 `tools`）；思考（`echo_reasoning`、`disable_reasoning_for_quiet`、`reasoning`、`think_tag`、`think_prefilled`）。

`reasoning.toml` 的每个 profile 有 `default` 和按弱到强排列的 `levels`；每个 level 的 `body` 会合并进该次请求。请求骨架字段不能由 profile 覆盖。目录只在模型声明了 `reasoning` 时加载旁边的 `reasoning.toml`，找不到时回退到 `platform/reasoning.toml`。

## 观测

API、Agent、Broker、Worker 各暴露一个 `/metrics`（Prometheus 文本格式），用 ops 身份的 `AILAB_SERVICE_CREDENTIAL` 认证；只有 `ops` scope 含 `metrics:read`。`platform-ops-identity` 是唯一应给 Prometheus 的身份，不要把完整服务凭据放进公开 ConfigMap。

| 问题 | 指标 |
|---|---|
| 队列、outbox 是否积压 | `ailab_queue_depth`、`ailab_outbox_backlog`、`ailab_outbox_oldest_seconds` |
| Run 状态和耗时 | `ailab_runs_in_state`、`ailab_runs_total`、`ailab_run_seconds`、`ailab_agent_run_seconds` |
| 沙箱容量和申请延迟 | `ailab_sandbox_leases_active`、`ailab_sandbox_capacity`、`ailab_sandbox_acquire_seconds` |
| 模型、缓存和工具 | `ailab_llm_calls_total`、`ailab_llm_call_seconds`、`ailab_model_tokens_total`、`ailab_prompt_cache_tokens_total`、`ailab_tool_calls_total` |
| 文件和交付 | `ailab_files_processed_total`、`ailab_file_processing_seconds`、`ailab_artifact_bytes_total` |
| HTTP 和进程 | `ailab_http_requests_total`、`ailab_http_request_seconds`、`process_resident_memory_bytes` |

抓取配置示例和告警/看板说明见 `platform/deploy/observability/README.md`。API 的 scrape-time gauge 会做少量数据库查询；最老 outbox 使用 `(created_at, id)` 索引，不要通过遍历对象存储来估算容量。可选追踪用 `platform[otel]` 和 `AILAB_OTEL_ENDPOINT`，默认不加载 SDK；边界见 [ADR 0006](../architecture/adr/0006-optional-bounded-tracing.md)。

## 备份与恢复

在 `platform/` 下用与目标环境一致的 `AILAB_ENV_FILE` 运行：

```bash
python -m ailab_platform.ops.backup create --target /backup/snapshot
python -m ailab_platform.ops.backup verify /backup/snapshot
python -m ailab_platform.ops.backup restore /backup/snapshot --into /restore/ailab
python -m ailab_platform.ops.backup verify /restore/ailab
```

备份包含数据库快照、对象及 size/sha256 引用、派生资源、预览、有效工作区和脱敏配置。创建目标必须为空，`backup.json` 只在完整校验通过后写入；失败目录不能当备份。跨数据库和对象存储没有分布式快照，备份窗口要暂停删除、GC、Worker 派生写入和 Broker 预览/工作区写入。SQLite 恢复把 `AILAB_DATABASE_URL`、`AILAB_STORAGE_ROOT`、`AILAB_DATA_DIR`、`AILAB_MODELS_FILE` 指向恢复目录，并注入新的服务凭据和模型 Key；PostgreSQL 只恢复 SQL，由运维导入新的空库。完整操作、RPO/RTO 和生命周期默认值见[备份恢复 runbook](../architecture/runbooks/backup-restore.md)。

## 本机测试环境

在 Linux 机器上（Docker 可用；k3s 和 `runsc` 可由 `tests/t0_base.sh` 安装），仓库根目录执行：

```bash
sudo ./deploy.sh                  # 完整构建 + 部署 + Portal + 冒烟
./deploy.sh status                # Pod、健康端点、模型列表
sudo ./deploy.sh --skip-build     # 只改配置/Key/注册方式
./deploy.sh down --yes            # 删除 namespace 和 PVC
```

Portal 默认只在 `127.0.0.1:3000` 监听，远程用 `ssh -L 3000:127.0.0.1:3000 <host>`。`--listen 0.0.0.0` 是明文 HTTP，只在可信网络使用。宿主机 `platform/scripts/dev_up.sh` 与这套 k3s 部署互斥（8080/8081 冲突）：切回宿主机模式前先 `bash platform/scripts/k8s_down.sh`。

节点基座由 `tests/t0_base.sh` 检查和安装（固定 k3s `v1.34.12+k3s1`，要求预先放好 `/tmp/gv-deb/runsc.deb`），`tests/t1_image.sh` 构建沙箱镜像并在 Docker/gVisor 下做断网冒烟。`tests/lib.sh` 会读取被 gitignore 的 `tests/local.sh`；`tests/local.sh.example` 展示 `guard_busy_window` 和 `host_probe` 两个保护钩子。

## 相关决策

- [ADR 0001](../architecture/adr/0001-single-node-scope.md)：单机默认值、并发、保留期和 Redis 可重建的边界。
- [ADR 0002](../architecture/adr/0002-service-identity.md)：五个服务身份、scope 和 secure 启动门禁。
- [ADR 0006](../architecture/adr/0006-optional-bounded-tracing.md)：默认只靠指标和请求 ID，追踪按需启用且有界。
- [ADR 0007](../architecture/adr/0007-controlled-public-fetch.md)：沙箱断网、Agent 抓取和 Broker 注入。
- [ADR 0020](../architecture/adr/0020-elastic-sandbox-allowance-and-idle-reclaim.md)：沙箱弹性额度和闲置回收。
- [ADR 0021](../architecture/adr/0021-isolated-browser-rendering-for-read-url.md)：弱正文才交给隔离抓取器。

## 已知的坑

见 [HANDOFF · 部署](../../HANDOFF.MD#部署)。

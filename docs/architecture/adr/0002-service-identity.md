# ADR 0002 · 单机服务身份与请求绑定

状态：accepted，2026-10-23。覆盖差距审查 T03/T05；不实现 Kubernetes TokenReview。

单机先采用每服务独立的高熵 bearer credential，沿用 `X-Service-Token` 传输。原共享令牌使 Worker 可调沙箱、Agent 可回写扫描；拆开后，每个请求先过声明式 scope，再检查资源上下文。认证端只分发 SHA-256 验证摘要，不分发其他调用方的原始令牌，避免接收端能冒充任意服务。比较使用 constant-time digest compare。

| 调用身份 | scopes |
|---|---|
| api | `agent:dispatch`, `worker:dispatch` |
| agent | `jobs:run`, `runs:read`, `runs:status`, `runs:events`, `usage:reserve`, `usage:write`, `files:read_for_run`, `sandbox:use` |
| broker | `runs:read`, `runs:events`, `files:read_for_run`, `artifacts:commit_for_run` |
| worker | `jobs:file`, `files:ocr`, `files:read_pending`, `files:patch_processing`, `files:preview_write`, `sandbox:render` |
| ops | `metrics:read`, `sandbox:inspect` |

`api` 的 dispatch 和 Worker render/preview、Agent reserve scope 已定义，端点由相应工程师实现。Agent/Worker 当前只有健康、就绪与 metrics 入站接口，无 `/internal` 业务路由；新增内部路由必须显式依赖 `Depends(require_scope("…"))`。`require_scope(a, b)` 表示任一 scope 允许，资源限制仍需 handler 校验。API 的文件读取接受 agent/broker 的 `files:read_for_run` 或 Worker 的 `files:read_pending`，按返回的 `ServicePrincipal` 执行不同绑定，不能仅用 scope 代替文件归属检查。

文件访问：Agent/Broker 必传 `run_id`；API 解析 `RunContext(run_id, user_id, conversation_id)`，检查活跃 Run、同用户、READY、当前/同会话附件或产物。Agent 还能读取该用户 API 已列出的可用个人技能，这是明确的系统附件来源。产物提交仅 Broker，可由 `body.run_id` 推导用户。Worker `job=processing` 只能访问 PENDING；`job=thumbnail|preview` 只能访问 READY，且只写允许的 derived 字段、路径限定 `derived/{file_id}/`，不得写 scan/status。READY 重投的普通处理作业应丢弃，认证错误保留重试。

配置契约：

- `AILAB_PROFILE=dev|test|secure` 优先于旧 `AILAB_ENV`。未配置 profile：`test → test`，`dev/smoke/k3s → dev`，其余（含 prod）→ secure。`env` 继续作为环境标签；k3s 不是安全承诺。
- `AILAB_SERVICE_NAME=api|agent|broker|worker|ops`；`AILAB_SERVICE_CREDENTIAL` 是该进程自己的原始令牌。也支持 `AILAB_SERVICE_{API,AGENT,BROKER,WORKER,OPS}_TOKEN` 单项设置，但 secure 客户端只准读取与当前身份相符的一项。
- `AILAB_SERVICE_CREDENTIALS` 是 JSON：`{"agent":"sha256:<64 hex>", …}`，五个身份值必须不同。secure 要求五个 verifier、自己的 credential 至少 24 字符且匹配，不接受任何非空 `AILAB_SERVICE_TOKEN`。dev/test 可接受旧共享令牌，启动会警告；这是兼容权限，不应当作安全隔离验证。E2E 禁用它。
- `AILAB_SCANNER_BACKEND=stub|clamav` 仅本分支定义配置与门禁，ClamAV 实现属于 T04。`AILAB_ALLOW_EXTERNAL_MODELS=false` 默认拒绝外部模型主机；内部仅认 loopback、RFC1918/ULA IP、localhost、`.svc`/`.svc.cluster.local`/`.ailab.internal` 名称，不启动时解析 DNS。公开模型需显式设 true；网络/DNS 出口管控仍由部署负责。
- 所有 backend/provider、registration、日志格式、OSB 协议均为严格 Literal。容量、字节和 timeout 有范围；配额、缓存权重、重试次数、保留天数可按其语义取零。
- `Settings.validate_secrets(service)` 是所有服务统一启动安全门：secure 禁 mock、stub、所有 `DEFAULT_SECRETS`、旧共享令牌、身份不匹配和未授权外部模型；API 开放/邀请注册须有邀请码，open 会呈现并执行 invite 模式。OSB Broker 必须有非空 key。日志输出用 `redacted()` 隐藏令牌、Key、密码、URL userinfo/query。
- `AILAB_TRUSTED_PROXIES` 是 JSON CIDR 列表，默认 `[]`。从 socket peer 向左逐跳检查 XFF，在首个不可信 hop 停止，非法或超过 32 hops 的头忽略。Uvicorn 必须 `--no-proxy-headers`，以免它在应用判断前改写原始 peer。部署填真实 Portal/反代 CIDR，不默认信任全网。内部 HTTP 客户端 `trust_env=False`，不把服务凭据发送给宿主环境代理。

部署生成 `platform-{service}-identity` 与公共 verifier Secret，每个 Deployment 只挂自己原始 credential；OSB key 仅 Broker，账号密码/邀请码仅 API，模型 key 仅 API/Agent。旧 Secret 升级保留已有值；身份只在缺失时生成，重跑不轮换。轮换先更新对应 identity Secret、重新生成 verifier，再重启相关 Deployment；本阶段没有双版本凭据的无中断轮换。现有 platform.yaml 明示 dev 兼容部署，shared 部署需选择 secure、真实 scanner 和正确模型策略。

性能：每请求 1 次 SHA-256 + 固定 5 项比较，本机 100,000 次微基准约 3.04 µs/次（非目标机容量承诺），无额外网络、后台任务、新依赖或大文件复制。文件直接附件路径只查主键；会话附件用 indexed Run filter + JSON EXISTS，不拉全部 Run 到 Python；SQLite/PG EXPLAIN 已检查，使用现有 user/conversation 索引。历史附件仍随该会话 JSON 元素数量线性扫描，未来关系表可替换此查询。增加 catalog 的只读 ConfigMap 挂载用于所有服务启动门禁。

未来在 `ports/service_auth.py:ServiceAuthenticator` 实现 audience 固定、有限缓存/超时的 projected-SA TokenReview adapter，将 SA 映射到相同 `ServicePrincipal`；scope 表和 RunContext 校验保持不变。当前未实现 TLS/SA 自动轮换、真实 k3s/ClamAV/OpenSandbox 验收，仍需网络隔离。无数据库迁移。

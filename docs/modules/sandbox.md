# 沙箱

Broker 是沙箱的唯一持有者：租约、exec 代理、文件注入与两段式交付、渲染沙箱调度都在这里。模型工具的选择与调用在 [Agent](agent.md)；上传、扫描、解析和派生资源在 [文件](files.md)；镜像构建与部署在 [部署](deploy.md)。

## 代码地图

| 要改的东西 | 位置 |
|---|---|
| 租约生命周期、准入、回收、对账 | `platform/ailab_platform/broker/service.py::LeaseManager` |
| Broker 内部 HTTP 接口 | `platform/ailab_platform/broker/main.py::router` |
| 准入队列与每用户额度 | `platform/ailab_platform/domain/admission.py::AdmissionPolicy` |
| 单机容量预算 | `platform/ailab_platform/ports/capacity.py::StaticCapacityProvider` |
| 租约记录、CAS 与 owner 锁 | `platform/ailab_platform/ports/lease_store.py::LeaseStore`、`platform/ailab_platform/adapters/lease_store_redis.py::RedisLeaseStore` |
| provider 抽象与共用路径守卫 | `platform/ailab_platform/sandbox/base.py::SandboxProvider` |
| provider 工厂 | `platform/ailab_platform/sandbox/__init__.py::get_provider` |
| OpenSandbox 适配 | `platform/ailab_platform/sandbox/opensandbox.py::OpenSandboxProvider` |
| Docker 适配 | `platform/ailab_platform/sandbox/docker.py::DockerSandbox` |
| mock（仅测试，无隔离） | `platform/ailab_platform/sandbox/mock.py::MockSandbox` |
| 流式传输与硬限额 | `platform/ailab_platform/sandbox/transfer.py::copy_chunks` |
| 路径规则 | `platform/ailab_platform/paths.py::normalize_path` |
| 沙箱内脚本 | `platform/ailab_platform/sandbox_tools/` |
| 后台命令 | `platform/ailab_platform/agent/background_exec.py::background_exec`、`platform/ailab_platform/agent/background_helper.py::control` |
| 渲染租约与文件库预览 | `platform/ailab_platform/broker/render.py::LibraryRenderer` |
| 系统文档读取 | `platform/ailab_platform/broker/documents.py::DocumentReader` |
| Work 内查看与 OCR | `platform/ailab_platform/broker/workspace_view.py::view`、`platform/ailab_platform/broker/workspace_ocr.py::recognize` |
| 镜像技能证明 | `platform/ailab_platform/broker/routes_skills.py::verify_lease_index` |
| 受控联网代理（只服务抓取容器） | `platform/ailab_platform/egress.py::handle` |
| 沙箱镜像 | `images/sbx-office/Dockerfile` |
| 办公技能生成 | `vendor/office-suite/build.py` |
| 技能索引与一致性检查 | `images/sbx-office/skill_index.py::build_index`、`platform/scripts/check_skills.py` |
| 运行时与网络 | `k3s/opensandbox-template.yaml`、`k3s/install-runsc.sh`、`platform/deploy/base/config.yaml` |

## 关键流程

### 一次 Work Run 领取租约
1. `LeaseManager.create` 从 API 读取 Run 的 owner 与附件；owner 只来自 API，不接受 Agent 请求体。
2. `LeaseManager.acquire` 先过 `AdmissionPolicy.admit`，最多等待 `sandbox_wait_seconds`；排队时通过 `on_queued` 写 `agent.waiting_sandbox`，Run 结束或取消则中止。
3. 先以 `creating` 写入租约记录，再调 provider `acquire`，把 owner、lease_id、run_id、spec 和期限写进资源标签。
4. `prepare` 在激活前注入输入、模板/品牌并恢复上一轮工作区；失败则释放资源，半准备的沙箱不会暴露给 Agent。
5. provider 收到 `renew` 和最终 `update_metadata` 后，租约 CAS 为 `active`，返回 `LeaseHandle`。

### 准入
- 两个容量类独立排队：通用池是 `max_sandboxes - sandbox_render_reserved_slots` 及其内存，render 用预留槽；Work 不能借用 render，render 也不占用户的 Work 额度。
- 容量允许时，每人第一个 Work 沙箱直接放行；第 2 个起只在放行后通用池仍低于 `sandbox_burst_watermark` 时放行，否则有效上限回到 1。已有沙箱不因水位变化被回收。
- 同一类内 FIFO；被额度或内存挡住的请求不阻塞其他可放行请求。队列上限 `sandbox_queue_limit`，超时返回 `sandbox_queue_deadline`。同一 Run 已有 active 租约时幂等返回，并发等待直接拒绝。

### 续租与失联
- `LeaseManager.renew` 只接受不超过原租约的 TTL，`expires_at` 不超过 `max_expires_at`；provider 的到期时间是兜底，比 Broker 租约多 `SANDBOX_EXPIRY_GRACE_SECONDS`。
- 每次操作都经 `_load` 校验状态、期限、handle 与 provider 标签；资源消失返回 410 `lease_lost` 或 `lease_expired`，不自动换一个新沙箱重放工具。
- Agent 在回合进行中按 `runtime_heartbeat_seconds` 续租；续租失败按可恢复错误重试，只有 410 才算租约丢失。

### 闲置回收
- 只有等待中的 Work 请求因 `user_limit` 或 `capacity` 被挡时才触发 `_reclaim_for`；render 容量不足时先回收 standby 或其他用户的 `idle`，不碰正在执行的转换。`_pick_victim` 只选 `active`、非 render、没有进行中操作、闲置达到 `sandbox_evict_idle_seconds` 的租约。
- 心跳仍新鲜的租约视为回合进行中，门槛乘 3；心跳停止后回到底线，崩溃的 Run 不会永久占住沙箱。
- 用户上限只回收该用户自己的沙箱；池满时要求通用池占用率不低于 `sandbox_preempt_utilization`，先动持有最多沙箱的用户。
- 选中后先置 `releasing`（原因 `evicted_idle`，调用方立刻得到 410），后台线程等 30 秒在途操作、快照工作区、写 `eviction_snapshot`，再删除资源。

### 崩溃与重启恢复
- `recover` 用 Redis owner 锁取得唯一 Broker 身份；心跳每 TTL/3 续锁，丢锁即 fence 所有资源操作。
- `cleanup_once` 只列本 owner 的资源：带完整标签的 active Work 租约被认领；Redis 被清空时从标签重建；render 租约一律释放，不跨 owner 认领。
- 创建中断的租约通常删除；只有 provider 元数据已证明激活完成时才接管。重复或不完整标签的资源记为 `orphan_*` 并删除；删除失败保持 `releasing`，按 2→60 秒退避重试。
- 终态记录保留 `broker_record_retention_seconds` 后清理。

### 安全边界
- Docker 后端用 `--network none`、`--cap-drop ALL`、`no-new-privileges`、`--pids-limit 256`、swap 与内存相同（禁用额外 swap）、`/tmp` tmpfs，不挂宿主目录；PID1 是过期监督进程，只读 `/run/ailab-lease.json`，到点或租约进入 `releasing` 就退出。
- Docker 的隔离强度由 `AILAB_SANDBOX_RUNTIME` 决定：默认 `runc`，设为 `runsc` 才是 gVisor；secure profile 拒绝 Docker 配 `runc`。部署验收必须记录实际运行时，不能按镜像名推断。
- OpenSandbox k3s 路径由 `k3s/opensandbox-template.yaml` 固定 `runtimeClassName: gvisor`、`runAsUser: 10001`、丢弃全部 capabilities、不挂 ServiceAccount token；Broker 只按 spec 传 image、cpu、memory 和期限，模型不能选运行时或网络。
- `osb_network_mode=sidecar` 时创建带 deny-all 策略；`kubernetes` 模式依赖 sandbox 命名空间的 default-deny NetworkPolicy，secure profile 还要求 `osb_kubernetes_isolation_verified`。
- 沙箱内没有平台凭据、服务 token 或宿主目录；用户命令在 `/workspace` 工作，`/tmp` 是 tmpfs，`.ailab/` 是平台自己的目录；可变租约状态在 root-only 的 `/run/ailab-lease.json`，用户命令不可写。
- `mock` 是宿主临时目录加 subprocess，不是隔离边界；secure profile 在 `Settings.validate_secrets` 拒绝它。

### exec 与文件读写
- Agent 只按 `lease_id` 调用 `POST /internal/leases/{id}/exec`；Broker 每次先 `_load` 再转发，非零退出会再检查一次资源是否消失。
- 内部接口都在 `/internal`，按 scope 分：`sandbox:use`（租约、exec、文件、交付）、`sandbox:render`（render、预热、文档读取）、`sandbox:view_workspace`（Work 查看）、`sandbox:inspect`（列出所有租约）。
- 所有命令在 `/workspace` 用 bash 执行，超时默认 `sandbox_exec_timeout_seconds`；stdout/stderr 各只保留尾部 128 KiB。
- 写：路径经 `normalize_path` 限定在 `/workspace`，大小受 `max_sandbox_write_bytes`；HTTP 层在进入进程前拒绝声明超限的请求体。
- 读：整文件受 `max_sandbox_read_bytes`；`read` 用 `line`/`length` 只取窗口，沙箱内 `tail`/`head` 定位，Broker 不搬整文件。
- 路径守卫由 provider 共用：`file_size` 与 `lines_script` 解析父目录并拒绝链接；OpenSandbox 能用文件 API 证明时走快路径，否则回落到同一段 shell。

### 交付两阶段
1. Agent 用 `output_manifest` 对 `/workspace/outputs` 做有界枚举与 sha256（最多 500 个文件，总量受 `max_artifact_bytes` 限制）。
2. 自动交付时，变化文件先写 delivery intent（sha256/size）并进 checkpoint，再调 `commit` 并带 `expected_sha256`。
3. Broker 只接受 `/workspace/outputs` 下的普通文件，大小不超过 `max_artifact_bytes`；流式读到 staging 对象并计算 sha256。
4. sha 与 intent 不符则删 staging 并报错；一致则调 API `commit_artifact`，由 API 写文件记录并复制到 `runs/...`；API 失败时删 staging。

### 工作区跨轮
- `snapshot` 打包 `/workspace`，排除 `inputs/` 与平台目录 `.ailab/`，上限 `max_workspace_bytes`，键为 `workspaces/{run_id}/{uuid}.tgz`。
- 下一轮 `prepare` 调 `_restore_workspace`：先下载快照，再一次 exec 解包、清理 `.template`/`.brand`、计算预览指纹；解包失败只损失连续性，模板或品牌清理失败则失败租约。

### 渲染沙箱
- `render_activity` 是全机唯一的转换闸门：按优先级 0/1/2 排队，同级按用户轮转，队列上限 `render_queue_limit`，截止 `render_queue_wait_seconds`。
- `acquire_render` 依次尝试同用户 `idle`、未绑定的 `standby`、冷启动；standby 由 `_replenish_render` 在空闲且近期有文件活动时补充，失败按 2→60 秒退避。
- standby 预热时通过私有 LibreOffice listener 做一次最小转换，并记录 tmpfs 基线；`office_listener=False` 的 provider 跳过 listener。
- Worker 发布结果后调 `POST /internal/render-finish`；`finish_render` 只在成功、健康检查通过（清理工作目录和 tmp、listener 存活、RSS 低于内存上限的 70%、任务数与寿命未超限）时把租约放回 `idle`，只留给同一用户；任一失败则删除，脏沙箱不回到池。
- Work 内的 `view_workspace_file` 用 Run 自己的租约，懒启动独立 profile/pipe 的 listener（120 秒闲置或 RSS 超限退出），不复用文件库预览结果。

### 镜像与办公技能
- `vendor/office-suite/upstream` 是只读的上游快照，适配只改 `patches/series.json` 与对应补丁；`build.py` 生成 `images/sbx-office/skills` 和可选 runtime。
- 同一份技能树进两个镜像：沙箱的 `/opt/skills`（`images/sbx-office/Dockerfile`）和平台的 `/opt/ailab/skills`（`platform/deploy/Dockerfile`）；平台侧 `settings.skills_path` 默认指向仓库里的同一目录。
- 镜像构建固定 Debian snapshot、Python hash 锁和 Node runtime；构建时跑 `skill_index.py --check --requirements`，Agent 首次 `load_skill` 时 Broker 用同一脚本对租约再做一次证明。
- `images/sbx-document-reader` 与 `settings.document_sandbox_image` 当前没有调用点；系统文档读取走 render 租约。

### 受控联网
- 沙箱始终无网络：`download_url` 由 Agent 取回、扫描后经 `put_fetched` 流式写入；`fetch_repo`/`fetch_packages` 也是 Agent 取包后在沙箱内解包或离线安装。
- `platform/ailab_platform/egress.py` 不服务沙箱，只给 `read_url` 的抓取容器做唯一出口：只允许 CONNECT 443 和绝对形式 HTTP 80，每次 DNS 解析都必须是公网地址，并连接到已检查的地址。
- 代价是 npm、apt、私有仓库和需要认证的 API 在沙箱内不可用；页面脚本也拿不到沙箱的路由。

### 后台命令
- `bash` 启动、`bash_wait` 等待、`bash_kill` 终止；句柄由 run_id、调用标识和命令派生，恢复后等待同一执行而不是再启动一次。
- 等待窗口是检查点（默认 30 秒、上限 300 秒），每次 Broker 调用最多 30 秒；硬期限由沙箱内 supervisor 按 `run_max_seconds` 执行，没人轮询也会杀掉进程组。
- helper 按内容哈希安装到 `/tmp/ailab-background/helper-<digest>.py`，每次调用校验 sha256；并发上限 `exec_background_max_jobs`，日志环形缓冲 `exec_background_log_bytes`。

## 不变量

- Broker 是唯一持有 provider 句柄的服务；Agent 的所有操作只按 `lease_id` 寻址，响应里的 `sandbox_id` 只是展示，不能用来选择或直连沙箱。
- 同一 owner 只允许一个 Broker；第二个进程拒绝启动，丢锁的 Broker 被 fence。钉住它的测试：`platform/tests/faults/test_broker_recovery.py::test_second_live_broker_refuses_to_start`。
- 资源在可被发现前就带 `ailab.owner`、`ailab.lease_id`、`ailab.run_id` 标签；Redis 丢失后带完整标签的 active 资源可以重建租约。钉住它的测试：`platform/tests/faults/test_broker_recovery.py::test_redis_flush_rebuilds_from_labels_including_renewed_expiry`。
- 租约记录在确认删除前不会过期消失；`releasing` 仍占容量；删除失败不是成功。钉住它的测试：`platform/tests/faults/test_broker_recovery.py::test_delete_failure_retains_ownership_and_retries_with_backoff`。
- 所有路径先 `normalize_path` 到 `/workspace`，交付再收紧到 `/workspace/outputs`；链接一律拒绝。钉住它的测试：`platform/tests/test_broker_lease.py::test_path_traversal_is_refused_on_every_file_operation`。
- 大小限制在传输中强制执行，不信任 `stat`；超限返回 413 或 `TransferLimitError`。钉住它的测试：`platform/tests/test_broker_streaming.py::test_delivery_enforces_limit_even_if_stat_is_stale`。
- `exec` 在所有后端都是 bash，输出有界，超时是第二道边界。钉住它的测试：`platform/tests/test_background_exec.py::test_model_commands_run_in_bash_like_every_sandbox_backend`。
- 沙箱无网络、无宿主目录、无平台凭据；`mock` 不是隔离边界，secure profile 拒绝它。钉住它的测试：`platform/tests/contracts/test_sandbox_network.py::test_sandbox_cannot_initiate_tcp_udp_or_icmp`。
- 渲染沙箱失败不复用；render 租约在 Broker 重启后不认领。钉住它的测试：`platform/tests/test_render_standby.py::test_failed_cleanup_never_returns_render_to_idle`。
- 工作区快照永远不含 `inputs/` 和 `.ailab/`；快照与恢复只经流式传输，不把整包读进内存。钉住流式路径的测试：`platform/tests/test_broker_streaming.py::test_snapshot_restore_uses_only_streams`。

## 怎么扩展

### 加一个在沙箱里执行的工具或脚本
1. 在 `platform/ailab_platform/agent/tools.py` 加入 `SANDBOX_TOOLS`、工具定义和 `execute_tool` 分支；会改文件时同步 `platform/ailab_platform/agent/loop.py` 的 `FILE_TOOLS` 与 `PREVIEW_AFTER`。
2. 脚本放 `platform/ailab_platform/sandbox_tools/`，调用前用 `put_file` 拷到 `/workspace/.ailab/`（参考 `platform/ailab_platform/broker/service.py::_render_preview`）；不要假设沙箱能联网或安装依赖。
3. 会写临时文件的，先在 `LeaseManager.register_scratch` 登记 `/workspace/.ailab/<kind>-<32 hex>`，`finally` 调 `clear_scratch`；否则崩溃后没有清理依据，文件会留在租约里直到沙箱销毁。
4. 输出必须是沙箱内的普通文件，经 `get_file`、`commit` 或受控 exec 返回；不要把不可信内容拼进宿主命令。
5. 测试：`platform/tests/test_broker_lease.py` 的路径与大小用例、`platform/tests/test_sandbox_paths.py`；真实 provider 用 `platform/tests/contracts/test_sandbox_transfer.py`。

### 更新沙箱镜像或办公技能
1. 改 `vendor/office-suite/upstream` 的适配必须改有序补丁 `vendor/office-suite/patches/series.json` 与对应 JSON，不能只改生成树。
2. 从仓库根运行 `platform/.venv/bin/python vendor/office-suite/build.py` 重建 `images/sbx-office/skills`（同时重写 `index.json` 和 `platform/ailab_platform/agent/office_guides_v2.json`）；改 runtime 时加 `--runtime-output vendor/office-suite/runtime`，再按顺序执行 `npm ci --ignore-scripts && npm run build`。
3. 用 `platform/scripts/check_skills.py` 核对索引，并断言镜像与平台两份 `skill_index.py` 一致。
4. 重建 `sbx-office:lite` 镜像（`deploy.sh` 按内容哈希判断是否过期，或从仓库根 `docker build -f images/sbx-office/Dockerfile .`），同时重建平台镜像，因为同一份技能也打进 `/opt/ailab/skills`。
5. `load_skill` 会先让 Broker 在租约里跑 `skill_index.py --check --requirements`，索引与平台不一致返回 409；镜像和 Agent 必须同批发布。
6. 测试：`platform/tests/test_base_office_skills.py`、`platform/tests/test_office_skill_checks.py`、`platform/tests/contracts/test_office_production.py`。

### 调整租约或资源限额
1. 单机预算在 `platform/ailab_platform/config.py::Settings` 的 `max_sandboxes`、`sandbox_total_memory_bytes`、`sandbox_render_reserved_slots`、`sandbox_render_memory_bytes`；`validate_broker_limits` 要求 render 预留至少留一个通用槽和内存。
2. 默认 spec 在 `LeaseManager.default_spec`；用户额度与爆发水位在 `AdmissionPolicy.effective_user_limit`。
3. Docker 的 cpu/memory/pids/tmpfs 在 `DockerSandbox.acquire`；OpenSandbox 的资源在 `OpenSandboxProvider.acquire`；k3s 的 overlay 上限在 `k3s/runsc-options.toml`，沙箱 CPU 在 `platform/deploy/overlays/lite/compatibility.yaml`。
4. 改上限要同时核对 `platform/.env.example`、`platform/deploy/base/config.yaml` 和 lite overlay；新增上限必须能在 provider 侧执行，否则只是声明。
5. 测试：`platform/tests/test_sandbox_reclaim.py`、`platform/tests/faults/test_broker_recovery.py` 的准入与预留用例。

### 接一种新的沙箱后端
1. `platform/ailab_platform/sandbox/base.py::SandboxProvider` 就是 provider 端口；实现它并放到 `platform/ailab_platform/sandbox/<name>.py`：生命周期、`inspect`（只有确认不存在才返回 `None`）、`list_owned`、`update_metadata`、`renew`、`exec`（bash）、文件流、`release`、`health`；按需覆盖 `cancel_exec`、`reap_expired`、`office_listener`。
2. 在 `platform/ailab_platform/sandbox/__init__.py::get_provider` 注册，并把 `platform/ailab_platform/config.py::Settings` 中 `sandbox_provider` 的 `Literal` 加上新值；工厂对未知后端直接抛错，不回落 mock。
3. 在 `Settings.validate_secrets` 加安全门禁：secure profile 拒绝无隔离后端；若隔离委托给集群，必须有显式的已验证标志（参考 `osb_kubernetes_isolation_verified`）。
4. 复用 `platform/ailab_platform/sandbox/base.py::lines_script` 与 `file_size` 的路径规则；新后端不能放宽 `/workspace` 限制。
5. 测试：`platform/tests/test_sandbox_lifecycle_adapters.py` 的适配器用例；真实后端加进 `platform/tests/contracts/test_sandbox_lifecycle.py`、`test_sandbox_transfer.py`、`test_sandbox_network.py` 的参数化。

## 配置

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_SANDBOX_PROVIDER` | `mock`/`docker`/`opensandbox` | `mock` |
| `AILAB_SANDBOX_IMAGE` | 沙箱镜像 | `harbor.ailab.local/sandbox/sbx-office:lite` |
| `AILAB_SANDBOX_RUNTIME` | Docker 运行时；`runsc` 才是 gVisor | `runc` |
| `AILAB_SANDBOX_TTL_SECONDS` | 单次租约期限 | `1200` |
| `AILAB_SANDBOX_MAX_LIFETIME_SECONDS` | 租约绝对上限 | `14400` |
| `AILAB_MAX_SANDBOXES` | 单机沙箱总数（含 render 预留） | `2` |
| `AILAB_SANDBOX_TOTAL_MEMORY_BYTES` | 沙箱总内存预算 | `3 GiB` |
| `AILAB_SANDBOX_MAX_PER_USER` | 每用户硬上限（受爆发水位约束） | `3` |
| `AILAB_SANDBOX_BURST_WATERMARK` | 通用池超过此占用率后每人只保留一个沙箱 | `0.8` |
| `AILAB_SANDBOX_PREEMPT_UTILIZATION` | 允许回收他人沙箱的池占用率 | `0.95` |
| `AILAB_SANDBOX_EVICT_IDLE_SECONDS` | 可回收前的闲置时间 | `300` |
| `AILAB_SANDBOX_QUEUE_LIMIT` | 等待队列上限 | `32` |
| `AILAB_SANDBOX_WAIT_SECONDS` | 一次领取最多等待多久 | `300` |
| `AILAB_SANDBOX_RENDER_RESERVED_SLOTS` | render 预留槽 | `1` |
| `AILAB_SANDBOX_RENDER_MEMORY_BYTES` | render 预留内存 | `768 MiB` |
| `AILAB_SANDBOX_RENDER_MAX_TTL_SECONDS` | 单个 render 任务期限 | `120` |
| `AILAB_RENDER_PREWARM` | standby 预热数量；0 关闭 | `1` |
| `AILAB_RENDER_REUSE_MAX_JOBS` | 同一 render 沙箱最多处理的任务数 | `50` |
| `AILAB_MAX_SANDBOX_READ_BYTES` | 单次读取上限 | `10 MiB` |
| `AILAB_MAX_SANDBOX_WRITE_BYTES` | 单次写入上限 | `20 MiB` |
| `AILAB_MAX_ARTIFACT_BYTES` | 单个交付件上限 | `100 MiB` |
| `AILAB_MAX_WORKSPACE_BYTES` | 跨轮工作区快照上限 | `60 MiB` |
| `AILAB_EXEC_BACKGROUND_MAX_JOBS` | 每个租约的后台命令并发 | `8` |
| `AILAB_OSB_NETWORK_MODE` | `sidecar` 用 deny-all 策略；`kubernetes` 依赖命名空间 | `sidecar` |

完整清单见 `platform/ailab_platform/config.py::Settings`。

## 相关决策

- [ADR 0003 Broker exec 代理与可恢复租约](../architecture/adr/0003-broker-exec-proxy-and-leases.md)：Broker 独占沙箱边界、租约状态与错误映射。
- [ADR 0020 弹性沙箱额度与闲置回收](../architecture/adr/0020-elastic-sandbox-allowance-and-idle-reclaim.md)：每用户弹性额度、先保存再回收。
- [ADR 0032 预热渲染沙箱](../architecture/adr/0032-prewarmed-single-use-render-sandboxes.md)（“单次使用”部分已被取代）与[渲染沙箱生命周期](../architecture/design/2026-10-31-render-sandbox-lifecycle.md)：预热、按用户复用、槽位与失败不复用。
- [ADR 0007 受控公网访问](../architecture/adr/0007-controlled-public-fetch.md)：沙箱保持无网络，公开字节由 Agent 获取、Broker 注入。
- [ADR 0021 隔离浏览器渲染](../architecture/adr/0021-isolated-browser-rendering-for-read-url.md)：`egress.py` 只服务抓取容器的出网。
- [ADR 0004 自研 Runtime 与平台检查点](../architecture/adr/0004-self-developed-runtime.md)：租约丢失后的中断与续跑。
- [ADR 0023 系统文档读取](../architecture/adr/0023-system-document-reader-and-concurrent-capabilities.md)：短生命周期 render 租约承担系统解析。

## 已知的坑

见 [HANDOFF · 沙箱](../../HANDOFF.MD#沙箱)。

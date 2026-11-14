# API

平台 API（`platform/ailab_platform/api`）是业务数据库的唯一写入方：身份与授权、Run 生命周期、文件元数据、交付件记录、配额与用量账本、Run 事件、会话/分叉/定时/搜索，以及管理接口。它不执行用户代码（沙箱）、不解析不可信文件（Worker）、不跑交互式 Agent 循环（Agent）；API 进程内只做摘要、记忆整理和技能草稿等受计费的后台模型调用。

## 代码地图

| 要改的东西 | 位置 |
|---|---|
| 服务入口、路由注册、后台循环 | `platform/ailab_platform/api/main.py::app`、`platform/ailab_platform/api/main.py::lifespan` |
| 公开 Run 接口（创建、取消、事件流、预览） | `platform/ailab_platform/api/routes_runs.py::router`、`platform/ailab_platform/api/routes_runs.py::start_run` |
| Run 事件与 SSE | `platform/ailab_platform/events.py::append_event`、`platform/ailab_platform/api/routes_runs.py::stream_events` |
| 会话、轮次、分叉 | `platform/ailab_platform/api/routes_conversations.py::router`、`platform/ailab_platform/api/routes_conversations.py::fork_conversation` |
| 内部 Run/用量/交付件/文件接口 | `platform/ailab_platform/api/routes_internal.py::router` |
| 持久任务 claim/续租/ACK | `platform/ailab_platform/api/jobs.py::stage_job`、`claim_job`、`ack_job` |
| outbox 投递循环 | `platform/ailab_platform/api/preview_dispatch.py::dispatch_forever`、`dispatch_preview_jobs` |
| 服务身份与 scope 表 | `platform/ailab_platform/service_auth.py::SERVICE_SCOPES`、`require_scope`、`assert_internal_scopes` |
| 用户认证 | `platform/ailab_platform/deps.py::current_user`、`platform/ailab_platform/security.py::authenticate`、`platform/ailab_platform/accounts.py::issue_session` |
| Portal 同源代理 | `platform/portal/app/api/v1/[...path]/route.ts::proxy` |
| ORM 模型 | `platform/ailab_platform/models.py::Run`、`Conversation`、`FileObject`、`RunEvent`、`RunAttempt`、`RunDelivery`、`UsageReservation`、`ModelUsage`、`OutboxMessage` |
| 迁移执行与版本发现 | `platform/ailab_platform/migrate.py::run_migrations`、`platform/ailab_platform/migrations/versions/` |
| Run 状态机 | `platform/ailab_platform/run_state.py::transition_run`、`platform/ailab_platform/contracts/jobs.py::RunStatus` |
| Run 暂停、checkpoint、续跑 | `platform/ailab_platform/api/routes_runtime.py::pause_run`、`queue_continuation`、`resume_run` |
| 执行围栏（attempt + 用户锁） | `platform/ailab_platform/api/runtime_fence.py::fenced_run`、`fence_attempt` |
| 配额预留与结算 | `platform/ailab_platform/quota.py::reserve_usage`、`settle_usage`、`usage_summary`、`expire_reservations` |
| 计价公式 | `platform/ailab_platform/domain/ledger.py::weighted_amount`、`reserved_amount` |
| 定时任务 | `platform/ailab_platform/api/routes_schedules.py::dispatch_schedules`、`dispatch_task`、`run_schedules` |
| 全局搜索 | `platform/ailab_platform/api/routes_search.py::search_conversations`、`platform/ailab_platform/search_index.py::run_search`、`platform/ailab_platform/api/search_index.py::index_forever` |
| 管理接口与权限判定 | `platform/ailab_platform/api/routes_admin.py::router`、`platform/ailab_platform/domain/authorization.py::policy` |
| 审计 | `platform/ailab_platform/audit.py::record_audit`、`AuditedRoute` |
| Portal 侧 API 类型 | `platform/portal/lib/api.ts` |

## 关键流程

### 一次浏览器请求的鉴权路径
1. Portal 的登录路由调用 `POST /v1/auth/login`，拿到 session token 后写入 httpOnly cookie（`platform/ailab_platform/accounts.py::issue_session`）。
2. 浏览器只请求 `/api/v1/*`；`platform/portal/app/api/v1/[...path]/route.ts::proxy` 只转发到上游 `/v1/*`，从 cookie 取出 token 并加 `Authorization: Bearer`，所以 `/internal/*` 从浏览器不可达。
3. `platform/ailab_platform/deps.py::current_user` 调用 `security.authenticate`：只按 token 的 SHA-256 查找 `ApiToken`，拒绝已撤销、已过期和已停用的用户。token 只接受 Authorization 头，不接受查询串。
4. 内部请求走 `X-Service-Token`：`platform/ailab_platform/service_auth.py::CredentialAuthenticator` 对每个服务的摘要做 constant-time 比较，返回该服务的 scope 集合。`/internal` 路由分散在 `platform/ailab_platform/api/routes_internal.py` 与文件、文档、OCR、任务等子路由，统一由 `platform/ailab_platform/api/main.py::app` 注册；每个路由必须有 `Depends(require_scope(...))`，`assert_internal_scopes` 在启动时检查遗漏。

### 创建一次 Run
1. `platform/ailab_platform/api/routes_runs.py::start_run` 在 `user_critical_section` 内先处理 `client_send_id`：同一用户、同一内容摘要的重试返回原 Run；同 ID 不同内容返回 409。去重先于就绪、配额和并发检查，保证丢响应后可重试。
2. `validate_run_input` 校验提示词长度、模型是否可用、技能、模板、附件状态与文档输入计划，并调用 `quota.enforce_token_quota` 做准入。
3. 在同一事务里检查活跃 Run 上限、会话的 `active_run_id` 与 `branch_revision`、同一会话只能有一轮在跑、路径最多 500 轮。
4. 写 `Run`、文档来源绑定、`append_event(..., commit=False)` 和 `stage_job(db, "run", ...)`；全部在一个事务里提交。提交后 outbox 唤醒投递循环，不重新入队。

### Run 状态机
1. 合法迁移表是 `platform/ailab_platform/run_state.py::ALLOWED`；`_transition_run` 用 `WHERE id = ? AND status = 旧状态` 做 compare-and-swap，竞争时只有一个写入者赢。
2. 终态是最终状态；迟到的 `succeeded` 不能覆盖用户的 `canceled`，重复写同一状态是 no-op。
3. 副作用与状态变化同事务：取消未完成的 `ClientCall`、给 `RunCheckpoint` 设过期时间、保留/释放 web 图片引用、追加 `run.*` 事件、更新指标、通知 inbox。
4. Agent 只能通过 `POST /internal/runs/{run_id}/transition` 写 `running|succeeded|failed|canceled`；`waiting_user|interrupted` 走各自带 checkpoint/问题的专用路径，`queued` 只由 API 创建。

### outbox 与幂等
1. `stage_job` 只向 `outbox` 表插入 `OutboxMessage`，不直接发 Redis；业务行和任务行在同一事务提交（`platform/ailab_platform/api/jobs.py` 的 after_flush/after_commit 钩子只负责唤醒）。
2. `dispatch_preview_jobs` 按 `next_attempt_at`、`needs_attention` 领取一批，发布到 `queue.job_topic` 对应的 Redis 流；发布失败指数退避，超过 `outbox_max_attempts` 置 `needs_attention` 等人工处理。
3. 消费方用 `POST /internal/jobs/{job_id}/claim` 拿带过期时间的 `claim_token`，用 `renew` 续租，完成后 `ack` 才删除 outbox 行；`release` 清空 claim 并按 `retry_after_seconds` 重投。所有写入都要匹配 token 和过期时间，旧执行者不能复活。
4. 幂等键按真实工作单元区分：公开发送用 `client_send_id`，模型调用用 `call_id`，交付/工具调用用 `X-Delivery-Id`（落 `RunDelivery`），上传用 `client_upload_id`（落 `UploadReceipt`）。不要用 Run ID 去重整轮重试。

### 配额与账本
1. 每次模型调用前由 Agent 调 `POST /internal/usage/reserve`（scope `usage:reserve`，只有 Agent 有），`quota.reserve_usage` 在用户锁内校验 Run 仍活跃、`attempt_id` 仍持有租约、模型别名在目录中可用，再写 `UsageReservation`。
2. 调用后 `POST /internal/usage/settle` 写一条 `ModelUsage`，把实际 token、后端和金额固定下来；同一 `call_id` 的第一次结算生效，重试不会改账。
3. 预留时冻结 `weight`、`cached_token_weight`、`policy_version` 和整数 milli 金额；预留在 `usage_reservation_ttl_seconds` 后过期，reaper 每批最多把 200 条转为 `unreconciled`，晚到的结算只修正这一条。
4. `GET /v1/usage` 用一条 SQL 同时读账本和未结算预留，避免结算瞬间重复计数。完整字段、公式、错误码和运维边界见 [用量账本契约](../architecture/usage-ledger.md)。

### 会话、分叉、定时、搜索、管理
- 会话的每个用户消息是一轮 Run；`_start_turn` 复用 `start_run`，同一会话只能有一轮在跑（409）。
- 分叉只复制最多 500 个已结束轮次、派生对象和同模式 checkpoint，不复制账本或文件字节；模型可见字节不能因换会话 ID 而改变，详见 [ADR 0041](../architecture/adr/0041-fork-reuses-checkpoint.md)。
- 定时任务每用户最多 5 条，用 `next_run_at` 的 CAS 领取；到点那一轮与 Run/outbox 同事务写入。会话忙时按 1 分钟重试、最多顺延 2 小时，详见 [ADR 0040](../architecture/adr/0040-schedule-in-conversation.md)。
- 全局搜索按会话维护词项并集：SQLite 用 FTS5，PostgreSQL 用触发器维护的 tsvector + GIN；候选会话再精确扫描，因此一次请求搜完整个账号，没有游标，详见 [ADR 0039](../architecture/adr/0039-global-search-index.md)。
- 管理接口在 `/v1/admin/*`：用户、部门、配额策略、审计、按用户/部门/模型的用量、文件元数据、记忆整理状态和上下文设置。权限由 `policy.can` 的能力表判定；`dept_admin` 的能力还必须把 SQL 限定在自己部门，`auditor` 只能看审计和用量。

## 不变量

- 业务数据库只有平台 API 写；Agent、Broker、Worker 只能走 `/internal`，不得导入 `platform/ailab_platform/models.py` 或持有数据库连接。
- 身份和归属永远从已认证主体或资源行推导，绝不接受 Agent/模型传来的 `user_id`；文件读取先按 `run_id`/`job` 绑定 `RunContext`，再检查状态与归属。
- 每个 `/internal` 路由都有 scope；`platform/tests/test_service_identity.py::test_all_service_internal_routes_are_scoped` 会拦住新增的未鉴权端点。
- Run 状态只能经 `transition_run` 改变，终态不可再变；迟到写入不能覆盖取消（`platform/tests/test_run_state.py::test_late_success_cannot_overwrite_a_cancel`）。
- 业务变更、outbox 任务和 Run 事件在同一事务提交；失败一起回滚（`platform/tests/test_transactional_jobs.py::test_business_creation_and_outbox_roll_back_together`）。
- 同一 `call_id` 的结算可重试且第一次结算生效；预留重试在同一 attempt 仍持有租约时返回原预留。价格输入在写入时冻结，之后改 `models.toml` 不重算历史（`platform/tests/test_usage_ledger.py::test_changing_a_weight_later_does_not_rewrite_past_calls`）。
- SQLite 部署只能有一个 API 写进程；多副本写 PostgreSQL。`platform/ailab_platform/db.py` 的用户锁在 SQLite 上没有数据库级保护。
- 列表接口必须有上限或游标；大表上的新查询要建索引并在 SQLite 与 PostgreSQL 上验证执行计划。

## 怎么扩展

### 加一个公开 API
1. 在 `platform/ailab_platform/api/routes_*.py` 加端点，沿用 `/v1` 前缀和 `current_user`；Run 用 `_get_owned_run`，会话用 `_owned`，其他资源按行上的 `user_id` 检查。
2. 在 `platform/ailab_platform/api/main.py` 的 `include_router` 注册；请求/响应模型放 `platform/ailab_platform/schemas.py` 或 `platform/ailab_platform/contracts/`。
3. 同步改 `platform/portal/lib/api.ts` 的类型和调用，否则 Portal 的 `tsc` 与运行时字段会对不上。
4. 加测试到 `platform/tests/`，至少覆盖未登录、他人资源、正常路径；有写操作时验证与 outbox/事件同事务。

### 加一个内部端点
1. 路由必须放在 `/internal` 下，并显式依赖 `Depends(require_scope("<scope>"))`；需要的 scope 先加进 `platform/ailab_platform/service_auth.py::SERVICE_SCOPES` 对应服务。
2. 用 `X-Service-Token` 认证；资源必须绑定 `run_id`/`file_id`/`job_id`，Run 写入用 `fenced_run`，文件读取用 `_bound_file`。
3. 新增 scope 或路由后跑 `platform/tests/test_service_identity.py::test_all_service_internal_routes_are_scoped`。

### 加表或加列
1. 改 `platform/ailab_platform/models.py`；迁移放 `platform/ailab_platform/migrations/versions/NNNN_slug.py`，`revision` 是顺序号，`down_revision` 指向前一个，`upgrade` 和 `downgrade` 都要写。
2. SQLite 加列用普通 `ALTER TABLE`，不要重建被其他表引用的表；需要放宽 NOT NULL 时只对叶子表用 batch 模式（参考 `platform/ailab_platform/migrations/versions/0018_file_usage.py`）。
3. `platform/tests/test_migrations.py::test_migrations_and_models_do_not_drift` 检查模型与迁移漂移；再在 `AILAB_TEST_POSTGRES_URL` 下跑同一测试，确认两种数据库都能升、降、再升。

### 加一个 outbox 任务
1. 在 `platform/ailab_platform/contracts/jobs.py::JobEnvelope` 的 `kind` 字面量里加类型，并在 `JobEnvelope.resource` 校验里说明它绑定哪个资源；在 `platform/ailab_platform/queue.py::job_topic` 决定投递到哪个流。
2. 用 `stage_job(db, "<kind>", ...)` 在业务事务里写 outbox，绝不在提交后手动 enqueue。
3. 消费方实现 `claim → renew → ack`；失败用 `release` 而不是直接删除。`platform/tests/test_transactional_jobs.py` 覆盖重复投递、崩溃租约和 scope 隔离。

### 加一项配置
1. 在 `platform/ailab_platform/config.py::Settings` 加字段并把枚举/范围写进 `Field`；秘密值同时加进 `DEFAULT_SECRETS`。
2. 若它影响安全默认，在 `Settings.validate_secrets` 的 `secure` 分支加拒绝条件；不安全的默认值不能只在部署文档里说明。
3. 同步 `platform/deploy/` 的环境变量与 `docs/modules/deploy.md`；有行为开关时在 `platform/tests/test_service_identity.py` 覆盖 secure 拒绝路径。

### 加一个新的列表查询
1. 参数用 `limit`（带 `ge/le`）或 `(created_at, id)` 这类稳定游标，禁止无界 `all()`。
2. 在 `platform/ailab_platform/models.py` 的 `__table_args__` 里建匹配过滤和排序的索引，并写迁移。
3. 加一个 `EXPLAIN` 测试（参考 `platform/tests/test_migrations.py::test_template_and_slides_queries_use_indexes`），SQLite 与 PostgreSQL 都要看计划。

## 配置

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_DATABASE_URL` | 业务数据库 | `sqlite:///<repo>/platform/var/platform.db` |
| `AILAB_PROFILE` | `dev`/`test`/`secure`；`secure` 启用不安全的默认值拒绝 | 未设时按 `AILAB_ENV` 推导 |
| `AILAB_SERVICE_NAME` / `AILAB_SERVICE_CREDENTIAL` | 本进程的服务身份与原始凭据 | `api` / 空 |
| `AILAB_SERVICE_CREDENTIALS` | 五个服务身份的 SHA-256 验证摘要 | `{}` |
| `AILAB_MAX_UPLOAD_BYTES` | 请求体与上传上限 | 50 MiB |
| `AILAB_MAX_PROMPT_CHARS` | 单轮提示词上限 | 8000 |
| `AILAB_DAILY_TOKEN_LIMIT` / `AILAB_MONTHLY_TOKEN_LIMIT` | 每用户加权 token 配额 | 2,000,000 / 30,000,000 |
| `AILAB_LIBRARY_QUOTA_BYTES` | 每用户资料库配额 | 20 GiB |
| `AILAB_MAX_ACTIVE_RUNS_PER_USER` | 同时活跃 Run 上限 | 2 |
| `AILAB_QUOTA_TIMEZONE` | 日/月配额边界的时区 | `UTC` |
| `AILAB_USAGE_RESERVATION_TTL_SECONDS` | 预留过期时间 | 3600 |
| `AILAB_SESSION_TTL_SECONDS` | 登录会话有效期 | 8 小时 |
| `AILAB_JOB_LEASE_SECONDS` | Run 任务 claim 租约 | 300 |

完整清单与校验在 `platform/ailab_platform/config.py`。

## 相关决策

- [ADR 0001 单节点范围与默认值](../architecture/adr/0001-single-node-scope.md)：用户 token 配额是硬限制，单 Run token 预算是软限制，周期按 `quota_timezone` 切分。
- [ADR 0002 服务身份与请求绑定](../architecture/adr/0002-service-identity.md)：每服务独立凭据、声明式 scope，以及按 Run/文件资源绑定的内部调用。
- [ADR 0008 长 Run 预算与可续租约](../architecture/adr/0008-long-run-budgets-and-leases.md)：job claim/续租、checkpoint 与预算暂停；状态机之外的长时间执行合同。
- [ADR 0039 全量对话搜索用会话级词项索引](../architecture/adr/0039-global-search-index.md)：为什么搜索没有游标、索引如何按数据库实现。
- [ADR 0040 定时任务绑定一个会话](../architecture/adr/0040-schedule-in-conversation.md)：任务与会话、Run、outbox 的绑定关系。
- [ADR 0041 同模式分叉沿用被分叉轮次的上下文](../architecture/adr/0041-fork-reuses-checkpoint.md)：分叉复制什么、为什么不能改写模型可见字节。

## 已知的坑

见 [HANDOFF · API](../../HANDOFF.MD#api)。

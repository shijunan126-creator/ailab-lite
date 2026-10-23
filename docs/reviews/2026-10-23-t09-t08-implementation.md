# T09/T08 实现与接续接口

日期：2026-10-23。独立工作树 `/Users/michael/.t3/worktrees/ailab-lite/t3-4466c212`，分支 `t3/transactional-queue-replay`，干净起点 `a8843a4bb0c475b39982e06d2b4dd7abd1daf342`（含 `origin/main@6410fe9`）。没有采用被停止 Gemini 任务的未提交修改，没有推送、PR 或部署。测试基线是该起点加本报告同提交的代码变更。按主任务要求，仅在 docs/reviews 记录本子任务，不更新全局 current/roadmap/HANDOFF/status-matrix。

## 行为与边界

- Run 创建、普通上传、预签名上传完成、产物缩略图、资料库预览、缩略图回填均通过 API 的 `stage_job` 在业务事务内写入 outbox。回填 Worker 不再直接读业务 DB 或直接发布队列，改为调用 API 的有界 keyset 批次接口；原 CLI 的 Redis 配置要求保留。
- Run 创建事件、终态状态事件与业务状态同事务；artifact.created 和产物/缩略图意图同事务。新增 transition 接口可把状态和调用者事件合并；Agent 原循环的 agent.started 等信息事件仍独立、尽力写入，T07 接续时应改为使用 transition 事件数组。
- 发布后保留 outbox；消费者先从 API CAS 领取，成功完成业务后先 ACK API，再 ACK 传输。有效领取期间不重复发布；未领取的未 ACK 工作每 60 秒可重放。Redis 的 job→stream-ID 哈希及 XADD 使用 Lua 原子去重，ACK 的 XACK/XDEL/哈希删除同脚本；旧 ACK 不删除新的重放映射。Memory 是测试用同语义实现（去重扫描现存条目，非生产性能路径）。
- API 领取按业务状态跳过终态 Run、完成/耗尽/已删文件及过期预览；Run attempt 编号来自业务库，传输 delivery 次数不消耗 Run 尝试额度。发布失败指数退避至 300 秒，默认 10 次后保留 needs_attention=1，日志只记录异常类型，避免保存连接串/秘密。人工修复依赖后，在受控数据库事务中清零 attempts/needs_attention 并将 next_attempt_at 调回当前时间；本轮没有新增运维 UI。
- API 派发每秒一批，最多 50 条，有 5 秒批次预算（单个外部 I/O 仍可能延长至自身超时）；Redis leader lock TTL 60 秒，PG `FOR UPDATE SKIP LOCKED`。SQLite 单 API 进程线程锁；派发提交使用原 next_attempt_at/claim_token/lease 状态 CAS，防止旧快照覆盖消费者刚获得的长 lease。
- 两条 Run 流 `${run_queue}:chat`、`${run_queue}:work`，默认固定分区为 Chat 1 槽、Work 2 槽、总信号量 3。不相互借位，不在 Agent 增加会话锁；同会话串行及每用户额度仍由现有 API 准入实现。消息在容量内领取，无无限预取/任务堆积。Worker 仍单线程。
- SIGTERM 先取消消费池，再让所有活动 Run 的既有取消/沙箱释放逻辑运行；随后 API release、传输 ACK。API release 失败则保留 durable lease；到期重新领取。drain_seconds 默认 20 秒；超过期限再次取消，后台 gather 注册结果收集器，避免无人领取异常。无法强制终止忽略取消的任意 Python/外部依赖；此时依赖进程退出及 lease 到期，而不是提前承诺工作已交还。

## 0014 与锁契约

迁移链为 `0013 → 0014`，不占用 0015/0016/0017/0018/0019。新增 outbox 的 attempts、next_attempt_at、last_error、claimed_until、claim_token、deliveries、needs_attention；新增 run_attempts（run_id 外键 ON DELETE CASCADE；run_id/number 唯一）。索引 `ix_outbox_due(needs_attention,next_attempt_at,id)`、`ix_outbox_created_at(created_at,id)`。

升级时保留旧预览 job_id，转换成新信封；从旧 queued/running Run 和待处理文件一次性补入 durable 意图。downgrade 保留 outbox 行、移除新字段/attempt 表，再升级不会重复 Run 意图。测试包括带旧数据的升级/降级/再升级及 ORM 漂移。部署必须协调停止旧生产者/消费者再升级和启动新消费者：本轮没有验证新旧 Agent 混跑。旧 runs 单流不会被新调度器消费；旧业务行由迁移补意图，新双流为正式接口。

API claim 在对应资源的 user_critical_section 内检查状态、刷新 ORM 与写入过期元数据，PG 用户行锁串行多副本；SQLite 仍是单 API 进程的进程内锁。Worker 不写 DB，只通过已有同锁 PATCH 合并 meta。缩略图回填也使用用户锁，避免与产物登记的固定 thumbnail job_id 发生 PK 竞争。transition 的可选 attempt 检查持有 outbox 行锁到状态/事件事务完成。ACK 与 release 以 token CAS，旧 token 返回 409。

## T07 接续（尚未实现完整业务写入栅栏）

信封定义见 `platform/ailab_platform/contracts/jobs.py::JobEnvelope`：

```json
{"job_id":"run_x","schema_version":1,"kind":"run","run_id":"run_x","file_id":null,"mode":"work","attempt":1,"deadline":"2026-10-24T00:00:00Z","enqueued_at":"2026-10-23T00:00:00Z","request_id":null,"traceparent":null}
```

kind 为 run/file/preview/thumbnail；run_id/file_id 二选一，deadline 和 enqueued_at 必须带时区。job_id 即 outbox 主键；当前初始 Run 的 job_id=run_id，缩略图为 `thumb_{file_id}`，其余随机。attempt 是发布初始值；执行的权威 attempt 来自 claim 返回值，不能凭 Redis deliveries 生成栅栏。

1. `POST /internal/jobs/{job_id}/claim {consumer}`：Agent 需要 jobs:run，Worker 需要 jobs:file。返回 `{state:"claimed",token,attempt,attempt_id}`，或 `{state:"busy"|"done"}`。Run 的 token=attempt_id，并写 run_attempts（id/run_id/number/agent_instance/started_at/ended_at/outcome）。旧未结束 attempt 在重新领取时结束为 abandoned。Run lease 为 `run_timeout_seconds + drain_seconds + 60`，Worker lease 为 660 秒。该接口就是当前领取与 attempt 分配入口，不要再独立领取一次。
2. `POST /internal/jobs/{job_id}/ack {token}`：有效 token 且未过期才删除 durable 意图，重复 ACK 已删除行返回 200；新 attempt 或新一轮同 job_id 的旧 ACK 返回 409。消费者须先确认业务已提交；API ACK 本身并不代替 checkpoint/工具交付提交。当前 Agent wrapper 会复查业务终态后才 ACK；T07 waiting_user/interrupted 的 ACK 条件需一起扩展。
3. `POST /internal/jobs/{job_id}/release {token}`：CAS 清除领取并使意图立即到期，下一轮派发领取；RunAttempt 结束为 released。先 release 后 ACK Redis，失败不删 durable row。该接口不重置 Run 的 status（T07 尚无 interrupted）。
4. `POST /internal/runs/{run_id}/transition {status,summary?,error?,result?,events:[{type,payload}],attempt_id?}`：状态及事件一次事务，状态未变化时不重复追加附带事件。传 attempt_id 时校验当前 job token 和截止时间，过期返回 409 stale_attempt。原 `/status` 保留兼容；正式 clients.set_run_status 已调用 /transition，`clients.transition_run` 可传完整事件与 attempt_id。
5. 续跑时在同一个 API 业务事务调用 `stage_job(db,"run",run_id=...,mode=...)`；原 job 应先被 ACK 才能建立新的业务工作单位。ask_user/answer/checkpoint/lease heartbeat 与新增运行状态均留给 T07。

**未完成的栅栏范围必须由 T07 补齐**：当前 AgentLoop.process 仍仅接收 attempt 数字，模型调用的 usage attempt_id 仍沿用原循环生成；claim 返回的 attempt_id 尚未穿透 events、artifacts、usage、文件读取/交付、checkpoint 等写入。/transition 的 attempt_id 目前可选，legacy /status/events 等端点也未强制它。当前实现保证 durable job 的有效 lease 内只有一个领取者，以及状态终态后重复投递跳过；**不能据此宣称不可重放工具、交付或整个 Run 已具备端到端 at-most-once**。lease 失效后旧进程仍活着的全部写入拒绝，需要 T07 统一的 attempt 校验和运行时协议。

## T16 接入

- `stage_job` 从 `obs.traceparent_var.get()` 读取（常量尚不存在时兼容 None）；Agent/Worker 消费时设置并在退出时恢复 ContextVar。没有修改 obs.py。
- Agent 成功 claim 后用信封 enqueued_at 记录 `metrics.QUEUE_WAIT_SECONDS.labels(mode).observe(seconds)`；mode 由 JobEnvelope 限定为 chat/work，负值截断 0。常量缺失时不记录，不阻塞消费。
- Worker 在扫描失败持久标记确认后调用 `metrics.SCAN_EXHAUSTED.inc()`；业务状态 skip 与 durable ACK 防止重放重复计数，回归验证计数前标记已提交、重复投递后仍为 1。该进程内 Counter 与 DB 标记无法跨 SIGKILL 原子提交，窗口内可能少计，不是财务账本。
- outbox 最老 created_at 查询有新增索引；COUNT 仍随未 ACK 行数线性，沿主 Agent 的 collector 成本说明。没有修改 metrics.py。

## 验证

当前最终代码的验收结果（不是历史基线结果）：

- `cd platform; ruff check .; ruff format --check .`：通过，204 个 Python 文件格式一致；`git diff --check` 通过。
- SQLite + 临时真实 Redis 8.10.2：完整 Python 套件 **751 passed / 44 skipped**，116.72 秒。
- 临时 PostgreSQL 16.15（两个一次性数据库）+ 真实 Redis：完整 Python 套件 **779 passed / 16 skipped**，129.65 秒。迁移升级/降级/模型漂移与双库索引 EXPLAIN 均包含在内；SQLite 专属 CAS 竞态在 PG 跳过，因为 PG 行锁在提交前阻止另一个 claim。
- Portal：`npm ci`、`npm test`（**11 passed**）、`npm run build` 均通过，Portal 源码无变更。
- 专项红绿、真实 SIGKILL/SIGTERM、Redis 丢失重放与 60 秒负载见下文。最终完整套件包含专项回归，未另拿局部测试替代全套。
- 两个数据库与 Redis 均为本轮自行创建的临时服务，验收后关闭；没有使用开发数据库或 .env。最终完整日志为本机 `/tmp/t09-accept-sqlite.log`、`/tmp/t09-accept-pg.log`，Portal 日志 `/tmp/t09-portal-{tests,build}.log`；这些绝对路径不是仓库交付依赖。

跳过项为未配置的外部组件（真实 ClamAV/Keycloak/S3/Docker/OpenSandbox）、Memory 上的 Redis 专用扫描项、SQLite 套件的 PostgreSQL 专项，以及 PG 上的 SQLite 并发快照项。Python 套件共同产生一条既有 Starlette/httpx 弃用警告。

新增回归与故障验证包括：业务事务回滚时不遗留 Run；发布依赖故障；提交后 SIGKILL；真实 Redis stream/consumer-group/pending 丢失后的同身份重放；重复发布有界；并发 claim 仅一胜者；旧 ACK/release 拒绝；release 和 lease 过期后的 attempt 递增；终态业务去重；发布退避与人工状态；状态/附带事件回滚一致；扫描耗尽计数防重；完整三槽调度器实际 SIGTERM；慢取消超时收集；派发快照与 claim 并发；文件过期与并发 meta PATCH；双回填与产物 thumbnail 身份一致。

红绿证据：修复前 Run 发布故障返回 503（应 201 并保留意图），重放模块不存在；之后额外独立撤掉三项修复并分别运行对应回归：派发 CAS 撤掉时 next_attempt_at 被短重放时间覆盖；锁内 refresh 撤掉时并发 meta.other_writer 丢失；传输重复发布去重撤掉时队列从 1 条膨胀为 10 条。三项均真实失败，恢复代码后重新验收。日志在本机 /tmp/t09-red-{dispatcher-cas,file-meta-refresh,queue-dedup}.log，非仓库交付依赖。

SQLite 与 PostgreSQL 的 10,000 条未来 outbox 合成数据 EXPLAIN 均断言 due/oldest 命中相应索引。负载脚本 `platform/tests/load/agent_scheduler.py`：两个 60 秒慢 Work + 100 个持续 Chat，真实内部 HTTP client pool + 替身 runtime，峰值活动任务 3，Chat 排队 p95 **0.0525 秒**，RSS 从 **81.625 MiB 到 91.6875 MiB（+10.0625 MiB）**。这是 macOS 单进程调度开销，不是 2CPU/8GiB 验证机生产模型/工具/文件上下文的 RSS 验收；CPU/磁盘成本尚未专项量化。无新增第三方依赖，新增成本为每 job durable insert/delete、claim/ACK HTTP 与索引更新、Redis 身份哈希；派发空闲每秒一次有界查询。

## 可复用陷阱与未覆盖项

- SQLite 的 SELECT 不等于持有写锁，不能在读快照后直接 ORM 覆盖消费者的新 lease 调度时间；必须用 CAS。
- 内部业务状态读到的 FileObject 必须在用户锁内 refresh；外层已经读取的 ORM identity map 会把并发 meta 新字段覆盖掉。
- SIGTERM 只测 handle_message 不等于调度器排空；必须实际占满各池并对进程发信号。取消线程中的 dequeue 并不会终止底层线程，当前阻塞 dequeue 上限 1 秒；SIGKILL 依赖 durable lease。
- 故障测试显式驱动 dispatcher tick，避免后台 tick 抢先发布造成“应派发 1 条却返回 0”的竞态假失败；PG SKIP LOCKED 与 SQLite CAS 分别验证。
- Redis Lua ACK 现在有两个 key（stream 与 job 映射），旧原子脚本测试的参数断言需同步；实际 Redis 合同才验证旧 ACK 不伤新映射。
- Python 测试只使用 conftest 临时 DB 与本轮自建临时 PG/Redis；不读取开发 .env。外部 S3/ClamAV/OIDC/Docker/OpenSandbox、浏览器 E2E、支付模型、真实部署容量及跨主分支集成后的完整测试均未在本子任务验收。
- 主分支 F8/T14/T16 尚未进入本 worktree，主 Agent 必须审查 cherry-pick 与后续 0016→0014 链；本子任务不修改 llm.py、会话详情 GET、Portal 或 Broker/sandbox 实现。

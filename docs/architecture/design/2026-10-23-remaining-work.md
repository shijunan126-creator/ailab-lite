# 剩余工作设计（T07 / T08 / T09 / T14 / T15 / T16 / T19 / T20）

2026-10-23。基线 `feat/arch-hardening`。当前状态见 [status-matrix](../status-matrix.md)，范围与默认决策见 [ADR 0001](../adr/0001-single-node-scope.md)。本文只做设计，不含实现。

## 0. 先确认目标

用户可感知的三个问题和一个运维问题，决定了这批工作的顺序：

1. **长任务不可靠**：Agent 重启、Broker 重启或模型断流，Work 要么从头重跑，要么直接失败。重跑会重复不可重放的副作用。→ T09、T07
2. **一个慢 Work 卡住 Chat**：Agent 串行处理，Chat 要排在 15 分钟的 Work 后面。→ T08
3. **长对话变笨、变贵、偶尔失败**：历史固定截取最近 12 轮；单一模型出错就整轮失败。→ T14、T15
4. **出了问题看不见**：没有首字时延、排队时间、积压、未对账等信号，也没有告警。→ T16

T19（OCR/多模态）和 T20（Skills 治理）是能力扩展。T20 的业务效果依赖公司模板与评测样本这两项外部输入（ADR 0001）。

总原则（AGENTS.md）：

- 副作用经 outbox 与业务变更同事务写入。
- 幂等键就是真实的工作单位。
- 恢复时不重复已确认的交付或不可重放的工具。
- 不引入还没有具体行为需要的框架或开关。

## 1. 依赖与顺序

```
阶段 1  T09 outbox 全面化 ──┐
        T08 Agent 并发     ─┤（共用新的队列信封）
阶段 2  T07 运行时 checkpoint / ask_user / 续跑 / 租约心跳   ← 依赖 T09 的 attempt 与信封
阶段 3  T14 模型网关与上下文      T15 会话分页（可并行）
阶段 4  T16 可观测性与负载基线    F8 余项：Broker 流式注入（可并行）
阶段 5  T19 OCR / 多模态（依赖 T14 能力声明）   T20 Skills 治理（依赖外部模板与样本）
```

| 阶段 | 估算（单人有效日） | 迁移 |
|---|---:|---|
| 1 | 4–6 | 0014 outbox 重试列 + run_attempts |
| 2 | 7–10 | 0015 run_checkpoints + 新状态 |
| 3 | 5–7 | 0016 conversations.summary |
| 4 | 4–6 | — |
| 5 | 6–9 | 0017 skill_policies |

## 2. T09 · outbox 全面化

**现状**：`outbox` 表和派发器只承载资料库预览入队。开始 Run、上传完成后的扫描入队、产物缩略图入队仍是“提交后再入队”。进程在两步之间退出时，靠 reaper 的“孤儿重新入队”兜底。这是事后补救，不是契约。

**设计**：

- `OutboxMessage` 增加 `attempts`、`next_attempt_at`、`last_error`，以及 `(next_attempt_at, id)` 索引。所有入队改为与业务行同事务写 outbox：`start_run`、`complete_upload`、普通上传、产物登记、预览（已是）、续跑（T07）。
- **派发器**仍在 API 进程，单实例由 Redis 锁保证（沿用 lifecycle 的锁模式）：
  - PostgreSQL 用 `FOR UPDATE SKIP LOCKED`，SQLite 单进程直接读；
  - 每批 50 条，失败指数退避，超过 N 次进入“需人工”状态并报警（T16）。
- **队列信封**统一为 `contracts/jobs.py`：

  ```
  {job_id, schema_version: 1, kind: run|file|preview|thumbnail, run_id|file_id, attempt, deadline, request_id, traceparent}
  ```

  `job_id` 就是 outbox id。消费者凭业务状态 CAS 去重（Run 的 attempt 栅栏见 T07，文件按 status/job），重复投递无害。
- **事件**：RunEvent 本身就在业务库里，`transition_run(commit=False)` 已能与状态同事务。剩下的工作是让 Agent 写状态与写事件合并为一次内部调用（`POST /internal/runs/{id}/transition {status, events[]}`），消灭“状态已改、事件没写”的窗口。
- 删除 reaper 中“找没有消息的 queued Run/文件再入队”的逻辑，以 outbox 为唯一来源。Redis 被清空时，从 outbox 中尚未 ACK 的 job 重建；为此派发后不立即删行，消费者 ACK 时回调 `/internal/jobs/{id}/ack` 删除。

**故障契约与验收**（`tests/faults/`）：

- DB 提交后、入队前进程被杀 → 派发器补发。
- Redis 清空 → 未 ACK 的 job 被重新派发。
- 同一 job 投递两次 → 只执行一次。
- 派发持续失败 → 退避，到上限后报警。
- 每条测试都要在撤掉修复时失败。

**成本**：每次开始 Run 多一行 insert 和一次 delete；派发器空闲时每秒 1 次索引查询。

## 3. T08 · Agent 有界并发与 Chat 保留槽

**现状**：`consume_forever` 逐条 `await` 完整 Run，用户并发上限 2 并不等于 Agent 实际并发 2。

**设计**：

- 两条流：`runs:chat` 与 `runs:work`，按 Run 的 mode 由 outbox 派发到对应流。
- Agent 内两个消费池共享一个总信号量：
  - `agent_concurrency=3`，其中 `chat_reserved_slots=1` 只给 Chat；
  - Work 最多占 `agent_concurrency - chat_reserved_slots`；
  - Chat 空闲时不借用 Work 位，以简单为先。
- 同会话串行：API 已在创建时拒绝同一会话的并发 Run（409），保持不变，不在 Agent 侧再加锁。
- 每用户公平：用 T13 的 `max_concurrent_runs` 在 API 准入。沙箱容量仍由 Broker 准入独立控制（已完成），等沙箱的 Work 只占 Agent 的一个 asyncio 任务，不占 CPU。
- SIGTERM：所有在跑任务按现有交还逻辑交回，等待上限 `drain_seconds`。

**验收**：

- 负载脚本（见 T16）在 1 个慢 Work（模型替身延迟 60 s）加持续 Chat 下，Chat 排队 P95 < 1 s。
- 并发 3 时 Agent RSS 增量写入 status-matrix。

## 4. T07 · 运行时、checkpoint、ask_user、续跑

### 4.1 运行时边界

保留自研循环。按审查建议放到 `ports/runtime.py`：

```
AgentRuntime.start(run) / resume(run, checkpoint) / cancel(run) / snapshot(run)
```

当前循环是第一个适配器。目标文档里的 LangGraph/deepagents 以后作为另一个适配器接入。checkpoint 的格式归平台所有，不依赖框架私有状态。这一点写入 ADR 0004。

### 4.2 状态与尝试

- `RunStatus` 增加 `waiting_user`、`interrupted`。`waiting_sandbox` 仍只用事件表达，因为它不影响谁能写这个 Run。
- 允许的转移：
  - `running → waiting_user → queued → running`
  - `running → interrupted → queued`（续跑）
  - `interrupted` 超时后 → `canceled`
  - 以及原有转移。
- **`run_attempts`** 表：`(id, run_id, number, agent_instance, started_at, ended_at, outcome)`。Agent 每次领取 Run 先 `POST /internal/runs/{id}/attempts` 拿到 `attempt_id`，之后所有内部写入都带上它（状态、事件、checkpoint、交付、usage 已有 attempt_id）。API 拒绝过期 attempt 的写入（409 `stale_attempt`）。这道**栅栏**防止被认为死掉、其实还活着的旧 Agent 继续写。

### 4.3 checkpoint

- **`run_checkpoints`** 表：`(run_id, attempt_id, step, state_key, plan JSON, tool_log JSON, deliveries JSON, runtime_version, created_at)`。
  - 完整消息状态（messages 游标与压缩后的历史）以 gzip JSON 存入对象存储 `checkpoints/{run_id}/{step}.json.gz`，行里只记 key，避免业务库膨胀。
  - 每个 Run 只保留最近两个 checkpoint；Run 终态后 7 天清理（lifecycle 新增一类）。
- 写入时机：每个模型响应及其工具结果之后。`PUT /internal/runs/{id}/checkpoint` 对 `step` 做 CAS，旧 step 被拒绝。
- **工具重放策略**在工具声明中给出：

  | 工具 | 重放策略 |
  |---|---|
  | `read_file`、`list_files`、`load_skill` | 可重放 |
  | `write_file` | 可重放，内容覆盖幂等 |
  | `deliver_file` | 以 `delivery_id = run_id + tool_call_id` 幂等，已确认交付直接返回原结果 |
  | `exec` | 不可重放 |

  续跑时，已有结果的调用直接复用 checkpoint 中的结果。“已开始、无结果”的调用：可重放的重新执行；不可重放的给模型一条工具结果：“此命令在中断前可能已部分执行，未重跑；请先检查工作区”，由模型决定。
- **工作区**：checkpoint 不打包工作区，开销太大。只在三种时刻打包，并去抖 60 s：写文件类工具之后、`ask_user` 之前、交付之后。续跑时用最近的工作区快照恢复，与 checkpoint 的 step 对齐记录。若快照落后于 checkpoint，按上面的不可重放规则告知模型。
- **租约心跳**：Agent 持有租约期间每 60 s 调 Broker renew。租约丢失（410 `lease_lost`/`lease_expired`）时：可恢复则写 checkpoint 并转 `interrupted`，自动续跑一次，再次失败才停在 `interrupted` 等用户。之后删除 `sandbox_ttl_seconds > run_timeout_seconds` 的临时配置约束。

### 4.4 ask_user 与计划

以下契约已在 Portal 实现，后端照此提供：

| 事件 / 接口 | 载荷 |
|---|---|
| `agent.ask_user` | `{question_id, question, options[], allow_text}` |
| `agent.ask_answered` | `{question_id, answer}` |
| `plan.updated` | `{steps: [{id, title, status: pending|running|done|skipped|failed}]}` |
| `run.resumed` | `{from_step}` |
| `POST /v1/runs/{id}/answer` | `{question_id, answer}` → 202 |
| `POST /v1/runs/{id}/resume` | → 200 Run |
| `Run.resumable` | bool |

- `ask_user(question, options?, allow_text?)` 工具的流程：
  1. Agent 写 checkpoint，打包工作区，释放沙箱；
  2. 发 `agent.ask_user`，状态转 `waiting_user`，ACK 队列消息，退出。不占 Agent 槽，也不占沙箱。
- `POST /answer`：在同一事务里记录 `agent.ask_answered` 事件、状态转 `queued`、写 outbox 续跑 job。重复回答同一 `question_id` 返回 409。
- 等待超时设置为 `ask_user_timeout_hours`（默认 72）。超时后转 `interrupted`，用户仍可回答并续跑。
- `update_plan(steps)` 工具：写 checkpoint 的 plan，并发 `plan.updated`。Chat 模式不暴露 `ask_user`/`update_plan`。

**验收**：

- 每个工具步骤后杀掉 Agent，续跑后结果一致；不可重放工具不重复执行；已确认交付不重复。
- 旧 attempt 的写入被拒绝。
- `ask_user → 回答 → 完成` 的浏览器 E2E（Portal 侧已就绪）。
- 等待用户期间不占沙箱和 Agent 槽。

## 5. T14 · 模型网关与上下文

- **拆分 `llm.py`**：`ModelPolicy` 负责选择与降级，`ModelTransport` 负责 HTTP。
  - models.toml 每个别名增加 `fallback = ["alias-b"]`、`context_tokens`、`max_output_tokens`、`capabilities = ["tools", "vision"]`。
  - 选择时检查能力，例如有工具调用就不降级到无工具模型。
- **超时分层**：connect 5 s；首字 `ttft_timeout_seconds`（默认 30）；流中空闲 60 s；总时长 `llm_total_timeout_seconds`。现有单一 `llm_timeout_seconds` 拆分后保留为总时长的默认值。
- **降级规则**：只在没有任何输出之前降级，包括首字超时、429/5xx、连接失败；已经输出就不重播（现有原则）。
  - 每次尝试都是独立的 `call_id` 与预留，账本记录实际后端；
  - 发 `agent.model_fallback {from, to, reason}`（Portal 已支持显示）；
  - 降级用量计入同一额度池，按目标模型的倍率计费（ADR 0001）。
- **上下文预算**：去掉固定的 `HISTORY_TURNS=12`，改为按 token 预算组装：系统提示 + 技能 + 附件摘录 + 历史 ≤ `context_tokens × 0.7`，其余留给输出与工具结果。
  - token 估算用可插拔估算器：默认按字符类别启发式（中文约 1 字 1 token，拉丁约 4 字符 1 token），并用上游返回的 `prompt_tokens` 持续校准每个别名的系数。不引入重量级 tokenizer 依赖。
- **摘要记忆**：超出预算时，把较早的轮次压缩成会话摘要，存入 `conversations.summary` 与 `summary_through_run_id`（迁移 0016）。
  - 由最便宜的可用别名生成，这次调用有自己的 call_id 并照常计费；
  - 发 `agent.context_compacted {turns}`；
  - 摘要在新轮次超出预算时增量更新，不每轮重算。
- **不新增 messages 表**：一个 Run 就是一轮。摘要加 checkpoint 已覆盖需要；等出现“一轮多条用户消息”这种具体需求再加。写入 ADR 0005。

**验收**：

- 可控的慢上游与坏上游替身：首字超时降级、已输出不降级、429 降级、全部失败给出明确错误。
- 账本记录实际后端。
- 长会话（200 轮）请求体不超过预算，且摘要被使用。

## 6. T15 · 会话详情分页

- `GET /v1/conversations/{id}?before=<run_id>&limit=20` 返回最近一页的轮次，加上 `has_more`。
  - 每轮只带展示所需字段；结果大字段、事件仍按需加载（现状已如此）。
  - 预览列表只取最近一页涉及的文件。
- 索引 `runs(conversation_id, created_at, id)` 已存在（0010 的 `ix_runs_conversation_id_id`，需加 `created_at` 或按 id 排序，用 EXPLAIN 定）。
- Portal：滚动到顶部时加载更早的轮次，保持滚动位置；切换会话时取消请求（已有乱序保护）。

**验收**：500 轮会话首屏 API p95 < 150 ms（SQLite 与 PG 各测）。浏览器 E2E 覆盖上翻加载。

## 7. T16 · 可观测性与负载基线

- **Trace**：OpenTelemetry SDK 可选启用（`otel_endpoint` 为空即不加载导出器，lite 默认关闭）。
  - 采样 5%，错误全采；
  - span 覆盖：HTTP 请求、队列等待、模型调用（属性含 TTFT、别名、实际后端）、工具执行、沙箱申请、文件渲染；
  - `traceparent` 经内部 HTTP 头和队列信封传播（T09 的信封已预留字段）。
- **指标**（低基数，不按用户/Run/文件打标签）：

  | 指标 | 标签 |
  |---|---|
  | `ailab_model_ttft_seconds` | model |
  | `ailab_queue_wait_seconds` | mode |
  | `ailab_outbox_backlog`、`ailab_outbox_oldest_seconds` | — |
  | `ailab_usage_unreconciled` | — |
  | `ailab_storage_bytes` | kind = library/derived/previews/workspaces/trash |
  | `ailab_run_resumes_total` | outcome |
  | `ailab_lease_heartbeat_failures_total` | — |

- **看板与告警**：`platform/deploy/observability/` 放 Grafana 看板 JSON 与 PrometheusRule。告警条件：
  - outbox 最老 > 5 分钟；
  - 未对账 > 0 持续 1 小时；
  - Chat 排队 p95 > 5 s；
  - clamd 不可用；
  - Pod OOMKilled；
  - 磁盘 > 80%；
  - 扫描耗尽的文件数增长。
- **负载基线**：`platform/tests/load/` 用纯 asyncio 脚本，不引入 locust，配合可配置延迟的模型替身。场景：
  - 混合队列；
  - 50 MB 上传；
  - 100 MB 交付；
  - 20 MB Office 解析；
  - 多路 SSE + 上传 + 下载同时进行。

  每个场景记录 p95 与各 Pod 峰值 RSS。门槛写进 status-matrix，在 2 vCPU / 8 GB 的验证机（colima `ailab-verify` 配置）上跑。
- **成本**：未启用 OTel 时为零。启用时按采样比例实测 CPU 后再定。

## 8. F8 余项 · Broker 流式注入

Broker 目前从 API 取整份 bytes，再 put 到沙箱（最多 50 MB，Pod 上限 384 MiB）。

改为：API 的流 → Broker 临时文件（有大小上限，按块写）→ provider 的 `put_file_from_path` 按块上传。Docker 用 tar 流，OpenSandbox 用 multipart 流。交付与工作区快照反向同理。

验收：内存峰值与文件大小无关，以验证机上的 RSS 曲线为证据。

## 9. T19 · OCR 与多模态

- **能力声明**来自 T14 的 `capabilities`。`ocr_alias` 设置指向一个支持 vision 的别名；未配置时 OCR 显式不可用，不伪装成功。
- **Worker OCR 任务**：图片，以及每页可提取文字少于阈值的 PDF（扫描件）。
  - PDF 页面先经 Broker 渲染保留槽出图（复用资料库预览渲染），再按页批量调用 vision 模型，结果写回 `text_key`，meta 标 `ocr: done|failed|unavailable`。
  - **计费**：预留与结算扩展出 `subject = file_job`，计入文件所有者额度，mode 记为 `ocr`。账本的 `run_id` 改为可空，并新增 `file_id`，属于迁移的一部分。
  - 超长文档分块，单文件页数上限可配。
- **对话多模态**：所选模型支持 vision 时，图片附件以 image 内容部件发送；否则使用 OCR 文本；两者都没有时，明确告诉模型“图片未能读取”。

**验收**：扫描件 PDF 与手机照片产生可读文本。vision 模型不可用时显示“不可用”，而不是空文本。用量计入正确的人。

## 10. T20 · Skills 治理

- **manifest**：每个技能目录一个 `skill.toml`，包含 `name, version, description, license, requires`（镜像中需要的 Python 包或命令）`, roles`（可用角色，空表示全部）。
  - 镜像构建时生成 `skills/index.json`，包含每个技能目录的内容哈希；CI 校验 manifest 与目录一致。
- **一致性**：Broker 启动时读取沙箱镜像里的 index，经 `/internal/skills/index` 交给 API。Agent 加载技能时比对哈希，不一致则拒绝并报警。这防止“界面上的技能”和“镜像里的技能”漂移。
- **策略**：`skill_policies` 表（迁移 0017）。
  - 平台管理员可全局或按部门禁用内置技能；
  - 个人技能策略为 `allowed | review | disabled`，选 `review` 时管理台出现待审队列。
  - 管理台加一个“技能”页签。
- **评测**：`platform/tests/golden/` 每个任务一个目录：提示词、输入文件、检查器。
  - 检查器打开产物做断言：页数、字体、文字溢出（借助渲染出图后检测越界）、公式错误。
  - 夜间用真实模型跑，费用可控，报告进 CI 产物；回归即阻止发布。
  - 公司模板与首批样本到位后填充（ADR 0001）。

## 11. 不做的事

保持 ADR 0001 的范围：

- 不做多机调度、跨节点租约、15 分钟闲置沙箱复用；
- 不做部门额度池；
- 不建分布式事件总线。

本设计里每个新端口（runtime、gateway policy、scanner/OCR 能力）都对应已存在的第二种实现或已确定的目标实现，没有空开关。

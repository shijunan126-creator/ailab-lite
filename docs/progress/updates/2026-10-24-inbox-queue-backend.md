# 2026-10-24 Inbox / Queue / Steer 后端 B

范围：`feat/inbox-queue`，起点 `00c5cc9`（父功能分支基于 `main@74e66c0`）。仅后端；不修改 Portal，不 push/PR/deploy。代码验证基线 `9992308e7a1a636a7888676a0f79fddc93d6341d`（包含实现提交 `fa763ad`）；随后仅补记录，不改产品代码。契约见[工作区设计 §3/§4 与 B 补充](../../reviews/2026-10-24-workspace-features-design.md)。

## 实现

- 0026（临时 `down_revision=0024`）：通知、排队表及索引；会话持久 `queue_paused`，双向迁移不重建既有表。父任务集成时改接0025。
- 所有 Run 状态变更的 `transition_run` 统一钩子，与终态事件/通知/队列派发同事务。刷新前先 flush 调用方待写状态（尤其 ask_user 的 question/runtime_meta），避免覆盖。用户锁改为可重入，使既有事务入口和统一状态入口共享锁；PG 用户行锁跨进程持有至提交。
- 通知 helper `ailab_platform.api.inbox.create_notification(db, user_id, kind, title, conversation_id=None, run_id=None, share_id=None)` 供 C 使用，不自行 commit；内部取得用户锁并在插入事务裁剪到200。普通任务长于配置阈值才通知；计划任务启动时冻结 `options.schedule_id`；ask_user 每个 Run 一次，即使通知已裁剪；canceled 不通知。
- 通知接口按用户授权、keyset分页≤50、UTC时间；read 的 ids≤200/all/conversation_id 三种选择器互斥；unread 查部分索引计数+最新通知时间。
- Queue 的创建/编辑复用真实启动准入检查（模型、附件、技能、额度），不抢占正在执行的并发槽；最多5 queued。成功活动叶事务内按序启动一条，复用 `start_run(locked=True, commit=False)` 与 `stage_job`；重复终态不二次启动。预期准入失败用 savepoint 后暂停，基础设施异常回滚。失败/取消、重试/编辑、切换活动叶暂停；继续显式启动下一条；会话/项目删除取消 queued。
- Steer 仅 running 原子领取，Agent scope+run/attempt fence。领取记录可重投到 checkpoint 确认，checkpoint IDs、消息与事件同事务，重启不重复。等待用户回答不领取；未领取的 steer 随普通队列启动。下一轮两条历史读取路径都在原 prompt 后加入 steer，分页历史含额外字符预算。分叉复制已确认的steer历史并重映射事件ID；不复制待处理queue。会话GC按既有batch预算先清queue，再删会话，避免新外键阻塞回收。

## API 交接

公开 DTO 保持设计中的 Notification / QueuedMessage，ConversationDetail 增 queue 与 queue_paused。`queue/resume` → QueuedMessage（state=started），失败409；删除 queue 为204。claim → `{items: QueuedMessage[]}`，state/heartbeat 增 `steer_pending: bool`。checkpoint state 增累计 `steer_ids`；事件 `agent.steered {queued_id,prompt}`。共享 DTO/事件在 `contracts/inbox.py`，没有 ORM/HTTP import。没有需要修改的现有五服务 E2E scripted-agent 精确心跳断言。

## 验证

最终基线 `9992308`：Ruff check / format --check通过（303文件）；SQLite全套 **1057 passed / 114 skipped**（147.59s）；PG+Redis全套 **1138 passed / 33 skipped**（186.86s）。两套均无失败，包含0026 up/down/up、最新ORM迁移漂移、SQLite/PG EXPLAIN与三项fault测试；本分支通知/队列/fault模块收集22项并全部通过。跳过为未配置的真实外部组件/另一数据库等既有条件。警告为既有Starlette/httpx弃用及SQLite表达式索引反射限制（该索引有独立结构断言），并非失败。所有测试先由 tests/conftest.py 设置独立目录、隔离模型和不存在的 ENV_FILE；不读取开发 `.env`。临时本机 PostgreSQL 16.15 / Redis 8.10.2 使用25432/26379，独立数据库，测试结束已停止两个实例；Docker daemon 本轮不可用，未以 Docker 验收替代声明。

新增回归包括通知阈值/定时来源/裁剪/分页/归属/已读/等待去重、队列准入/数量/编辑删除/暂停继续/路径变化、事务故障回滚、Agent权限与attempt绑定、checkpoint事件和两条历史路径；faults 三项覆盖并发终态重复、并发queue POST/完成、真实独立Agent进程claim后SIGKILL并重认领恢复。

初轮错误与修复：未给macOS测试加GNU工具路径造成stat失败（环境）；独立状态轮询改变取消时序并升级短暂API失败（产品回归，已改为复用旧轮询）；旧迁移集成测试硬编码0024（升级最新head后比较ORM漂移，保留旧链往返断言）。这些初轮失败不计作最终通过。

## 成本（短时本机测量）

每项30次，通知测量前已种200条；通知测helper加commit，其他测TestClient完整HTTP请求（含认证/序列化）；dispatch测原Run完成并启动一条，claim测领取一条。毫秒，p50 / p95：

| 操作 | SQLite | 本机PG | SQL次数（SQLite/PG） |
|---|---:|---:|---:|
| 通知 insert + prune + commit | 0.98 / 1.18 | 1.50 / 1.66 | 2 / 3 |
| `/notifications/unread` | 4.28 / 5.82 | 4.78 / 5.31 | 4 / 4（含认证；业务2条） |
| completion + dispatch | 11.46 / 13.96 | 20.12 / 27.18 | 35 / 37 |
| steer claim | 5.92 / 10.15 | 8.67 / 15.71 | 10 / 11.2 |

通知裁剪是insert+单条DELETE，其子查询按user/created/id取最近200；PG额外用户行锁。unread按部分索引最多200项计数，latest按user/created/id倒序limit1，业务不载入title/prompt。dispatch新增最早queued索引查询与一次既有start_run；无新后台任务。claim更新state/run_id后返回未确认消息，已确认ID不再返回；平时状态轮询只查pending ID，普通checkpoint没有新steer时不读队列表。SQL数量包含既有认证、fence、事件序列、额度查询和savepoint，不是全部新增开销。无高基数指标。

测量期间进程峰值RSS SQLite132.5MiB / PG134.6MiB（含API/TestClient/测试导入，不是增量RSS或五服务总量）；CPU均值通知1.02/0.94ms、unread4.53/3.92ms、dispatch11.44/16.01ms、claim7.36/6.97ms（PG服务器CPU不在Python进程计量内）。SQLite整份测试DB0.88MiB；PG本轮通知含索引168KiB、队列表144KiB（200条通知、60条排队，含30steered/30started；空页/分配粒度使其不能推算逐行成本）。无新依赖、常驻任务、模型调用或无条件额外Agent HTTP轮询。

EXPLAIN测试覆盖通知列表/最新/裁剪候选/未读、等待通知查找、queue FIFO/steer候选、run已领取列表；SQLite实际选索引，PG小空表测试用 `enable_seqscan=off` 验索引路径存在，不冒称实际大表规划或目标机吞吐。

## 剩余与集成责任

- 父任务审核合入B，改迁移链0026←0025，同步集成链断言，再做A/B/C+Portal统一双库/E2E/浏览器验收。`routes_conversations.py` 的queue详情、分叉历史、删除与版本切换暂停，以及 `routes_projects.py` 的项目删除queue取消，与A的改动需保留合并；`routes_model_context.py` 的history steers与A bootstrap改动需保留。C接上述helper，并在其分享事务内commit。
- 未跑五服务E2E、真实付费模型、Portal build/浏览器（本任务明确由父任务集成），未外部验收、未合入main、未部署。单机短测不证明长期容量、跨API进程SQLite部署或网络分区表现。SIGKILL证明claim/checkpoint窗口恢复；没有穷举每个CPU指令或所有对象存储故障。

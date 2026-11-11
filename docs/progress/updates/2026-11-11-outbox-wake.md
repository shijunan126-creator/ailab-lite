# Outbox 提交即派发（2026-11-11）

分支 `perf/outbox-wake`，base `main@70e04e57`。

## 做了什么

- 问题：Run 创建后要等 `dispatch_forever` 每秒一次的轮询才被推进 Redis，实测 `run.created → agent.started` 有 0.5–1.2 秒固定等待；模型思考的首字延迟（2–4.5 秒）不受影响。
- 现在 outbox 插入随事务提交后立即唤醒派发循环，轮询保留为兜底。
- 触发点在会话层而不是各个接口：`after_flush` 发现本事务新增了 `OutboxMessage` 就记一个标记，`after_commit` 再调 `wake_dispatch()`。Run、文件、预览、OCR、文档任务和直接写入 `OutboxMessage` 的路径都覆盖，后续新增入口不用再接线。
- savepoint 内的提交不会提前唤醒：`after_commit` 在保存点结束时同样触发，但此时行对派发器不可见；`session.in_nested_transaction()` 为真时跳过，由外层事务提交唤醒。
- `dispatch_forever` 改为等一个 `asyncio.Event`，超时仍是 1 秒；`reconcile()`（文件依赖巡检）保持原来的 1 秒节拍，不会因唤醒变密。
- 测试侧：提交即派发后，原本“创建 job 再手动派发并断言返回值”的用例会与后台派发竞争；`test_library_summaries` 改为显式驱动发布（停用后台派发），与 `test_library`、`faults/test_transactional_jobs` 的做法一致。
- 可靠性契约不变：同事务落库、失败退避重试、Redis 按 `job_id` 去重、Agent `claim`/ACK 都未改；唤醒只是缩短等待，不是新的投递通道。

## 验证

代码基线为本分支工作树（`platform` 目录，SQLite 测试库）：

- `ruff check .`、`ruff format --check .` 通过。
- 新增 `tests/test_outbox_wake.py` 5 项通过，覆盖：提交唤醒、回滚不唤醒、savepoint 等外层提交、无绑定循环时唤醒为空操作、唤醒先于轮询、Run 创建在轮询间隔设为 30 秒时仍立即入队。
- 红证据：把 `wake_dispatch` 改成空操作后，`test_run_creation_is_published_without_waiting_for_the_poll` 失败；恢复后通过。
- 相关模块回归 232 项通过 / 7 跳过（`test_outbox_wake`、`test_transactional_jobs`、`faults/test_transactional_jobs`、`test_file_previews`、`test_eager_previews`、`test_library`、`test_conversations`、`test_chat_fork_retry`、`test_inbox_queue`、`test_document_view`、`faults/test_document_view`、`faults/test_long_run_recovery`、`test_queue`、`test_library_summaries`、`test_ocr`）。

- 完整双库、E2E 与 Portal build 交 CI（AGENTS §9 不在本地重复全套）。[PR #185](https://github.com/shijunan126-creator/ailab-lite/pull/185) 11 项 CI 全绿（最终 head 见 PR）：SQLite/PostgreSQL 全套、三片 E2E、quality、crawler、shell、storage-contracts。首次 postgres 失败是 `tests/faults/test_broker_recovery.py::test_reserved_render_slot_survives_full_general_pool_and_queue[redis]` 的 0.1 秒墙钟断言（实际 `queue_deadline`、期望 `queue_full`），不经过本次改动的 API/outbox 路径，重跑通过。之后仅文档更新。

已合并：用户要求合并，PR #185 squash 为 `27ed82cd`，提交树与 head `39d85b02` 一致；未部署。

## 没覆盖到 / 风险

- 唤醒是进程内的：多 API 副本时，请求所在进程与派发器不在同一进程就等 1 秒轮询；当前单进程部署无影响，行为最多和改动前一样。
- 每次提交可能多跑一轮派发查询；批量上传时一秒钟可能多几轮，单轮是索引查询加 Redis 入队，未做单独压测。
- 未改模型调用亲和度、思考档位或首字延迟；这轮只去掉了派发等待。
- 本机部署与真实模型验收未做（未授权部署）。

# 同模式分叉沿用 checkpoint（2026-11-12）

分支 `feat/fork-reuse-checkpoint`，base `main@cf758dd1`。决定见 [ADR 0041](../../architecture/adr/0041-fork-reuses-checkpoint.md)。

## 做了什么

- 问题：分叉只复制轮次记录，分支第一轮找不到父快照，从数据库把历史重建成纯文本问答（工具过程全丢），又换了供应商会话头。真模型实测分支第一次调用 `cached_tokens = 0`。
- API：分叉事务在同模式时读取被分叉轮次的完成快照，只保留下一轮会读的字段，截图引用和 `context_sources` 换成分支副本，正文里的源 run ID 记为 `run_aliases`，写成分支叶子轮自己的 checkpoint（`fork_context.fork_checkpoint` / `store_fork_checkpoint`）。复制是可选优化：读不到、超限、存储写失败或分叉剩余时间不足 5 秒都退回重建，不让分叉失败。
- API：`model-input` 返回 `cache_session`（分叉血缘的根会话），Agent 用它作为供应商会话头。
- Agent：快路径继承 `run_aliases`；压缩摘要来源、`recall_history` 的 `run_id` 和继承 recall 结果的重新校验都按别名换成分支副本。
- 测试 `test_context_isolation.py::test_fork_siblings_share_ancestor_bytes_but_advance_epochs_independently` 原来断言“分叉必然重建”，按新合同改为断言分支拿到祖先字节而不是兄弟分支的压缩。

## 验证

代码基线为本分支工作树（SQLite 测试库）。

- `ruff check`、`ruff format --check` 通过。
- 新增 `tests/test_fork_checkpoint.py`：复制内容与源快照不变、跨模式重建、压缩前后分叉各得其轮、无快照/过期/失败轮/未完成重建、超限与存储故障时分叉成功并重建、Work 工作区可用、分支换模型记边界、结构化 run 句柄迁移与正文别名（含分叉的分叉）、别名用于摘要来源/recall/继承 recall 校验、截图引用迁移并能在分支读图（v1 临时截图退回重建）、血缘根会话（根被清除后不变），以及删除：分叉失败清掉副本、崩溃孤儿被扫描回收、清除分支删副本且源不受影响、清除源分支副本仍可用、分支下一轮成功后 7 天到期并删除、文件来源失效删副本。
- `tests/test_prefix_cache.py::test_same_mode_fork_extends_the_forked_turns_last_request[chat|work]`：真 API + 真 AgentLoop，分支第一次请求 = 源最后一次请求 + 助手回答 + 新用户消息，工具列表相同、会话头相同、无边界事件；Work 保留 tool 消息。
- 红证据：撤掉 `ailab_platform` 改动后上述新用例及更新的兄弟分支用例全部失败（回退路径用例按设计在新旧代码上都通过）。
- 相关模块（fork/上下文隔离/前缀缓存/会话树/文档视图/agent loop/删除审查/生命周期契约/recall 等）在本地全部通过；完整双库与 E2E 交 CI。
- 独立审查：无阻塞问题；已修其中 5 条（复制失败不让分叉失败、继承 recall 校验用别名、摘要先按原文判 16000 字节、去掉深拷贝与二次序列化、补字段/超限/存储故障/工作区/换模型用例）。

### 真模型（DeepSeek V4.1 Flash / OpenCode Go）

真 API + 真 AgentLoop，同一会话两轮后从第二轮分叉，分支问一个新问题；取分支第一次主调用的 `cached_tokens / prompt_tokens`：

| 场景 | 改前（main 代码） | 改后 |
|---|---|---|
| Work 分叉 | 0 / 7260（`rebuilt`） | 7168 / 7281 = **98.4%**，无边界 |
| Chat 分叉 | 0 / 6282（`rebuilt`） | 6016 / 6145 = **97.9%**，无边界 |
| 对照：改后代码但分支用自己的会话头 | — | Work 0 / 7284；Chat 6144 / 6145（偶然落到同一后端） |

单独探针：同一 4.4k 前缀换会话头后命中 0，同会话头 4352。短对话里系统提示占大头；对话越长、工具过程越多，重建丢掉的部分越多，差距越大。

### CI

- 首轮 `0f4f114` 双库各挂 2 项，均为测试问题：
  - `test_invalidated_file_source_removes_the_branch_snapshot`：用例按 `drain_invalidations` 返回 0 判断排空，但某阶段返回 0 后仍有后续阶段，且它先处理全库最早的文件；改为只排空本文件并按状态等待。
  - `test_review_regressions.py::test_api_stays_ready_but_degraded_without_redis`：既有的顺序依赖。无启动钩子的 `TestClient` 不建存储目录，worker 的第一个用例跑到它就 503（单独运行即可复现）；新增测试改变了分配才暴露。用例先调用 `settings.ensure_dirs()`，与启动一致。

- 最终 `bcf7bdf1` 的 11 项 CI 全绿：SQLite 3046 passed / 225 skipped，PostgreSQL 3180 / 91，E2E 三片 66 / 61 / 69 passed，quality、crawler、shell、storage-contracts 通过。

## 没覆盖到 / 风险

- 已有的分叉会话在部署后第一次调用会从自己的会话头切到根会话头，冷启动一次；跨模式分支也共用根会话头（本来就整段不命中，无额外损失）。
- 复制在用户锁内完成：默认 4 MiB 上限时约 30 MiB Python 峰值、0.2 秒级（审查实测于 Mac）；若把 `checkpoint_max_bytes` 调到 16 MiB，峰值约 120 MiB，对 256–384 MiB 的 API pod 偏紧。
- 未在 PostgreSQL 本地跑；交 CI。
- 不被继续的分支叶子 checkpoint 一直保留到会话清除（既有行为，每次同模式分叉多一份）；是否加闲置过期见对话中的存储估算，未实施。
- 清除会话时 SQLite 报 `conversation_search` 删除 0 行的 SAWarning，是搜索索引的既有警告，行已被外键级联删掉，无残留，本次未改。

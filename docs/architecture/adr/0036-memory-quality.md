# ADR 0036 · 记忆质量标准、每晚一条整理链与来源项目标签

状态：已实施，PR 验收中（2026-11-10）。在 [ADR 0035](0035-memory-v2.md) 有界记忆文档之上修改整理节奏、记忆标准、来源删除语义和项目模式；不改文档结构、快照/失效通知和计费合同。计划与代码证据见[设计](../design/2026-11-10-memory-quality.md)。

## 整理节奏

每个用户每个本地日期最多一条 cron 链，只在 `[window_start + 错峰, window_end)` 内建链。链停止时不再新建续批行；没处理完的对话依靠 `MemoryWorkItem` 状态留到下一晚，`recover_pending` 只续跑租约过期的已有批次。链在 `report` 中持久化 `cutoff_at`（建链时间，只处理此前结束的对话）、`chain_started_at` 和 `deadline = chain_started_at + AILAB_MEMORY_DREAM_CHAIN_TIMEOUT_SECONDS`（默认 1800，60..7200），后继批次原样继承。每批开始前检查：deadline 已过 → 链尾标 `reason: timeout`，待执行批次 `failed`；05:00 后才被接纳且尚未开始的链 `skipped(reason: late)`，不调用模型，仍算当晚名额。手动整理不占名额、不受窗口限制。

## 记忆标准与操作

整理（`DOCUMENT_SYSTEM`）、页面编辑（`EDIT_SYSTEM`）和对话工具（`MEMORY_RULES`）共用 `memory_dream/prompts.py` 的中文 `STANDARD`：判断标准、五类记/不记、判断顺序、转变和取自真实截图的正反例。

- `transition {id, past, now?, quote, run, r:E, past_kind?}`：旧行 `superseded_reason=correction`、由过去式行接替（`created_by=transition`，继承来源和项目标签），沿用现有纠正通知；现状行按新增处理，撞活动唯一键或墓碑时只写过去式。受保护行需要更新的用户原话引文。
- 每个 op 和 `ignored_runs` 带理由代号 `r`，只计数（`report.codes`，未知记 `?`），不授权；唯一例外是 delete：只接受 H（重复）或 I（错误，必须附用户原话 quote 和 run）。去掉 delete 的 `reason`/`outdated`。`ignored_runs` 中 T 对应原 test/repro，其余对应 no_signal。
- 对话工具不加新操作：状态变化用 replace 改写为过去式再 add 现状；页面编辑的 replace 可带 `kind`。
- 工具描述和规则变化使上下文 ABI 升到 **6**；已打开的会话遇到一次 `context_abi_changed` 边界。

## 删除来源不删除记忆

删除对话、文件来源失效、删除项目使用同一规则：从当前行移除失效的 run、引文，行保留并标 `evidence_deleted`（`FORGET_DERIVED_FACTS=False`）。只依赖被删来源的已关闭历史版本照旧清理；原文、episode、撤销副本中的引文照旧清除。忘记一条记忆只能在记忆页删除或在对话中明确要求，两者留墓碑。

## 项目模式与来源标签

`memory_items.origin_project_id`（迁移 0067，可空，无外键/索引，SQLite 普通 `ADD COLUMN`）记录账号行学到时所在的项目。写入整条往返路径（`legacy`、`replace_entries`、`MemoryEntryInput`、`write_memory` 合并表、`_stored_entry`、维护输入）；旧客户端不带此字段时保留原值。Dreaming 每批一个项目，标签取批次 `pid`；工具 add 取当前会话项目；transition/update 继承；merge 仅在标签一致时保留。回填只给 `project_id IS NULL` 且全部来源指向同一项目的行。删除项目时同事务置空标签。

- 共享 → 独立：带本项目标签的 active/review 行移到项目范围，不复制；独立 → 共享：沿用程序融合并补标签。
- 不新增上下文边界：已打开会话通过现有失效通知得知，移出范围的行措辞为「已不在当前范围」。已校验并缓存的历史摘录（`validated_recall_run_ids`）不撤回。
- 独立项目的文件隔离放在 `authorized_source` 一个入口（search/read/documents 共用），本会话用户附加的文件（`MessageFileRef`）放行；非独立范围的 `library_query` 排除独立项目的文件。Portal 资料库页面不受影响。
- 共享项目的 `recall_history` 在候选阶段额外取一批本项目近期对话（上限 200），合并去重后本项目得分 ×1.2；翻页语义不变。

## 旧数据整理

成功的 cron/manual 批次在 `report.prompt_version` 记录 `PROMPT_VERSION`。`claim_due` 只读比较最近一次成功整理的版本；过期且存在非受保护行时，即使没有新对话也建链，并先按新标准整理每个作用域一次（只用 merge/H/I，受保护行不动）。版本不写入 `user.settings` 或 `MemoryDreamSettings`。

## 后果

- 每晚每用户最多一条链，白天不再自动整理，模型调用只减不增；静态提示词增加约 740 token（跨用户共享前缀）。
- 受保护（explicit/manual/edited）的旧行不会被旧数据整理改动；真实库里由旧流程写成 explicit 的流水账行需要用户在页面清理。
- 部署：一个迁移；API、Agent、Worker 同时更新。

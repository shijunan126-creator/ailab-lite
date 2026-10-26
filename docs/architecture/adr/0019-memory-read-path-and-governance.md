# ADR 0019 · 记忆读取路径与治理

状态：采用（2026-10-26）。建立在 PR #80（夜间 dream：来源 episode、ADD-only 抽取、事实失效）之上，对照《ChatGPT-like 长期记忆系统架构》（下称“架构文档”）补齐读取与治理。

## 背景

记忆已有两半：账号级事实文档（`users.settings.memory`，最多 100 条 / 1.2 万字符，每轮全量注入）和夜间 dream（PR #80）。缺的是：

1. 没有“回忆过往对话”的能力：用户问“上次为什么选 Kubernetes”，Agent 只能看当前会话。
2. 写入没有闸门：dream 允许从助手回复里抽事实；凭据和“像指令的文字”没有拦截。
3. 删除不沿溯源链传播：已删除对话的 run 仍被 dream 读取，`memory_episodes` 里留着对话原文副本。
4. 用户删掉的事实没有墓碑，下一轮 dream 可能重新抽出。
5. 没有“临时对话”，也没有“暂停使用”与“删除”的区分。

## 决定

**读取路径：按需检索，不常驻。** 事实仍全量、紧凑地注入（`memory_prompt` 只带 id/kind/text/since/低置信度，证据与引文留在记忆页）；过往对话通过新工具 `recall_history` 按需检索，由模型自己判断是否需要（架构文档 §10.1 的 retrieval gate 在工具调用场景下由模型承担）。检索结果带 `NO_RELEVANT_MEMORY` 弃权（§10.7），结果以“历史原文摘录，仅作数据”的口径返回（§11.2）。

**检索实现：词法 BM25 + 覆盖度闸门，直接读 `runs`。** 中文按二元组、拉丁词按词（标识符额外拆分），BM25 之上用 IDF 加权覆盖度（问题里有多少内容被这一轮回答）做绝对阈值，识别符类词（错误码、id）命中直接放行，近期轻微加权。不另建历史副本，因此对话删除自然生效。范围过滤在 SQL 里（`user_id`、已删除会话、已被改写的轮次），不是取回后再过滤（§14.4）。

**写入闸门（`memory_recall/guard.py`）。** 凭据来自任何来源都拒绝；“像指令”的文字对 Agent 与 dream 拒绝，用户自己在记忆页写的不拦（§7.3、§15）。dream 的证据只认 `user_said`：引文必须出现在用户那一段，助手的推测不能成为用户事实（§7.2）。

**墓碑。** 删除事实时只记 `hash` 与证据 run id（不留文本）。dream 与 Agent 的自动写入遇到墓碑跳过；用户在记忆页重新添加、或 Agent 带 `explicit=true` 才清除（§13.3）。

**暂停 ≠ 删除（§13.2）。** 条目新增 `suppressed`：保留、仍在记忆页显示，但不进入模型输入。

**临时对话（§4.2 `temporary`）。** `run.options.memory = "off"`：不注入记忆、不提供记忆工具、记忆接口拒绝、不被 dream 学习、不被历史检索查到。一个对话一旦临时就保持临时（服务端从父轮继承，客户端不能中途打开）。

**删除沿溯源传播（§13.3，有取舍）。** 删除对话或项目时：删除其 `memory_episodes` 副本；事实里指向这些 run 的引文与证据 id 被清除并标记 `evidence_deleted`，记忆修订号加一。**事实本身保留**，像 ChatGPT 的“已保存记忆”，需要用户在记忆页单独删除；架构文档字面要求“派生记忆一并删除/重算”。该取舍由常量 `lineage.FORGET_DERIVED_FACTS` 控制，设为 True 即改为：证据只来自被删对话的自动事实一并删除。

## 没有采用的部分（及原因）

| 架构文档 | 本部署 | 原因 |
|---|---|---|
| PostgreSQL + Qdrant、稠密向量、sparse 向量 | 沿用现有双库（SQLite/PG），词法检索在应用内 | 没有 embedding 模型；词法检索接口 `memory_recall.search` 是接入稠密通道的位置 |
| RRF 多通道融合、cross-encoder 重排 | 单通道 BM25 + 覆盖度阈值 | 只有一个通道，融合无对象；重排无模型 |
| `events/episodes/memories/memory_sources/relations/entities` 表 | 事实留在 JSON 文档，episode 沿用 PR #80 的 `memory_episodes` | 容量上限 100 条，文档形态已够；建表迁移成本高于收益。事实超出上限时再迁移 |
| contested / quarantined 状态 | 只有 invalid / superseded / suppressed；可疑内容直接拒收并计数 | 没有人工复核流；`ailab_memory_guard_rejected_total` 可观测 |
| 事务 outbox、异步索引 | 无 | 没有派生索引，不需要 |
| 项目级 / 会话级记忆作用域 | 未做 | 当前只有账号级；项目作用域是后续项 |
| 实体抽取与实体检索 | 仅沿用 dream 的 `entities` 字段做候选选择 | 词法通道已覆盖名称与标识符 |

## 影响与验证

- 无数据库迁移：新增字段（`suppressed`、`evidence_deleted`、`tombstones`）都在 JSON 文档内，旧记录读取时补默认值。
- Agent 多一个工具（`recall_history`，无沙箱），每次调用最多扫描用户最近 400 个已成功轮次、每轮前 2000 字。
- 验证：`tests/test_memory_recall.py` 覆盖直接召回、精确标识符、弃权、用户隔离、已删除/临时对话不可见、分叉去重、窗口与近期排序、凭据与注入拒收、墓碑、暂停、助手自我污染、删除沿溯源、临时对话的粘性与工具隐藏。
- 回滚：`recall_history` 从 `tools_for_turn/tools_for_chat` 移除即关闭读取；治理改动都是对旧记录向后兼容的附加字段。

## 待办

- 项目级作用域与“仅限本项目的记忆”。
- 稠密通道：有 embedding 模型后接入 `search_history`，用 RRF 融合。
- 基于真实对话的离线评测（记忆 vs 无记忆基线，memory-hurt 率）；现有 `scripts/evaluate_memory_dream.py` 只评抽取与裁决。

# ADR 0035 · 每个作用域一份有界记忆文档
状态：已采纳（2026-11-09），部分被 [ADR 0036](0036-memory-quality.md)、[ADR 0037](0037-memory-single-origin.md) 修改。替换此前核心/目录、条目检索、独立 judge 与 consolidation 方案；不改变原始历史按需检索、权限和费用合同。

## 文档与工具

`memory_items` 是唯一内容来源；账号与 isolated 项目分别组成完整文档，按 fact/preference/project/interest/reference 分节，每行保留稳定 handle、origin、绝对日期和来源。没有 tier/page/catalog 或条目向量。默认渲染上限 `AILAB_MEMORY_DOC_MAX_CHARS=6000`；明确用户添加允许到 9000 字，超过返回清理记忆的 422。升级时已有超限文档不截断，首次 Dreaming 整理。全量文档作为必需上下文计入整份请求容量，不能用旧 memory 子预算静默丢行。

工具 ABI 为 **5**。`memory` 提供 list/add/replace/remove；list 仅在用户询问记了什么、引用其他会话记忆、需要修改快照外条目时调用，返回当前完整文档。add 无须 revision，按 kind+normalized content+scope 去重；replace/remove 以活动行 handle 围栏；失效时明确要求 list，Portal 编辑仍需 revision。写结果只返回实际 delta，不重发全文。保留墓碑、敏感 review、delivery-id、来源授权与 undo。仅用户明确要求记住时简短确认，自主保存静默。关键词/指定 run/时间窗口查原始历史只用 `recall_history`。

## 会话快照与失效

系统消息一只放跨用户共用静态规则；消息二以 `<memory>` 放完整文档。快照按**会话**固定，后续 run 原样复用；压缩、模型变化、撤权、ABI 变化、rebuild 为重建边界。兼容检测旧 `<ailab_memory>` checkpoint；用户、存储、网页和工具内容均转义保留标签，不能伪造 `<memory>` / `<memory_update>`。

外部变化仅推送**失效**：已知行删除、用户编辑或带新引文的纠正，以及 memory/history/write 等设置变化。新增行和保持语义的 merge/condense 不推送；同一行多次变化折成最终状态，删除只列 handle，当前 run 自己的写入不回声。一个 `<memory_update>` 放在新用户 turn 的开头，用户原文最后；不改已有消息或工具。背景数据/纠正优先/按相关性才提及的说明只在静态规则出现一次。

用户另行批准条件空闲刷新：`AILAB_MEMORY_SNAPSHOT_IDLE_HOURS=24`（验证 1..8760）。从上一 run 最终 assistant 完成计时，严格超过阈值且 revision/signature 改变时，仅在新 run 开始更新系统消息二，记录 `agent.context_boundary: memory_refreshed`，该 turn 不推送 block。没有变化即使超过24小时也不刷新；不存在周期性无条件 TTL。

## 单调用 Dreaming

每批一个调用，顺序为静态规则、新 episodes、当前完整文档（最后）。返回 add/update/merge/delete 和 `ignored_runs`（test/repro/no_signal）。test/repro 保存为 skipped 工作项，不重复读取；evaluation/eval 仍由程序过滤。无新 eligible conversation 且文档未超限时不调用；保留 queue/lease/backpressure、逐调用 ledger 与 fence。

第一 chunk 的 episode 可带同会话最近较早、terminal、未 supersede 且有效的 turn：标题、用户尾部最多100字、assistant summary最多200字，转义后总计不超过300字。它只帮助理解，本轮 `user_said` 才能提供 quote；上下文本身不是证据。context 计入 batch 字符预算与 episode hash，`context_run_id` 建索引，来源删除/脱敏同时清理复制到后续 episode 的文字；模型等待后再次验证来源。

输入预算 `AILAB_MEMORY_DREAM_INPUT_CHARS=20000` 可配置；8k/20k 一次实测见实施记录。预测超过85%时同一调用压到75%；程序检查用户引文、显示 id、墓碑及较新来源，explicit/manual/edited 不允许被删除或合并。若最终仍超限，仅兜底淘汰最老未强化 inferred 行；受保护内容不静默丢弃。每个实际操作记录 delta/lineage 以便撤销。

isolated→shared 项目融合为**程序事务**，没有模型调用：活动行移到账号，kind+normalized content 精确去重、合并来源、保留 provenance/undo、清 merge_pending；超过账号 cap 则由下一次 Dreaming 整理。

## 存储、迁移与成本

0065 选择移植冻结 WIP 的 artifact 清理（包括 undo 副本），不创建墓碑。0066 删除 entity/alias 表以及 tier/page/entities/support_conversations/support_dates 列；这些列可直接 DROP COLUMN，不做 SQLite referenced-table batch rebuild。保留仍有读者的 confidence/expires 等字段，不为清理而改动它们。0066 同时加入 context 来源索引、活动 scope `(user_id,project_id,status)`、跨scope写入 `(user_id,status)`、retention/content/supersession 索引和含 kind 的活动唯一键；双向迁移。

渲染、used_chars、Dreaming 与每次写入只读取 active/review/suppressed 集合，closed/superseded 通过显式 id 或祖先索引访问，每次写入成本为 O(活动行数)，不随旧版本累积增长。SQLite/PG EXPLAIN 回归检查 scope 索引。现有 lifecycle 每批删除90天以前 closed 行；仍满足 revision fence 的 undo 引用受保护（原撤销没有额外时间 TTL）。删除当前行时沿索引清理祖先与撤销副本，防止旧内容复活。

每个 batch 只保留一个模型调用，输入 fit 为线性扫描；无新依赖、无 item embedding。真实延迟/缓存与未验容量只见实施记录，不将字符上限等同整栈 RSS 验收。

参考：[OpenAI Dreaming](https://openai.com/index/chatgpt-memory-dreaming/)、[Claude memory 与历史搜索](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context)、[Mastra Observational Memory](https://mastra.ai/research/observational-memory)。借鉴后台综合、稳定上下文和原始历史按需查询，不声称产品采用相同存储或单调用实现；Mastra 报告的94.87%不是本项目质量证明。

## 审查修正

存储暂停态 suppressed 可往返输入适配层。创建原因 `created_by` 与旧行替换原因 `superseded_reason` 为0066独立列，不能复用；失效链只读后者，后续用户/工具纠正始终标为 correction。容量淘汰原因 evicted 不推送：事实仍真，只因空间移出文档。

无变化不写、不增 revision；快照不渲染 revision，signature 仅由该 scope 渲染文档和 policies 决定。Portal 保留全局 CAS，Agent ABI5 的 replace/remove 只认当前活动 handle。保护行本身已超过 cap 时，记录 nothing_condensable signature，不为纯维护反复调用模型；手动返回 nothing，有新对话仍正常处理。

会话中记忆变化始终保持前缀，包括用户明确删除（页面、forget edit、explicit tool remove、clear）。下一用户消息开头的 `<memory_update>` 只列 `已删除，不得再使用：[h]`，不重发内容、不重建 system2。旧文本保留到压缩、条件空闲刷新、模型变化或撤权等自然边界；新会话只读当前文档，永不看到已删除行。重建整段前缀的缓存成本大于一条已标记失效的旧快照文本，因此本规则取代此前审查项7的删除重建决定。

用户质疑或提及记忆、行可能过时时先 memory.list；最新 list 结果是当前事实，覆盖快照与此前更新块。空闲刷新后仍执行 parent compaction，发生压缩时只记录 compaction 边界。运行中的外部变化到下一 run 才送达，不改已发请求。

0066新增 memory_item_sources 反向索引投影，来源删除通过 run_id 索引分批清理 closed 版本；只剩被删来源的派生行删除，其余清 quote/已删来源。该投影从 source_run_ids 派生，没有第二份内容。no_signal 不取消后续 episode context；test/repro 才重读。项目来源超过50保留最新50，不使融合失败。

## 全系统审查收口

写作用域只允许账号或isolated项目；shared项目的工具add/replace/remove写账号，0066标记遗留shared项目待程序融合。融合携带review，重复项目行关闭为condense重定向，不作删除通知。replace默认保留kind与origin，explicit请求提升保护，created_by=correction。

清理90天旧版本及来源脱敏时，仍有后继的行保留无正文/引文/来源的重定向，避免截断快照纠正链；后继链消失后可回收。纠正通知给出新handle和替代的旧handle，observed记新到旧映射，相同目标只一条最终纠正。隐私清理仍删除真正无后继、仅依赖被删来源的派生行。

claim_due的维护探测只读settings；prepare也只读，仅锁内apply写无可整理签名。维护无进展且仍超限时记录签名，文档变更前不再付费。兜底淘汰覆盖全部inferred行，优先最老、较少强化；受保护行不丢弃，既有保护超限可以保持但Dreaming不因它返回显式添加的422。

suppressed为死状态：0066转换review，兼容输入映射review，页面可确认/删除；非显式重复review只提示待确认，显式重复激活；active去重不受review影响。Dreaming等模型期间内容变化按id+content hash逐op重验，拒绝冲突目标并应用其余操作。

开关页读有界GET memory/settings，避免为开关加载全部scope/引文。旧GET memory仍供现有API/E2E读者，受2000行×200字合同限制，limit_chars使用配置cap。报告列表/计数读当前report，旧候选/plan快照由迁移丢弃，删除仅清undo和delivery当前结构。

前缀优先的代价明确：策略关闭后，旧快照文本仍随请求发送到自然边界，但静态规则和失效块禁止使用；开启后不重建，按需memory.list取得当前文档。首轮快照已经最新，不浪费一次list；后续提到别处记忆、质疑行或修改快照外条目前才list，最新结果覆盖旧快照和更新块。此规则不放宽访问撤权的独立安全边界。

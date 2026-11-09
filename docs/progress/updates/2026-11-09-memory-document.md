# 有界记忆文档 · PR #174

基线 `origin/main@c4e886135675e8dff545b37b71ae3951b1dd2e3b`，分支 `feat/memory-doc`，产品/测试基线 `74215bf163ec907bd032bdbe4fdb51f1feee48fa`。已实施，最终 CI 验收中；未合并、未部署。选择移植78b2c171的0065清理，没有合并其分支。完整合同见 [ADR0035](../../architecture/adr/0035-memory-v2.md)。

## 结果与集成

- 完整有界文档、ABI4/list/add/replace/remove、无 revision add、来源/墓碑/敏感 review、单调用 Dreaming、程序项目融合、90天 closed 回收及索引已接通；旧检索/extract/judge/consolidation/entity/tier/page 路径删除。相对基线整个差异约3900行删除/3000行新增（包含前端和测试迁移），不是净删3900行。
- 会话快照 `<memory>` 原样复用；只有删除/纠正/设置失效在新用户原文之前追加一个 `<memory_update>`，不推新增或 condense，不回声本 run 写入；条件24小时刷新记录 memory_refreshed 边界。转义标签、legacy checkpoint、完整文档容量及请求前缀有录制回归。
- 第一 episode chunk 带前序有效 turn 的限300字 context，参与 hash/预算；引文只来自本 run。确认句、错用 context 引文、superseded/排除/删除与后续 episode 脱敏都有回归。
- 前端 `4bf62947c1a1561649594270b2aec8813135d53f` 经 merge commit `e844c341` 合入，保留 fd19c202。最小整合修正：项目切换提示改为全部迁入账号；shared 项目 view 显示融合后的账号文档；整理报告有修改但无新增时显示实际 changed 数。project-memory 仍被 project-home 使用，未删除。
- Portal 只用 view、PATCH/DELETE item、edit、dream/run、dream report/revert 与 DELETE entries；移除无人使用的 GET items。E2E legacy kind:user 仍兼容，scripted memory 写入改为 canonical preference + 无 revision。
- 0065 删除 artifact/undo 副本且不写墓碑；0066 删除不用的表/列、调整唯一键/索引并增加 episode context_run_id。SQLite 不做 batch rebuild；仍有 reader 的列保留，细节见 ADR。

## 检查与失败修复

本地只跑 Ruff 和改动模块，没有重复全套 Python/Portal build/E2E。`cd platform && .venv/bin/ruff check . && .venv/bin/ruff format --check .` 通过（676 files）。分组定向检查通过 memory document/dream/hardening/summary/v2/recall/retention、episode context、migrations、fault chain 与 prefix；模块收集规模分别11/11/24/45/14/30/2/8/139（收集数不是额外通过数）。最终维护组64项通过。补充索引/去重后的定向组：SQLite 65 passed /48 skipped（PG环境未启用的参数），PG文档/保留13 passed，显式PG迁移URL的双库查询计划/迁移专项14 passed /86 deselected。较早联合 prefix 组有1个旧测试把 system1 中标签说明也计为 block，改为仅计用户 turn 后定点通过；不将此前失败运行写为全绿。

临时 PostgreSQL16（独立15443端口）定向 document/episode/retention 19项通过，另运行迁移/索引回归。SQLite/PG EXPLAIN 覆盖 scope 与真实跨scope适配层查询：补 `(user_id,status)` 索引避免 scope 索引中 project_id 缺省时扫描账号历史范围；closed 行不会进入逐写扫描。PG 初版 fixture 只堆同一账号旧行，planner 合理选择全局 status 索引且失败后污染后续 GC；修为跨账号真实选择性并清理 fixture，未强制 planner 或放宽阈值。

后续代码复核补齐查询计划中旧30候选断言（默认现100），以及Dreaming按kind+normalized content去重（一个重复op不应让整批失效，跨kind同文允许）。早期76项旧模块失败已迁移。保留安全、并发、删除/脱敏、undo、ledger 与 fence 行为；只删下文失去生产路径的内部断言。旧 E2E 因 memory.list 被移除失败，按新批准合同恢复只读 list，并更新 scripted fixture。最终全套由 [PR CI](https://github.com/shijunan126-creator/ailab-lite/pull/174/checks) 验收，本文不把历史 SHA 的通过冒充最终 head。

## 真模型：删除失效与短时恢复

使用同一 deepseek-flash 端点及仓库脚本 `PYTHONPATH=platform platform/.venv/bin/python platform/scripts/evaluate_memory_document_prefix.py`；AgentLoop + API/Broker doubles，不代表部署整栈/真实沙箱。四轮包含自身 memory 写入、外部删除/更正/新增（新增不推）、2小时恢复；检查系统一/二、tools/settings/session 和先前请求字节保持稳定，外部 block 恰好1条。只计第二次调用起（包括首轮工具后续调用）：

| 样本 | cached / prompt | 比例 |
|---|---:|---:|
| Chat |36864 / 37522|98.25%|
| Work 首次完整运行 |25344 / 34443|73.58%|
| Work 诊断复测 |34304 / 34729|98.78%|

Work 首次有一次 cached=0，尽管请求是严格追加且 session/settings 不变；其他调用约98%。复测每调用97.73%–99.61%。不能以复测掩盖首次供应商冷失效，也不能宣称每次均达95%。脚本和录制测试证明请求稳定；供应商缓存可用性仍有边界。

跨身份诊断（早期同端点样本）：短 Chat 完全重放0/4474。Work 同user同session4992/5106，改user保持session仍4992/5106，仅改session则0/5106；共享 system1/tools 完全一致。证据指向端点 session 相关缓存边界，未证明按API key隔离；保留必需 session header。

## 批次预算与真实库副本

部署 SQLite 通过只读 URI + iterdump 复制到 /private/tmp，源/副本 quick_check=ok；29条active/903内容字。未写部署库。初次8k/20k对照（增加前序context以前，每档一次）：

| episode预算 | episodes | 请求内容字 | 新增 | 无效JSON | prompt/completion | 秒 |
|---:|---:|---:|---:|---:|---:|---:|
|8000|18|10458|0|0|5386/6|6.46|
|20000|30|16231|0|0|8637/132|5.07|

按用户决定默认设 **20000** 且可配置：每 episode tokens 相近，样本未见额外延迟；较大批次减少固定规则和完整文档重复，110会话回填约从6批降到3批（随文本密度变化）。0新增因为这些会话已经产出了现有29行，**不是 recall 测量**。

新 context 合同真实 `process_dream` 一批在迁移0064→0066的副本上经过 lease/reserve/settle/apply：30 episodes、0新增/0 ops、ignored22、无效JSON0、9255 prompt /1108 completion tokens、36.94秒、applied。该样本在候选上限30→100之前；现候选100、预算fit线性，未重复付费测量。用量只写副本，部署账本未变。副本与模型原始输出结束清理，不提交个人数据。

## 未覆盖 / 下一步

最终 head CI 仍待核对并修复；不合并部署。未做独立人工标注 recall、多模型质量、300用户整夜压力、目标机 CPU/RSS 峰值或部署端到端缓存验收；不增加新依赖/embedding，每批一个调用，逐写 O(活动行数)，历史版本按索引回收。Portal 浏览器覆盖由 CI E2E 执行，本轮没有再启动本地 UI 服务。

## 删除的旧测试及替代

- `test_memory_v2.py::test_alias_lookup_plan_uses_bounded_user_name_index`：alias表与查询已删除；active/closed双库EXPLAIN替代。
- `test_memory_v2.py::test_archive_not_in_standing_prompt`：archive不常驻合同已删除；完整文档渲染回归替代。
- `test_memory_v2.py::test_capacity_evicts_oldest_unreinforced_archive`：archive数量上限已删除；render字符上限/最老未强化inferred淘汰回归替代。
- `test_memory_v2.py::test_catalog_active_safety_decay_whole_names_and_determinism`：实体目录与衰减排序已删除。
- `test_memory_v2.py::test_core_budget_selects_priority_before_page_and_recent_ties`：核心挑选与tier/page排序已删除。
- `test_memory_v2.py::test_extraction_core_requires_explicit_preference_or_identity`：核心晋升策略已删除；受保护来源由文档保护回归覆盖。
- `test_memory_v2.py::test_fact_correction_and_preference_change_close_old_version_with_as_of`：memory.as_of工具已删除；更新关闭旧版本与撤销、较新引文保护回归替代。
- `test_memory_v2.py::test_history_and_items_share_conversation_cap_and_earliest_order`：条目与历史融合检索已删除；历史pool/排序/窗口回归保留。
- `test_memory_v2.py::test_indexed_alias_expansion_and_metadata_search`：别名投影与条目检索已删除。
- `test_memory_v2.py::test_multiple_alias_matches_expand_in_canonical_order_and_bound_encoded_history`：别名检索已删除；历史有界与编码回归保留。
- `test_memory_v2.py::test_page_and_search_echo_encode_stored_delimiters`：主题页与条目search已删除；memory/document/tool标签编码录制回归替代。
- `test_memory_v2.py::test_reinforcement_merges_sources_and_counts_distinct_support`：reinforce阶段与支持计数投影已删除；程序merge来源合并与上限/undo验证替代。
- `test_memory_v2.py::test_retrieval_evaluation_uses_both_channels_and_abstains`：条目检索通道已删除；原始历史检索/拒答回归保留。
- `test_memory_v2.py::test_temporal_visibility_includes_past_before_expiry_and_correction`：按过期时钟过滤当前文档合同已删除；绝对日期/无时钟快照/关闭版本验证替代。
- `test_memory_v2.py::test_test_delivery_is_not_an_artifact_memory_but_formal_delivery_is`：artifact记忆已整体删除；禁止伪造artifact和ignored_runs持久排除分别验证。
- `test_memory_v2.py::test_tokenized_caches_are_byte_bounded_and_clearable`：条目分词缓存已删除；历史缓存与删除传播回归保留。
- `test_memory_dream.py::test_dream_plan_is_append_only_and_requires_an_invalidation_target`：旧DreamPlan DTO没有生产reader，删除；实际JSON ops未知id/引文/版本保护验证保留。
- `test_memory_dream.py::test_extraction_prompt_distinguishes_durable_memory_from_task_requests`：独立extract提示词已删除；单调用静态提示词及test/repro排除验证替代。
- `test_memory_dream.py::test_extractor_reports_rejected_candidates_and_does_not_send_existing_facts`：旧extract不接收已有条目合同相反，现必须传完整文档；程序guard回归保留。
- `test_memory_dream.py::test_fuzzy_candidate_selection_prefers_related_facts`：候选预筛选已删除，Dreaming直接接收完整文档。
- `test_memory_dream.py::test_judge_sorts_existing_memories_before_new_candidates`：独立judge已删除，现episodes先于完整文档。
- `test_memory_hardening.py::test_judge_sees_every_existing_fact_so_a_paraphrase_is_a_duplicate`：独立judge与fuzzy门槛已删除；完整文档输入和程序merge/dedup验证替代。
- `test_memory_summary.py::test_failed_extraction_still_refreshes_stale_summary`：抽取失败后再调用摘要的阶段已删除，单调用失败原子回归保留。
- `test_memory_summary.py::test_pages_are_derived_without_model_calls_when_auto_save_off`：主题页taxonomy已删除；view直接分节及无工作不调用验证替代。

## 独立审查修正（基线7d9a503e）

一次性处理用户8组意见：suppressed全路径输入兼容；0066拆 created_by/superseded_reason（0064不动）；no-op不写、scope渲染签名不含revision、ABI5工具按活动handle围栏；保护行超限无可整理时记录签名并停止纯维护；新增来源反向索引分批清closed隐私；空闲刷新仍走parent compaction；用户删除快照行下一run memory_forgotten；no_signal不重读context、融合取最新50来源。运行中外部变化仅下一run送达，是明确边界。

新增 test_memory_review 参数回归覆盖 PATCH/DELETE、工具add/replace/remove、Dreaming save/edit/undo、项目模式/融合；prefix录制覆盖用户忘记重建与Dreaming删除block、闲置刷新进入压缩。定向组128 passed /45 skipped（SQLite migrations，PG参数环境未启用）；此前测试 fixture 使用固定全局id冲突已改为用户唯一id，未放宽产品约束。真实请求前缀与双库新迁移仍待本轮核对。

前轮CI E2E通过；SQLite唯一失败为OCR checkpoint把新增静态记忆规则计入图片载荷固定10KB上限。修正断言扣除两条固定系统消息，仍保留非静态checkpoint 10KB约束及图片占位断言，不增加上限。全套只由最终head CI验收，不复用历史head通过。

本轮补充结果：prefix/summary/episode/review/OCR联合227 passed；升级运行时构建依赖后source-context/user/review安全组49 passed（此前3个文档fixture失败因本地缺少Office reader dist，未修改产品跳过）；PG审查/retention/迁移组23 passed /92 deselected，SQLite+PG查询计划8 passed（含closed source反向索引）。ABI5真模型第二次调用起Chat36608/37343=98.03%、Work34432/34820=98.89%，逐请求追加与会话设置检查通过；仍为服务doubles，不据此消除历史供应商冷失效边界。新增MemoryItemSource投影带来每条来源一行磁盘/索引开销，来源最多50，内容不复制，写入成本仍随活动行/有界来源数增长。

删除边界追加回归：清空201行超出200条re-add tombstone保留窗口后，最早快照handle仍触发forgotten（按权威行存在性区分用户硬删除与Dreaming closed版本，不依赖墓碑窗口）；来源文件失效实际清除closed私密行。该组46 passed，窗口专项1 passed；来源投影刷新合并为一次批量delete，避免每活动行一条删除SQL。没有改0064。

## 用户决策更正：会话记忆变化始终保持前缀

基线 a79c712a，已先ff拉取Portal移动布局和新到旧排序。本次撤销此前审查项7：删除额外重建路径与其内部API字段，用户删除同Dreaming删除以handle-only block置于下一用户消息开头；旧system2原样保留到自然边界，新会话只读当前文档。静态规则补充质疑/提及记忆或可能过时时先list，最新list覆盖快照和历史更新。ADR0030/0035及prefix设计同步更正；此前删除重建的测试/数字仅为历史证据。

本轮本地只跑改动的prefix/review模块及Ruff；完整套件由最终head CI执行，不合并、不部署。

本次录制专项3 passed，确认用户删除/Dreaming删除都以handle-only block追加，旧前缀UTF-8序列化字节相等且没有额外边界查询。首次整组160 passed/2 failed来自新增fixture在首轮也误返回“已删除”，修正为首轮返回当前行后专项通过，最终prefix+review整组162 passed（194.32秒）。真模型相同多轮/删除/2h恢复场景Chat36864/37665=97.87%；Work首次25728/34505=74.56%（一次供应商全冷，严格追加检查仍过），一次复测34560/34880=99.08%。不隐藏首次未达标，不将供应商缓存可用性写成已修复。

# 记忆与技能

负责用户的长期记忆，以及随镜像、管理员或用户文件进入平台的技能包。不负责普通附件的扫描与解析、沙箱执行和交付件版本，分别见[文件](files.md)与[沙箱](sandbox.md)。

## 记忆

### 代码地图

| 要改的东西 | 位置 |
|---|---|
| 记忆行、来源索引、episode 与整理进度 | `platform/ailab_platform/models.py::MemoryItem`、`MemoryItemSource`、`MemoryEpisode`、`MemoryWorkItem` |
| 整理批次、租约、报告与撤销快照 | `platform/ailab_platform/models.py::MemoryDreamRun` |
| 记忆 DTO、操作 ABI 与上限 | `platform/ailab_platform/contracts/memory.py::MemoryEntryInput`、`MemoryUpdate`、`MemoryOperation`、`DreamChange` |
| 分节、确定性渲染与增量结果 | `platform/ailab_platform/memory_recall/document.py::SECTIONS`、`render`、`write_result` |
| 关系表与旧文档形态之间的适配 | `platform/ailab_platform/memory_recall/store.py::legacy`、`replace_entries` |
| 作用域、权限与模型可见性 | `platform/ailab_platform/memory_recall/policy.py::model_view`、`run_permissions`、`project_mode` |
| 会话快照、失效通知与工具结果折入 | `platform/ailab_platform/agent/context.py::refresh_memory`、`observe_memory_result`；`platform/ailab_platform/agent/prompts.py::memory_prompt` |
| `memory` 工具声明与执行 | `platform/ailab_platform/agent/tools.py::MEMORY`、`execute_tool`；`platform/ailab_platform/api/routes_memory.py::run_memory` |
| 管理页读写、清空与立即整理 | `platform/ailab_platform/api/routes_memory.py::memory_view`、`patch_memory_item`、`delete_memory_item`、`clear_summary_scope`、`run_dream_now` |
| 原始历史检索 | `platform/ailab_platform/memory_recall/search.py::search_history`、`platform/ailab_platform/memory_recall/retrieval.py::browse_history`、`platform/ailab_platform/api/routes_memory.py::run_recall` |
| 夜间调度、单批整理与恢复 | `platform/ailab_platform/memory_dream/service.py::claim_due`、`process_dream`、`recover_pending`、`dream_forever` |
| 整理标准、提示词版本与程序应用 | `platform/ailab_platform/memory_dream/prompts.py::STANDARD`、`DOCUMENT_SYSTEM`、`PROMPT_VERSION`；`platform/ailab_platform/memory_dream/document.py::apply_ops` |
| 来源删除、墓碑与历史版本回收 | `platform/ailab_platform/memory_recall/lineage.py::forget_conversations`、`redact_memory_copies`、`clear_memory_scope_copies`；`platform/ailab_platform/memory_recall/tombstones.py::blocked_by_tombstone`；`platform/ailab_platform/memory_recall/retention.py::purge_closed` |
| 项目模式切换与项目行融合 | `platform/ailab_platform/api/routes_projects.py::rename_project`；`platform/ailab_platform/memory_dream/maintenance.py::merge_pending_projects` |

### 数据结构与作用域

`memory_items` 是记忆正文的唯一来源；`users.settings.memory` 只保存开关、全局 revision、墓碑和维护签名，渲染文本不入库。每行保存稳定 handle、`project_id`、`kind`、正文、来源 run、引文、观察时间、有效期和替代链；`source_run_ids` 与 `memory_item_sources` 让来源删除不必扫描全部历史。`status` 只有 `active` 和 `closed`，`origin` 恒为 `memory`；旧 `origin`/`suppressed` 只作为输入兼容。活动唯一键由用户、作用域、kind 和正文 hash 组成。

| 作用域 | `project_id` | 可见范围 |
|---|---|---|
| 账号 | `NULL` | 普通对话与 shared 项目 |
| 独立项目 | 项目 id | 只在该项目内；看不到账号行，也看不到其他项目 |

shared 项目不单独建文档，读写账号文档；行上的 `origin_project_id` 记录它学到时所在的项目，供模式切换按来源移动。切到 isolated 时把本项目的账号行移入项目文档，切回 shared 时由 `platform/ailab_platform/memory_dream/maintenance.py::merge_project` 程序合并并按 kind 与正文去重；未完成的合并在下一次整理前由 `merge_pending_projects` 补齐。`project_mode` 默认 `shared`。

上限分三层：单行 200 字；每个作用域渲染后的文档默认 `AILAB_MEMORY_DOC_MAX_CHARS` 字符，用户显式写入可到 1.5 倍；请求硬上限是 `MAX_ENTRIES`（2000）行和 `MAX_CHARS`（400000）字符。超限的自动写入由整理压缩或拒绝，不能静默丢弃。

### 关键流程

#### Run 读取与会话快照

1. `internal_get_run` 调用 `memory_for_run`，按会话所属项目、共享模式和 conversation/run 冻结的 `memory_policy` 算出 read/write 权限（`platform/ailab_platform/api/routes_internal.py::internal_get_run`、`platform/ailab_platform/api/routes_memory.py::memory_for_run`）。
2. 模型每次请求的 `messages[0]` 是跨用户稳定的平台规则；`messages[1]` 里的 `<memory>` 是本会话的快照（`platform/ailab_platform/agent/context.py::_build`、`platform/ailab_platform/agent/prompts.py::memory_prompt`）。
3. 快照在同一会话中复用；只在压缩、模型变化、撤权、上下文 ABI 变化或条件空闲刷新时重建或刷新。空闲刷新要求超过 `AILAB_MEMORY_SNAPSHOT_IDLE_HOURS` 且文档签名变化，不是按时间无条件重绘。普通新增不推给已打开会话，删除、编辑、纠正和开关变化只在下一用户消息开头追加 `<memory_update>`（`platform/ailab_platform/agent/context.py::refresh_memory`）。
4. 失效查询只沿已知 handle 的 `superseded_by` 链解析，不扫描历史；输出当前内容、删除或“移出当前范围”，合并/淘汰保留旧文本的语义（`platform/ailab_platform/memory_recall/invalidations.py::for_handles`）。
5. 模型调用 `memory.list` 时返回当前可见的完整文档和条目；最新 `list` 覆盖旧快照和更新块。`recall_history` 是另一条路径，按需搜索原始对话，不把摘录写进记忆文档；检索以 BM25 为基线，配置 embedding 时融合 dense 通道，端点失败回退词法（`platform/ailab_platform/memory_recall/search.py::search_history`）。

#### 写入与护栏

1. `memory` 工具提供 `list/add/replace/remove`。`add` 不要求 revision，按作用域、kind 和规范化正文去重；`replace/remove` 只接受当前活动 handle，失效时要求先 `list`（`platform/ailab_platform/agent/tools.py::MEMORY`）。
2. 工具的写请求必须带稳定的 `delivery_id` 并通过 Run attempt 围栏；重复投递返回已有结果。用户明确说“忘了”才允许 `remove`；纠正用 `replace` 追加新行并关闭旧行（`platform/ailab_platform/api/routes_memory.py::run_memory`）。
3. 管理页的整文档写入和条目删除走全局 revision CAS；服务端在用户锁内重读当前文档，再写 `memory_items`（`platform/ailab_platform/api/routes_memory.py::write_memory`）。
4. 凭据、证件号、卡号和外部指令形状的文本在 `guard` 拒绝；Dream 的引文必须逐字来自该批用户消息原文。来源删除后，当前记忆行保留但清掉来源和引文；用户主动删除则写入只含 hash 与来源 id 的墓碑，Dream 和自动工具写入不能把它重新带回来。

#### 夜间整理

1. `dream_forever` 周期性调用 `claim_due`。每个用户每个本地日期最多建一条 cron 链，只在配置窗口内、带错峰启动；链记录 `cutoff_at`、`chain_started_at` 和 `deadline`，只处理建链前已结束的对话。
2. 每批最多一次模型调用：静态规则、本批 episodes、当前完整文档依次组成输入。没有新对话且文档不需要压缩时不调用模型（`platform/ailab_platform/memory_dream/document.py::messages`、`platform/ailab_platform/memory_dream/service.py::process_dream`）。
3. 模型返回 `ops` 和 `ignored_runs`；`apply_ops` 校验 handle、用户引文、墓碑、重复和容量，再以“新行 + 旧行失效”应用。日常删除只接受重复或用户明确说错；按新标准整理的一次性批次可以删一次性、已完成任务或外部常识，并可拆分混合分类。
4. `MemoryDreamRun.change_set` 保存逐行 before/after 与 revision；只有 revision 仍匹配且不是编辑删除的批次可以回退。`PROMPT_VERSION` 变化时先按新标准整理每个作用域一次，再处理新对话。
5. “立即整理”复用同一批次逻辑，`trigger=manual`，不占当晚 cron 名额、不受窗口限制。清空作用域会为被删行留墓碑、清掉副本并取消排队中的编辑；删除项目同时清项目文档和该项目上的来源标签，账号行只失去 `origin_project_id`。
6. 关闭的历史版本保留 90 天供撤销和纠正链使用；没有后继的旧版本到期回收，仍能提供当前 handle 的重定向行在需要时保留空文本。

### 不变量

- 记忆正文只在 `memory_items`；渲染内容由分节顺序、行 id 和存储的时间字段决定，不读当前时钟。钉住它的测试：`platform/tests/test_memory_document.py::test_full_document_has_every_section_handles_dates_and_escapes`。
- 常规 Dream 批次不会原地改写正文，而是追加新行并关闭旧行；页面编辑、对话式修改和撤销恢复可以更新正文，均经 revision CAS（`platform/tests/test_memory_document.py::test_update_is_append_only_and_any_line_can_change`）。
- 会话快照与前缀不能因记忆变化被重写；变化只追加到新用户消息。钉住它的测试：`platform/tests/test_prefix_cache.py::test_changed_memory_records_one_delta_and_preserves_system`、`platform/tests/test_prefix_cache.py::test_user_and_dream_deletion_preserve_snapshot_and_push_handle_only`。
- 用户删除的记忆不会被 Dream 或非显式工具写入复活；钉住它的测试：`platform/tests/test_memory_recall.py::test_a_deleted_fact_is_not_resurrected_by_the_dream_unless_restated`。
- 删除对话、文件来源或项目不清掉当前记忆事实，只清来源；钉住它的测试：`platform/tests/test_memory_source_retention.py::test_deleting_the_only_source_conversation_keeps_the_line_without_a_source`。
- 每个用户每晚只有一条 cron 链，超时或批数用完后不留下白天继续跑的后继；钉住它的测试：`platform/tests/faults/test_memory_nightly_chain.py::test_chain_stops_at_batch_limit_without_parking_a_successor`。
- 独立项目与账号、其他项目互不可见；shared 项目使用账号文档，但历史检索优先本项目。钉住它的测试：`platform/tests/test_memory_projects.py::test_switching_modes_moves_lines_by_tag_and_back`、`platform/tests/test_memory_projects.py::test_isolated_to_shared_merge_tags_moved_lines`。
- 完整文档是必需上下文，放不下就暂停并提示整理记忆，不静默截断；钉住它的测试：`platform/tests/test_context_band.py::test_memory_prices_rendered_projection_and_protects_preferences`。

### 怎么扩展

#### 改记忆行格式或工具 ABI

1. 先在 `platform/ailab_platform/contracts/memory.py::MemoryEntryInput`、`MemoryUpdate`、`MemoryOperation`、`DreamChange` 和 `platform/ailab_platform/models.py::MemoryItem` 加字段；新字段必须可选，`extra="forbid"` 要求旧客户端不发送时保留原值。
2. 同一个字段要贯通 `platform/ailab_platform/memory_recall/store.py::legacy`、`replace_entries`、`platform/ailab_platform/api/routes_memory.py::write_memory` 的合并字段、`_stored_entry`、`platform/ailab_platform/memory_dream/maintenance.py::inputs`，以及页面 DTO。漏掉任一路径，旧客户端或夜间保存会把字段写回空值；用 `platform/tests/test_memory_projects.py::test_origin_project_survives_every_write_path` 的模式做全路径往返测试。
3. 新增列或索引要加 Alembic revision，写 upgrade 和 downgrade，SQLite 用普通 `ALTER TABLE`；旧行用迁移回填，模型与迁移不能漂移。检查：`platform/tests/test_migrations.py::test_migrations_and_models_do_not_drift`。
4. 请求里可见的行格式、工具 schema 或系统消息布局变化时，提升 `platform/ailab_platform/contracts/model_context.py::CONTEXT_ABI_VERSION`（当前为 `8`）；旧会话在 `platform/ailab_platform/agent/context.py::_build` 检测到不匹配后重建一次并记 `context_abi_changed`。不要在一个 Run 中途换工具清单；旧输入适配（如 `origin`、`suppressed`、`status`）要保留到旧会话可以安全重建。
5. 同步检查录制请求和旧会话：`platform/tests/test_prefix_cache.py::test_parent_abi_match_exact_reuse_mismatch_rebuild`、`platform/tests/test_memory_document.py::test_list_is_read_only_full_document_and_correction_chain_resolves_net_state`。只改整理标准而请求格式不变时，提升 `platform/ailab_platform/memory_dream/prompts.py::PROMPT_VERSION`，不要提升上下文 ABI。

#### 加一种记忆 kind 或分节

1. 在 `platform/ailab_platform/contracts/memory.py` 的三处 `kind` Literal、`MemoryItem` 输出、`platform/ailab_platform/memory_dream/document.py::KINDS`、`platform/ailab_platform/memory_recall/document.py::SECTIONS` 和 `platform/ailab_platform/agent/tools.py::MEMORY` 的 enum 同时加入新值。
2. 在 `platform/ailab_platform/memory_dream/prompts.py::STANDARD`、`DOCUMENT_SYSTEM`、`EDIT_SYSTEM` 写清这一类记什么、不记什么和判断顺序；只改 enum 会让模型继续按旧分类产出。
3. 更新页面类型 `platform/portal/lib/api.ts` 的 kind 联合和分节显示；若要把旧 kind 迁移到新 kind，写数据迁移而不是只在渲染时改名。
4. 加测试覆盖分节顺序、活动唯一键去重、`transition` 的过去式与现状、旧 kind 的别名归一化（`platform/ailab_platform/memory_recall/document.py::kind`）；参考 `platform/tests/test_memory_document.py::test_full_document_has_every_section_handles_dates_and_escapes`。

### 配置

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_MEMORY_DOC_MAX_CHARS` | 每个作用域渲染后的文档上限 | `6000` |
| `AILAB_MEMORY_DREAM_INPUT_CHARS` | 单批整理的 episode 与文档输入预算 | `20000` |
| `AILAB_MEMORY_SNAPSHOT_IDLE_HOURS` | 无压缩时允许条件刷新快照的空闲阈值 | `24` |
| `AILAB_MEMORY_DREAM_CHAIN_TIMEOUT_SECONDS` | 一条夜间整理链的总时限 | `1800` |
| `AILAB_MEMORY_DREAM_AUTO_BATCHES` | 每条夜间链最多批数 | `6` |
| `AILAB_MEMORY_DREAM_DEFAULT_TIMEZONE` | 未设置用户时区时的整理时区 | `Asia/Shanghai` |

完整设置见 `platform/ailab_platform/config.py::Settings`。

### 已知的坑

见 [HANDOFF · 记忆](../../HANDOFF.MD#记忆)。

## 技能

### 代码地图

| 要改的东西 | 位置 |
|---|---|
| 镜像技能目录、名称与别名 | `platform/ailab_platform/skill_registry.py::BASE_SKILLS`、`catalog`、`resolve_name` |
| 技能包清单与哈希校验 | `platform/ailab_platform/skill_index.py::build_index`、`checked_index` |
| 个人技能文件识别与安全解包 | `platform/ailab_platform/skillfiles.py::inspect`、`unpack` |
| 技能列表、开关、在线 Markdown 版本 | `platform/ailab_platform/api/routes_skills.py::personal_skills`、`usable_personal`、`skill_input_for`、`list_skills`、`toggle_skill`、`save_skill_content` |
| 平台技能订阅、冻结版本与发布 | `platform/ailab_platform/api/platform_skills.py::added_skills`、`usable_platform`、`frozen_platform`、`activate`、`check_name` |
| 管理员平台技能接口 | `platform/ailab_platform/api/routes_platform_skills.py::create`、`upload_version`、`publish`、`add`、`reset`、`customize` |
| 草稿提取、编辑、确认与失效 | `platform/ailab_platform/api/routes_skill_drafts.py::extract`、`open_skill`、`confirm`；`platform/ailab_platform/skill_drafts/service.py::process_draft`、`invalidate_drafts` |
| 草稿清单与路径安全 | `platform/ailab_platform/skill_drafts/files.py::validate_manifest`、`checked_path` |
| Worker 扫描、技能解包与只读查看 | `platform/ailab_platform/worker/pipeline.py::process_file`、`platform/ailab_platform/worker/parsers.py::run`、`platform/ailab_platform/worker/skill_unpack.py::process_skill_unpack`、`process_skill_view` |
| 文件扫描结果与模型保存技能 | `platform/ailab_platform/api/routes_internal.py::internal_patch_file`、`internal_save_skill`、`_bound_file` |
| `load_skill` 的导航与资源解析 | `platform/ailab_platform/agent/prompts.py::load_skill`、`platform/ailab_platform/agent/skill_modules.py::read_module`、`visible_modules` |
| Agent 预加载、安装与跨轮恢复 | `platform/ailab_platform/agent/loop.py::_ensure_skill`、`_install_skill`、`_register_carried_skills` |
| 沙箱技能清单证明 | `platform/ailab_platform/agent/skill_verification.py::verify_sandbox_skills`、`platform/ailab_platform/broker/routes_skills.py::verify_lease_index` |
| Run 选择、冻结与拒绝 | `platform/ailab_platform/api/routes_runs.py::validate_run_input`、`start_run`；`platform/ailab_platform/api/routes_internal.py::internal_get_run` |
| 审核策略与审批 | `platform/ailab_platform/skill_policy.py::effective_policy`；`platform/ailab_platform/api/routes_skills.py::set_policy`、`pending_reviews`、`review_skill` |
| 技能文件查看器 | `platform/ailab_platform/api/skill_view.py::zip_view`；`platform/ailab_platform/api/routes_skills.py::skill_files`、`skill_file` |
| 模型保存个人技能 | `platform/ailab_platform/agent/net_tools.py::save_skill` |

### 关键流程

#### 一次 Run 的技能选择与加载

1. `validate_run_input` 最多接收 5 个技能，按 `file_id` 的 lineage head 或名称解析，拒绝关闭、未审核、已停用和不存在的技能；`start_run` 把已启用平台技能冻结进 `run.runtime_meta.platform_skills`。用户文本里只插入 `[技能：标题]`，不预先放全文（`platform/ailab_platform/agent/prompts.py::render_skill_mentions`）。
2. Agent 初始化时加载该 Run 选中的技能，并按附件格式、Work 文本关键词补加载对应 `platform.*` 基础技能；跨轮从历史登记已读技能，不重复发出同一加载步骤（`platform/ailab_platform/agent/loop.py::_register_carried_skills`、`_ensure_skill`）。
3. 基础技能先返回导航而不是整份 SKILL.md；导航列出包内资源和 `deps`，模型再以 `load_skill(name, resource=...)` 读取单个完整文件。资源路径、模块 hash 和包 hash 都要匹配，未知资源明确报错，不截断（`platform/ailab_platform/agent/skill_modules.py::read_module`）。同一回复可以并列多个 `load_skill`，批次按累计结果准入，放不下时整份拒绝。镜像包更新后，已读旧正文仍留在历史；再次读取新包时追加版本说明，不按 `loaded_skills` 拒绝。
4. 内置技能返回完整 `SKILL.md`；个人技能从资料库读取正文后返回全文，ZIP 的附带文件在租到沙箱时安装到 `/workspace/.ailab/skills/<name>/`。Chat 模式不租沙箱，个人技能的脚本步骤会附带说明。
5. 本 Run 使用过基础或内置技能后再租沙箱，Broker 和 Agent 必须对同一份技能清单达成证明；镜像里的 `index.json` 与 Agent 源码不一致时拒绝加载（`platform/ailab_platform/agent/skill_verification.py::verify_sandbox_skills`、`platform/ailab_platform/broker/routes_skills.py::sandbox_index`）。

#### 个人技能入库与版本

1. 上传文件走 `POST /v1/files?purpose=skill`；在线编辑走 `save_skill_content`；模型保存走 `internal_save_skill`。三条路径都创建 `FileObject(purpose="skill")` 并先进入 `PENDING`；两个保存接口携带 `skill_claim` 校验声明名称，普通上传由 Worker 识别。
2. Worker 的 `process_file` 调用 `skillfiles.inspect` 识别 `SKILL.md` 或带 `SKILL.md` 的 ZIP，结果写入文件 meta；安全扫描通过后才是 `READY`，格式错误写入 `skill_error`。ZIP 拒绝绝对路径、`..`、符号链接、过多文件和解压超限。
3. `personal_skills` 按版本链当前 head 列出，同名旧文件标为不再使用；`usable_personal` 再叠加用户开关、管理员策略和逐文件审批。删除文件即删除技能。
4. `save_skill` 的 `create` 拒绝同名，`update` 必须带 `load_skill` 返回的 `expected_file_id`；旧版本通过 lineage 保留并由版本清理策略回收。服务端用 delivery id 保证同一保存只落一次。

#### 平台技能发布与订阅

1. 管理员在 `platform/ailab_platform/api/routes_platform_skills.py` 创建条目并上传 `.md` 或 `.zip` 版本；文件归系统所有者，`purpose="platform_skill"`，不计用户配额。
2. Worker 扫描完成后，`platform_skills.activate` 校验名称、系统所有者、head、无 `skill_error` 后把版本设为 `current_file_id`；管理员也可以显式 `publish` 选择或回退版本。
3. 用户 `add` 只建 `user_platform_skills` 订阅，不复制内容；每用户最多 100 个订阅。下一次 Run 读取当前发布版本，已开始的 Run 仍用冻结的 `file_id`。用户 `customize` 后使用自己的副本，`reset` 清副本并回到跟随平台；管理员删除技能只脱钩副本，不删除用户文件。
4. 平台技能、镜像技能和个人技能共享名称空间；平台名创建后不能改，删除后名称仍保留，避免旧订阅和策略指向新内容。

#### 草稿：提取、编辑与确认

1. `extract` 从对话可见分支建立草稿；`open_skill` 从已有个人技能或镜像技能打开；平台技能走 `customize`/`admin_draft`。每个用户最多 20 个活动草稿。
2. 对话提取由 `process_draft` 在后台调用模型，输入是固定提取规则、最近可见轮次、已有技能摘要和目标技能；模型只返回 JSON 文件清单。来源删除、轮次被改写或文件失效会在提交前被围栏拒绝。
3. 已有 ZIP 不交给 API 打开，`skill_unpack` 作业让 Worker 在受限子进程解包并回报内容寻址的文件清单；草稿编辑只操作这份清单。
4. `confirm` 重新校验 SKILL.md、名称、目标版本和并发 revision，然后生成新的 `PENDING` 文件版本。它仍要经过扫描和审核，确认草稿不等于技能已可用。

#### 扫描与审核

1. 用户、部门和全局策略存于 `SkillPolicy`；按技能名的 `disabled` 直接拒绝，`*personal*` 只决定个人技能与自定义副本的 `allowed/review/disabled`。全局禁用不能被部门设置覆盖，镜像清单里的 `roles` 也在这里生效（`effective_policy`）。
2. `review` 策略下，`pending_reviews` 只返回尚未审批的 READY 文件元数据；`review_skill` 写 `SkillApproval(file_id, state)`。审批绑定具体文件版本，新版本不会继承旧审批。
3. 基础技能不能关闭；未自定义的平台跟随版本不受 `*personal*` 审核影响，但受按名称策略和角色限制；自定义副本与个人技能适用 `*personal*`。

### 不变量

- `index.json` 必须与镜像技能目录逐文件 hash 一致；不一致时 Agent 拒绝使用沙箱技能，不能靠放宽证明解决（`platform/tests/test_skill_governance.py::test_drift_is_refused_and_never_cached`）。
- 基础技能与内置技能的名字、内容和资源 hash 随镜像发布；个人技能只在 Run 输入明确列出且属于用户时可读，平台技能只从已发布或曾发布版本读取（`platform/tests/test_platform_skills.py::test_named_service_content_route_enforces_run_binding`）。
- 技能正文整份返回或明确拒绝；`load_skill` 不截断，也不能把“导航索引”当成正文（`platform/tests/test_skill_modules.py::test_navigation_and_whole_file_reads`、`platform/tests/test_skill_modules.py::test_budget_refusal_and_group_cumulative_admission`）。
- 压缩摘要里的已读模块只是导航；正文不可见时必须重读，不能因 `loaded_skills` 认为内容仍在上下文（`platform/tests/test_skill_modules.py::test_visibility_requires_paired_complete_current_text`）。
- 已开始的 Run 不中途换平台版本；改版只影响下一次 Run（`platform/tests/test_platform_skills.py::test_follow_updates_next_run_but_running_run_stays_pinned_and_unadded_user_cannot_read`）。
- 个人技能只属于其所有者；平台技能使用系统所有者，普通用户文件接口不能读取未订阅或未发布的平台文件。
- 关闭的技能不再出现在能力搜索和入口，也不能被 `load_skill` 或消息选择绕过（`platform/tests/test_skill_settings.py::test_load_skill_is_refused_for_a_switched_off_skill`）。
- 不可信 ZIP 只由 Worker 在受限子进程解包，API 不打开；确认草稿生成的新文件仍是 `PENDING`，必须重新扫描和审核。
- 审核是逐文件版本的；全局禁用优先于部门、用户开关和个人审批（`platform/tests/test_skill_governance.py::test_personal_review_approval_and_deleted_file`）。

### 怎么扩展

#### 加一个平台技能

1. 用管理员接口创建条目，再上传包含有效 `SKILL.md` 的 `.md` 或 `.zip`；`SKILL.md` 的 `name` 必须与条目名一致，`description` 非空，脚本和素材用相对路径。
2. 等 Worker 扫描为 READY，确认 `activate` 或显式 `publish` 后，用户 `add` 订阅；不需要改代码或重建镜像。若内容依赖新的沙箱库或字体，那不是平台技能能解决的，应改沙箱镜像。
3. 加接口测试覆盖：名称冲突、未发布/未订阅读取、下一 Run 跟随、自定义副本不跟随、重置和管理员删除脱钩；参考 `platform/tests/test_platform_skills.py`。

#### 改技能审核策略

1. 先改 `platform/ailab_platform/skill_policy.py::effective_policy` 的状态合并和优先级；全局禁用必须在并集里，不能被部门或逐文件审批降级。若新增状态，同步改 `platform/ailab_platform/api/routes_skills.py::PolicyInput`、`review_skill` 的合法状态、`usable_personal`、`usable_platform`、`frozen_platform` 和 `review_queue`。
2. 检查三条使用路径：列表/开关、Run 输入校验、Agent `load_skill`；只改其中一条会让 UI 显示可用而模型实际不能读，或相反。自定义平台副本与个人技能共用逐文件审批，平台跟随版本不继承 `*personal*`。
3. 新状态是否需要迁移取决于它是否落库；`SkillPolicy.state` 是字符串，但旧数据回填、管理页文案和策略排序仍要一起改。测试至少覆盖全局/部门冲突、用户关闭、审批通过前后、版本更新后的重新审批（`platform/tests/test_skill_policy_api.py`、`platform/tests/test_skill_governance.py`）。

### 配置

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_SKILLS_DIR` | 平台进程读取技能清单的目录；镜像里指向 `/opt/ailab/skills` | 仓库 `images/sbx-office/skills` |
| `AILAB_SKILL_DRAFT_INPUT_CHARS` | 技能提取的对话与目标文件输入预算 | `24000` |

文件大小和数量上限是 `platform/ailab_platform/skillfiles.py` 中的常量：`MAX_SKILL_MD`、`MAX_ZIP`、`MAX_UNPACKED`、`MAX_ENTRIES`。沙箱里的同一技能包位于 `/opt/skills/<name>`，技能正文里的绝对路径按沙箱副本写。

### 已知的坑

见 [HANDOFF · 技能](../../HANDOFF.MD#技能)。

## 相关决策

- [ADR 0005 有预算的历史与会话元数据](../architecture/adr/0005-budgeted-conversation-memory.md)：上下文按预算组装、摘要 CAS 和账本边界；记忆文档仍遵守“不静默截断”的约束。
- [ADR 0019 记忆读取路径与治理](../architecture/adr/0019-memory-read-path-and-governance.md)：按需历史检索、写入护栏、墓碑和来源删除的出发点；条目存储与上限已由后续 ADR 取代。
- [ADR 0022 有来源、可隔离、可续的记忆](../architecture/adr/0022-memory-consistency-and-scoped-recall.md)：项目隔离、来源和后台 claim 的合同。
- [ADR 0030 模型请求前缀稳定](../architecture/adr/0030-prefix-stable-model-requests.md)：记忆和技能变化只能追加，不能改写已发请求。
- [ADR 0035 每个作用域一份有界记忆文档](../architecture/adr/0035-memory-v2.md)：`memory_items`、完整文档、单调用 Dreaming、会话快照与撤销。
- [ADR 0036 记忆质量标准、每晚一条整理链与来源项目标签](../architecture/adr/0036-memory-quality.md)：整理节奏、转变、删除来源保留记忆和 `origin_project_id`；其中受保护/待确认语义已被 ADR 0037 取代。
- [ADR 0037 记忆只有一种行](../architecture/adr/0037-memory-single-origin.md)：现行的一行一分类、统一整理、按内容拒绝凭据的规则。
- [ADR 0043 低频工具经 call_tool 按需加载](../architecture/adr/0043-on-demand-tools.md)：`memory` 与 `save_skill` 经 `call_tool` 按需展开，`load_skill` 仍负责整份技能正文。

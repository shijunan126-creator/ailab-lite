# HANDOFF

踩过的坑，按模块分节。改哪个模块，就读对应的分节；模块的代码地图和扩展步骤在 [docs/modules/](docs/modules/)。

- 每条先写规则，再写原因和代码位置，不超过 4 行。
- 新坑先找所属分节，能并进已有条目就改那一条，不能才新加。
- 坑被代码消除后直接删掉这一条。不写日期、PR 号和“已修复”。

## 通用

### 本地测试环境
- **在 `platform/` 下运行 Ruff 和 pytest，并使用当前 worktree 的虚拟环境**。仓库根包含 vendored 与生成文件，共享 editable 安装还可能加载另一个 checkout 的代码。代码：`platform/pyproject.toml::[tool.ruff]`。
- **同一个一次性 PostgreSQL 只允许一个 pytest 进程使用**。`conftest.py` 会清空 `public` schema，xdist 又按 worker 建库；并行启动第二个 pytest 会互相清库。代码：`platform/tests/conftest.py::_worker_postgres`。
- **Office reader 缺失按环境故障处理，先构建 runtime，不要 skip**。跳过会掩盖整条文档读取链路未被执行。代码：`platform/tests/conftest.py::office_reader_built`；构建：`vendor/office-suite/build.py::main`。

### 验证与进程
- **健康探针通过不代表端口属于本次启动的进程**。旧 Portal、SSH 转发或残留服务也可能返回健康；重启前核对 pidfile、命令行和监听端口。代码：`platform/scripts/dev_up.sh::start`。
- **期限算术用虚拟时钟，性能边界用行数或语句数证明**。共享 runner 上的绝对墙钟会把相邻 worker 的负载算进产品。测试：`platform/tests/faults/test_job_heartbeat.py::VirtualTimeLoop`。

### 协作与本机环境
- **`gh pr merge --auto` 不是等待器**。仓库没有 required checks 时它会立即合并，即使检查还在跑；要“绿了再合”，先自己确认最终 head 的检查全部通过。
- **同一分支不能由两个 worktree 并发提交**。另一处推进 HEAD 后，本地旧 index 看起来像反向暂存；发现时立即隔离到新分支，不要提交那些反向差异。
- **用 `git hash-object -w` + `update-index --cacheinfo` 部分暂存时，路径相对仓库根**。在子目录执行会在根目录生成错误的索引项。
- **`docker build` 报 “No matching distribution” 先查是不是 DNS 失败**。只重试失败的层，保留已完成的大依赖层。
- **Colima 不把宿主 `/tmp` 挂进 VM，`docker run -v /tmp/...` 得到空目录**。共享文件放用户缓存目录或经 stdin 传入；自定义 `COLIMA_HOME` 要先建目录，否则悄悄回退默认目录。
- **DNS 解析到 `198.18.*` 时是宿主 VPN 的 fake-IP**。按宿主 VPN → Colima → 容器的顺序查解析，不要为它放行非公网地址或关掉 SSRF 检查。

## Agent

### 工具目录与按需工具
- **工具目录在 Run 开始时冻结，新工具只影响之后启动的 Run**。暂停中的 Run 恢复后继续使用自己的目录，否则前缀和权限边界都会变。代码：`platform/ailab_platform/agent/loop.py::_tools_for_run`；测试：`platform/tests/test_deferred_tools.py::test_a_frozen_catalog_keeps_its_on_demand_names`。
- **按需工具只在 `call_tool` 的名字枚举里冻结名字，说明和参数取当前代码**。判断“说明已读”只认与 `call_tool` 调用 ID 配对、顶层带 `tool_doc` 和 `parameters` 的结果；其他工具输出不能冒充说明。代码：`platform/ailab_platform/agent/deferred.py::_documented`；测试：`platform/tests/test_deferred_tools.py::test_only_a_call_tool_result_counts_as_the_description`。
- **新增按需工具必须在 `SUMMARY` 写一句话简介**。`call_tool` 的枚举和目录测试都从这里生成，漏写会让模型看到空项。代码：`platform/ailab_platform/agent/deferred.py::SUMMARY`。
- **退役工具要同时摘目录、handler、内部端点和文件依赖，并加入 `RETIRED_TOOLS`**。恢复旧 checkpoint 时重建 `messages[0]` 与目录并记边界；只删目录会留下走不到的代码。代码：`platform/ailab_platform/agent/tools.py::RETIRED_TOOLS`、`platform/ailab_platform/agent/context.py::ContextManager::build`；测试：`platform/tests/test_prefix_cache.py::test_a_checkpoint_with_retired_tool_names_is_rebuilt_at_a_recorded_boundary`。
- **不要恢复按模型预算裁剪的精简目录**。每个模式只有一份目录，`_tools_for_run` 不再按预算删 schema；小窗口模型多付 schema，但不能在请求构造后改工具集合。代码：`platform/ailab_platform/agent/loop.py::_tools_for_run`。
- **`load_skill` 对基础技能先返回导航，正文按资源名再读**。把导航当整份技能会让模型缺操作细节；导航只列包和依赖。代码：`platform/ailab_platform/agent/prompts.py::load_skill`、`platform/ailab_platform/agent/skill_modules.py::read_module`；测试：`platform/tests/test_skill_modules.py::test_navigation_and_whole_file_reads`。

### 消息、图像与上下文
- **用户消息文本只由 `user_text` 生成**。技能标记按用户原文码点插入，之后才套定时任务头和文档引用；实时、插话、历史必须走同一函数，否则历史字节与首次发送不同。代码：`platform/ailab_platform/agent/prompts.py::user_text`；测试：`platform/tests/test_prefix_cache.py::test_scheduled_turn_and_its_steer_place_skills_and_document_references_the_same_way`。
- **浏览器时区在发送时冻结到 Run；`user_timezone` 只是回退口径，不代表用户此刻所在时区**。服务器解析不了浏览器时区时按未提供处理并回退账号时区；不能因此拒收消息。代码：`platform/ailab_platform/api/routes_runs.py::validate_run_input`、`platform/ailab_platform/agent/tools.py::local_time`；测试：`platform/tests/test_modes_skills_attachments.py::test_the_browser_timezone_is_frozen_on_the_run`。
- **已准备的图片和文档页必须真的带 `image_url`，取不到就让该 Run 失败**。降级成文字说明会让模型以为自己看过图；工具图片与用户图片都要登记来源。代码：`platform/ailab_platform/agent/loop.py::AgentLoop::_user_content`、`platform/ailab_platform/agent/multimodal.py::image_part`。
- **压缩或重建按整份待激活请求校验，不只校验摘要或单个块**。追加只按窗口阈值触发，替换必须在没有 pending 且工具组配对完整时进行；放不下用 `ContextPause`。代码：`platform/ailab_platform/agent/context.py::ContextManager::compact`、`platform/ailab_platform/agent/context_protocol.py::validate_safe_compaction_point`。
- **上下文计数以供应商 `prompt_tokens` 锚点为准，锚点绑定模型、ABI、纪元、工具指纹和前缀摘要**。边界时清空锚点；假 usage 若为常数，会把已发送内容算成免费。代码：`platform/ailab_platform/agent/context_usage.py::request_tokens`。
- **工具结果按整批首次准入，技能正文要么完整放入要么结构化拒绝**。批次上限从硬上限扣除必须内容后计算，不能在 call/result 中间压缩，也不能截断后标完整。代码：`platform/ailab_platform/agent/context_band.py::admit_tool_result`；测试：`platform/tests/test_context_band.py::test_admission_accounts_batch_args_results_images_and_whole_file`。
- **旁路调用必须复用主请求的消息、工具、`tool_choice` 和思考档位**。临时关思考或改成 `tool_choice=none` 会改写供应商模板并丢掉整段前缀；摘要、收尾、检查都走同形请求。代码：`platform/ailab_platform/agent/loop.py::AgentLoop::_answer_without_tools`；测试：`platform/tests/test_prefix_cache.py::test_summary_keeps_the_runs_thinking_level`。

### 思考与模型输出
- **所有思考字段只经 `reasoning_of` 读取，`</think>` 标签拆分只能按模型配置开启**。字段来源包括 `reasoning_content`、`reasoning`、`thinking`、`reasoning_details`；自动猜标签会误伤正文。代码：`platform/ailab_platform/thinking.py::reasoning_of`、`::ThinkSplitter`；测试：`platform/tests/test_think_tags.py::test_non_streamed_calls_split_tags_and_read_any_reasoning_field`。
- **prefilled 模板到结尾都没有 `</think>` 时按正文处理，关闭思考的请求不按 prefilled 处理**。否则正常回答会被整段藏进思考。代码：`platform/ailab_platform/thinking.py::ThinkSplitter::never_closed_prefill`；测试：`platform/tests/test_think_tags.py::test_never_closed_prefill_is_the_answer`。
- **思考档位在一次回答内固定，旁路调用沿用同一档位**。网关按档位分缓存，换档那一轮必然冷启动；只有用户换档才应多一轮重建。代码：`platform/ailab_platform/agent/loop.py::AgentLoop::_call_model`。

### 恢复、预算与后台命令
- **付费响应先 checkpoint 再执行工具；有副作用的工具开始前写 `tool_log`**。只读工具不单独 checkpoint，恢复时允许重做；恢复调用用 `announce=False`，否则 UI 会多出一个永远转圈的步骤。代码：`platform/ailab_platform/agent/loop.py::AgentLoop::_durable_tool`、`::_run_tool`；测试：`platform/tests/test_agent_loop.py::test_tool_events_carry_the_call_id_that_pairs_them`。
- **同一 Run 的 checkpoint 写入必须串行，`expected_step` 只能有一个 CAS 在途**。心跳、预算暂停和并发工具都走同一把按 Run 的锁，否则会随机 409。代码：`platform/ailab_platform/agent/loop.py::AgentLoop::_checkpoint`；测试：`platform/tests/test_agent_loop.py::test_two_concurrent_checkpoints_never_share_one_expected_step`。
- **checkpoint 超限写有界冻结记录并暂停，不能变成 413 失败或恢复被省略的付费结果**。已消费日志只保留身份指纹和完成状态，pending 结果与不可重放标记不能提前删。代码：`platform/ailab_platform/agent/checkpoint_budget.py::bounded_checkpoint`。
- **`budget_window_id` 与 `job_id` 分开；只有用户明确继续预算类暂停才重置窗口**。回答 `ask_user` 和租约自动恢复只换 job；累计 token 与配额永不随时间续跑重置。代码：`platform/ailab_platform/agent/budgets.py::WorkBudget`、`platform/ailab_platform/agent/loop.py::AgentLoop::_process`。
- **命令的 `wait_seconds` 只是本次返回窗口，硬期限由平台 supervisor 执行**。等待结束不是失败；重试同一调用只能继续等待原 handle，不能重跑命令。代码：`platform/ailab_platform/agent/background_exec.py::command_handle`、`platform/ailab_platform/agent/background_helper.py::supervise`；测试：`platform/tests/test_bash_wait_window.py::test_a_retried_start_waits_on_the_running_command_instead_of_launching_it_twice`。
- **完成态 `bash_wait` 没有 `handle` 时从原调用参数取**。后台句柄清理与快照判断都要兼容这一点，否则 `KeyError` 会把整轮判失败。代码：`platform/ailab_platform/agent/loop.py::AgentLoop::_durable_tool`。
- **输出头尾拼接必须按 `total` 和 `tail_start` 判断是否重叠**。短输出直接相加会重复内容。代码：`platform/ailab_platform/agent/background_exec.py::_BoundedOutput`；测试：`platform/tests/test_bash_wait_window.py::test_output_stays_bounded_across_chunks`。

### 工作区工具
- **`edit` 的容错匹配用折叠后的 `str.find`，不要用逐字符表或大正则**。折叠保持长度不变，行尾空白用删除点和 `bisect` 映射回原文；大正则会按文件长度乘 `oldText` 长度退化。代码：`platform/ailab_platform/domain/text_edit.py::_fuzzy_spans`、`::apply_replacements_to_original`；测试：`platform/tests/test_text_edit.py::test_a_fuzzy_edit_does_not_rewrite_the_rest_of_the_line`。
- **文件工具只把 400/404/413 映射成模型可修正的错误，410/401/403/503/5xx 原样上抛**。把租约丢失当 `ENOENT` 会让模型继续在失效沙箱上工作。代码：`platform/ailab_platform/agent/tools.py::_file_error`。
- **`read` 按 Broker 的 `line`/`length` 取窗口，并多读 1 字节区分“读到末尾”**。完整文件读取会撞上限，且未到末尾时不能报总行数。代码：`platform/ailab_platform/agent/tools.py::execute_tool`；测试：`platform/tests/test_workspace_tools.py::test_read_windows_by_line_and_reports_the_next_offset`。

### 分叉
- **同模式分叉复制 checkpoint 时沿用血缘根会话头，且不改模型可见字节**。供应商按会话头缓存；源 run ID 留原值，用 `run_aliases` 在摘要来源和历史读取处换成分支身份。代码：`platform/ailab_platform/api/fork_context.py::cache_session`、`platform/ailab_platform/agent/loop.py::AgentLoop::_call_model`；测试：`platform/tests/test_prefix_cache.py::test_same_mode_fork_extends_the_forked_turns_last_request`。

## API

### 创建与会话
- **新会话的首条消息必须与 Conversation、Run、outbox 在同一事务创建**。先提交空会话再发消息时，429/422 会留下 `runs=0` 的幽灵会话。代码：`platform/ailab_platform/api/routes_conversations.py::start_conversation`；测试：`platform/tests/test_first_message_atomic.py::test_rejected_first_message_leaves_no_empty_conversation`。
- **`client_send_id` 重放必须返回原 Conversation/Run，且去重先于附件、额度、会话忙碌检查**。同一身份换内容返回 409；直接发送与队列发送共用用户级身份。代码：`platform/ailab_platform/api/routes_runs.py::start_run`；测试：`platform/tests/test_client_send_id.py::test_retry_returns_current_original_run_and_exactly_one_dispatch`。
- **历史、摘要、工作区、预览和分叉都按 Run 祖先读取，不按时间或 `superseded_by_run_id` 推断**。树形会话里“时间更早”不等于祖先；测试数据也要写 `parent_run_id` 与 `active_run_id`。代码：`platform/ailab_platform/conversation_tree.py::path_ids`；测试：`platform/tests/test_conversation_tree.py::test_edit_earlier_path_versions_switch_restore_and_fork`。
- **`branch_revision` 只在编辑、重试或切换活动路径时递增，普通续聊和排队续轮不递增**。Portal 在提交时冻结修订并贯穿上传等待；API 先做成功请求幂等重放，再校验修订。代码：`platform/ailab_platform/api/routes_conversations.py::select_active`、`platform/ailab_platform/api/routes_runs.py::start_run`；测试：`platform/tests/test_branch_consistency.py::test_branch_fence_rejects_stale_actions_but_preserves_send_replay_and_continuation`。
- **摘要缓存必须检查 `through_run_id` 是当前 Run 祖先，并用数据库实际边界做 CAS**。只比较客户端看到的边界会泄漏另一分支的旧答案或永久冲突。代码：`platform/ailab_platform/api/routes_model_context.py::save_model_summary`；测试：`platform/tests/test_conversation_tree.py::test_summary_cache_boundary_never_leaks_and_recomputes_after_switch`。

### Run 状态与 outbox
- **`transition_run` 在取得用户锁后先 `flush` 再 `refresh`**。调用方在锁前写的 question/runtime_meta 必须先落库，否则会被 refresh 丢掉；用户锁必须可重入。代码：`platform/ailab_platform/run_state.py::transition_run`。
- **outbox 唤醒要跳过 savepoint 提交**。`after_commit` 在嵌套事务也会触发，此时 outbox 行尚未对外提交；只让外层提交唤醒。代码：`platform/ailab_platform/api/jobs.py::_wake_dispatch_after_commit`。
- **409 只表示失去 Run attempt 或路径版本冲突，普通业务冲突用 422/404**。Agent 把 409 当 fencing 并终止 Run，同名技能之类的正常拒绝会变成“意外崩溃”。代码：`platform/ailab_platform/api/routes_internal.py`、`platform/ailab_platform/api/runtime_fence.py::fenced_run`。
- **pending 队列的五条是待领取容量，不是累计插话上限**。已确认插话按 checkpoint 的 ID 和顺序全部展示，不能截断前五条或暴露未确认领取。代码：`platform/ailab_platform/api/inbox.py::pending_query`、`::acknowledge_steers`。
- **API 的事件循环上不能执行同步数据库、用户锁或存储 I/O**。SQLite 写锁与 teardown 会互相等待并卡满 busy timeout；异步处理器里的阻塞段放进线程池。代码：`platform/ailab_platform/api/routes_auth.py::oidc_callback`、`platform/tests/conftest.py::no_sql_on_serving_loop`。

### 配额与账本
- **只在 reserve 接缝把带 `quota_exceeded` 的 429 转成额度暂停**。其他 429 是上游或并发限制，不能触发额度暂停、模型 fallback 或失败计数。代码：`platform/ailab_platform/quota.py::reserve_usage`、`platform/ailab_platform/agent/budgets.py::QuotaPause`；测试：`platform/tests/test_quota_pause.py::test_reservation_quota_bypasses_model_retry_fallback_and_failure_metrics`。
- **额度暂停跳过 `outputs/` 兜底交付，只保存 checkpoint、工作区和已有结果**。取消宽限期从 `max(now, resets_at)` 起算；成功续跑或其他暂停都要清掉旧 quota 元数据。代码：`platform/ailab_platform/api/inbox.py::on_transition`、`platform/ailab_platform/api/reaper.py::purge_expired_workspaces`；测试：`platform/tests/test_quota_pause.py::test_monthly_pause_survives_timeout_and_workspace_gc_until_reset`。
- **部门归属和价格输入在 reservation 时冻结，settle 只修正同一条预留**。报表按调用时快照分组，不 join 当前 Run；历史记录不按新配置重算。代码：`platform/ailab_platform/quota.py::reserve_usage`、`::settle_usage`。
- **用量页面必须用一条 SQL 同时读已结算与未结算预留**。分开查询会在结算切换瞬间双计。代码：`platform/ailab_platform/quota.py::usage_summary`。

### 搜索
- **SQLite 的 FTS 候选先 `MATERIALIZED` 再 JOIN，PostgreSQL 用持久 `terms_tsv` + GIN**。直接平铺 JOIN 会对每个会话跑一次 `MATCH`，表达式 `to_tsvector` 在小表会被规划器放弃。代码：`platform/ailab_platform/search_index.py::candidate_query`。
- **词项并集只增不减，最终命中只认归一化后的可见内容**。编辑、重试、删除后旧词项可以留下，但 SQL 预筛选必须是 NFKC/casefold 的候选超集，隐藏 UI 源码不能兜底。代码：`platform/ailab_platform/search_index.py::_fold`、`::_indexed_search`；测试：`platform/tests/test_search.py::test_search_does_not_match_hidden_ui_source`。
- **无索引词项的输入只回退扫描最近 200 个会话，不能扩大成无界扫描**。单字、纯标点或分词器不覆盖的文字无法承诺全量。代码：`platform/ailab_platform/search_index.py::_fallback_search`。
- **PostgreSQL 的 Unicode 大小写搜索依赖 ICU root collation，不能把 UTF8 当 casefold**。C locale 下 `lower('ÉCOLE')` 仍是 `École`；SQLite 用注册的 Unicode lower 函数。代码：`platform/ailab_platform/db.py::_sqlite_pragmas`、`platform/ailab_platform/search_index.py::_lower`。

### 定时任务
- **定时执行在用户锁内 CAS `next_run_at`，并用 `start_run(locked=True, commit=False)` 与 Run/outbox 同事务提交**。不能调用会自行提交的入口；项目删除要清空 `project_id` 并停用任务。代码：`platform/ailab_platform/api/routes_schedules.py::dispatch_schedules`、`::execute_task`。
- **平台代发轮次继承叶子 Run 的 `options.client`**。`run_js` 是否进工具列表取决于 `client == "portal"`，缺省会改变目录并丢掉前缀。代码：`platform/ailab_platform/api/routes_schedules.py::execute_task`、`platform/ailab_platform/agent/loop.py::_full_catalog`。
- **墙钟时间必须做 UTC roundtrip，秋季重复时间固定 `fold=0`**。`zoneinfo` 对不存在的本地时间也会构造 `datetime`；每周缺失时间要扫描超过 7 天。代码：`platform/ailab_platform/api/routes_schedules.py::next_occurrence`、`::first_occurrence`。
- **一次性任务执行完要删除，否则占满每用户五条名额**。代码：`platform/ailab_platform/api/routes_schedules.py::dispatch_schedules`。

### 分叉、文件版本与管理
- **分叉不复制账本或待执行任务，Artifact 复用 `file_id`，工作区复制成新 Run 自有对象**。复制失败要回滚 DB 并清理新 key；源工作区过期时 Work 分叉返回 422，不能换用别的快照。代码：`platform/ailab_platform/api/routes_conversations.py::fork_conversation`；测试：`platform/tests/test_chat_fork_retry.py::test_fork_copies_history_artifacts_usage_summary_and_independent_objects`。
- **文件版本号取整条 lineage 的 `max(version)+1`，不是父版本加一**。上传从旧版分支也要取整链最大值；唯一约束与单 head 索引兜底。代码：`platform/ailab_platform/library.py::assign_file_version`；测试：`platform/tests/test_file_versions.py::test_concurrent_artifact_commits_allocate_consecutive_versions`。
- **`/files?ids=` 是精确版本查找，不能套浏览列表的 `is_head` 过滤**。历史版本引用、预览和下载必须保留原 `file_id`；常规列表仍只列 head。代码：`platform/ailab_platform/api/routes_files.py::list_files`；测试：`platform/tests/test_file_versions.py::test_browsing_lists_heads_but_explicit_ids_preserve_version_identity`。
- **内部 bootstrap 新增字段必须显式加入 `InternalRunDetail`**。FastAPI 的 `response_model` 会静默过滤未声明字段，handler 单测也发现不了。代码：`platform/ailab_platform/schemas.py::InternalRunDetail`。
- **管理路由新增 HTTP 方法时要同步审计映射**。`AuditedRoute` 的操作名表漏掉 PUT 会让正常 403/422 变成 `KeyError`/500。代码：`platform/ailab_platform/audit.py::AuditedRoute`；测试：`platform/tests/test_identity_admin.py::test_context_settings_platform_admin_only_and_audited`。
- **`users.settings` 是整份 JSON 写入，必须在同一用户锁内 refresh 后写新副本**。用认证时的旧对象覆盖会丢掉并发开关。代码：`platform/ailab_platform/api/routes_memory.py::update_memory_settings`、`platform/ailab_platform/locks.py::user_critical_section`。
- **事件历史的内容字段名是 `payload`，不是 `data`**。读取事件、分叉重映射和 SSE 都必须使用同一字段。代码：`platform/ailab_platform/api/routes_runs.py::event_history`、`platform/ailab_platform/models.py::RunEvent`。

## 文件

### 入库与解析
- **`scan_unavailable` 绝不能当 clean，文件保持 pending 并重试到 `scan_exhausted`**。默认三次、指数退避；缩略图/预览分支失败也不能把文件回退成 READY。代码：`platform/ailab_platform/worker/main.py::_handle`、`platform/ailab_platform/worker/pipeline.py::_process_file_impl`；测试：`platform/tests/test_scanner_isolation.py::test_scan_unavailable_retries_and_remains_quarantined`。
- **不可信文件只在 Worker 的受限子进程解析，子进程只继承 `PATH`/`LANG`/`LC_ALL`/`TMPDIR`**。API 和 Broker 不在进程内解码；内存、CPU、墙钟和输出都设上限。代码：`platform/ailab_platform/worker/isolate.py::execute`、`::_child`。
- **ZIP 必须先有界遍历 central directory，再交给 `ZipFile` 分配目录**。同时校验实际展开字节和 CRC，拒绝 ZIP64、分卷、重复/绝对/穿越路径。代码：`platform/ailab_platform/worker/safety.py::directory_guard`、`::inspect`；测试：`platform/tests/test_upload_safety.py::test_zip_directory_count_rejected_before_full_directory_allocation`。
- **缩略图解码只在 Worker，失败只丢派生资源，不能让原文件失败**。JPEG 可用 draft 降采样，其他格式按全尺寸像素上限；SVG、损坏和超限都跳过缩略图。代码：`platform/ailab_platform/worker/pipeline.py::_images`；测试：`platform/tests/test_worker.py::test_bad_thumbnail_never_rejects_file`。
- **`ready` 只证明安全与身份，不证明正文、页图或 OCR 已准备好**。缺少派生资源时显式准备或等待，不能返回假空文本；新普通输入还要通过自己的 `input_lifecycle`。代码：`platform/ailab_platform/api/native_preparation.py::native_state`、`platform/ailab_platform/api/document_preparation.py::bind`；测试：`platform/tests/test_lazy_input_preparation.py::test_safe_original_is_ready_before_lazy_native_and_failure_does_not_break_work`。
- **旧 sandbox 文件只有 `ready`、没有持久 `scan` 记录时必须重扫，不能补写成 clean**。重扫走现有 Worker 队列，校验摘要和大小后才允许预览或原生打开。代码：`platform/ailab_platform/api/preview_requests.py::legacy_preview_scan_required`、`::request_legacy_scan`；测试：`platform/tests/test_file_previews.py::test_legacy_native_open_rescans_once_without_requesting_render`。
- **原生文本包只接受 `manifest.json` 和 `text.txt` 两个成员，并校验传输摘要、大小和每个 range**。多一个成员或摘要不符都拒绝，不能放宽 tar 解包。代码：`platform/ailab_platform/worker/native_preparation.py::unpack`；测试：`platform/tests/test_native_text_ranges.py::test_native_bundle_unpacks_exactly_two_files_and_rejects_extra_or_corrupt_members`。

### 容量与派生资源
- **派生容量预留必须在首字节 I/O 前提交，业务引用与 ready 发布同事务**。失败对象在实际删除成功前继续计占用；正常放弃清写租约，崩溃孤儿才用宽限。代码：`platform/ailab_platform/api/derived_assets.py::reserve`、`::publish`、`::abandon`；测试：`platform/tests/test_derived_assets.py::test_staged_upload_is_not_published_until_business_transaction`。
- **容量压力只在维护循环按创建时间淘汰可重建的 `cache`**。`native`/`ocr`/`capture`/`transfer`、有 pin、仍在用和刚创建的缓存不能淘汰；先失效元数据，再删对象，成功后才释放字节。代码：`platform/ailab_platform/api/derived_assets.py::evict_oldest`、`::evictable`；测试：`platform/tests/test_derived_invalidation.py::test_capacity_pressure_evicts_the_oldest_rebuildable_caches_and_nothing_in_use`。
- **规范消息截图用可空到期时间表示长期保留，不要用 `datetime.max` 哨兵**。PostgreSQL 在非 UTC 时区回传可能超过 Python 年份上限；最后一条消息引用释放后才清容量。代码：`platform/ailab_platform/api/viewer_captures.py::save`；测试：`platform/tests/test_capture_assets.py::test_canonical_capture_survives_temporary_ttl_but_last_message_release_cleans_capacity`。
- **原生范围缓存绑定基础索引代际，source/parser 相同也不能复用已退役代际**。同一批多页应批量取，不要每页一次 RPC。代码：`platform/ailab_platform/api/native_ranges.py::cached_scope`、`::request_scope`；测试：`platform/tests/test_native_document_scopes.py::test_scoped_pages_join_available_search_and_base_generation_fences_reuse`。

### OCR 与页图
- **OCR 操作身份绑定完整请求范围，失败恢复不能把“剩余页”当新身份**。共享消费者用独立引用持有 operation；已有部分页缓存不等于选定页全部完成。代码：`platform/ailab_platform/api/document_ocr.py::request_scope`、`::read_cached`；测试：`platform/tests/test_document_operations.py::test_ocr_execution_ids_are_unique_per_image_and_dispatch`。
- **只有页码属于文件本身的格式才能在浏览器和服务端渲染间互换**。PDF 页和 PPT 幻灯片可以；Word 分页由排版引擎决定，页图只走服务端。代码：`platform/ailab_platform/api/document_visual.py::browser_capable`；测试：`platform/tests/test_document_visual.py::test_only_intrinsic_page_numbers_use_the_open_preview`。
- **页图清单的选页、`page_count` 和 `truncated` 必须共用同一有效总页数**。已缓存前 20 页不代表整份可用；部分或损坏缓存不能宣称完整预览。代码：`platform/ailab_platform/contracts/file_preview.py::cached_page_preview`；测试：`platform/tests/test_eager_previews.py::test_partial_or_invalid_cache_does_not_claim_complete_preview`。
- **旧预览恢复要同时覆盖公有预览入口和 `request_preview` 的失败缓存短路**。只改排队入口，旧的 `render_failed` 仍会永久返回；恢复扫描期间必须有终态且不能提前排渲染。代码：`platform/ailab_platform/api/preview_requests.py::request_preview`、`platform/ailab_platform/api/routes_files.py::get_preview`；测试：`platform/tests/test_file_previews.py::test_legacy_artifact_rescans_before_retrying_failed_preview`。

### 交付与清理
- **`/workspace/outputs` 是自动交付区；交付身份按修订意图生成，不能只按路径或 hash**。A→B→A 是三次修订；恢复先读 receipt 确认结果，再核对当前字节。代码：`platform/ailab_platform/agent/loop.py::AgentLoop::_deliver_leftovers`、`platform/ailab_platform/api/routes_internal.py::_commit_artifact`；测试：`platform/tests/test_file_identity.py::test_output_revision_slot_replay_and_legacy_repair`。
- **交付 receipt 丢失后必须能幂等重放，且不能删除可能已发布的对象**。先查 `RunDelivery`，同 id 异内容与租约 fencing 分别报错。代码：`platform/ailab_platform/api/routes_internal.py::delivery_receipt`、`::internal_commit_artifact`；测试：`platform/tests/test_artifacts.py::test_retry_after_staging_was_already_consumed_is_idempotent`。
- **工作区交付名与资料库冲突后的显示名是两个身份**。metadata 保留 `artifact_filename`，同会话祖先链按它找父版本；否则后续同名交付会不断变成新 lineage。代码：`platform/ailab_platform/api/artifact_identity.py::repair_metadata`、`platform/ailab_platform/api/routes_internal.py::_commit_artifact`；测试：`platform/tests/test_file_versions.py::test_collision_renamed_artifact_keeps_lineage_and_name_on_later_turn`。
- **所有物理清理统一走 `delete_file_rows`，包含登记的 key 和 `derived/{id}/` 前缀**。DB 删除与删除意图同事务，提交后才删对象；失败由 lifecycle 重试。代码：`platform/ailab_platform/library.py::delete_file_rows`；测试：`platform/tests/test_file_versions.py::test_purge_cleans_unregistered_nested_derived_objects`。
- **共享文档测试夹具必须显式 `disposition=copy`**。同一秒生成的相同 PDF 会合法复用前一测试的 READY 文件与 OCR 缓存；不能靠改时间戳或清产品缓存掩盖状态串用。代码：`platform/ailab_platform/api/routes_files.py::upload_file`；测试：`platform/tests/test_document_reading_reliability.py::test_document_upload_fixture_keeps_identical_pdfs_and_ocr_state_independent`。

## 沙箱

### 租约与恢复
- **Broker 启动时接管仍有效的租约，并从资源元数据重建丢失的 Redis 状态**。不能先删光租约；创建中被打断的资源删除，`releasing` 删除失败保留容量并退避重试。代码：`platform/ailab_platform/broker/service.py::LeaseManager::recover`；测试：`platform/tests/faults/test_broker_recovery.py::test_restart_adopts_active_lease`、`::test_redis_flush_rebuilds_from_labels_including_renewed_expiry`。
- **同一 owner 只能有一个 Broker，所有权丢失后先停止变更再重新对账**。并行 Broker 会互相收养或删除对方的沙箱；启动锁和心跳都按 owner 隔离。代码：`platform/ailab_platform/broker/service.py::LeaseManager::_assert_owner`；测试：`platform/tests/faults/test_broker_recovery.py::test_second_live_broker_refuses_to_start`。
- **续租只接受未过期的原 token，且不能超过原始最大生存期**。410 立即停止；瞬态失败有界重试，只有成功响应才延长本地截止。代码：`platform/ailab_platform/broker/service.py::LeaseManager::renew`；测试：`platform/tests/faults/test_broker_recovery.py::test_renew_is_capped_by_original_max_lifetime`。
- **租约 TTL 不能按 Run 时长设置；长任务必须独立心跳续租**。模型调用或长命令不能饿死心跳；失租后 Agent 以安全 checkpoint 暂停。代码：`platform/ailab_platform/agent/loop.py::AgentLoop::_heartbeat`、`platform/ailab_platform/broker/service.py::LeaseManager::renew`。
- **资源删除失败不能当作不存在**。Docker 自动到期会与 inspect/元数据读取竞态；只有明确确认停止或不存在才收敛，daemon/权限错误要继续重试。代码：`platform/ailab_platform/sandbox/docker.py::DockerSandbox::release`；测试：`platform/tests/test_sandbox_lifecycle_adapters.py::test_docker_nonzero_delete_is_retryable_not_success`。

### 准入与渲染
- **渲染沙箱只能由受信系统主体从保留槽取得**。普通租约池满或排队不能占掉 render 槽；渲染有独立短 TTL 和内存上限。代码：`platform/ailab_platform/broker/service.py::LeaseManager::acquire_render`；测试：`platform/tests/faults/test_broker_recovery.py::test_reserved_render_slot_survives_full_general_pool_and_queue`、`::test_render_class_requires_trusted_system_principal`。
- **准入队列按 FIFO，单用户达到上限不能堵住其他用户**。队列满和等待截止是不同错误码；取消排队请求不能留下创建中的资源。代码：`platform/ailab_platform/broker/service.py::LeaseManager::acquire`；测试：`platform/tests/faults/test_broker_recovery.py::test_fifo_queue_and_position`、`::test_user_at_cap_does_not_block_other_users`。
- **Office 渲染只在沙箱预览链路执行，不装进 Worker 或平台镜像**。Worker 只消费受限输出并校验归档、摘要和 block 哈希；沙箱内 listener 的健康与渲染代际由 Broker 管理。代码：`platform/ailab_platform/worker/office.py::read`、`platform/ailab_platform/broker/service.py::LeaseManager::acquire_render`。

### 提供商语义
- **Docker 标签不可变，租约状态和到期时间写容器的 root-only `/run/ailab-lease.json`**。不能把可变状态放进用户 workspace 或快照。代码：`platform/ailab_platform/sandbox/docker.py::DockerSandbox::acquire`、`::update_metadata`；测试：`platform/tests/test_sandbox_lifecycle_adapters.py::test_docker_root_only_metadata_overrides_creation_labels`。
- **Docker 的 `/tmp` 是独立 tmpfs，跨目录原子替换必须把临时文件放在目标目录**。从 `/workspace` 直接 `Path.replace` 到 `/tmp` 会报 `EXDEV`；先验证内容，再在目标目录写临时文件并原子替换。代码：`platform/ailab_platform/agent/background_helper.py::put`、`platform/ailab_platform/sandbox/docker.py::DockerSandbox::acquire`。
- **OpenSandbox 的 `get_file_info` 是 lstat，批量查询任一路径缺失整批失败**。穿过链接父目录时要逐级判断；文件 API 能证明时先用它，证明不了再走 shell guard。代码：`platform/ailab_platform/sandbox/opensandbox.py::_plain_size`、`::_direct_write`。
- **OpenSandbox 的 `write_file` 会建父目录，`move_files` 目标已存在时拒绝，`read_bytes_stream` 的 offset/limit 不可靠**。新文件用文件 API 发布，替换走 `mv`；范围读取用独立的行窗口实现。代码：`platform/ailab_platform/sandbox/opensandbox.py::_guard_write`、`::put_file_from_path`、`::get_file_lines_to_path`；测试：`platform/tests/test_sandbox_file_transfers.py::test_opensandbox_writes_through_real_directories_without_an_exec`。
- **一次 exec 固定约一秒，文件 API 是毫秒级；多一次 exec 就多一秒工具延迟**。读写守卫先问文件 API，只有链接、缺失或 API 失败才退回 shell。代码：`platform/ailab_platform/sandbox/opensandbox.py::_plain_size`、`::_direct_write`。
- **OpenSandbox SDK 在 CI 可不安装，SDK 导入必须放在用到它且有退路的位置**。`MoveEntry` 失败要退回 `mv`，单测替身在 SDK 缺失时自己注入。代码：`platform/ailab_platform/sandbox/opensandbox.py::put_file_from_path`。
- **provider 测试必须执行真实 provider 类，只替换最底层传输**。mock 重写同名方法会漏掉范围读、临时路径和 SDK 语义；真实契约另跑 Docker/OpenSandbox 测试。代码：`platform/tests/test_sandbox_file_transfers.py::test_opensandbox_reads_a_plain_file_without_an_exec`。
- **沙箱路径替换要一次完成且只匹配路径起点**。Linux pytest 临时目录本身在 `/tmp` 下，逐个 `replace` 会把刚插入的根目录再改一遍。代码：`platform/ailab_platform/sandbox/mock.py::MockSandbox::_translate_command`；测试：`platform/tests/test_background_exec.py::test_mock_translates_command_paths_inside_file_payload`。
- **文件守卫的退出码避开 1 和 125–127**。`docker exec` 在容器不存在时返回 1，会被误读成路径越界；使用独立码再映射错误。代码：`platform/ailab_platform/sandbox/base.py::lines_error`。

### 镜像与网络
- **OpenSandbox 的 DNS-only egress 不能拦字面 IP，Docker 侧车模式必须用 `dns+nft`**。修改配置文件不等于运行策略已更新；重启前要核对实际进程和配置 hash，不能从 `/health=200` 推断策略正确。代码：`platform/scripts/osb_up.sh::OSB_CONFIG`。

## 记忆

### 整理提示词
- **整理提示词的操作格式必须写出 `"op"` 键**。真实模型会照抄示例；写成 `add {kind,...}` 时它会省略 `op`，`apply_ops` 整批丢弃，整理看似成功却没有改动。代码：`platform/ailab_platform/memory_dream/prompts.py::DOCUMENT_SYSTEM`、`platform/ailab_platform/memory_dream/document.py::apply_ops`。
- **手动写入的记忆同样按不可信数据在模型边界转义**。只在写入时过滤挡不住旧行或管理页文本；渲染时用 `model_text` 编码标签。代码：`platform/ailab_platform/memory_recall/document.py::render`。
- **被忽略原因 `T` 是永久跳过，其余原因只是无新事实**。`T` 表示测试/复现，它的正文还要从后续 episode 的 context 清掉；其他原因只推进游标。代码：`platform/ailab_platform/memory_dream/service.py::process_dream`。

### 整理调度
- **cron 链在窗口后准入要直接跳过，deadline 或批数用完后不再留后继**。没有 `chain_started_at` 的 cron 行在窗口外会被标记 `skipped`，不发模型调用；未处理完的对话留在 `MemoryWorkItem`，下一晚重新整理。代码：`platform/ailab_platform/memory_dream/service.py::process_dream`、`platform/ailab_platform/memory_dream/service.py::_chain_clock`。
- **后继批次原样继承链时钟**。`cutoff_at`、`chain_started_at`、`deadline` 在 `report` 里；测试直接造 cron 行时要带 `chain_started_at` 或改用 manual，否则会被当作过窗作废。代码：`platform/ailab_platform/memory_dream/service.py::CHAIN_KEYS`、`platform/ailab_platform/memory_dream/service.py::_chain_clock`。
- **按用户派发整理任务，并在有进展且仍有积压时继续领取**。等整条链跑完再认领会让一个慢用户挡住后面所有人；无条件睡固定间隔会让到期积压空转。代码：`platform/ailab_platform/memory_dream/service.py::dream_forever`。
- **`claim_due` 只读用户设置**。维护探测不能写 `user.settings`；`nothing_condensable` 这类签名只在持锁的 prepare/apply 里记录。代码：`platform/ailab_platform/memory_dream/service.py::claim_due`、`platform/ailab_platform/memory_recall/document.py::maintenance_needed`。

### 快照与来源
- **空闲刷新只在最终回答完成超过阈值且文档确实变化时发生**。没有变化的时间驱动重绘会破坏前缀；`memory_refreshed` 是显式边界。代码：`platform/ailab_platform/agent/context.py::ContextManager._build`。
- **删除或脱敏一个 Run 时，同时清掉后续 episode 里引用它的 context**。`context_run_id` 是跨轮理解的来源，只删本 episode 会留下已删除对话的正文。代码：`platform/ailab_platform/memory_recall/lineage.py::redact_episode_context`。
- **90 天回收旧版本时保留跳转，不要断开 `superseded_by`**。有后继的行清空正文、quote 和 sources 留空重定向，无后继才删；空跳转也要排除在回收候选外，避免占满每批前列。代码：`platform/ailab_platform/memory_recall/retention.py::purge_closed`。

## 技能

### 生成树与补丁
- **改 `vendor/office-suite/patches/*.json` 后必须连同 runtime 一起重建**。`UPSTREAM.json` 的 `adapter_revision` 哈希整个补丁目录；只重建 skills 会让 runtime 与补丁不一致。命令：`platform/.venv/bin/python vendor/office-suite/build.py --runtime-output vendor/office-suite/runtime`。代码：`vendor/office-suite/build.py::build`。
- **新补丁不能改上游原文，只能串联哈希**。每个 change 的 `before_sha256` 必须等于前一个补丁的 `after_sha256`；`start`/`end` 是对该状态文本的偏移，同一文件多个 edit 按输入文本计算、构建时倒序应用。代码：`vendor/office-suite/build.py::adapted`。
- **Office runtime 是生成物，改正文或范围逻辑要落在 runtime-adapter 与有序补丁**。只改 `vendor/office-suite/runtime` 会被下一次构建覆盖；生成器用 `platform/.venv` 的 Python。代码：`vendor/office-suite/build.py`、`vendor/office-suite/runtime-adapter/`。
- **整文件读取的上限在构建期拒绝，不在 loader 截断**。`SKILL.md` 不超过 100,000 bytes，其他可读资源不超过 256 KiB 或 2000 行；超限直接构建失败。代码：`vendor/office-suite/build.py::validate_readable_resource`。
- **复制资源时不要过滤 `LICENSE*`/`NOTICE*`**。四个基础包必须带固定来源的 Apache-2.0 LICENSE 与 vendor NOTICE；许可参与生成漂移和递归 hash。代码：`vendor/office-suite/build.py::build`。

### 镜像与技能清单
- **`images/sbx-office/skills/index.json` 一变，Agent 与旧镜像的证明就对不上**。`load_skill` 会直接拒绝；部署 Agent 时必须同时重建 sbx-office 镜像。代码：`platform/ailab_platform/agent/skill_verification.py::verify_sandbox_skills`、`platform/ailab_platform/skill_index.py::checked_index`。
- **重建 runtime 会清掉 `node_modules` 和 `runner.mjs`，npm 构建必须放在最后一次重生成之后**。先 `npm ci --ignore-scripts && npm run build`，再跑后端套件；早先的构建成功不能推断前置还在。代码：`vendor/office-suite/runtime/package.json`。
- **技能包更新后旧会话看到的是旧包正文**。按上下文里实际可见的模块判断过期并追加说明，不能按 `loaded_skills` 拒读；旧结果缺版本时保守重载一次。代码：`platform/ailab_platform/agent/loop.py::_register_carried_skills`、`platform/ailab_platform/agent/skill_modules.py::visible_modules`。
- **生成器导入镜像内 `build.py` 会写 `__pycache__`，导致技能校验失败**。测试要导入临时副本；改 `SKILL.md` 或 `build.py` 后同步重新生成 `images/sbx-office/skills/index.json`。代码：`platform/ailab_platform/skill_index.py::build_index`。

### 字体与探针
- **字体别名必须写 `binding="same"`**。否则 Chromium/Skia 不认替代字体，`fc-match` 看着对、PDF 里仍是 DejaVu；matplotlib 不走 fontconfig，要 `addfont` 实际文件。代码：`images/sbx-office/fonts-local.conf`。
- **ReportLab 读不了 Noto CJK 的 CFF 轮廓**。能嵌入的中文只有 `wqy-zenhei.ttc` 和 TeX Live 带入的 AR PL 字体；`--no-install-recommends` 下 `gs`、DejaVu 不会被自动带入。代码：`images/sbx-office/Dockerfile`。
- **上游技能按它自家镜像写路径、探针和脚本名**。改技能后要在构建出的镜像里断网实跑文档里的命令，逐个核对代码块中的绝对路径；静态看 apt 清单不够。代码：`images/sbx-office/skills/`。

### 平台技能
- **平台技能是 `purpose=platform_skill` 的系统所有者文件**。Worker 回写、metadata 合并和版本激活都要保留平台来源，不能统一改写成 `purpose=skill`。代码：`platform/ailab_platform/api/routes_internal.py::internal_patch_file`、`platform/ailab_platform/api/platform_skills.py::activate`。
- **新 Run 冻结平台技能版本，恢复时不能重新挑最新版**。版本写进 `runtime_meta.platform_skills` 并用 `run_asset_refs` 保留；读取同时核对冻结 ID、用户订阅和策略。代码：`platform/ailab_platform/api/routes_runs.py::start_run`、`platform/ailab_platform/api/platform_skills.py::frozen_platform`。
- **`platform_skills.current_file_id` 是 `files.id` 的外键，软删 registry 行不会自动清掉引用**。测试 teardown 要删自己的 registry 行，否则随后 `DELETE FROM files` 会撞外键。代码：`platform/ailab_platform/models.py::PlatformSkill`。
- **自定义副本只在 confirm 后登记关联**；reset 或取消添加要废弃未确认草稿，管理员删除只删关联并保留个人文件；在线草稿要对当前发布和打开时 head 做 CAS。代码：`platform/ailab_platform/api/routes_platform_skills.py::customize`、`platform/ailab_platform/api/routes_platform_skills.py::reset`。

## Portal

### 预览与查看器
- **预览外壳是唯一标题栏**。侧栏标签栏和全屏查看器顶栏已经命名文件并放操作；文档控件经 `DocTools` 插槽 portal 进去，不要在预览内部再画一行工具栏，上游 docbar 由 `office/build.mjs` 的补丁去掉。代码：`platform/portal/components/preview/doc-tools.tsx::DocTools`、`platform/portal/office/build.mjs`。
- **预览 iframe 用 `aria-label` 命名，不用 `title`**。Chromium 会把 `title` 当悬停提示盖在整页文档上；E2E 用 `iframe.office-frame[aria-label=…]` 定位。代码：`platform/portal/components/preview/office-view.tsx::OfficeView`、`platform/e2e/test_office_native.py`。
- **派生 preview 的 revision 不是原生文档内容身份**。用它作 iframe 的 React key 会在同字节渲染结果迟到时打断 capture；按来源稳定挂载，revision 触发可取消的索引复核，只有内容 hash 变化才换 frame。代码：`platform/portal/components/preview/office-view.tsx::useDocumentIndex`。
- **`pdfjs-dist` 必须与 `react-pdf` 锁定的版本完全一致**。两个版本不一致时 PDF.js 拒绝打开所有 PDF；顶层 `pdfjs-dist` 写成精确版本并随 `react-pdf` 一起升级。代码：`platform/portal/package.json`、`platform/portal/tests/pdf-worker-version.test.mjs`。
- **PDF 预览保留内联加载与错误提示**。`react-pdf` 11 默认 Suspense + Error Boundary，预览组件用 `suspense={false}`；CMap、标准字体、Wasm 和 ICC 由 `app/pdfjs` 从已安装的 `pdfjs-dist` 提供。代码：`platform/portal/components/preview/pdf-document.tsx::PdfDocument`、`platform/portal/next.config.mjs`。
- **Next 查看器资产根依赖 `process.cwd()`**。启动 Next 时 cwd 必须是 `platform/portal`，`next start` 指定目录不会改变这个路径；依赖目录符号链接跨 Turbopack 根也会被拒。代码：`platform/portal/app/office-viewer/[version]/[...path]/route.ts`。
- **网页预览按 URL 保留独立标签**。不要只用一个 `paneReader` 覆盖；最近 4 个文档保留挂载，较早标签保留但内容按需重载，不要无限保留第三方 iframe。代码：`platform/portal/components/chat/preview-pane.tsx::PreviewPane`。
- **frame ready 只证明握手完成**。加载、准备和渲染器捕获的终态错误必须显式桥接给宿主；全局 error 监听收不到被 `setFailure` 吞掉的异常，索引 unsupported 的用例不能证明握手后失败也会回退。代码：`platform/portal/components/preview/office-view.tsx::OfficeView`。

### 样式与布局
- **过程框的收起只认准确信号**。思考之后来了任何非思考事件，思考就 `done`；按“回答已开始”猜会让每步先说一句再调工具时反复开合。最大高度滚动框要平滑框本身的可见高度（`useSmoothShrink`），并在缩小内容的那次 commit 启动。代码：`platform/portal/lib/work-process.ts::reduceEvent`、`platform/portal/components/motion/use-smooth-shrink.ts::useSmoothShrink`。
- **收起 Grid 侧栏要把 `--side-w` 设为 0**。只隐藏侧栏不会归还列宽。代码：`platform/portal/styles/app.css`、`platform/portal/styles/shell.css`。
- **不要用 `transform` 定位按钮**。全局 `button:not(:disabled):active { transform: none }` 会在按压时取消定位；改用 inset/margin 或独立的 `translate` 属性。代码：`platform/portal/styles/app.css`。
- **固定菜单统一经 `placeMenu()` 定位，并保留 `.pop-menu.fixed { min-width: 0 }`**。基础 `.pop-menu` 的 `min-width: 200px` 会覆盖较小的内联宽度，造成锚点偏移。代码：`platform/portal/lib/menu-place.ts::placeMenu`、`platform/portal/styles/app.css`。
- **行内来源、文件和技能标签统一用 `vertical-align: middle`**。带 `overflow: hidden` 的 inline-block 按底边参与基线对齐，再用正数 `vertical-align` 会继续上抬；不要用固定像素补偿，数字引用角标保留上标语义。代码：`platform/portal/styles/chat.css`、`platform/portal/styles/app.css`。
- **手机文本字段统一 16px**。iOS 会聚焦任何小于 16px 的字段并留下缩放；这条规则只在 `app.css` 的媒体块里写一次，不要在组件里设置更小字号。代码：`platform/portal/styles/app.css`。
- **preflight 去掉了浏览器默认样式**。标题粗体与字号、段落外边距、列表标记、`svg` 的 block 显示、输入框边框都不再自动存在；Portal 依赖的部分在 `base.css` 顶部补回，新元素不要假设有默认样式。代码：`platform/portal/styles/base.css`。
- **文件列表的表头与正文共用 table 布局，悬停操作只用 opacity 隐藏**。分开用 flex 会失去连续分隔与列对齐；给操作区加 `pointer-events: none` 会让指针移到按钮位置时命中下面的单元格，Playwright 点击也会被挡住。代码：`platform/portal/styles/library.css`。
- **弹窗遮罩用 `grid-template-columns: minmax(0, 1fr)`**。grid 自动轨道按子项 min-content 定宽，一个不换行的长标题会把对话框撑出手机屏幕；不要在每个弹窗上补 max-width。代码：`platform/portal/styles/library.css`。
- **CSS 自定义属性会继承，排版元素要在自身规则里声明默认值**。`Grid` 的 `--cols` 曾渗进卡片里的 `KeyValue`，使字段竖着挤成一行一个字；不要只依赖 `var(--x, 默认)` 的回退。代码：`platform/portal/styles/genui.css`。
- **删除样式块时连带检查 `@keyframes`**。删掉 `@keyframes spin` 会让所有转圈停在第一帧，看起来像卡死；`css-animations.test.mjs` 检查 `globals.css` 里用到的每个动画都有 keyframes。进度类转圈在 `prefers-reduced-motion` 下要豁免为慢速持续转。代码：`platform/portal/tests/css-animations.test.mjs`。
- **确认、下拉和时间选择不要用浏览器原生控件**。确认用 `platform/portal/lib/dialog.ts::confirmDialog`（根布局挂 `DialogHost`，Escape 在捕获阶段拦截），下拉用 `platform/portal/components/select.tsx`，时间用 `TimeSelect`。代码：`platform/portal/lib/dialog.ts`、`platform/portal/components/select.tsx::TimeSelect`。

### GenUI
- **OpenUI Lang 只有位置参数**。命名参数（`Metric("营收", 5, unit: "亿")`）会让整句解析失败；可选值放末尾的 `opts` 对象，对象里写错的键会被单独丢弃。主数据不超过 4 个位置参数。代码：`platform/portal/components/genui/catalog/schema.ts`。
- **任何解析 OpenUI 的地方都先过 `toProgram`**。它的词法器会无报错地吃掉中文语句名、单引号字符串里的反斜杠，以及中文里用 ASCII 引号引词；渲染、复制和评测都走同一入口。代码：`platform/portal/components/genui/catalog/program.ts::toProgram`。
- **类型不符的数组元素会被逐个丢弃，留下空数组**。类别、标签、坐标轴一律用 `label`（string 或 number），渲染端统一 `text()`；新增标签参数不要写成 `z.array(str)`。代码：`platform/portal/components/genui/catalog/schema.ts`。
- **读组件参数前先求值，不能直接读解析树**。`@Each`、`@Sum`、`$变量` 在树里仍是 AST；复制和纯文本生成要先 `evaluateRoot`，再按 Renderer 的方式播种 store，否则表格复制会抛 `rows.slice is not a function`。代码：`platform/portal/components/genui/openui/block.tsx::pictured`。
- **图表标签的 `textWidth` 估算对中文偏窄**。SVG 标签列在估算值上多留 16px；流程图按宽度 `wrap` 成多行、节点高度跟行数走，不要固定宽度后 `fit` 成省略号。代码：`platform/portal/components/genui/catalog/charts.tsx`、`platform/portal/components/genui/catalog/diagrams.tsx`。
- **截图前固定单行文本，并排除不进图片的元素**。`modern-screenshot` 会把计算宽度写成固定像素，小数宽度下单行会换行；`holdLines` 只给单行文本加 `nowrap`。操作条、`CopyButton` 和图表读数标 `data-capture-skip`。代码：`platform/portal/components/genui/image.tsx::holdLines`、`platform/portal/components/genui/image.tsx::placeTools`。
- **Safari 的图片复制必须在点击当下创建 `ClipboardItem`**。值传尚未完成的 `Promise<Blob>`；先 `await` 画图再写剪贴板会被拒绝。代码：`platform/portal/components/genui/image.tsx`。
- **只加载平台签名的网页图片**。GenUI 的 `src` 只接受工具结果里平台登记的 `img_*` 或签名的 `/v1/web-images/...`，其他地址显示占位、不发请求；不要为“能显示”放宽 CSP 的 `img-src`。代码：`platform/portal/components/genui/catalog/media.tsx`、`platform/portal/components/genui/catalog/data.ts::webImage`。
- **生成页高度按 body 内容范围测量**。`documentElement.scrollHeight` 不会小于 iframe 自身高度，页面变短后框永远不缩；用 Range 包围盒加内边距/外边距。代码：`platform/portal/lib/frame.ts::RUN_HELPER`。
- **容器查询不能作用于容器自身**。`.gu-imagetext` 设了 `container-type`，栅格和查询变量要放在内层 `.gu-imagetext-inner`。代码：`platform/portal/styles/genui.css`。
- **`white-space: pre-wrap` 的 contenteditable 末尾 `\n` 是占位符**。Shift+Enter 插入的是文本不是 `<br>`；删到只剩末尾 `\n` 时要像末尾 `<br>` 一样丢掉，否则空消息会多一行。代码：`platform/portal/components/chat/rich-input.tsx`。
- **聊天首包不要从 `catalog/schema.ts` 取常量**。取一个数字会把 zod 和 lang-core 拉进对话页首包；聊天侧只从 `catalog/limits.ts` 取常量。代码：`platform/portal/components/genui/catalog/limits.ts`。
- **滚动跟随统一用 `lib/use-follow.ts`**。`stopAt` 只允许向下跟随，停点在视图上方视为已过；“回到底部”按钮在内容变化时按 `heading()` 重算，按当前 `scrollTop` 算会在滑行中闪现。代码：`platform/portal/lib/use-follow.ts`。
- **同源代理要透传 API 的缓存头**。`cache-control`、`etag`、`if-none-match` 必须转发，否则 API 的 immutable 缩略图在 Portal 侧全被 `no-store`；API 未声明时仍保持 `no-store`。代码：`platform/portal/app/api/v1/[...path]/route.ts`。

## 部署

### 清单与镜像
- **旧 kustomize 会在 lite overlay 的顶层 `$patch: delete` 上崩溃**。用 `kubectl kustomize` 的内嵌版本渲染，或升级 kustomize；不要改清单迁就旧工具。代码：`platform/deploy/overlays/lite/kustomization.yaml`。
- **每个服务只挂自己的 `platform-<svc>-identity` 和共享 verifier**。模型 Secret 只给 API/Agent，OpenSandbox key 只给 Broker；不要给被调方分发调用方原始令牌。代码：`platform/deploy/base/apps/services.yaml`。
- **所有 uvicorn 入口加 `--no-proxy-headers`，应用按 `trusted_proxies` 从右向左验证**。只改登录 handler 不够，Uvicorn 会先解释 XFF 并改写 peer。代码：`platform/deploy/base/apps/services.yaml`、`platform/ailab_platform/config.py::Settings.trusted_proxies`。
- **内部 httpx 必须 `trust_env=False`**。本机系统代理会截走内部请求，表现为 E2E 的 `httpx.ReadError`。代码：`platform/ailab_platform/clients.py`。
- **导入 k3s 后按配置 digest 核对镜像，不要只看 tag**。Docker 的 `.Id` 是配置 digest，containerd 的 `ctr images ls` 展示 manifest digest；两者都对上才部署。代码：`deploy.sh::build_images`。
- **Portal 镜像的 COPY 源路径保持稳定，依赖层放在源码前**。每次变化的版本号路径会丢缓存；只改 Portal 时复用后端和沙箱镜像，只重建 Portal。代码：`platform/deploy/portal.Dockerfile`。
- **standalone 运行包必须带 `.next/static`**；`public/` 存在时也要一起复制。代码：`platform/deploy/portal.Dockerfile`。
- **`deploy.sh --dry-run` 不需要集群，真正部署要以 root 运行**。`--skip-build` 只复用已有镜像，内容是否过期不会阻止部署。代码：`deploy.sh`。

### 节点与沙箱
- **lite 的 Broker 用 `hostNetwork`，宿主防火墙必须限制 8081 和 8090**。base 的 NetworkPolicy 不覆盖 hostNetwork Pod；不能把集群策略当作节点防火墙。代码：`platform/deploy/overlays/lite/compatibility.yaml`。
- **runsc 根 overlay 必须显式限量**。`--memory` 不限制磁盘；用 `overlay2="root:self,size=4g"` 并让 k3s 通过 `ConfigPath` 读取，改完重启 k3s。代码：`k3s/runsc.toml`、`k3s/check-node.py`。

### 备份与扫描
- **备份完成标记 `backup.json` 只在全部 verify 后写**。失败留下的目录不算备份，换空目录重试；恢复拒绝非空目录。代码：`platform/ailab_platform/ops/backup.py::backup`、`platform/ailab_platform/ops/backup.py::restore`。
- **确定性备份窗口要停写**。SQLite 用 online backup，PostgreSQL 用同一快照的 `pg_dump --snapshot`；跨 DB/S3 不能原子快照。代码：`platform/ailab_platform/ops/backup.py`、`docs/architecture/runbooks/backup-restore.md`。
- **恢复 PostgreSQL 时用与服务端兼容的 `pg_dump`/客户端**。高版本 dump 可能写入低版本不认识的参数；恢复只导入新空库，不覆盖旧库。代码：`docs/architecture/runbooks/backup-restore.md`。
- **ClamAV 是一个共享扫描器，不是每 Worker 一个 sidecar**。签名约占 1 GiB RSS，预留 1.5 GiB；clamd 不可用时上传保持 pending（fail closed），不能放行。代码：`platform/deploy/base/scanner/clamav.yaml`、`platform/ailab_platform/adapters/clamav.py`。
- **ClamAV 冷启动先等签名和 PING**；暂停后文件回 pending 或 `scan_unavailable`，恢复后重新扫描才 READY。代码：`platform/ailab_platform/adapters/clamav.py`、`platform/ailab_platform/worker/pipeline.py`。

### 本机操作
- **启动本机栈前确认旧 `next-server` 和 uvicorn 已退出**。端口被占时新进程只在日志里报 `EADDRINUSE`，页面仍由旧构建提供；用 `dev_down.sh` 的 pidfile 或按端口 `lsof -ti` 结束，不要反复重启。代码：`platform/scripts/dev_up.sh`、`platform/scripts/dev_down.sh`。
- **Redis 健康检查用独立短超时客户端，但不要给连接池设上限**。池满会直接抛错，readiness、liveness、metrics 同时 ping 会把健康 Redis 判成宕机。代码：`platform/ailab_platform/queue.py::RedisQueue._health_client`。

## 测试

### 隔离与并发
- **autouse 清理 fixture 只比较少量活行**。每个测试都 `select` 全部 Run id 再 `NOT IN` 会随全套增长，最终撞上 PostgreSQL 的 CI 时限；只查运行中 Run、有租约或 pending 的整理任务。代码：`platform/tests/conftest.py::release_test_memory_jobs`。
- **不带 `with` 的 `TestClient(app)` 不跑启动钩子**。存储目录不会 `ensure_dirs()`，第一个 `/readyz` 用例会因 `storage: False` 得到 503；要么用上下文管理器，要么显式建目录。代码：`platform/tests/test_review_regressions.py::test_api_stays_ready_but_degraded_without_redis`。
- **查询次数测试只监听被测 session 的连接**。在 `Engine` 上挂监听会把 API 后台 outbox 和 reaper 的查询算进来；用 `db.connection()`，并只计调用线程。代码：`platform/tests/test_conversation_paging.py::test_detail_batches_related_rows_instead_of_querying_each_turn`。
- **共享测试库的 outbox 会被 API lifespan dispatcher 扫描**。要隔离缩略图或发布测试，在启动 `TestClient` 前替换 `main.dispatch_forever`，不要只替换 `preview_dispatch.get_queue`。代码：`platform/tests/test_library.py::api`。
- **迁移漂移比较必须在 head 上做**。升到中间 revision 再和最新 ORM 比较会把后续合法表误判为漂移；逐步升、降、再升链另测。代码：`platform/tests/test_migrations.py::assert_schema_matches`。
- **EXPLAIN 测试用独立库、有代表性的数据并 ANALYZE**。不要强制索引名或关闭顺序扫描；SQLite 的 skip-scan 和 PostgreSQL 的等价索引都有效。代码：`platform/tests/test_memory_query_plans.py`。
- **测试 HTTP 服务器统一 `serve_forever(poll_interval=0.01)`**。`shutdown()` 默认要等下一次 0.5 秒轮询；测试里的 `AILAB_LLM_RETRY_BACKOFF_SECONDS` 默认 1ms，测退避节奏的用例自己设置。代码：`platform/tests/conftest.py`、`platform/tests/prefix_harness.py`。

### E2E
- **E2E 只复制 conftest 列出的 Portal 目录和文件**。新增顶层目录或配置（如 `styles/`、`postcss.config.mjs`）要同步加入 `_build_portal`，否则构建失败或 Tailwind 静默无输出。代码：`platform/e2e/conftest.py::_build_portal`。
- **E2E 端口被占时直接报错，不要复用旧服务**。遗留 `next start` 会占住端口并通过健康探针，套件测到旧 Portal；按提示结束进程或改 `AILAB_E2E_BASE_PORT`/`AILAB_E2E_PORTAL_PORT`。代码：`platform/e2e/conftest.py::stack`。
- **E2E 种子文件走 `assign_file_version`**。直接插 `FileObject` 没有 head，删除接口返回 204 却什么都没删。代码：`platform/ailab_platform/library.py::assign_file_version`。
- **Playwright `goto` 完成不代表 React effect 已注册**。全局快捷键测试先等 effect 后才出现的 `.user-chip`，再发键；不要加 sleep 或重复发键。代码：`platform/e2e/test_portal.py`。
- **“保存”按钮进入“保存中…”后精确的“保存”消失，不代表落库成功**。刷新或后续操作前要等两种状态的按钮都消失，失败仍保留断言。Next 16 会把 loopback URL 规范化成 localhost，内部重写依赖 `skipProxyUrlNormalize: true`；不能用登录重试掩盖 socket hang up。代码：`platform/portal/next.config.mjs`、`platform/portal/tests/auth-rewrite.test.mjs`。
- **Playwright 的异步谓词不会被 `wait_for_function` 等待**。`"async ... => ..."` 返回的 Promise 本身为真；等 API 状态用 Python 侧带截止时间的轮询。代码：`platform/e2e/test_ask_user_ui.py::_wait_status`。

### CI
- **PR 范围用 `base...head` 全量 diff，并关闭重命名检测**。只看最后一次 push 会漏验前面的代码；删除或重命名保留两个路径，未知路径跑全套。代码：`.github/scripts/ci_scope.py`。
- **CI 门禁核对 JUnit 中真实契约用例**。job success 不够；新增必须执行的组要加进 `verify_junit.py --require`，拒绝无报告、无匹配或跳过。代码：`.github/scripts/verify_junit.py`、`.github/workflows/ci.yml`。
- **CI 被 cancelled 先看 annotation 和逐段日志**。总 timeout 包含容器、依赖准备和 artifact 收尾；持续推进到末段撞总时限与断言失败不同，不要调高上限掩盖。代码：`.github/workflows/ci.yml`。

### 真模型与性能
- **真模型场景至少四个样本再比较**。同场景调用次数波动很大，单样本会被路线差异主导；比较工具序列、失败原因和每轮 token，不只看一次是否成功。代码：`platform/scenarios/`。
- **真模型场景会撞并发 Run 上限和每日额度**。默认每用户 2 个并发、200 万加权 token；被暂停的 Run 不能计入对比。代码：`platform/ailab_platform/config.py::Settings.max_active_runs_per_user`、`platform/ailab_platform/config.py::Settings.daily_token_limit`。
- **GenUI 评测的 parser 合法率不是质量**。必须人工核对派生值、表头与行、比较方向和块外数字；单轮结果波动明显，判断一条规则至少跑两次。代码：`platform/evals/genui/`。

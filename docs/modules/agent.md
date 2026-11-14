# Agent

Agent 消费 Run 队列，组装前缀稳定的模型请求，执行工具，并把执行状态写入 checkpoint；暂停、崩溃或换部署后从 checkpoint 继续。业务授权、配额与文件记录在 [API](api.md)，沙箱租约与工具代理在 [沙箱](sandbox.md)，上传解析与派生资源在 [文件](files.md)，记忆与技能内容在 [记忆与技能](memory-skills.md)。

## 代码地图

| 要改的东西 | 位置 |
|---|---|
| Run 入口、队列消费、Chat/Work 泳道 | `platform/ailab_platform/agent/main.py::handle_message` |
| 一次 Run 的主循环 | `platform/ailab_platform/agent/loop.py::AgentLoop::_process` |
| 调模型、选后备模型、用量结算 | `platform/ailab_platform/agent/loop.py::AgentLoop::_call_model`、`::_model_attempt` |
| 追加式摘要、收尾、看门狗提问 | `platform/ailab_platform/agent/loop.py::AgentLoop::_answer_without_tools` |
| 请求消息布局与恢复 | `platform/ailab_platform/agent/context.py::ContextManager::_build` |
| 轮内与跨轮压缩 | `platform/ailab_platform/agent/context.py::ContextManager::compact`、`::_compact_parent_context` |
| 压缩安全点 | `platform/ailab_platform/agent/context_protocol.py::validate_safe_compaction_point` |
| 上下文大小与供应商锚点 | `platform/ailab_platform/agent/context_usage.py::request_tokens` |
| 工具 schema 与模式目录 | `platform/ailab_platform/agent/tools.py::TOOLS`、`::tools_for_turn`、`::tools_for_chat` |
| 工具分发 | `platform/ailab_platform/agent/tools.py::execute_tool`、`platform/ailab_platform/agent/loop.py::AgentLoop::_run_tool` |
| 工具重放与副作用围栏 | `platform/ailab_platform/agent/loop.py::AgentLoop::_durable_tool` |
| 按需工具 | `platform/ailab_platform/agent/deferred.py::DEFERRED`、`::split`、`::resolve`、`::describe` |
| 本 Run 的冻结目录 | `platform/ailab_platform/agent/loop.py::_tools_for_run`、`::_full_catalog` |
| 退役工具名 | `platform/ailab_platform/agent/tools.py::RETIRED_TOOLS` |
| 上下文 ABI 版本 | `platform/ailab_platform/contracts/model_context.py::CONTEXT_ABI_VERSION` |
| checkpoint 与容量预算 | `platform/ailab_platform/agent/loop.py::AgentLoop::_checkpoint`、`platform/ailab_platform/agent/checkpoint_budget.py::bounded_checkpoint` |
| 恢复状态结构 | `platform/ailab_platform/adapters/runtime.py::fresh_state`、`::restored_state`、`::NON_REPLAYABLE` |
| 工作区快照 | `platform/ailab_platform/agent/loop.py::AgentLoop::_workspace_checkpoint` |
| 平台规则与用户消息 | `platform/ailab_platform/agent/prompts.py::system_prompt_for`、`::user_text` |
| 技能导航与模块读取 | `platform/ailab_platform/agent/prompts.py::load_skill`、`platform/ailab_platform/agent/skill_modules.py::read_module` |
| 并行子任务 | `platform/ailab_platform/agent/subtasks.py::TOOL`、`platform/ailab_platform/agent/loop.py::AgentLoop::_subtasks` |
| 联网、下载、依赖、保存技能 | `platform/ailab_platform/agent/net_tools.py::download_url`、`::fetch_repo`、`::fetch_packages`、`::save_skill` |
| 网页搜索与读取 | `platform/ailab_platform/web.py::search`、`::read_url` |
| 工作区 ls/grep/find | `platform/ailab_platform/agent/workspace_query.py::list_directory`、`::search_text`、`::find_files` |
| Chat 文档工具 | `platform/ailab_platform/agent/document_tools.py::TOOLS` |
| 思考字段与 `<think>` 拆分 | `platform/ailab_platform/thinking.py::reasoning_of`、`::ThinkSplitter` |
| 模型目录、请求封装 | `platform/ailab_platform/catalog.py::get_catalog`、`platform/ailab_platform/model_transport.py::ModelTransport` |

## 关键流程

### 一次 Run 的执行
1. `platform/ailab_platform/agent/main.py::handle_message` 从 `chat`/`work` 队列取消息，claim 持久 job，再用 `AgentLoop.process` 执行；没有 claim 到的重复消息直接丢弃。
2. `AgentLoop::_process` 读取 Run 与 checkpoint，终态、`waiting_user`、`interrupted` 直接返回；`restored_state` 恢复消息、工具目录、`pending`、`tool_log`、计划、交付与工作区快照。重试 attempt 发 `agent.recovered`。
3. 转 `RUNNING`，启动租约心跳。首次进入时把 `_tools_for_run(run)` 存为 `state["tool_catalog_v1"]`，本次 Run 后续请求都从这里取。
4. `ContextManager.build` 组装或重建上下文；`build` 失败只回滚未提交的装配，已提交的 checkpoint 仍是权威。
5. 每一步：检查取消、预算、插话和重复操作 → 需要时压缩 → 调模型 → 追加 assistant（含 `reasoning_content`）→ checkpoint → 执行工具批次 → 结果准入并追加 → checkpoint。`next_step` 在工具批次结束后加一。
6. 模型不再调用工具即 `finished`：收尾预览、自动交付 `outputs/` 剩余文件、保存工作区快照，写 `SUCCEEDED`。步数、时间、token、额度、租约、取消按各自合同转暂停、等待或失败。

### 模型调用
1. 请求由冻结的消息列表、冻结的工具目录和本次输出上限/思考档组成，框架字段由 `platform/ailab_platform/model_transport.py::ModelTransport::_request` 组装；不重排历史，也不按当前目录重新过滤已冻结的工具。
2. `_call_model` 按目录能力筛候选模型（有工具调用、视觉）；后备模型只在尚未产生输出时启用，切换前记 `agent.context_boundary(model_changed)`。
3. 每次调用先按 `call_id` 预留用量，再按模型窗口和剩余空间算输出上限；`_model_attempt` 流式收正文与思考，`_DeltaWriter` 约每 0.25 秒或 120 字写一次 `agent.delta`/`agent.thinking`，思考不进入正文。
4. 调用结束结算用量，以供应商返回的 `prompt_tokens` 作为下一次请求的上下文锚点；已输出后失败不重试、不换模型。
5. 摘要、收尾回答、看门狗检查与子任务都复用主请求的模型、工具、`tool_choice=auto` 和思考档位，只把指令追加在末尾（`continues_prefix`）。`tool_choice=none` 或临时关闭思考会改写供应商模板，丢掉整段前缀。
6. 标题推断是独立的安静调用，有独立超时和计费，失败只影响标题。

### 工具执行
1. 常驻工具直接声明，名单就是模式目录减去 `DEFERRED[mode]`；其余按需工具只在 `call_tool` 的 `name` 枚举里冻结名字。模型第一次调用按需工具时只拿到说明，说明已在上下文后才执行。
2. 历史保留模型写的 `call_tool` 原文，执行、事件、工具日志和授权都用解析后的真实工具名。
3. `_durable_tool` 以 `step:ordinal` 查 `tool_log`：已有结果直接复用；没有结果的 `bash` 属于 `NON_REPLAYABLE`，只按已有 handle 继续等待，绝不重跑。
4. `READ_ONLY_TOOLS` 同批并行，结果仍按 ordinal 顺序消费；有副作用的工具开始前先写 `tool_log` 并 checkpoint。
5. 工具结果按整批做上下文准入：单个结果放不下时替换成 `cannot_fit` 拒绝，同时为每个待配对调用保留最小结果；连这些也放不下就 `ContextPause`。结果写入 `tool_log`、追加 tool 消息、更新来源/交付/后台句柄，再 checkpoint。
6. 工具异常转成 `{ok:false, error}` 交给模型；租约 410 向上抛 `LeaseInterrupted`，不伪装成普通工具失败。

### 上下文、压缩与恢复
1. `messages[0]` 是按模式与能力生成的平台规则，`messages[1]` 是会话上下文（工作区说明、记忆快照、资料索引、过期提示）。之后只有用户消息、assistant、工具结果和平台提示依次追加。
2. 会变的内容只能追加：插话、技能读取结果、工具结果、记忆增量、图片和平台提示。运行中不主动改更早消息、工具声明、system 内容或思考设置。
3. 上下文达到模型窗口 90% 时触发压缩。`validate_safe_compaction_point` 要求没有 `pending`、没有进行中的模型调用，并且每条 tool 结果与 assistant 调用配对。
4. 压缩是一次同形旁路调用：摘要指令追加在原请求后，`stage_context` 重新排出固定前缀、有界运行状态、当前用户输入和最近完整协议组。`agent.context_boundary(compacted)` 与 checkpoint CAS 在同一事务里推进 `context_epoch`。
5. 摘要失败、输出截断、非法来源或容量不足都是 `ContextPause`：旧纪元保持权威，不重复付费，保留存活的后台任务。
6. 续轮优先复用上一轮 checkpoint，请求等于上一轮最后一次请求加新消息；同模式分叉复制 checkpoint 并沿用血缘根会话头，跨模式分叉重建。
7. `agent.context_boundary` 的 reason 包括 `compacted`、`model_changed`、`platform_rules_changed`、`tool_catalog_changed`、`context_abi_changed`、`access_revoked`、`rebuilt`、`memory_refreshed`。`compacted` 与 `model_changed` 发生在运行中，其余发生在恢复或换轮装配时。

## 不变量

- 同一上下文纪元内，工具声明、system 消息和主模型的思考档位不变，请求只在前一次请求后追加；换模型、换规则、压缩或 ABI 变化都先记边界。后备模型没有该档位时用自己的默认档，并已有 `model_changed` 边界。钉住它的测试：`platform/tests/test_prefix_cache.py::test_next_run_extends_the_previous_run_wire_messages`、`::test_work_twenty_steps_strictly_extend_wire_bytes`、`::test_checkpoint_continue_keeps_previous_request_prefix`。
- 工具目录在 Run 开始时冻结；暂停中的 Run 保留自己的目录，只有出现退役工具名、规则或 ABI 变化才重建。钉住它的测试：`platform/tests/test_deferred_tools.py::test_a_frozen_catalog_keeps_its_on_demand_names`、`platform/tests/test_prefix_cache.py::test_deploy_prompt_and_tool_changes_preserve_resumed_checkpoint`。
- 只有配对完整的工具组才能压缩；`pending` 或进行中的模型调用拒绝压缩。钉住它的测试：`platform/tests/test_prefix_cache.py::test_run_compaction_extends_wire_prefix_and_retains_complete_groups`。
- 付费模型响应先 checkpoint 再执行工具；有副作用的工具开始前写 `tool_log`。只读工具不单独 checkpoint，恢复时允许重做。钉住它的测试：`platform/tests/test_prefix_cache.py::test_failed_model_request_retry_reuses_identical_body`。
- `bash` 不重放；中断后只按 handle 继续等待，交接不到就不确定结果，让模型先检查工作区。代码：`platform/ailab_platform/adapters/runtime.py::NON_REPLAYABLE`。
- 恢复旧 checkpoint 时，`CONTEXT_ABI_VERSION` 不符或目录含 `RETIRED_TOOLS` 就重建 `messages[0]` 与工具目录，并记一次边界；其余历史字节保留。钉住它的测试：`platform/tests/test_prefix_cache.py::test_a_checkpoint_with_retired_tool_names_is_rebuilt_at_a_recorded_boundary`、`::test_parent_abi_match_exact_reuse_mismatch_rebuild`。
- 平台规则不含用户、技能、记忆或禁用的具体状态；这些只能出现在追加消息里。钉住它的测试：`platform/tests/test_prefix_cache.py::test_platform_system_prompt_does_not_include_user_skill_state`。
- Chat 目录不含沙箱工具；Chat 的文档工具只在 Chat 提供。钉住它的测试：`platform/tests/test_net_tools.py::test_tools_offered_per_mode`。
- 按需工具只有在它的说明已作为配对的 `call_tool` 结果出现后才执行；其他工具输出里的同名文本不算。钉住它的测试：`platform/tests/test_deferred_tools.py::test_call_tool_runs_only_after_the_description_was_shown`。
- 摘要、收尾和子任务保持主请求的形状与思考档位，模型仍调工具时用配对的拒绝结果重问一次。钉住它的测试：`platform/tests/test_prefix_cache.py::test_summary_keeps_request_shape_and_appends_paired_tool_refusals`、`::test_summary_keeps_the_runs_thinking_level`。

## 怎么扩展

### 加一个常驻工具
1. 在 `platform/ailab_platform/agent/tools.py` 定义 schema，放进 `tools_for_turn`（Work）或 `tools_for_chat`（Chat）的稳定位置；参数尽量由 `platform/ailab_platform/agent/tool_schema.py::parameters` 从 Pydantic 契约生成。
2. 在 `execute_tool` 加 handler。需要循环状态、暂停或图片的操作放 `AgentLoop::_run_tool`；需要租约的加入 `SANDBOX_TOOLS`（漏了会拿到空租约），无副作用的加入 `READ_ONLY_TOOLS`（漏了只是少并行），改工作区的加入 `FILE_TOOLS`/`PREVIEW_AFTER`；子任务也要用就加入 `platform/ailab_platform/agent/subtasks.py::ALLOWED`。
3. 只对某些模型、配置或客户端可用时，在 `_full_catalog` 过滤；漏了会把模型不支持的 schema 发出去。不要在请求构造之后临时删工具。
4. 在 `platform/ailab_platform/agent/prompts.py` 写使用规则并提升该模式的 `PROMPT_VERSIONS`。只加工具会改变目录；同时改提示词才会改变 `messages[0]`，两种情况都让已有会话的下一个 Run 重建一次。
5. 测试：`platform/tests/test_net_tools.py::test_tools_offered_per_mode` 钉住 Chat 的完整集合；另加 handler 测试。改动请求构造时补 `platform/tests/test_prefix_cache.py` 的录制请求测试。

### 加一个按需工具
1. 按常驻工具的方式定义 schema 并放进 `tools_for_turn`/`tools_for_chat`，`deferred.split` 会把它从声明里移出；handler 仍按常驻工具接入。
2. 在 `platform/ailab_platform/agent/deferred.py::DEFERRED` 的 `work`/`chat` 加入名字，并在 `SUMMARY` 写一句话简介；`call_tool` 的枚举和描述由这两处生成，漏了简介会列出空项并让目录测试失败。
3. 授权自动认“声明的工具 ∪ 冻结的按需名字”，子任务另在 `platform/ailab_platform/agent/subtasks.py::allowed` 决定。名字冻结、说明取当前代码，因此冻结时已列出该名字的旧 Run 在部署后仍能读到新说明。
4. 测试：`platform/tests/test_deferred_tools.py::test_request_declares_frequent_tools_and_lists_the_rest_in_call_tool` 检查枚举与简介齐全；`::test_on_demand_tool_reads_its_description_then_runs_and_keeps_the_prefix` 检查第一次只返回说明、第二次执行且前缀连续。

### 删除或退役一个工具
1. 从 `tools_for_turn`/`tools_for_chat`、`DEFERRED`/`SUMMARY`、`execute_tool` 或 `_run_tool` 的分支、`SANDBOX_TOOLS`/`READ_ONLY_TOOLS`/`FILE_TOOLS`/`PREVIEW_AFTER`、`platform/ailab_platform/agent/subtasks.py::ALLOWED` 和提示词中去掉。
2. 删除只服务它的客户端方法、API `/internal` 端点和 `file_dependency` 动作；只摘目录会留下走不到的代码。
3. 把名字加入 `platform/ailab_platform/agent/tools.py::RETIRED_TOOLS`，让暂停中的旧 Run 恢复时重建规则与目录。只删一个工具不需要改 `CONTEXT_ABI_VERSION`；只有消息布局或含义变化才提升 ABI。
4. 更新钉住目录集合的测试，保留 `test_a_checkpoint_with_retired_tool_names_is_rebuilt_at_a_recorded_boundary` 覆盖恢复路径。

### 改 Chat/Work 提示词
1. 改 `BASE_PROMPT`、`CHAT_MODE_NOTE` 或 `system_prompt_for` 追加的能力段落。规则只写行为，不写用户、技能或记忆的具体状态。
2. 提升 `platform/ailab_platform/agent/prompts.py::PROMPT_VERSIONS` 中该模式的值；它嵌在 `messages[0]`，让一次有意的改写可追踪。另一个模式不受影响。
3. 更新 `platform/tests/test_prompts.py` 里的文本断言；Chat 的完整提示词由 `platform/tests/test_prefix_cache.py::test_a_chat_conversation_keeps_its_prefix_across_this_deploy` 钉住长度和 SHA256，改完必须同步更新。
4. 新 Run 第一次请求记 `platform_rules_changed`，暂停中的旧 Run 保持旧字节。行为变化用 `platform/scenarios/` 的真模型场景验证，不能只看单元测试。

### 接一个新模型或思考字段格式
1. 在 `platform/models.toml`（或本机 `models.local.toml`/`AILAB_MODELS_FILE`）加 `[[models]]`：`id`、`provider`、`base_url`、`upstream_model`、`api_key_env`（必须以 `AILAB_KEY_` 开头）、真实 `context_tokens`/`max_output_tokens`、`capabilities`、`fallback`。
2. 需要回传思考内容的模型设 `echo_reasoning`；支持关闭思考的设 `disable_reasoning_for_quiet`；按会话缓存的网关设 `session_header`。目录校验在 `platform/ailab_platform/catalog.py::parse_catalog`，缺失或不符直接启动失败。
3. 思考档位在 `platform/reasoning.toml` 定义，模型用 `reasoning = "<组名>"` 引用；档位只合并请求字段，不能覆盖模型、消息、工具等框架字段。
4. 上游把思考留在正文时，在模型上配 `think_tag`（如 `think`），模板已开思考块的再配 `think_prefilled`。标准字段无需额外配置，`reasoning_of` 已读 `reasoning_content`/`reasoning`/`thinking`/`reasoning_details`。
5. 新增一种思考字段时改 `platform/ailab_platform/thinking.py::FIELDS` 或 `reasoning_of`，保持“只读一个来源”，否则同一段思考会被计两次。测试：`platform/tests/test_think_tags.py::test_non_streamed_calls_split_tags_and_read_any_reasoning_field`、`::test_catalog_rejects_bad_think_settings`；目录本身由 `platform/tests/test_catalog.py::test_the_shipped_catalog_is_valid_and_keyless` 检查。
6. 真模型确认流式正文/思考分栏、工具轮次回传和 `cached_tokens`；部署时模型目录与 `reasoning.toml` 一起更新。

## 配置

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_MODELS_FILE` | 模型目录路径；未设置时优先 `platform/models.local.toml`，否则 `platform/models.toml` | 仓库内目录 |
| `AILAB_MAX_AGENT_STEPS` | 每个预算窗口的模型步数上限 | 300 |
| `AILAB_RUN_MAX_TOKENS` | 单个 Run 的 prompt + completion 累计上限 | 2,000,000 |
| `AILAB_RUN_MAX_SECONDS` | 单个 Run 的活跃工作时长上限 | 14400 |
| `AILAB_CHECKPOINT_MAX_BYTES` | checkpoint 未压缩字节上限 | 4 MiB（最大 16 MiB） |
| `AILAB_SUBTASK_MAX_STEPS` | 每个子任务的最大步数 | 20 |
| `AILAB_WATCHDOG_MODE` | Work 看门狗：`off`/`observe`/`nudge` | `observe` |
| `AILAB_LLM_MAX_RETRIES` | 模型瞬时错误的重试次数 | 2 |
| `AILAB_SKILLS_DIR` | 基础技能目录 | `images/sbx-office/skills` |

完整配置见 `platform/ailab_platform/config.py::Settings`；压缩阈值是平台设置 `context_settings.threshold_ratio`（默认 0.90），不是环境变量。

## 相关决策

- [ADR 0005 有预算的历史与会话元数据](../architecture/adr/0005-budgeted-conversation-memory.md)：历史、摘要、标题都是独立的有界调用。
- [ADR 0007 受控公网获取](../architecture/adr/0007-controlled-public-fetch.md)：沙箱保持断网，公开字节由 Agent 取回后经 Broker 注入。
- [ADR 0008 长 Run 预算与租约](../architecture/adr/0008-long-run-budgets-and-leases.md)：步数、时间、token、job 租约是四个独立边界。
- [ADR 0024 模型窗口压缩与动态输出](../architecture/adr/0024-model-window-compaction-and-adaptive-output.md)：按完整窗口比例触发压缩，输出上限随剩余空间缩减。
- [ADR 0030 前缀稳定的模型请求](../architecture/adr/0030-prefix-stable-model-requests.md)：工具固定、历史原文渲染、会变内容追加。
- [ADR 0031 并行子任务](../architecture/adr/0031-parallel-subtasks.md)：子任务 fork 主请求前缀，只写中间文件并返回报告。
- [ADR 0033 上下文纪元与压缩区间](../architecture/adr/0033-context-epoch-band-process-snapshot.md)：纪元内只追加，压缩边界与 checkpoint CAS 同一事务。
- [ADR 0038 思考强度](../architecture/adr/0038-reasoning-effort.md)：档位按模型配置，整次回答固定。
- [ADR 0041 分叉沿用 checkpoint](../architecture/adr/0041-fork-reuses-checkpoint.md)：同模式分叉复制快照并沿用血缘会话头。
- [ADR 0042 单一工具目录](../architecture/adr/0042-single-tool-catalog.md)：每个模式一份目录，旧 Run 通过 ABI 和退役工具名重建。
- [ADR 0043 按需工具](../architecture/adr/0043-on-demand-tools.md)：低频工具经 `call_tool` 加载，运行时目录不变。
- [ADR 0044 工作区工具对齐 Pi](../architecture/adr/0044-pi-aligned-workspace-tools.md)：read/edit/bash 的语义、重活放在哪一侧、等待窗口与平台期限。

## 已知的坑

见 [HANDOFF · Agent](../../HANDOFF.MD#agent)。

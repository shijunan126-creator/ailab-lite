# 当前日期查询工具（2026-11-13）

用户要求：Chat 和 Work 都要能查“今天几号”，日期按用户浏览器的本地时区；提示词尽量短，模型想查才查。起因是 Work 里模型自己跑 `bash date` 很慢，Chat 里靠 `schedule` 工具顺带返回 `now` 虽然快，但语义上不是查询接口。

## 设计

- 新增零参数工具 `current_time`，Chat 和 Work 的目录都带，小上下文的精简目录也带。实现完全在 Agent 进程内（`tools.local_time`），不碰沙箱、不发 HTTP，Work 里也不触发租约。
- 返回 `{date, weekday, time, timezone}`，星期用中文（`周五`），时区名一并返回，模型不会把 UTC 当成用户本地时间。
- 时区来源：Portal 每次发送带 `Intl.DateTimeFormat().resolvedOptions().timeZone`，API 校验 IANA 名后写进 `run.options.timezone`，Run 内冻结。没有客户端时区的调用（直连 API、定时触发、重试）按账号既有口径回退：最近一次定时任务的时区 → 记忆整理时区 → `memory_dream_default_timezone`。没有该字段的老 Run 用 UTC 并在结果里写明。
- 同一个 `run.options.timezone` 也用于会话内 `schedule` 工具：`now` 和新建任务的时区都按它算。此前这里用 `user_timezone`（账号最近一次任务/整理设置/Asia-Shanghai），用户在其他时区第一次用会话内定时任务时会拿到错的“今天”和错的时区。
- 提示词只在两个模式各加一行：`需要今天日期、星期或当前时间时用 current_time 查（用户本地时间），不要按训练数据猜。` 两个模式的版本号分别升级。
- `run_subtasks` 的子任务也能用 `current_time`（`subtasks.ALLOWED`）。

## 前缀缓存影响

- 新增工具改变 `tool_catalog_v1`，两模式 `messages[0]` 各变一行：已有会话的下一个**新** Run 会有一次预期的重建（`tool_catalog_changed` / `platform_rules_changed`），之后恢复逐字节前缀。
- 工具目录按 Run 冻结（`state["tool_catalog_v1"]`），暂停中的旧 Run 恢复后不会中途多出新工具，也不会因此改请求前缀。
- Chat 提示词的字节闸门 `test_a_chat_conversation_keeps_its_prefix_across_this_deploy` 按本次改动更新了长度与哈希（3412 → 3471），不是意外漂移。

## 验证

- `ruff check` / `ruff format --check`：通过。
- 定向 Python：`test_workspace_tools test_prompts test_schedule_conversation test_modes_skills_attachments test_prefix_cache test_web test_net_tools test_document_catalog_v2 test_images test_context_band test_client_send_id test_queue test_conversations test_schedules_citations test_api test_conversation_mode_fixed` → **478 项，0 失败，7 跳过**。
- 新增用例覆盖：工具在两个模式四个目录里存在且无参数；上海/纽约/UTC 的日期与星期换算；无时区与非法时区回退到 UTC 并标明；API 存下浏览器时区、非法时区 422；会话内 `schedule` 用发送时的时区（纽约 09:00 的下次时间仍是本地 09:00）；发送体带浏览器时区（Portal）。
- Portal `npm test`：50 个文件 **302 项通过**；`npm run build` 通过。
- 定时任务时间口径复核（未改逻辑）：`next_run_at` 以 UTC 存，按任务时区计算墙钟；`<scheduled_task>` 头的 `at` 用 `local_text(occurrence, task.timezone)`；一次性任务落在 DST 空档时顺延到第一个有效分钟（`test_once_in_a_dst_gap_runs_at_the_first_valid_minute`）；结束日期按任务时区的日期比较；调度循环 30 秒一轮，最多晚约 `schedules_interval_seconds`。

## 未覆盖

- 真模型场景（`platform/scenarios`）与浏览器检查未跑：需要先部署本分支并提供测试账号 token；本机栈仍是旧代码。上线后建议跑一次“今天几号”的 Chat/Work 两轮，并看续轮 `cached/prompt`。
- 浏览器在 Run 中途改变时区（旅行、改系统时区）不更新已发出的 Run；插话、排队消息沿用 Run 冻结的时区。
- 工具取 API 进程时钟，不做其他时间源校准。

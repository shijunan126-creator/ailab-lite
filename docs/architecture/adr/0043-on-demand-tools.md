# ADR 0043：低频工具经 call_tool 按需加载

- 状态：已实施（2026-11-13，分支 `perf/prompt-diet`，base `main@e7737c2f`）
- 相关：[设计与实测](../design/2026-11-13-prompt-diet.md)、[ADR 0030](0030-prefix-stable-model-requests.md)、[ADR 0042](0042-single-tool-catalog.md)

## 背景

每次模型调用都携带完整工具 schema。Work 29 个、Chat 14 个工具的 schema 加上系统提示词，开着 GenUI 时首请求约 1.1 万 token。网关按会话缓存，新对话的首请求全部按未命中计费。本机 196 个 run 的统计显示，多数工具很少被调用：`memory` 4 个 run，`schedule`、`run_subtasks`、`current_time` 各 1–2 个，OCR、`save_skill`、`ask_user`、`update_plan`、`copy_library_file` 为 0。

供应商只接受请求里声明过的函数，而 `tools` 位于所有消息之前。10-29 的渐进披露靠运行中修改 `tools` 来“加载”工具，会让整段前缀失效，所以在 ADR 0030 被撤销。Claude Tool Search 和 OpenAI `tool_search` 的做法是由供应商把定义注入到上下文末尾。DeepSeek 和当前网关都不支持这种注入。

## 决定

1. 每种模式固定一组按需工具（`agent/deferred.py` 的 `DEFERRED`），按真实调用频率选定。请求只声明常用工具，再加一个 `call_tool(name, arguments)`。`call_tool` 的 `name` 枚举和描述列出全部按需工具，每个配一句话简介。
2. 第一次调用某个按需工具时只传 `name`，返回它的完整说明和参数，作为普通工具结果追加。说明已在上下文里时，`call_tool` 带 `arguments` 的调用会在执行前还原成真实工具调用：幂等、恢复、事件、工具日志都按真实工具名处理。历史里的 assistant 消息保留模型原本写的 `call_tool`。
3. 说明还没读过就带参数调用的，不执行，只返回说明。少一次往返，不值得用一次没读规则的定时任务或记忆写入去换。
4. 冻结的只是名字（`call_tool` 的枚举随目录在 Run 开始时冻结）。说明本身取当前代码里的版本，因为它以追加的工具结果送达，不影响前缀。授权检查认“声明的工具 ∪ 冻结的按需名字”。没有 `call_tool` 的旧 Run 没有按需工具，行为不变。
5. `search_capabilities` 同时搜索技能和按需工具。按需工具增多到描述里列不下时，改为只列类别，靠搜索发现，不需要改协议。

## 后果

- 每个模式一次预期的前缀重建（提示词版本与工具目录都变），之后恢复逐字节扩展。
- 第一次使用某个按需工具要多一次模型往返。这类工具本来就低频，常用工具仍直接声明。
- 子任务也能经 `call_tool` 使用允许的按需工具（如 OCR、复制资料）。
- 换到支持供应商侧工具搜索的协议时，`DEFERRED` 可以直接映射为 `defer_loading`，调用方语义不变。

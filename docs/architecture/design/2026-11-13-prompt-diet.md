# 提示词瘦身与按需工具（2026-11-13）

- 状态：已实施，本机真实模型对比见[记录](../../progress/updates/2026-11-13-prompt-diet.md)
- 决定：[ADR 0043](../adr/0043-on-demand-tools.md)
- 相关：[前缀缓存设计](2026-10-31-prefix-cache.md)、[ADR 0030](../adr/0030-prefix-stable-model-requests.md)、[ADR 0042](../adr/0042-single-tool-catalog.md)

## 目标与约束

- 目标：降低每次模型请求的固定开销（系统提示词 + 声明的工具），以及整个任务的 token 消耗，同时任务完成率和回答质量不下降。
- 硬约束：前缀稳定（[AGENTS.md §4](../../../AGENTS.md)）。一次 Run 内工具声明和更早的消息不变；会变的内容只能追加。
- 成功标准：用真实模型跑同一套场景对比改前改后。指标包括首请求 token、整个任务的 prompt / 未命中 / 输出 token、模型调用次数、计费额（`model_usage.weighted_amount`），以及场景断言和逐条阅读回答。

## 现状（`main@e7737c2`，o200k 分词口径，开 GenUI）

| | 系统提示词 | 工具 schema | 合计 |
|---|---|---|---|
| Work | 6,313 | 4,632 | 10,945 |
| Chat | 5,450 | 5,229 | 10,679 |

- 网关按会话缓存（`x-opencode-session`）。每个新对话的首请求约 1.1 万 token 全部未命中；这部分固定开销此后每一步都会再发，只是按缓存价计费。
- 本机 196 个 run 的真实调用：`web_search` 51 个 run、`read_url` 44、bash/exec、`view_workspace_file`、`deliver_file`、`load_skill`、`ui_components` 常用；`memory` 4、`review_artifact` 4、`search_images` 5、`schedule`/`current_time`/`run_subtasks`/`search_library`/`resolve_attachment` 各 1–2；OCR、`save_skill`、`ask_user`、`update_plan`、`copy_library_file` 为 0。
- 212 个回答里有 68 个带界面块。组件使用：Stack 57、Series 33、Table 26、BarChart 22、Callout 21、Metric 19、Grid 16、Text 14、HtmlArtifact 12、LineChart 11、Timeline 11、Highlights 9、Compare 8；Segments、Steps 各 2。

## 改动

1. **按需工具**（ADR 0043）：低频工具不再声明，统一由 `call_tool` 列出；第一次使用先读说明（作为追加的工具结果），之后调用。Work 声明 15 个，按需 14 个；Chat 声明 7 个，按需 7 个。`web_search`/`read_url`、文件工具、`deliver_file`、`load_skill`、`ui_components`、`current_time`、`bash_wait`、`review_artifact`、Chat 的文档三件套保持声明。
2. **参数 schema 精简**：从 Pydantic 契约生成的参数去掉 `title`、`maxLength`/`pattern`、`additionalProperties`、int32 上限，`anyOf [X, null]` 收成 X。handler 仍按完整契约校验。文档三件套各从约 1.1k 降到约 0.7k。
3. **系统提示词去重压缩**：合并两模式中重复的规则（PDF/Office 先读基础技能、`search_capabilities`、不可信数据、数据核对），保留每条行为规则。只在用某个工具时才需要的细则移到该工具的说明里，随按需加载送达：记忆的改行规则和“记什么”标准（约 1k）、技能保存约定。
4. **GenUI 索引**：常驻组件去掉 Segments、Steps；“何时用界面”、写法和规则的措辞压缩。由 `portal/components/genui/catalog/prompt.ts` 生成，3,306 → 2,789。

结果（同口径）：Work 约 6.7k、Chat 约 6.35k（未计开启联网后的 `web_search`/`read_url`）。

## 不做

- 不换 Responses 协议：DeepSeek 不提供 `tool_search`，网关也只转 `/chat/completions`。按需加载在客户端完成，不依赖供应商。
- 不改常驻组件签名的生成方式，也不压缩组件说明：这些会直接影响图表正确性，且由 Portal 渲染契约生成。

## 风险与验证

- 第一次使用按需工具多一次往返：用记忆、定时、OCR 场景实测往返次数和总 token。
- 模型可能跳过 `call_tool` 直接调用未声明的工具名：授权仍认冻结的按需名字，会直接执行。
- 规则移出系统提示词后的遵守情况：用场景断言（记忆保存与删除、定时任务、日期查询、图表）和逐条阅读回答核对。

# 思考来源兼容（2026-11-13）

分支 `feat/thinking-compat`，在 `main@910f1525` 上开发和验证，之后 rebase 到 `main@ccbc014e`（#194 只改了 Portal，与本次改动不重叠）。用户看过调研（Codex/DeepSeek：各家思考字段、vLLM `<think>`、Higress、回传思考）后授权实施，要求高性能，可以参考成熟项目的做法。

## 做了什么

- `ailab_platform/thinking.py`：
  - `reasoning_of` 只读一个来源，按 `reasoning_content` → `reasoning` → `thinking` → `reasoning_details` 的顺序取第一个有值的；流式和非流式调用共用（非流式以前只读 `reasoning_content`，读不到新版 vLLM 的 `reasoning`）。
  - `ThinkSplitter` 是流式状态机（START → THINK → TEXT）。开始标签只在输出开头（允许前面有空白）才认，结束标签在思考内任意位置都认。标签被拆到两个 chunk 时，最多暂存一个标签长度的文字。两个标签后面的空白都会去掉。
  - `prefilled` 用于模板已经打开思考块的情况；模型再写一次开始标签也接受。如果到结尾都没出现结束标签，说明模型没有思考，已经归为思考的内容在最终响应里改判为正文。
  - `thinking_disabled` 识别我们自己发出的关闭思考的请求（`thinking.type=disabled`、`enable_thinking=false`、`chat_template_kwargs`、`reasoning_effort=none`），这类请求不按 prefilled 处理。
- `_StreamState` 接入拆分器：只要收到思考字段就调用 `bypass()`，交还暂存的文字并停止拆分；流结束时由 `flush()` 把暂存的尾巴交给 `on_text` / `on_reasoning`。没有配置标签的模型完全不经过拆分器。
- `models.toml` 新增 `think_tag`、`think_prefilled` 两个配置项，在目录里校验（标签名格式；`think_prefilled` 必须是布尔值且要求配了标签）。README 写明推荐做法：上游用 `--reasoning-parser`，Higress 透传；通义千问 provider 用 `passthrough`，不要用 `concat`。

参考成熟项目的做法：Vercel AI SDK `extractReasoningMiddleware`（尾部暂存、`startWithReasoning`），Open WebUI（只回扫上一块的尾部）。没照搬的地方：开始标签只在输出开头识别，避免把正文里的 `<think>` 当成标签；收到思考字段就停止拆分；只有在模型配置里声明了才启用，不自动猜。

## 性能

本机测了 200k 个真实大小的 chunk（单线程，取 3 次中最好的一次）：

| 每个 chunk | 耗时 |
|---|---|
| `json.loads`（对照） | 0.80µs |
| `_StreamState.feed`，不拆分 | 0.49µs |
| `_StreamState.feed`，按标签拆分 | 0.71µs |
| 拆分器本身 | 0.23µs |

一次喂入 5MB 只要 2.1ms，耗时随长度线性增长：已经输出的部分不会再扫描，暂存的内容不超过一个标签长度，每个 chunk 只做一次 `find` 和一次 `rfind`。没有配置标签的模型不经过拆分器，`feed` 的开销和以前一样。JSON 解析本身是拆分器开销的 3 倍多，也不是瓶颈，所以没有为它引入 orjson。

## 验证

- `tests/test_think_tags.py`：
  - 开始/结束标签在每一个字节位置被切开，以及按单个字符逐块输入；200 次随机切分，结果都与整段输入一致；
  - prefilled 和重复写开始标签；正文里出现字面量 `<think>` 或 `</think>`；`<thinking>` 这类其他标签；
  - 暂存尾巴在流结束时交出；收到思考字段后停止拆分；prefilled 到结尾都没闭合时改判为正文；
  - 识别关闭思考的请求；非流式调用拆标签和读 `reasoning` 字段；没配置标签的模型原样保留正文；目录配置校验。
- 现有的 `test_llm_client.py`、`test_chat_agent_contracts.py`、`test_catalog.py` 都通过。Ruff 检查和格式化都通过。
- 完整套件交给 CI。

## 边界

- 没有接真实的 vLLM / SGLang / Higress，标签格式按各家文档和模板来实现。接入时要抓一次真实的 SSE 核对。
- 回传思考（`echo_reasoning`）的逻辑没改：标签模式下，正文里不带标签，思考单独放进 `reasoning_content` 回传，所以每轮请求的前缀构造是固定的。上游模板是否读取这个字段要按模型核实。
- 只支持 `<tag>…</tag>` 这种尖括号标签，`◁think▷` 这类非尖括号标记没有支持，需要时再加。
- 没有改请求构造，所以没有新增前缀缓存的录制测试，也没有跑真模型（当前 DeepSeek 走字段，这次改动对它没有影响）。

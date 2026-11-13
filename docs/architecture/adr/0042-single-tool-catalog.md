# ADR 0042：只保留一套工具目录，退役 V1 文档工具与精简目录

- 状态：已实施（2026-11-13，分支 `feat/current-time-tool`，base `main@d174905b`）
- 相关：[记录](../../progress/updates/2026-11-13-single-catalog.md)、[ADR 0030](0030-prefix-stable-model-requests.md)、[前缀缓存设计](../design/2026-10-31-prefix-cache.md)、[Pi 对齐设计](../design/2026-11-11-pi-aligned-workspace-tools.md)

## 背景

1. 文档工具曾有两套目录（`document_tools_version` V1/V2）。2026-11-08 起新 Run 一律 V2，V1 只为“部署前创建、仍可恢复”的 Run 保留冻结目录。实际没有任何新路径会产生 V1，维护成本却是两套目录、`permitted`/`catalog_version` 分支、四个旧 handler、提示词与技能模块的版本分支，以及两套测试矩阵。
2. 2026-11-01 的前缀缓存设计为了小上下文模型引入精简目录（`REDUCED_V1/V2`，全量 schema 超过输入预算 15% 时裁剪）。实际全量目录 V2 为 Chat 约 5.1k / Work 约 5.4k tokens，节省有限，却带来第二套目录、按模型预算分支、以及提示词与精简目录互相矛盾（规则要求 `ls`/`find`/`grep`/`web_search`/`save_skill`，精简集里没有）。低于精简目录所需上下文（Work 约 19k、Chat 约 25k）时 `catalog_for_budget` 直接抛错，Run 失败。

## 决定

1. **只保留 V2 文档工具**。删除 V1 的 `read_library_file`/`extract_text`/`view_attachment`/`document_view` 工具、`document_tools_version` 参数链与 `catalog_version`，以及提示词/技能模块里的版本分支。Chat 读资料库用 `read_document`/`search_document`/`view_document`；Work 只从 `/workspace/inputs` 读原件。
2. **删除精简目录**。每个模式只有一份目录；小上下文模型也拿全量 schema，不再按预算裁剪，不再因此失败。前缀稳定性不变：目录仍在 Run 开始冻结，运行中不增删工具。
3. **旧 Run 在恢复时升级**：四个退役工具名加入 `RETIRED_TOOLS`，`CONTEXT_ABI_VERSION` 升到 8。恢复旧 checkpoint 时重建 `messages[0]` 与工具目录，记 `context_boundary`（`context_abi_changed` / `tool_catalog_changed`），历史消息保留。
4. **强制过渡，不留兼容层**：测试平台没有旧 Agent 在跑，旧内部端点（`/internal/runs/{id}/library/read|extract-text|image-reference`、`/document-view`）及只服务它们的代码一并删除；API 不再写 `document_tools_version`。等待旧工具的 `file_dependency`（`extract-text`/`image-reference`）在对账时直接恢复，Run 按当前目录继续。

## 后果

- 前缀缓存：所有模式各一次预期重建（提示词与目录都变），之后恢复逐字节前缀；暂停中的 Run 恢复时各一次。
- 小上下文模型（32k）多付约 1.7k tokens 的工具 schema，但不再出现“规则可用、工具不存在”和低于阈值直接失败。低于约 25k 的 Chat / 19k 的 Work 模型此前根本无法运行，现在至少能发出请求，实际可用性取决于上下文窗口本身。
- 退役 V1 后，恢复中的旧 Run 若恰好停在一次旧工具调用上，该次调用会得到一次“tool is not available”，模型可改用 V2 工具；历史里的旧工具结果仍按原文保留。
- 修订 ADR 0030 与 2026-10-31 前缀缓存设计中“按预算选择完整或精简目录”的部分；决定 2（历史按原文渲染）与“固定目录、追加式上下文”不变。

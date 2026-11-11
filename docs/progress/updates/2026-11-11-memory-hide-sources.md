# 记忆页不再显示来源对话（2026-11-11）

- 用户反馈：记忆行右侧的「来源对话 · 等 N 段对话」占宽、无法展开，作用不大。决定：前端不显示，后端保留来源数据并继续负责整理与删除。
- 基线：`main@bc5c5b5c`，分支 `feat/memory-hide-sources`。

## 改动

- Portal：记忆行只留内容、日期和菜单；删去来源链接与「等 N 段对话」及对应样式，`MemoryItem` 类型去掉 `sources`。
- API：`/v1/memory/view` 与条目 PATCH 不再返回 `sources`，省去每次读页面时对 runs/conversations 的一次关联查询；删除无调用方的 `GET /v1/memory/{entry_id}/sources`；契约去掉 `MemorySource`。
- 未改：`memory_items.source_run_ids`、`memory_item_sources` 索引表、整理（拆分继承来源）、删除对话/项目时的来源清理（ADR 0036：行保留、来源与引文移除，仅由该来源支撑的历史版本删除）。

## 验证

- 核对删除链：删对话/项目 → `source_lifecycle` 清掉行的 `evidence_run_ids` 与引文并经 `replace_entries` 重写索引表；`redact_closed_sources` 处理历史版本；删除记忆行由外键级联清索引。
- `tests/test_memory_source_retention.py` 改为直接断言 `source_run_ids` 与 `memory_item_sources` 一致，并新增「删除记忆行后索引表无残留」；视图断言不含 `sources`。
- 本地：ruff 通过；`test_memory_source_retention/document/summary/recall/query_plans` 全部通过；Portal `memory-view` 10 项通过、`tsc` 干净；1280/375 宽浏览器截图确认（Playwright 拦截接口，临时文件已删）。
- `tests/test_document_context.py` 有 3 项单独运行时报缺表，在 `main` 上同样失败，与本改动无关，以 CI 全套为准。
- CI 首轮（`d85dff5c`）双库均只失败 `test_memory_v2.py::test_management_sources_are_batched_for_both_views`：它断言视图恰好一次来源批量查询，本次已去掉该查询；改为断言视图不查来源。
- 未做：真实模型检查（不涉及请求构造）；未部署。

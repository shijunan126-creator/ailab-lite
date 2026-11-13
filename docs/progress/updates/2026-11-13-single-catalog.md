# 只保留一套工具目录：退役 V1 与精简目录（2026-11-13）

用户要求：V1 文档工具彻底退役，全部改 V2（旧 Run 恢复时也走新版）；精简目录一并去掉，只保留全量目录。理由是维护成本高，精简实际省下的 token 不多。

设计与取舍见 [ADR 0042](../architecture/adr/0042-single-tool-catalog.md)。

## 改动

- 删除 V1 工具与实现：`read_library_file`、`extract_text`、`view_attachment`、`document_view` 的 schema 与 Agent handler；`document_tools_version` 参数链、`catalog_version`、`permitted(version)`、`user_prompt`/`skill_modules` 的版本分支全部移除。Chat 只保留 `read_document`/`search_document`/`view_document`，Work 只从 `/workspace/inputs` 读原件。
- 删除精简目录：`REDUCED_V1`/`REDUCED_V2`、`catalog_for_budget` 与小上下文裁剪分支；`_tools_for_run` 不再按 15% 预算裁剪，也不会因目录放不下而 `LLMError`。
- 提示词/技能：V2 文案直接写进 `BASE_PROMPT`/`CHAT_MODE_NOTE`，删除字符串替换；`read_module` 始终返回当前指南导航；两模式版本号升到 `2026-11-13-single-catalog-*`。
- 旧 Run 升级：四个退役工具名加入 `RETIRED_TOOLS`，`CONTEXT_ABI_VERSION` 7 → 8。恢复旧 checkpoint 时用当前提示词和目录重建，记 `context_boundary`，历史消息保留。
- 旧内部读取端点保留：`/internal/runs/{id}/library/read|extract-text|image-reference` 与 `/document-view` 不再按目录版本拒绝，供仍在运行旧 Agent 的部署调用；它们不是模型可见的工具。
- `scripts/evaluate_memory_trigger.py`、`scripts/evaluate_memory_prefix.py` 同步去掉版本参数。

## 验证

- `ruff check` / `ruff format --check`：通过。
- 本机全量 SQLite：**3368 项，0 失败，0 错误，257 跳过**（需按 AGENTS §9 把 GNU coreutils/findutils 加进 `PATH`；否则 sandbox/preview 环境用例会报 `realpath` 缺失，与本次改动无关）。
- 新增/改写用例：单一目录（两模式、Chat 文档工具、Work 不含读取器）；旧目录 checkpoint 恢复后升级并重建；`current_time` 仍在两模式目录；Chat 提示词字节闸门更新为 3463 字符 / `efadb284…`；退役工具的调度/捕获/资料库机制仍被覆盖。
- 未跑：PostgreSQL 全套、E2E、真模型场景、浏览器；这几项由 CI 与部署后验证承担。

## 边界

- 小上下文模型不再被裁剪：32k 模型多带约 1.7k tokens 的工具 schema；低于约 25k（Chat）/19k（Work）的模型此前直接失败，现在能发请求，但上下文仍是瓶颈。
- 恢复中的旧 Run 若恰好停在一次旧工具调用上，该调用会收到一次“tool is not available”，模型需改用 V2 工具；历史里的旧工具结果按原文保留。
- `load_skill` 对基础技能返回指南导航（原来返回整份 SKILL.md）；深内容需要按导航里的资源名继续读。
- 精简目录删除后没有替代的“工具白名单”；模型的工具可见性仍由模式、能力与 Run 冻结目录决定。

## 下一步

- 推送、提 PR、CI（双库 + E2E）按用户当次授权；部署同样等明确要求。

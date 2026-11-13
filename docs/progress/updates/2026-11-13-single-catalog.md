# 只保留一套工具目录：退役 V1 与精简目录（2026-11-13）

用户要求：V1 文档工具彻底退役，全部改 V2（旧 Run 恢复时也走新版）；精简目录一并去掉，只保留全量目录。理由是维护成本高，精简实际省下的 token 不多。

设计与取舍见 [ADR 0042](../architecture/adr/0042-single-tool-catalog.md)。

## 改动

- 删除 V1 工具与实现：`read_library_file`、`extract_text`、`view_attachment`、`document_view` 的 schema 与 Agent handler；`document_tools_version` 参数链、`catalog_version`、`permitted(version)`、`user_prompt`/`skill_modules` 的版本分支全部移除。Chat 只保留 `read_document`/`search_document`/`view_document`，Work 只从 `/workspace/inputs` 读原件。
- 删除精简目录：`REDUCED_V1`/`REDUCED_V2`、`catalog_for_budget` 与小上下文裁剪分支；`_tools_for_run` 不再按 15% 预算裁剪，也不会因目录放不下而 `LLMError`。
- 提示词/技能：V2 文案直接写进 `BASE_PROMPT`/`CHAT_MODE_NOTE`，删除字符串替换；`read_module` 始终返回当前指南导航；两模式版本号升到 `2026-11-13-single-catalog-*`。
- 旧 Run 升级：四个退役工具名加入 `RETIRED_TOOLS`，`CONTEXT_ABI_VERSION` 7 → 8。恢复旧 checkpoint 时用当前提示词和目录重建，记 `context_boundary`，历史消息保留。
- 旧内部端点与其专属代码一并删除（评审后，见下节）。
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

## PR 评审修复（2026-11-13，同一 PR）

- **附件图片缺失不再静默降级**：此前 V2 对已准备的图片和文档渲染页都要求真的带上图像，否则本轮报错；首版重构只保留了渲染页。改回两者一致，补 `test_a_prepared_image_the_model_cannot_see_stops_the_turn`。
- **未知浏览器时区不拒收消息**：`contracts/time.Timezone` 改为解析失败即视为未提供，回退账号时区；原来整条消息 422。
- **强制过渡，删除旧内部端点**：用户确认测试平台没有旧 Agent 在跑。删除 `/internal/runs/{id}/library/read|extract-text|image-reference`、`/internal/runs/{id}/document-view`，以及只服务它们的代码：Agent 里四个 V1 handler（首版只从目录里摘掉，handler 还在）、`client.document_view`、`viewer.event` 出站分发与 Portal 的 `document.reveal` 实时处理、`viewer_captures.render_capture`、`DocumentView` 契约、`file_dependency` 的 `extract-text`/`image-reference` 动作。遗留的这两类依赖在对账时直接恢复，Run 按当前目录继续。公开的 `/v1/files/{id}/extract-text`（资料库 UI）保留。
- **残留清理**：`viewer_captures.save` 去掉恒为真的 `canonical` 分支；`attachment_input` 删掉无调用方的 `chat_file`/`readable_text`/`prepare_native_input`/`image_reference_for_chat`，Work 附件不再写恒为空的 `text/chars/truncated/visual_pages`；`internal_skills.TOOLS` 与 recall 描述里的旧工具名移除（模型可见字节不变）。
- 历史展示保留旧工具名标签（`process_view`、Portal `work-process`），旧会话的事件仍要能显示。Portal `file_wait.action` 类型改为与 API 一致的四个 V2 动作（原类型早已与后端不符）。
- 测试：只验证已删代码的用例删除（`document.reveal` 两个故障用例、reveal 出站、`document_images` 相关 7 个）；借旧端点做探针的用例改走 `read_document`/`authorize-copy`/`native-text`，覆盖不减。新增：已准备图片取不到时本轮报错；等待 `extract-text`/`image-reference` 的旧 Run 被对账恢复而非永久等待；旧路由返回 404/405。
- 验证（评审修复后的工作区）：ruff 通过；本机全量 SQLite **3104 passed / 0 failed / 257 skipped**；Portal 302 项通过、`tsc --noEmit` 通过。PostgreSQL、E2E 由 CI 承担；真模型与浏览器未跑。

## 下一步

- 推送、提 PR、CI（双库 + E2E）按用户当次授权；部署同样等明确要求。

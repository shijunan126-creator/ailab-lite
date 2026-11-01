# GenUI 存图与表格复制：交接（2026-11-01）

- 分支 `feat/genui-image`（worktree `.worktrees/genui-image`），base `origin/main@4104d5a`。用户本轮授权：设计实施、PR、merge；测试与最终部署可交给 Codex（gpt-6.1-sol medium），质量由 Claude 把关。
- 已完成：GenUI 块悬停复制为图片/下载图片；Markdown 与 GenUI 表格复制为表格（HTML + Markdown）；代码块单独复制沿用。设计、成本、检查与未测边界见[记录](updates/2026-11-01-genui-copy.md)，陷阱见 HANDOFF 顶部。
- 下一步：E2E 新增用例 + 全套检查 → PR → CI 全绿后 merge → 只重建 Portal 部署。

---

# 生命周期收尾与定时任务规范（2026-11-01，已合并）

- 分支 `feat/lifecycle-limits`（worktree `.worktrees/deletion-review`），base `origin/main@ada5f60`；[PR #133](https://github.com/shijunan126-creator/ailab-lite/pull/133) 最终 head `27303a1` CI 9 项全绿，squash 为 `main@b42a3bd`。用户授权：按删除审查后续方案实现停用取消任务、模板/品牌素材回收、回收站预览，并设计实现定时任务规范；提 PR、测试后合并；未要求部署。
- 已完成：见[本轮记录](updates/2026-11-01-lifecycle-limits.md)（停用即取消任务、`run_asset_refs` 引用回收、回收站只读预览与恢复、定时任务结束日期/不重叠/无人查看与连续失败自动暂停，迁移 0045）。
- 用户决定：保存的记忆事实随来源对话删除时保留；删除定时任务不取消进行中的一轮；账号删除暂不做。
- 下一步：未部署（本轮未授权）；需要时由用户授权部署 `main@b42a3bd`（后端 + Portal，含迁移 0045）。账号删除（4b）暂缓，不自动推进。

---

## 上一交接：删除路径审查（已合并）

- 分支 `fix/deletion-review`（worktree `.worktrees/deletion-review`），base `origin/main@17aa2ef`。用户授权：审查并修复全部删除逻辑，提 PR、测试后合并；本轮未要求部署。
- 已完成：后端 12 项删除缺陷（暂停 Run 取消、锁内复验、来源 GC 串行、迟到任务、tombstone 与原件孤儿回收、历史/分享/分叉删除占位与来源围栏、purged 预览 410、0044 索引），回收站文件在本人历史中保留原名，Portal 把已删除附件/产物显示为划线“已删除”且无操作，E2E 端口占用即拒绝启动。
- 结论、缺陷表、检查与待决事项：[本轮记录](updates/2026-11-01-deletion-review.md)。
- 已合并：[PR #131](https://github.com/shijunan126-creator/ailab-lite/pull/131) 最终 head `764938f` CI 9 项全绿（含 PostgreSQL、全套 E2E），squash 为 `main@e5e78af`。首轮 PG 仅 `test_document_indexes_are_bounded_at_100000_blocks` 失败：批量插入后无统计信息，计划取决于 autoanalyze 时机；改为 EXPLAIN 前 ANALYZE，本机 PG16 连跑 3 次通过。
- 下一步：未部署（本轮未授权）；需要时由用户授权部署 `main@e5e78af`（后端 + Portal，含迁移 0044）。待产品决定的五项见本轮记录，不自动推进。

## 其他工作保持原边界

前缀缓存/渲染已合并部署的历史证据见[原记录](updates/2026-10-31-prefix-cache-render.md)。召回正文摘要约束、真实 Docker 多用户故障与 CPU/容量边界、DOCX 全页性能目标、Work watchdog 是否启用 nudge、GenUI P6 与组件目录扩展等仍待用户决定，不自动开展。

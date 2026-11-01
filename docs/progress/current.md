# 删除路径审查与修复（2026-11-01）

- 分支 `fix/deletion-review`（worktree `.worktrees/deletion-review`），base `origin/main@17aa2ef`。用户授权：审查并修复全部删除逻辑，提 PR、测试后合并；本轮未要求部署。
- 已完成：后端 12 项删除缺陷（暂停 Run 取消、锁内复验、来源 GC 串行、迟到任务、tombstone 与原件孤儿回收、历史/分享/分叉删除占位与来源围栏、purged 预览 410、0044 索引），回收站文件在本人历史中保留原名，Portal 把已删除附件/产物显示为划线“已删除”且无操作，E2E 端口占用即拒绝启动。
- 结论、缺陷表、检查与待决事项：[本轮记录](updates/2026-11-01-deletion-review.md)。
- 已合并：[PR #131](https://github.com/shijunan126-creator/ailab-lite/pull/131) 最终 head `764938f` CI 9 项全绿（含 PostgreSQL、全套 E2E），squash 为 `main@e5e78af`。首轮 PG 仅 `test_document_indexes_are_bounded_at_100000_blocks` 失败：批量插入后无统计信息，计划取决于 autoanalyze 时机；改为 EXPLAIN 前 ANALYZE，本机 PG16 连跑 3 次通过。
- 下一步：未部署（本轮未授权）；需要时由用户授权部署 `main@e5e78af`（后端 + Portal，含迁移 0044）。待产品决定的五项见本轮记录，不自动推进。

## 其他工作保持原边界

前缀缓存/渲染已合并部署的历史证据见[原记录](updates/2026-10-31-prefix-cache-render.md)。召回正文摘要约束、真实 Docker 多用户故障与 CPU/容量边界、DOCX 全页性能目标、Work watchdog 是否启用 nudge、GenUI P6 与组件目录扩展等仍待用户决定，不自动开展。

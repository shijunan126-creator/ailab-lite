# 后端删除正确性审查（2026-11-01）

- 工作树 `.worktrees/deletion-review`，分支 `fix/deletion-review`，base `origin/main@17aa2ef`；开始时 clean。旧页的前缀缓存工作树/发布状态不适用于本轮。
- 当次授权：审查并修复后端所有删除路径，新增回归，本地 conventional commits；不 push、不部署、不改 Portal。最终只跑一次 Ruff + 完整 SQLite，跳过 PG/E2E/Portal。
- 已修：来源失效取消等待/暂停 Run；工具读在用户锁内复验；来源 GC 与用户写入串行；已删除文件的任务重投安全完成；tombstone 派生对象孤儿收敛。历史附件/产物及分享/分叉增加明确删除占位；失效分叉不再复制旧预览、摘要、事件正文。补齐 purged 预览 410、原件孤儿收敛和系统 plan/question 清理；0044 仅补 artifacts.file_id 索引。
- 检查：完整 SQLite 在 `3459066` 一次运行 1835/152/2；两项修复后受影响组 84/16/0，最终迁移组 56/42/0；最终 Ruff 通过。最终 HEAD 未重跑全套，遵守当次一次全套限制。
- 证据与剩余风险：[本轮记录](updates/2026-11-01-deletion-review.md)；用户要求的完整报告位于仓库外 `/private/tmp/del-review/report.md`。
- 下一步：由用户/Claude 审阅 `/private/tmp/del-review/report.md`，接 Portal 删除状态处理；本地提交已完成，不自动 push/部署或追加 PG/E2E 验收。

## 其他工作保持原边界

前缀缓存/渲染已合并部署的历史证据见[原记录](updates/2026-10-31-prefix-cache-render.md)。召回正文摘要约束、真实 Docker 多用户故障与 CPU/容量边界、DOCX 全页性能目标，以及 Work watchdog 是否启用 nudge 等仍未据本轮验收，不自动开展。其他已暂停产品工作保持暂停。

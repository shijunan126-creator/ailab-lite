# 技能广场精选内容（2026-11-01）

- 分支 `t3/curated-office-skill-market`，base `origin/main@7f71d30`。本次授权：仅后端/内容实施、测试、一个 PR、CI 全绿后 squash merge；不改 Portal、不部署。Portal 重建由另一代理负责。
- 已完成：八个纯 Markdown 办公业务技能、目录元数据、wheel package-data 与安装回归测试。验证与未测边界见[本轮记录](updates/2026-11-01-curated-skill-market.md)。
- 下一步：完整 Python 套件一次通过；[PR #140](https://github.com/shijunan126-creator/ailab-lite/pull/140) 等待 CI 并合并；保留其他任务原有边界。起始工作树附带的无关 Portal 提交已保留在 `preserve/market-original-portal-follow`，不进入本 PR。

---

# 记忆摘要与项目记忆模式：交接（2026-11-01，已合并并部署）

- 分支 `feat/memory-summary`，base `origin/main@4c6f092`；[PR #137](https://github.com/shijunan126-creator/ailab-lite/pull/137) head `3af6da3` CI 9 项全绿，squash 为 `main@21f3300`。用户授权：设计实施、PR、merge、部署；后端实现、测试与部署由 Codex（gpt-6.1-sol medium），Claude 审查把关。指令、实现、验证与风险见[本轮记录](updates/2026-11-01-memory-summary.md)，设计见 [design](../architecture/design/2026-11-01-memory-summary-project-scope.md)。
- 已完成：项目记忆模式 shared/isolated 两向迁移与融合、记忆摘要与对话式修改/撤销/清空、`PATCH /v1/memory/settings`、记忆页/项目记忆弹窗与其他页签 UI 修整；审查后修复摘要阶段与批次终态解耦（终态/游标先落库，摘要阶段保留活动租约）、请求作用域优先、编辑本作用域围栏及若干查询开销。
- 已部署：Codex 把 `main@21f3300` 部署到本机 AILab-local：后端共用镜像与 Portal 为 `release-21f3300`，其余复用；迁移 0045→0046；12 服务 running、10 项健康检查 healthy、Portal 200。未备份、未做部署后验收，细节只在 AILab-local 记录。
- 下一步：本范围完成。未测边界：真实模型下摘要质量、编辑理解与融合判定；升级后既有共享项目历史互相可召回（设计决定）。待用户使用反馈。

---

# GenUI 存图与表格复制：交接（2026-11-01）

- 分支 `feat/genui-image`（worktree `.worktrees/genui-image`），base `origin/main@4104d5a`。用户本轮授权：设计实施、PR、merge；测试与最终部署可交给 Codex（gpt-6.1-sol medium），质量由 Claude 把关。
- 已完成：GenUI 块悬停复制为图片/下载图片；Markdown 与 GenUI 表格复制为表格（HTML + Markdown）；代码块单独复制沿用。设计、成本、检查与未测边界见[记录](updates/2026-11-01-genui-copy.md)，陷阱见 HANDOFF 顶部。
- 已合并：PR [#135](https://github.com/shijunan126-creator/ailab-lite/pull/135) CI 9 项全绿后 squash 为 `main@8890338`。
- 已部署：Codex 把完整 `main@8890338` 部署到本机 AILab-local（main 另含未部署的 #131/#133 后端与迁移，只换 Portal 会前后端错配）：后端共用镜像与 Portal 均为 `release-8890338`，其余镜像复用；迁移 0043→0045；12 服务 running、健康检查均 healthy、Portal 200。未备份、未做部署后验收，细节只在 AILab-local 记录。
- 下一步：本范围完成。未测边界（Safari/Firefox 图片剪贴板、iOS 下载、在 Office 内实际粘贴）见记录，待用户使用反馈。

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

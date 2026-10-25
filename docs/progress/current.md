# 当前工作

## 长任务与来源集成（2026-10-25，本地验收完成，交父任务集成，未合并/未部署）

用户授权：集成来源面板与长任务后端，修复审查发现，完整双库/Portal/五服务验收并本地提交；不push/PR/merge/deploy，不改Portal行为。历史发布授权不适用于本轮。

- 指定集成基线`abb4a9f`已包含Portal来源入口/引用气泡与预算/后台工具提示（`aa1b31e`、`abb4a9f`）、来源持久化`41da18f`、长任务后端`8d7762d..a1d2137`（含ADR0008）。已核对`loop.py`冲突解决同时保留BudgetPause与最终result.sources。各子分支历史结果见[来源](updates/2026-10-25-run-sources.md)与[长任务](updates/2026-10-25-long-runs.md)，本轮统一验收见[集成记录](updates/2026-10-25-long-runs-sources.md)。
- 产品修复`b23730e`，consumer契约回归`fe78365`，E2E定位器适配`c7a3290`：瞬态job续租失败在安全期限内退避重试，409立即停止；claim/renew响应时间不增加租约。API在用户锁内合并暂停/失败的部分结果；waiting_user、预算暂停与租约中断均持久sources；回归核对单Run/会话详情的来源与可继续中断原因。
- 当前工作树`/Users/michael/.t3/worktrees/ailab-lite/t3-12d66bb9`，分支`feat/long-runs-sources-fixes`，base `abb4a9f`。原要求分支`feat/long-runs-sources`同时被主checkout使用且并发推进`1d531fe`，因此隔离修复，未将Portal反向差异纳入提交。父任务须cherry-pick本轮修复及交接提交；本轮测试不包含并发的`1d531fe`。
- 最终验收：Ruff check/format（347文件）、SQLite1180通过/121跳过、PG16+Redis1266通过/35跳过、Portal37项/default production build、五服务E2E98通过/0跳过，均0失败。测试产品基线`c7a3290`（SQLite先验`fe78365`，后端树相同），初轮失败与修正保留在集成记录；专属PG/Redis及五服务已停止，七端口无监听。真实provider连续1–4小时、Linux sandbox后台exec、目标机RSS/容量未验。
- 下一步：父任务cherry-pick隔离分支的三个修复/测试提交及本轮文档提交，复核包含其并发Portal改动的最终集成树；本轮无待实现修复。未授权发布，不启动其他路线项。

## 真实沙箱可运行性验证（2026-10-25，PR #54已合并，未部署）

用户要求确认项目能否实际跑通，重点补足真实OpenSandbox验证。分支 `verify/real-sandbox-acceptance`，base `origin/main@9cfadfa`（已含PR #46/#49/#51）。允许隔离测试与必要修复；不替换现有部署、不迁移个人业务库、不清理现有服务/数据。

- 已读仓库与本机运行交接，现有五应用健康，实际provider为Docker；尚无OpenSandbox服务。本次部署只读基线SQLite0028/quick_check正常/FK0，尚无文件或Run数据。
- 修复Docker到期/删除竞态和OpenSandbox DNS-only字面IP绕过；新配置默认dns+nft、旧配置拒绝且绑定实际进程。代码/测试`6d5c305`、`1ca7c6d`、`6933c72`。真实Docker/runsc 9项、OpenSandbox/runc+dns+nft 9项、PG16+Redis+OpenSandbox五服务浏览器7项通过，均0跳过。SQLite/PG全套1178/1224通过（跳过81/35），Portal35/build、Ruff339、shell检查通过。
- 专属五服务/浏览器/PG/Redis/OpenSandbox及镜像已清理，原九容器保持运行、五应用健康；原0028业务库未迁移，原有ID保留并保留期间新增的用户数据。个人配置与三份.DS_Store未动。[PR #54](https://github.com/shijunan126-creator/ailab-lite/pull/54)最终head `16245a718e86ab61701441218b46c9360b67251f` 的8项远端检查全部通过，无阻塞审查/冲突，squash合入main `a646ba355dd35f6f65d300ee7b480338125b493e`；本PR的14个文件与测试head一致，同时保留main并行合入的账号UI变更。结果见[本轮记录](updates/2026-10-25-real-sandbox-acceptance.md)。
- 当前仅文档交接分支`docs/real-sandbox-merged`，base `origin/main@a646ba3`。本轮验证/修复及产品合并已完成，无待实现项；不自动部署或启动下一路线项，发布需后续授权。模型/扫描替身、外部组合及长期容量边界仍保留。

## 单机全量代码审查与修复（2026-10-25，PR #49已合入main，未部署）

用户授权全库审查、直接修复、完整验证、提 PR 并在检查通过后合并；不包含重新部署。目标是在单机上改善体验、调度、性能与可靠性，同时保留扩展边界。实现分支 `fix/single-node-review`，base `origin/main@df30dd5`，起点 `aadfa9a`（前轮合并交接文档）。当前仅文档交接分支 `docs/single-node-review-merged`，base `origin/main@d11f86e`。个人配置/运行数据和三份 `.DS_Store` 保留。

- 产品代码 `869e06b`，测试选择器修正 `e23cf4d`。修复九类问题：恢复预算、调用部门快照、详情批量查询、SSE 无进展重连、扫描轮询、分支分页/预览竞态、搜索续查、累计插话与历史文件引用；迁移 `0029 ← 0028`。
- 当前验证：SQLite+Redis 全套1170通过/79跳过、覆盖率87.04%；PG16+Redis全套1216通过/33跳过；Portal35通过/build、Ruff、导入/技能/依赖锁/golden、CI单测28项、ShellCheck/语法和三套部署静态渲染通过。20轮详情读取86→9次；500轮首屏本机SQLite/PG p95约20/21ms。
- 最终五服务E2E98通过/0失败/0跳过；首轮夹具与选择器失败及修正保留在报告。专用PG/Redis、五服务与浏览器均已停止，本轮端口无监听。
- 远端 `f601680`：PG、E2E、S3、quality/shell通过，SQLite仅500轮性能分项失败（exclusive插值p95=156ms、max209ms，另外24/25约25–33ms）。保留150ms门槛，将分项改为100次实测nearest-rank p95；双库性能各11项通过（SQLite覆盖率下18.31ms、PG16.40ms），SQLite覆盖率全套1170/79通过、87.04%。专用PG/Redis已再次停止。最新head `0dc288433f15916dcabc21ee6d987fd5a51b8bb8` 的八项检查全部通过，无阻塞审查或冲突，已squash合入main `d11f86ee2b40d3e9a0224358e4389f37b81fe357`；合并树与测试head一致。
- 下一步：代码审查与修复的合并授权已完成。本轮无待实现项；不自动启动其他路线项或部署，后续发布需用户新授权。[PR #49](https://github.com/shijunan126-creator/ailab-lite/pull/49)已合并并停止监控。详细发现/性能/边界见[审查报告](../reviews/2026-10-25-single-node-review.md)，本轮结果与PR将记录在[交付记录](updates/2026-10-25-single-node-review.md)。

## 已有发布状态与边界

PR #48 已合入主线 `df30dd5`，模板预览自动切图尚未重新部署。本机部署记录仍为 `b2c8394`；本轮不改变运行环境。旧交接中“工作区未部署”已被后续模板集成部署记录取代，不据历史小节判断当前状态。

历史证据：[模板修复/合并](updates/2026-10-24-template-preview-refresh.md)、[最近部署验收](updates/2026-10-24-ppt-templates-clean-acceptance.md)、[工作区集成](updates/2026-10-24-workspace-features-integration.md)、[早期发布](updates/2026-10-24-release-ui-features.md)。真实硬件链路/长期容量和未配置外部组件仍按报告保留，不自动启动下一路线项。

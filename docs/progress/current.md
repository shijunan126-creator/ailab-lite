# 当前工作

## 单机全量代码审查与修复（2026-10-25，已本地验收，PR #49待CI）

用户授权全库审查、直接修复、完整验证、提 PR 并在检查通过后合并；不包含重新部署。目标是在单机上改善体验、调度、性能与可靠性，同时保留扩展边界。分支 `fix/single-node-review`，base `origin/main@df30dd5`，起点 `aadfa9a`（前轮合并交接文档）。个人配置/运行数据和三份 `.DS_Store` 保留。

- 产品代码 `869e06b`，测试选择器修正 `e23cf4d`。修复九类问题：恢复预算、调用部门快照、详情批量查询、SSE 无进展重连、扫描轮询、分支分页/预览竞态、搜索续查、累计插话与历史文件引用；迁移 `0029 ← 0028`。
- 当前验证：SQLite+Redis 全套1170通过/79跳过、覆盖率87.04%；PG16+Redis全套1216通过/33跳过；Portal35通过/build、Ruff、导入/技能/依赖锁/golden、CI单测28项、ShellCheck/语法和三套部署静态渲染通过。20轮详情读取86→9次；500轮首屏本机SQLite/PG p95约20/21ms。
- 最终五服务E2E98通过/0失败/0跳过；首轮夹具与选择器失败及修正保留在报告。专用PG/Redis、五服务与浏览器均已停止，本轮端口无监听。
- 下一步：[PR #49](https://github.com/shijunan126-creator/ailab-lite/pull/49)已创建并登记，检查精确head的CI/审查与冲突后合并。详细发现/性能/边界见[审查报告](../reviews/2026-10-25-single-node-review.md)，本轮结果与PR将记录在[交付记录](updates/2026-10-25-single-node-review.md)。

## 已有发布状态与边界

PR #48 已合入主线 `df30dd5`，模板预览自动切图尚未重新部署。本机部署记录仍为 `b2c8394`；本轮不改变运行环境。旧交接中“工作区未部署”已被后续模板集成部署记录取代，不据历史小节判断当前状态。

历史证据：[模板修复/合并](updates/2026-10-24-template-preview-refresh.md)、[最近部署验收](updates/2026-10-24-ppt-templates-clean-acceptance.md)、[工作区集成](updates/2026-10-24-workspace-features-integration.md)、[早期发布](updates/2026-10-24-release-ui-features.md)。真实硬件链路/长期容量和未配置外部组件仍按报告保留，不自动启动下一路线项。

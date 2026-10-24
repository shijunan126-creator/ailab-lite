# 当前工作

更新：2026-10-24。最新用户授权：重建最新 Portal；将 CI 修复提交 PR，检查通过后合入 main。此前“先不 merge/先不删除”的暂停已由此次明确的 PR/合并指令更新；不做工作树、镜像或数据清理。

## 本轮

- CI 分支 `fix/ci-test-completeness`，独立工作树 `/Users/michael/Developer/ailab-lite-ci-test-completeness`；初始基线 `1a384558`，现集成 `origin/main@2093dfc`（含 PR #36 k3s/OSB、PR #37 Portal 思考状态头）。三个交接文档冲突均保留双方有效内容。
- 已实现范围漏测修复、可测稳定门禁、真实契约防 skip、独立生产依赖导入、E2E 日志/失败 trace、80% 覆盖底线、全部 Shell 检查，以及每周/手动 Docker、Office、ClamAV、Keycloak 验收。
- 首轮本地双库、Portal build/E2E、静态检查已通过；这是旧基线证据。当前集成与 PR 结果见 [CI 更新](updates/2026-10-24-ci-test-completeness.md)。
- 最新 Portal 重建/部署细节、外盘运行配置与实际状态仅记本机独立 HANDOFF；不把个人配置和数据提交到仓库。

## 先前成果与保留边界

- PR #33 受控联网、#36 k3s/OSB、#37 思考状态头已在 main；#36 的实测与约束见 [更新](updates/2026-10-24-k3s-osb-network.md)、[专项证据](../reviews/2026-10-24-controlled-network.md)。
- OSB/gVisor、多架构、付费模型、企业 IdP 与目标机长期联合容量不能由普通 CI 推导；已有实际 k3s 验收不等于本轮新增组件工作流已执行。
- 外盘断连恢复已由另一任务处理；本轮不重做断盘/恢复实验，不自动开展路线图其他事项。

## 下一步

完成 Portal 新版验收，提交 CI PR；核对实际 head 的全部检查并修复失败后合并。新增每周组件工作流须在可用分支实际运行后才标记远端验收。

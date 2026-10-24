# 当前工作

## 当前：工作区功能（2026-10-24，进行中）

用户授权：实现项目说明、公司品牌套件、完成通知与收件箱、回答中排队/插话、内部只读分享、资料库摘要/OCR核验/就文件提问、移入项目带文件（**不做**管理员用量看板）；测试通过后提 PR 并合并。**未授权部署。** 主 Agent（Claude）负责前端、架构与审核，后端由 Codex `gpt-6.1-sol` medium 三个并行分支实现（A `feat/projects-brand` 0025、B `feat/inbox-queue` 0026、C `feat/shares-library` 0027，集成时改链）。

- 分支 `feat/workspace-features`，基线 `main@74e66c0`。契约与分工见[设计](../reviews/2026-10-24-workspace-features-design.md)。
- 后端 A：`feat/projects-brand`（base `00c5cc9`）实现与独立双库验证见[记录](updates/2026-10-24-projects-brand-backend.md)；待父任务审核合入、迁移链/Portal/镜像集成。

- C 后端 `feat/shares-library`（起点00c5cc9/base74e66c0）已本地提交分享/摘要/OCR选页并通过双库；通知仍no-op、0027待接0026，父Agent下一步集成；[交付与测试](updates/2026-10-24-shares-library-backend.md)。
- 已完成：三个后端分支已合入并改链 0025←0024、0026←0025、0027←0026；分享通知接入收件箱；Portal 全部 7 项界面已实现并按合入后的接口对齐（`139ae5d`、`f106d28`）。主 Agent 本地检查：Portal 32 项 / tsc / build 通过，Ruff 通过，SQLite 迁移与品牌子集 39 通过。
- 进行中：Codex 集成任务（起点 `f106d28`）——补 `RunDetail.steered`、分享通知跨用户锁顺序、跨分支交互；双库全套、五服务 E2E 新增功能用例与截图、有限真实模型；推送并建 PR（不合并）。
- 下一步：主 Agent 审核截图与结果 → CI 通过后 squash 合并。不部署（未授权）；`sbx-office` 镜像需重建，部署时另行授权。

- B后端 `feat/inbox-queue`（起点00c5cc9）：9992308通知/队列/steer双库验证通过（SQLite1057、PG+Redis1138），见[B记录](updates/2026-10-24-inbox-queue-backend.md)；待父任务审核合入、0026改接0025及统一Portal/E2E验收。

## UI 功能发布完成（2026-10-24）

用户授权顺序：真实模型验收 → PR/CI green → squash merge → 备份并部署合并后的 main。三个门禁现均通过；本次没有产品代码或 `platform/portal/` 改动。

- **已合入 main**：[PR #42](https://github.com/shijunan126-creator/ailab-lite/pull/42)，head `16a88220e8c1ee9b6363fbc6563b21e8e0234277` 的8项检查成功；squash提交 `b07548995ffbce3fc618f94955c5d66b54b380c7`。合并树与测试head完全一致。
- **已部署并通过有限外部验收**：固定部署同一 `b075489`；真实旧数据升级0021→0022→0023→0024，完整备份checksum/恢复可读性、迁移前后ID/数量、会话线性树、文件哈希检查通过。五服务healthy、标题指标抓取、30秒调度器真实到期执行与全部部署浏览器smoke通过。
- 证据、资源数字、镜像ID、当前与历史验证边界见[发布记录 Gate 3](updates/2026-10-24-release-ui-features.md#gate-3部署合并后的-mainround-3)。个人配置、地址、账号、备份位置和原始日志仅在仓库外本机HANDOFF。
- 文档交付分支 `docs/release-b075489-deploy`，base `origin/main@b075489`。本PR仅记录结果，不再次发布应用；父任务检查文档PR的实际CI后决定合入，子任务不merge、不监控。

## 验收边界与下一步

本次发布范围已交付，无待实现项。剩余动作仅由父任务审核/合入本次文档PR；功能想法需用户新决定，不自动开始其他路线图任务。真机iOS/Android键盘、百万窗口压缩、PPT本轮真实模型场景、长期容量和USB物理链路稳定性不在本轮验收结论内。

Gate 1实际范围为临时16K/20%压缩、浏览器手机视口与Excel；完整本地双库/E2E数字见发布记录，不能称为本次部署重新跑过的测试。部署后测试会话/定时任务已清理，专用账号已禁用，临时浏览器/观察器退出；部署栈运行、卷按既有流程保留挂载。旧镜像与已验证备份保留供回滚。

历史实现与相邻发布：[UI集成](updates/2026-10-24-ui-polish-fork-retry.md)、[统一测试](updates/2026-10-24-integration-tests.md)、[技能/Memory](updates/2026-10-24-memory-skills.md)、[对话能力](updates/2026-10-24-chat-capabilities.md)。这些历史计划不代表新增授权。

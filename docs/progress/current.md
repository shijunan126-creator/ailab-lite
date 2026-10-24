# 当前工作

## PPT 模板真实首屏缩略图跟进（2026-10-24，本分支实现中）

- 父任务报告已将初版后端合入 `feat/ppt-templates-portal@1c53b46` 并完成公司/个人/one-off、冻结与turn chip的Portal验收；该信息来自父任务，不代表合入main或部署。本分支仍为 `feat/ppt-templates-backend`，本轮起点 `833f64b`，不改Portal、不push/PR/merge/deploy。
- 本轮改为共享LO/Poppler首屏JPEG，渲染失败/超时/OOM回退草图，保留ready；原有默认内存/wall预算不提高。Worker镜像补依赖并监测整进程组RSS，profile/PDF随临时目录清理。
- 目前专项51通过/1跳过；全量SQLite运行中。未改DB/队列，因此不按父任务条件重跑PG+Redis全套；初版双库历史结果见[原交付](updates/2026-10-24-ppt-templates-backend.md)，不能冒充本轮结果。
- 下一步：完成Ruff/SQLite及真实渲染成本测量，提交本分支后交父任务再集成。设计见[契约](../reviews/2026-10-24-ppt-templates-design.md)，本轮结果见[跟进记录](updates/2026-10-24-ppt-template-thumbnail-rendering.md)。

## 当前：工作区功能（2026-10-24，已合入 main，未部署）

用户授权七项工作区功能实现、测试、提PR后合并。**未授权部署本轮功能**；线上仍为 `b075489`。

- 交付分支 `test/workspace-features-integration`，推送目标 `feat/workspace-features`，base `main@74e66c0`。起点 `f106d28`，父任务Portal修复 `57ddabf`/`e452ed6`/`9f62667`，后端修复 `e34e67c`、新增E2E `d855d01`；[契约](../reviews/2026-10-24-workspace-features-design.md)。
- 已完成：A/B/C及Portal集成；0025←0024、0026←0025、0027←0026；分享通知接入；确认插话详情、排序分享锁、同对象有效分享复用；项目队列上下文/通知、文件移动/分享交互回归。
- 当前验收：SQLite1094通过/117跳过、PG+Redis1178通过/33跳过、双库迁移升降再升、Portal32通过/build、Ruff通过、完整五服务E2E87通过/0失败/0跳过；最新视觉修复后22项专项通过（含全部9项新增用例），清理结果见[集成记录](updates/2026-10-24-workspace-features-integration.md)。
- 有限真实模型：项目说明、计费摘要、重建本地技能层镜像的Docker品牌PPT、真实运行中插话均通过；证据和成本、历史失败与未验边界详见集成记录。没有把Mock/短样本当长期容量。
- [PR #44](https://github.com/shijunan126-creator/ailab-lite/pull/44) 已创建并登记；远端分支 `feat/workspace-features`，初始PR head `d488a30`，本次后续仅补PR链接。
- **已合入 main**：[PR #44](https://github.com/shijunan126-creator/ailab-lite/pull/44) head `d8690a6`（在 `e207580` 上仅加品牌资产名称手机不折行的 CSS）12 项检查全部通过，squash 提交 `2e64da51747fdd3012cb972d1b6d1ca513c80657`。主 Agent 复核了 28 张截图（14 个界面 × 桌面/手机）。
- **未部署**：部署需用户另行授权；届时须先备份、迁移 0025→0027，并重建发布 `sbx-office` 镜像（品牌 PPT 依赖新生成器）。
- 未验/未做：旧品牌资产自动回收、SVG logo、生产 secure profile 下 ClamAV/gVisor/OpenSandbox 联合重验、长期容量、真机键盘。品牌模板目前只套母版/配色/字体/logo，不沿用模板自带版式；用户提出的“参考模板版式、用户自带模板”已与用户讨论，待其决定是否作为下一轮。

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

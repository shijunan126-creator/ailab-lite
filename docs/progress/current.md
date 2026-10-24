# 当前工作

## 模板缩略图生命周期修复（2026-10-24，已本地验证，待PR合并）

用户授权修复、提PR并在检查通过后合并；未授权再次部署。分支 `fix/template-preview-refresh`，base `origin/main@6b6825d`，起点 `23c720b`；包含此前已部署但未合main的PPT模板集成。API新增独立 `preview_status`，公司管理/个人菜单跟踪pending预览至终态；菜单关闭时仅保留所选pending模板的可用性检查。请求可取消、有15秒超时、轮询五分钟截止并退避至5秒。Ruff、SQLite1123通过/118跳过、Portal33通过/build、模板专项五服务浏览器5通过。等待PR检查后合并；未重部署。验证记录见[本轮记录](updates/2026-10-24-template-preview-refresh.md)。三份`.DS_Store`保留。

## PPT 模板集成测试与干净部署（2026-10-24，已部署；Portal自动切图待修复）

- 当前分支 `feat/ppt-templates-portal`，测试提交`e905790`及后续文档提交；部署固定 `b2c8394047279c634802a8ef9591d8bf275fac56`，本地main merge-base`6b6825d`。未fetch/推送/PR/merge，未修改Portal，三份`.DS_Store`原样保留。下文旧后端分支/未授权部署状态为历史，本轮用户新授权允许保全配置后清空旧测试部署。
- 当前验证：SQLite1123通过/118跳过，PG+Redis1208通过/33跳过，完整五服务E2E88通过，Portal33项/build、Ruff与三套Kustomize静态渲染通过。临时服务已结束；测试原生控件适配没有放宽业务断言。
- 当前部署：空库0028/完整性ok/FK0；三类镜像冷构建并核对源码；九服务健康检查/HTTP探针通过，系统保持运行。真实公司/个人模板首屏JPEG、有模板/无模板各2页PPT、原生母版版式/无示例页、queue/steer/share/inbox、PPTX/DOCX浏览器预览通过。测试数据清空后重新空库启动和管理员浏览器登录通过，未合main。
- **Portal缺陷未修复**：ready后停止轮询，真实preview URL晚到，上传模板仍显示草图；公司页面刷新/个人菜单重开后才真实。禁止编辑Portal，已记录两种路径的精确复现、API/DOM证据。不能把整个模板UI外部验收记为完成。
- 下一步交Portal负责人修复缩略图生命周期跟踪、补回归并复验；父任务之后决定是否发布Git分支。个人配置/存储恢复诊断仅本机HANDOFF；外部存储稳定性仍为风险。资源与全部当前/历史边界见[本轮最终记录](updates/2026-10-24-ppt-templates-clean-acceptance.md)。

## PPT 模板缩略图改用 sandbox 预览（2026-10-24，已实现并本地验证，待父任务集成）

- 分支 `feat/ppt-templates-backend`，本轮base `0d12f75`，实现/测试提交 `ad04b19`；初版架构base为main@6b6825d。未改Portal、未push/PR/merge/deploy。目标分支原已被其他工作树检出，本轮按明确要求在当前工作树继续；接续前先核对共享分支与工作树文件基线。
- 已撤销平台镜像Office/Poppler/字体/procps、Worker专用Office renderer及其隔离豁免/进程树RSS逻辑。Worker仅保留受限草图；API在ready同事务登记一次原preview作业，sandbox从首张真实页生成480px JPEG缓存，完成时改变thumbnail_url/ETag。预览失败不改变模板ready或可用性。
- Ruff通过（329文件）；专项 **84通过/1跳过**，完整SQLite **1123通过/118跳过**，完整PG+Redis **1208通过/33跳过**，均无失败。临时PG/Redis25479/26479已停止且无监听，16379未动。
- 成本：Worker/平台镜像新增Office渲染成本为零；每模板一次sandbox preview，与资料库预览同预算，加一次小图缩放/存储。旧245MiB测量属于已否决方案，不能作为当前验收。
- 下一步：父任务审核/集成本分支，在真实OpenSandbox/cgroup与目标Linux上验收。未跑Portal/build/E2E（无Portal依赖且不修改其目录）、未构建镜像/部署/真实模型或长期容量；旧ready模板不批量回填。精确命令、当前与历史证据、清理和边界见[交付记录](updates/2026-10-24-ppt-template-thumbnail-rendering.md)，接口见[设计](../reviews/2026-10-24-ppt-templates-design.md)。

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

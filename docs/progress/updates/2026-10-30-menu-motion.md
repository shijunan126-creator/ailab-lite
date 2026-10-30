# 侧栏菜单定位与动效（2026-10-30）

基线：`fix/sidebar-menu-motion`，`origin/main@e586ff6`（PR #108 squash 合并）。Claude 完成并审核 UI 与回归测试；Codex 维护记录、提交与 PR/CI/合并，最终报告由 Claude 审阅。用户本轮明确授权此流程，并明确授权本轮增量部署（仅 Portal）、push 与 CI 全绿后 squash 合并。

## 根因与修复

- 侧栏 ⋯ 原用 `transform: translateY(-50%)` 居中，全局按钮 active 规则清除 transform，按下时下落 13px，transition 又把跳动动画化。改用 inset 与 `margin: auto 0`，删除 shell.css 旧定位。
- 全局 `pop` 从左下角向上移动 4px，与向下、右对齐菜单的锚点不符。关键帧由 library.css 移到 app.css，只淡入并从 .97 缩放，无位移。
- 新增纯定位函数 `lib/menu-place.ts::placeMenu()`，会话、项目、文件和分支菜单统一使用：优先方向有空间就使用，向上打开时用 bottom 锚定真实高度，transform-origin 指向触发按钮一侧的角。
- `.pop-menu` 的 `min-width: 200px` 覆盖会话菜单内联 176px，造成右边缘偏离 24px。固定菜单覆盖为 `min-width: 0`，会话菜单改为 200px。
- 会话菜单分为操作、记忆开关、删除三组；记忆项改用 IconBook/IconFilePen，文字勾改为右对齐强调色 IconCheck，保留 menuitemcheckbox 与 aria-checked。
- 收起侧栏只隐藏元素未释放 Grid 列，导致浮动按钮组离左边缘 264px、页面无法利用空位；app.css 在 `.shell.collapsed` 设置 `--side-w: 0px`，并接管收起时页面顶部 64px 留白，删除 shell.css 旧规则。
- 手机沿用桌面字号，且小于 16px 的文本字段触发 iOS 聚焦缩放：tokens.css 的 760px 媒体块统一 md/lg/xl 为 15/17/21px、阅读行高 1.7；控件仍 14px。app.css 统一强制手机文本字段至少 16px（项目名称标题除外），删除 chat.css 弱规则。
- 记忆页两个原生 select 改为共享 Select，搜索、范围、历史统一 38px 高；旧 chat.css/pages.css 规则删除，新规则归入 app.css。
- 无新增后台任务、依赖、网络请求或持久存储；只有 CSS 与打开菜单时的常数次纯定位计算，无额外常驻运行成本。

## 检查与边界

代码基线为 `e586ff6` 上本次 UI 与测试改动，未改 Claude 的 UI 代码。

- Claude 已完成：Portal 19 文件 / 96 项、`tsc --noEmit` 通过；临时 Redis 16379 下运行 `AILAB_E2E=1 pytest e2e/test_memory_skills.py e2e/test_integration_features.py -k "conversation_menu or move or pin"`，4 项通过，Redis 已停止；真实浏览器截图已目视检查。
- 新增 `test_conversation_menu_stays_put_and_hangs_from_its_button`：按压不移动、菜单右边缘贴齐按钮、下方间距 0–6px、原点右上角。旧 CSS 下按压与贴边断言分别暴露 13px 跳动和 24px 偏移。
- Codex 本轮提交前：`cd platform && .venv/bin/ruff check . && .venv/bin/ruff format --check .` 通过（464 文件）；`cd platform/portal && npm test` 通过（19 文件 / 96 项）。完整检查由 PR CI 执行，结果待补。
- Claude 扩展检查：Portal 96 项与 tsc、E2E Ruff 通过；临时 Redis 16379 下 `AILAB_E2E=1 pytest e2e/test_portal.py e2e/test_memory_skills.py -k "collapsed_sidebar or conversation_menu or memory"` 共 10 项通过（Redis 已停止）。home / conversation / files / skills / memory / schedules / account / project 在浅深色、1280/390px 和收起侧栏下已目视检查，临时文件已删除。
- 新增 `test_collapsed_sidebar_gives_back_its_column_and_phone_fields_never_zoom`：收起时主列 x=0、按钮组 x≤12px，390px 文件页全部文本字段 16px；记忆 E2E 同步驱动 Select 的 button → listbox → option。
- Codex 扩展改动提交前复核：Ruff check/format（464 文件）与 Portal 19 文件 / 96 项通过。未重复 Claude 浏览器/定向 E2E；完整套件和 build 由 PR CI 执行。
- 未测：真实 iOS/Android 设备（仅 Chromium 390px 视口）、新手机字号下 GenUI 组件截图。
- 部署 `ui-fix-1865bd458cbb`（源 `be68280` 固定产品树）：仅重建 Portal，npm 依赖层缓存复用，其他镜像沿用。sync/build-portal/up/health/portal-http/migration 全部 exit 0；12 服务 running，配置健康检查均 healthy，Portal HTTP 200，Alembic head/database 均 0039。无备份、部署后浏览器/模型或其他测试。个人状态与命令日志仅留本机部署目录。P6、目录扩展与多块付费评测不在本轮范围。

## PR 跟进

[PR #109](https://github.com/shijunan126-creator/ailab-lite/pull/109)：既有菜单修复 `b6b42b6`；扩展修复 `dbedb90`、记录 `be68280`，部署记录提交后等待最终 head CI，全绿即 squash 合并。

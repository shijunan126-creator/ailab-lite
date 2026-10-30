# 侧栏菜单定位与动效（2026-10-30）

基线：`fix/sidebar-menu-motion`，`origin/main@e586ff6`（PR #108 squash 合并）。Claude 完成并审核 UI 与回归测试；Codex 维护记录、提交与 PR/CI/合并，最终报告由 Claude 审阅。用户本轮明确授权此流程，未要求部署。

## 根因与修复

- 侧栏 ⋯ 原用 `transform: translateY(-50%)` 居中，全局按钮 active 规则清除 transform，按下时下落 13px，transition 又把跳动动画化。改用 inset 与 `margin: auto 0`，删除 shell.css 旧定位。
- 全局 `pop` 从左下角向上移动 4px，与向下、右对齐菜单的锚点不符。关键帧由 library.css 移到 app.css，只淡入并从 .97 缩放，无位移。
- 新增纯定位函数 `lib/menu-place.ts::placeMenu()`，会话、项目、文件和分支菜单统一使用：优先方向有空间就使用，向上打开时用 bottom 锚定真实高度，transform-origin 指向触发按钮一侧的角。
- `.pop-menu` 的 `min-width: 200px` 覆盖会话菜单内联 176px，造成右边缘偏离 24px。固定菜单覆盖为 `min-width: 0`，会话菜单改为 200px。
- 会话菜单分为操作、记忆开关、删除三组；记忆项改用 IconBook/IconFilePen，文字勾改为右对齐强调色 IconCheck，保留 menuitemcheckbox 与 aria-checked。
- 无新增后台任务、依赖、网络请求或持久存储；只有 CSS 与打开菜单时的常数次纯定位计算，无额外常驻运行成本。

## 检查与边界

代码基线为 `e586ff6` 上本次 UI 与测试改动，未改 Claude 的 UI 代码。

- Claude 已完成：Portal 19 文件 / 96 项、`tsc --noEmit` 通过；临时 Redis 16379 下运行 `AILAB_E2E=1 pytest e2e/test_memory_skills.py e2e/test_integration_features.py -k "conversation_menu or move or pin"`，4 项通过，Redis 已停止；真实浏览器截图已目视检查。
- 新增 `test_conversation_menu_stays_put_and_hangs_from_its_button`：按压不移动、菜单右边缘贴齐按钮、下方间距 0–6px、原点右上角。旧 CSS 下按压与贴边断言分别暴露 13px 跳动和 24px 偏移。
- Codex 本轮提交前：`cd platform && .venv/bin/ruff check . && .venv/bin/ruff format --check .` 通过（464 文件）；`cd platform/portal && npm test` 通过（19 文件 / 96 项）。完整检查由 PR CI 执行，结果待补。
- 未测：深色模式截图、移动端触摸；文件/项目/分支菜单未逐一目视，仅由已有 E2E 覆盖。Codex 未重复 Claude 的浏览器与定向 E2E；本地未重复完整 Python/Portal build，由 CI 执行仓库范围选择的套件。
- 未部署，线上仍为旧菜单，部署需用户另行要求。P6、目录扩展与多块付费评测不在本轮范围。

## PR 跟进

[PR #109](https://github.com/shijunan126-creator/ailab-lite/pull/109)：UI `b6b42b6`，记录 `593abdb`；补链接提交后等待 CI，全绿即 squash 合并，不部署。

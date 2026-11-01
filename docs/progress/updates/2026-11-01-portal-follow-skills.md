# Portal：回复滚动锚定、页面宽度、技能广场、基础技能不进菜单（2026-11-01）

分支 `fix/composer-scroll-anchor`，base `origin/main@21f3300`。用户本轮要求：输入框占位符靠左；发送后视图随回复滚到“回复开头到顶”即停，读者滑到底部后再贴底跟随；管理页加宽、资料库更宽；恢复“技能广场”；四个基础技能不出现在 `/` 与 + 菜单。后端两项（view_workspace_file 失败、联网搜索为空）与广场目录内容由 Codex 另行处理，见 current.md。

## 结论

- 占位符靠右：#135 把 `.rich-input.is-empty::before` 写成 `right: 4px`，#137 已改回 `inset: 0; padding: inherit`；本机 23000 现已靠左（无头浏览器实测），本轮无需改动。
- 滚动：`lib/use-follow.ts` 增加 `stopAt`（跟随的上限，即回复开头到顶时的 scrollTop）与 `heading`。跟随只向下；读者到达底部且已越过停点后清除停点、恢复贴底；短回复未到停点时上滑再回底不丢停点。`chat.tsx` 在发送、新对话首条、排队消息开始、恢复队列、重新生成/编辑时设停点；继续（resume）清除停点跟随末尾。“回到底部”按钮在内容变化时也按跟随目标重算，停住后自动出现，滑行中不闪。
- 宽度：`.page.mpage` 760→840；资料库 `.page.mpage.page-library` 1120。
- 技能广场：技能页分“我的技能 / 技能广场”（`?tab=market` 可直达），广场按类别展示 `/v1/market/skills` 卡片，添加后走个人技能扫描。2026-10 da76e3b 把办公技能并入平台内置后目录为空、d029166 移除了入口；目录内容由 Codex 补充。
- 菜单：`offeredSkills` 排除 kind=base 的四个基础技能；PPT 模板仍可经可选的“PPT 设计”使用。

## 检查（代码基线 7f0d099）

- `npx vitest run`：29 文件 135 通过（新增 stopAt 三例、offered-skills 一例）；`tsc --noEmit`、`next build` 通过。
- 真实浏览器（构建版 Portal 接本机 API，DeepSeek Flash 实时回复，1000×650）：新对话首条与同对话第二条都停在回复开头距顶 27px，停住后“回到底部”出现；滚轮到底后贴底；测试对话已删除。1440 宽：资料库 1120、技能/记忆/定时任务 840；广场卡片桌面三列、手机单列（目录用 mock 数据）；`/` 与 + 菜单只剩“PPT 设计”。
- 未测：Safari/Firefox 滚动；广场真实目录（等 Codex 合并）。

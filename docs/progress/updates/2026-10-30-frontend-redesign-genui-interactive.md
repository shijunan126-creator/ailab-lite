# 前端重设计与可交互 GenUI（2026-10-30）

基线：`feat/frontend-genui`，HEAD/base `origin/main@97793e6`，叠加此前未提交的 GenUI 集成与 Codex 本轮两组后端改动（[队列幂等](2026-10-30-queue-send-idempotency.md)、[固定模式](2026-10-30-conversation-mode-fixed.md)，已审阅）。未提交、未 push、未部署。

## 用户问题与根因

- **GenUI 不能鼠标交互**：不是 OpenUI 能力限制，是没实现。提示词 `bindings:false, builtinFunctions:false`，目录只有 Button/Tabs，图表无指针处理。现开启绑定与内置函数，新增 Select/Slider/Toggle/Input（`markReactive` + `useStateField`），表达式就地重算；图表指针/方向键读数、图例显隐，表格排序。
- **文字超出框**：flex/grid 子项缺 `min-width:0`/换行、SVG 随列宽缩放、网格按视口不按容器、超长数字。逐类修根因（见 HANDOFF 2026-10-30），不用 overflow:hidden。
- **字号不和谐**：Tailwind `text-[var(--text-*)]` 被当颜色不设字号，叠加 legacy 19 种 px 字号。统一为 7 级 rem token，legacy 205 处映射。
- **发送后仍显示对话/工作、技能堆叠**：模式只在新会话（首页标题下、项目页输入框上）选择，开始后固定，改模式用分支；技能收进 + 菜单（前 3 个 + “更多技能”分页搜索）。
- **视觉**：强调色改墨青 `#0F766E`/深色 `#3FD5C0`，石墨中性色；重写 tokens/app/genui 样式与 Composer，动效仅用于状态变化。决定记在 [HLD §4.6.3](../../architecture/design/2026-10-28-frontend-genui-hld.md) 与 [ADR 0028](../../architecture/adr/0028-generative-ui-openui.md)。

## 验证（本轮，同一代码基线）

- Ruff check/format：通过（446 文件）。
- Python 全套（SQLite，`-x`）：1695 项收集，退出码 0。未跑 PostgreSQL/Redis 后端变体。
- Portal：`tsc --noEmit` 通过；Vitest 15 文件 72 项通过（新增 4 项：绑定与求值后发送、只读控件、表格排序、图例显隐）；`npm run build` 通过。
- E2E（生产构建，独立 Redis）：全套 138 项，首轮 134 通过、4 失败，均为测试旧定位器/新测试未 reload（`.genui-chart`→`.gu-chart`、对话内已无模式单选、手机宽度需 reload）；修后受影响文件 10 项全部通过，未再重跑全套。新增：控件就地重算（980→5,049）、图表悬停读数、图例、按钮带变量发送；1440/390/320 长文本不溢出。
- 截图验收（dev 栈，1440/390、浅/深）：首页、+ 菜单、三类 GenUI 会话、五个管理页；GenUI 内无溢出、页面无横向滚动。发现并修复数字列表头断行（数值列 `nowrap`）。

## 成本

- GenUI 懒加载 chunk 83,131 B gzip（81.2 KiB，原 78.5 KiB），仍不在对话页首包；**超 HLD 60 KB 目标未解决**。
- 图表每个实例一个 ResizeObserver，仅在尺寸变化时重绘；控件状态只在页面内存。无新依赖、无后台任务。

## 未做 / 残余风险

- 控件状态刷新后回到初值（HLD V2 ui-state 未做）；分享/打印为只读。
- 严格性能闸门（输入到下一帧、长任务、4×CPU）、P5 全站组件/preflight、P4-H HtmlArtifact、P6 真实模型评测（新控件语法的模型合法率未测）。
- E2E 全套修后未完整重跑一次；PostgreSQL 全套本轮未跑。

下一步：用户审阅本地改动与截图；如需提交/PR 由用户明确授权。

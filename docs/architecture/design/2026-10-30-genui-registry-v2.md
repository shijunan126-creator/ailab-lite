# GenUI 组件库 v2：AILab 自有组件目录与按需加载（2026-10-30）

范围：替换 v1 的 21 组件目录，并把 GenUI 说明从“系统提示里整段注入”改为按需读取。决定记在 [ADR 0029](../adr/0029-genui-registry-v2.md)，v1 设计见 [HLD](2026-10-28-frontend-genui-hld.md) §6。

## 1. 要解决的问题（实测）

| 问题 | 事实 | 根因 |
|---|---|---|
| 上下文一开始就被塞满 | Chat 系统提示 5,613 token，其中 GenUI 说明 4,776 token（85%）；工具（469 token）和技能早已经 `search_capabilities` 按需发现 | GenUI 是唯一没有渐进披露的能力，每轮都付费，也诱导模型为普通问题画界面 |
| 表达力不足 | 21 个组件，只有折线/柱状/饼三种图；时间轴、偏差条、流程、对比矩阵都只能写 HtmlArtifact | 目录只覆盖基础原语，缺少常见 AI 回答的高语义组件 |
| P6 严格一致性 47.8% | 失败几乎都是“结论里的名次、最大值、变化、倍数是模型自己算的或硬写的” | 让模型做算术和比较，再要求它“用表达式”——这是在和模型习惯对抗。正确做法是**计算交给组件**，模型只给原始数据 |
| 签名混淆 | `LineChart(..., false, 2)` 把 BarChart 的参数套到 LineChart | 可选参数全按位置排，各组件位置不同 |

## 2. 总体架构

```text
系统提示：GenUI 索引（何时用界面 + 按类别列出组件名，约 500 token）
   │ 模型决定要画界面
   ▼
ui_components(names) ──► Agent 读 contracts/genui/catalog-v2.json
   │                       返回：语言指南（本 Run 首次）+ 所选组件的签名、参数、规则、示例
   ▼
模型写 ```openui 围栏（OpenUI Lang）
   ▼
Portal：toProgram → OpenUI 解析器（schema 校验）→ 求值（$变量、@函数）→ React 渲染
                    └─ 未知组件 / 形状错误 / 解析失败 → 逐组件降级，不影响其余回答
```

- **OpenUI 只当语言和运行时**（解析、流式、绑定、内置函数）。组件全部由我们定义：zod schema 在 `catalog/schema.ts`（无 React，Node 可跑），渲染在 `catalog/*.tsx`。
- **单一来源**：`npm run genui:prompt` 从 schema 生成 `contracts/genui/` 下的索引、指南和逐组件文档，Portal 测试逐字节比对，Python 校验 sha256。新增组件只改 schema + 渲染 + 生成，不动 Agent 和解析核心。

## 3. 渐进披露（对齐 Skills 的做法）

| 层 | 内容 | 何时进上下文 | 预算 |
|---|---|---|---|
| 索引 | 什么时候用界面、什么时候不用；按类别列组件名；决定对错的规则（数字和结论由界面给出、块外只解读、不做未要求的统计） | 每轮系统提示（模型支持 genui 时） | ≤ 800 token |
| 指南 | 语法、绑定、内置函数、流式顺序、通用规则 | `ui_components` 在本 Run 第一次调用时 | 约 2,250 token（HtmlArtifact 规则已移入其组件文档） |
| 组件文档 | 签名、opts 键、该组件规则、一行示例 | 调用时只给所点名的组件 | 每个 ≤ 150 token |
| 全量 | 索引 + 指南 + 全部文档 | 仅用于不支持工具调用的模型 | — |

`ui_components` 是 Agent 本地纯函数（不进沙箱、可重放），在启用 GenUI 的模型上两种模式都常驻（与 `load_skill` 一样属于 core）。HtmlArtifact 关闭时它既不在索引里也查不到。代价：要画界面的回答多一次模型往返（前缀缓存命中，约 1–3 秒）；普通回答省掉约 4k token，也不再被组件清单带偏。

## 4. 组件目录（v2）

统一约定（schema 设计原则）：
1. **位置参数只放主要数据**（≤4 个），其余全部放最后一个可选 `opts` 对象：`BarChart("销量", months, [Series("线上", a)], {stacked: true, unit: "万元"})`。各组件同义的键同名：`unit`、`decimals`、`tone`、`better`（`"high"`/`"low"`，数值越大越好或越小越好）。
2. **多实体数据用对象数组**，字段名固定且简单（`{name, value}`、`{date, title, text}`、`{from, to, value}`），与“原数据只写一次”规则一致。
3. **派生量由组件计算**：份额、排名、变化率、合计、累计、转化率、分箱、四分位、最优项高亮都不由模型给。
4. 文字参数只是纯文本；不接受 URL、HTML、CSS、类名。颜色只能选语义 `tone` 或用系列色序，宿主 token 着色，深浅色与打印一致。

| 类别 | 组件 | 计算交给组件的部分 | 分块 |
|---|---|---|---|
| 布局 | Stack、Grid、Card、Tabs/Tab、Details、Divider | — | 核心 |
| 文字 | Heading、Text、Callout、Badge、Quote、List、KeyValue、Code | — | 核心 |
| 指标 | Metric（`previous` 自动算变化率、`trend` 迷你线）、Progress（`max`/`target`）、Gauge、ShareBar、Highlights | 变化率、占比、最大/最小/合计/平均/首尾变化/最大增幅 | 核心 |
| 图表 | LineChart、AreaChart、BarChart（横向/堆叠/百分比/正负自动分色）、ComboChart、PieChart（环形） | 堆叠合计、百分比、份额 | 核心 |
| 高级图表 | ScatterChart（含气泡/分组）、RadarChart、Histogram、BoxPlot、Heatmap、Waterfall、Funnel、Treemap、Sankey、Candlestick、Dumbbell | 分箱、四分位、累计、转化率、布局 | 懒加载 |
| 过程 | Timeline、Steps、Checklist、Gantt | 进度、时间轴比例 | 核心 / Gantt 懒加载 |
| 对比 | Table、Compare（对比矩阵，按 `better` 标出每行最优）、Ranking（自动排序）、ProsCons、EntityCard | 排序、名次、最优项 | 核心 |
| 图示与代码 | Flow（流程/架构，自动分层布局）、Tree（层级/组织/思维导图）、Diff、FileTree、JsonView | 布局、行级差异 | 懒加载 |
| 交互 | Select（单选/多选）、Slider、Toggle、Input、Button（`@ToAssistant`） | — | 核心 |
| 页面 | HtmlArtifact | — | 核心（iframe） |

有意不做：
- 图片、视频、地图：需要外链，违反 CSP 与隐私边界。
- 来源卡片：网页引用已经由正文 Markdown 链接和来源记录承担，再做一份会重复。
- 天气、航班、股票等领域卡：没有对应的数据工具，EntityCard + Metric 已能覆盖。
- 跟进建议芯片：P6 评测表明模型会附加无需求的下一步，Button 已足够。
- Sparkline 单独组件：并入 Metric 的 `trend`。

优先级：P0 = 核心（首批全部完成）；P1 = 高级图表、图示与代码（同批完成，懒加载）。

## 5. 渲染、流式与降级

- **渲染器**：每个组件是只接收 schema props 的 React 组件；高级图表和图示放在独立 chunk，首次用到才加载（`React.lazy` + 组件内 Suspense 骨架），不增加普通回答的包体。
- **流式**：解析器每个 chunk 重解析，保留上一棵能显示的树；组件对还没到齐的数据显示骨架而不是报错（`useIsStreaming`）。形状校验只在块闭合后报“数据格式有误”。
- **降级层级**：
  - 单个 prop 不合法时，解析器丢弃该 prop，组件按默认值显示；
  - 数据形状错误时，该组件显示“数据格式有误”；
  - 未知组件在解析时丢弃（`unknown-component`），其余节点照常渲染，并上报遥测；
  - 渲染异常由组件级错误边界接住，只替换该组件；
  - 整块无法显示时显示“无法显示此内容”和源码。
- **复制、搜索、打印**：每个组件都有 Markdown 降级（`nodeToMarkdown`），派生量使用渲染时同一个计算函数（`catalog/compute.ts`），复制内容与页面显示一致。
- **主题与响应式**：只用 `styles/tokens.css` 的语义色与 `--series-1…6`；图表宽度跟随容器（container query），手机 320px 可读；深色模式与打印共用 token。
- **无障碍**：图表 `role="img"` 加 aria 描述，并带隐藏数据表；交互支持键盘（方向键读数、Tab/Enter）；`prefers-reduced-motion` 时不做动画。

## 6. 让模型选对、少滥用、控成本

- 索引里先写“不该用界面的情况”（简单事实、解释、翻译、改写、单个数值），再写按意图选组件的对照（趋势→Line/Area；构成→Pie/ShareBar/Treemap；排名→Ranking；多方案对比→Compare；前后/预期与实际→Dumbbell；增减拆解→Waterfall；转化→Funnel；过程→Timeline/Steps/Flow；层级→Tree）。
- 一个回答默认一个主图或主表，加上 Highlights 写结论；块外文字只写解读和建议，不复述数字。
- 成本：索引常驻约 500 token；画界面的回答多一次往返，只付所选组件的文档。全量说明只给不支持工具的模型。

## 7. 扩展方式

新增组件只需：在 schema 加定义（描述、规则、示例），在渲染表加实现（懒加载或核心），在 `nodeToMarkdown` 加降级，然后重新生成契约。Agent、解析器和协议都不用改。改签名、删组件或重排参数要升目录版本。

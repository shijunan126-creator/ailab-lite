# ADR 0028：回答内 Generative UI（OpenUI 结构化路径）

状态：本地实施（分支 `feat/frontend-genui`），设计见 [HLD](../design/2026-10-28-frontend-genui-hld.md) §6。用户随后要求默认启用：全局默认true，所有现有模型配置声明genui；仍可用模型capability与 `AILAB_GENUI_ENABLED` 显式关闭。真实模型评测（P6）记录见 [评测报告](../../progress/updates/2026-10-30-genui-real-model-eval.md)。

## 决定

- 模型在回答正文中写 ```` ```openui ```` 围栏（OpenUI Lang），每个围栏是带 `root` 的独立程序。传输复用 `agent.delta`，持久化复用 `Run.summary`，不新增事件、模型调用或表；完成时 `result.genui={format,catalog,blocks}` 记录目录版本。
- 拆分规则（行首围栏、同长以上闭合、普通代码围栏内不识别、流式时半截围栏行暂缓）在 TS（`lib/answer-parts.ts`）与 Python（`domain/answer_parts.py`）两侧实现，共用 `platform/tests/fixtures/genui/` 夹具。
- 目录 v1 只在 TS 定义（`components/genui/catalog/schema.ts`）：Stack、Grid、Card、Heading、Text、Badge、Callout、Metric、Table、Progress、LineChart、BarChart、PieChart（含 Series）、Tabs（含 Tab）、Button；2026-10-30 同版本新增 Select、Slider、Toggle、Input 四个控件（只增组件，符合下条规则）。提示词由 `npm run genui:prompt` 从目录生成到 `contracts/genui/openui-v1.prompt.md` 与 `catalog-v1.json`（含 sha256），Portal 测试逐字节比对、Python 校验 sha256；secure 下校验失败拒绝启动。同版本只能新增可选 prop 或组件。
- 渲染只在 `components/genui/**` 引用 `@openuidev/*`，渲染器与目录按首个 UI 块懒加载；流式时保留最后一棵可解析的树，闭合后无法渲染则显示源码降级，每块有错误边界。限额：每回答 8 块、每块 32k 字（HtmlArtifact 的 document 另限 24k、fallbackText 2k）、表格 200 行、图表 500 点；Python 侧 `genui_max_block_chars` 与之一致。
- 图表用自绘 SVG（宿主 token 着色、打印为矢量、附读屏表格），不引入 Recharts。
- 页内交互（2026-10-30）：提示词开启 `bindings` 与 `builtinFunctions`，控件写 `$变量`，Metric/图表/文本用表达式（`@Round`、三元等）就地重算，不产生消息；图表有指针/方向键读数与图例显隐，表格可排序；Metric、Table 和三种图表有可选 `decimals`（0–4），复制时同样生效。Metric 的 `delta` 定义为相对上一期的变化率（%），显示为一位小数加 `%`。表达式求值后形状不对的表格或图表数据（不是二维数组、行宽超过列数、系列长于类别）显示“数据格式有误，无法显示”，不显示空表或错位图。
- 控件状态（2026-10-30）：绑定变量、Tabs 当前页、表格排序、图例显隐都存进 OpenUI store。键名为 `$变量` 或 `组件:语句名`，按语句名而不是渲染位置，模型改写其它语句时旧状态仍对得上。每次变化后 600ms 合并保存到 `PUT /v1/runs/{id}/ui-state`，离开页面时用 keepalive 发出未保存的变化。
  - 存储：Run 的 `ui_state` JSON 列（迁移 0039）。只有 Run 所有者能写；值只接受标量和 `{value, componentType}`，最多 64 项、每项键名不超过 120 字、整体不超过 16KB，超出返回 422；写入按用户限速。
  - 读取：会话详情和分享视图返回 `ui_state`，刷新、分享和打印都从保存的状态开始，分享与打印只读，不写回。
  - 状态只是界面偏好，不进入模型上下文，也不改写回答。
- 自由页面 HtmlArtifact（HLD §6.16，P4-H）：
  - 写法：`HtmlArtifact(title, document, fallbackText)`，提示词只把它留给组件画不出的需求（模拟、小游戏、动画讲解）。
  - 运行：页面只在 `app/frame/[kind]` 里运行，该 iframe 为 `sandbox="allow-scripts"`，是不透明源，没有 cookie 和存储，不能访问父页。
  - 流式：书写中显示 `genui-preview`，CSP 只放行壳脚本的 hash，页面自己的脚本和内联事件都不执行；围栏闭合后整页在 `genui-run` 运行，CSP 为 `default-src 'none'` 加 `script-src 'unsafe-inline'`，无网络。
  - 通信：文档通过 postMessage 投递；页面能发回宿主的只有高度。宿主注入主题变量（`--bg --fg --accent --series-1…` 等），主题切换时同步。
  - 导航：页面试图跳到别的地址时，父页 `frame-src` 会拦截，宿主随即停止该页面，提供“重新运行”。
  - 资源：离开视口约一屏后卸载 frame，回来时重建，历史里的页面不会一直占用计时器和动画。
  - 降级：复制与打印输出 `fallbackText`。
  - 开关：服务端 `AILAB_GENUI_HTML_ENABLED` 默认开，经 `/v1/auth/me` 的 `features.genui_html` 告知 Portal；关闭时提示词不含此组件，已存在的页面只显示 fallbackText。
- 发往模型的交互只开放 `@ToAssistant`（可拼接 `$变量`）：经与输入框相同的发送路径（运行中进入排队）；分享/打印为只读，按钮禁用不隐藏。`@OpenUrl`、Query/Mutation 未开放。
- 复制输出各组件的 Markdown 降级，取自求值后的树和读者当前的控件状态，与页面显示一致；打印等待 GenUI 块加载完成；历史给模型时上一轮保留围栏，更早轮次换成 `[界面：…]` 占位，截断不切开围栏。
- 客户端只上报低基数计数（`POST /v1/genui/telemetry`，无 id、不入库）。OpenUI 的 Node 端安装/提示词遥测在脚本中以 `OPENUI_TELEMETRY_DISABLED=1` 关闭，浏览器端无遥测；渲染器 `publishObservability=false`。

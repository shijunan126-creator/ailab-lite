# OpenIntelligentUI 与 thesysdev/openui 深度比较

日期：2026-10-28。比较对象明确为 **CopilotKit/OpenIntelligentUI** 与 **thesysdev/openui**；后者正是 HLD 的 OpenUI Lang，不是其它同名项目。

## 结论与此前判断的修正

1. 原 HLD 只开放十几种自定义组件，是我们自己的产品裁剪，不能用来描述 OpenUI 项目的能力上限。固定源码的完整 openuiLibrary 有 **82 个注册条目**，包含 Col、Series、Slice、TabItem 等辅助结构，不等于82种独立视觉控件。
2. OpenUI 不仅能组合静态卡片：有响应式变量、算术/条件表达式、聚合/过滤/排序、action 链、Query/Mutation、表单状态保存/恢复接口。
3. **OpenUI 官方仓库已有 HtmlArtifact 示例**，由模型生成完整 HTML/CSS/JS，再由注册的可信组件承载到 iframe。不能说“用 OpenUI 就不能自由生成 HTML”。
4. 该 HtmlArtifact 是最小示例：等待生成结束才加载渲染 iframe；CSP、网络限制、HTML 处理和 bridge 验证明确留给生产集成。不能反过来说 OpenUI 已有与 OpenIntelligentUI 同等完整的自由 HTML 流式 runtime。
5. 对用户当前“自由解释、交互工具、图解”的目标，**OpenIntelligentUI 的现成体验更匹配**；对 AILab 已有业务栈的接入，**OpenUI 更易作为可嵌入底座保留**。不能因此宣布一个全局优胜者，更不能从演示推断画面质量/正确率。
6. 当前建议：停止把“OpenUI只能十几种组件”作为替换理由。将 **OpenUI + 扩展目录/HtmlArtifact** 与 **移植 OpenIntelligentUI renderer/工具协议** 做同题运行比较，之后再定型。不建议未经验证整体替换 AILab Agent 和会话系统。

## 证据与验证边界

本轮固定上游树：
- OpenUI：`1492107c8d24805a19651ad57420156f702bbda5`。
- OpenIntelligentUI：`8391a94c1c3939c679e40ddd7bfc08a9ef690e70`。
- AILab：`fix/full-thinking-rendering@ab0e8d94595e11cc3deedf7556ddac9b9ea87492`；已有未提交文档及 .DS_Store 均保留。

通过 GitHub 连接器直接读取工具/renderer/状态/runtime/示例源码，另查官方文档；没有安装依赖、运行上游、调用付费模型、改产品代码或部署。82为注册数组静态计数；1秒为节流常量；版本是源码声明，均不是运行实测。以下“更容易/开销更大/适合”是有依据的架构判断，非性能或生成质量测量。

## 对比矩阵

| 维度 | thesysdev/openui | CopilotKit/OpenIntelligentUI |
|---|---|---|
| 项目形态 | 语言、解析/求值器、React等框架runtime、组件库、可选聊天面/Cloud | Next.js聊天应用 + CopilotKit runtime + Python Deep Agent/LangGraph + AG-UI + Websandbox |
| 模型主要输出 | OpenUI Lang：组件/数据/绑定/声明式动作；扩展组件可承载HTML | 普通文字、A2UI Table、generateSandboxedUi自由CSS/HTML/JS |
| 自由度边界 | 默认须注册组件/函数；HtmlArtifact可突破布局/逻辑边界，但宿主承担执行隔离 | 模型直接编写布局和JS行为，受iframe/CSP/importmap/宿主bridge约束 |
| 标准展示 | 完整目录含表单、编辑表格、散点/雷达等图表、卡片、Modal等 | 内置结构化基础表格，其它图表/解释/计算器多走自由代码 |
| 流式 | 结构化增量解析、前向引用与局部错误；HTML示例等待结束才渲染 | CSS完成后HTML预览渐进morph；HTML完整后销毁预览、创建最终sandbox，注入函数并执行表达式 |
| 交互逻辑 | $变量、表达式/注册函数、@Set/@Reset/@Run、action链、Query/Mutation | 生成JS实现本地逻辑；宿主显式开放sendPrompt/openLink等能力 |
| 状态恢复 | onStateUpdate/initialState及消息context接口；业务持久化由宿主实现 | 当前outputs独立快照，无跨调用DOM patch；控件状态不自动回传给agent |
| 智能选表达 | library.prompt可加additionalRules/examples；不必有额外路由模型 | 当前Jev外部API先为新用户消息做呈现决策，再由回答模型生成；不是只加一段提示词 |
| 模型/云 | 核心renderer与本地prompt无需Cloud；若照搬HtmlArtifact等示例则使用OpenUI Cloud | 当前默认依赖回答模型+Jev；移除Jev需改设计，不能当作现成配置 |
| 商用许可 | 本轮核对SDK/仓库MIT；Cloud另算 | 仓库MIT；外部模型/路由服务费用另算 |
| 可靠性 | SDK不是业务后端；需接入AILab Run/账本/存储 | 应用checkpoint进程内存；不能照搬为AILab持久恢复契约 |
| AILab适配 | 小包runtime可接正文delta；扩HTML需新增执行边界 | 工具参数增量/activity转换、sandbox、路由计费、历史快照需适配 |
| 默认生成效果 | 设计系统一致、布局/交互有边界；不保证数据与组件选择正确 | 视觉与行为上限高、输出变化更大；不保证每次好看或计算正确 |

## 1. OpenUI 实际能力

### 1.1 框架能力与稿子选择是两回事

[完整组件注册表](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/packages/react-ui/src/genui-lib/openuiLibrary.tsx)共82条；[react-ui说明](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/packages/react-ui/README.md)提供完整 openuiLibrary 与聊天子集 openuiChatLibrary。不限于稿子的Stack/Grid/Card与三种图表。已有 EditableTable、ScatterChart、RadarChart、Slider、DatePicker、OptionCards、Carousel、Accordion、Modal 等，且允许 library.extend 和自定义组件。

模型仍不能在默认DSL里随意创造一个未注册标签；新增陌生专业控件通常需注册可信实现。但可注册一个通用绘图/HTML产物组件开放更大表达空间，框架并未禁止。开放后安全属性相应变化，不能沿用“永不执行模型代码”的旧承诺。

### 1.2 并非只能静态展示

[React runtime README](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/packages/react-lang/README.md)及[actions](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/docs/content/docs/openui-lang/actions.mdx)：
- reactive绑定与$变量，可结合条件/算术表达式构建本地计算，不是每滑一次Slider就调用模型。
- @Set、@Reset与Action([...])可变更状态/顺序执行动作。
- @Run执行注册的Mutation或重取Query；toolProvider接受函数映射或MCP client。
- onStateUpdate/initialState支持宿主保存恢复表单/$变量，不能说“OpenUI不支持状态持久化”；准确说法是“提供接口，应用要落库”。

[函数定义](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/docs/content/docs/openui-lang/functions.mdx)包括Count/Sum/Avg/Filter/Sort/Round/Each等；自定义函数由开发者注册纯同步实现，不是模型任意编写函数后直接eval。[求值器](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/packages/lang-core/src/runtime/evaluator.ts)按AST解释表达式。Query/Mutation仍必须通过AILab API鉴权，不能让Portal直连内部服务或把业务权限交给模型。

### 1.3 自由HTML并未被框架排除

[HtmlArtifact README](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/examples/miscellaneous/html-artifact/README.md)明确生成自包含HTML/CSS/JS；[library.tsx](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/examples/miscellaneous/html-artifact/src/library.tsx)注册 Response/Markdown/HtmlArtifact；[组件源码](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/examples/miscellaneous/html-artifact/src/html-artifact.tsx)的schema只有title/document，最终用 `sandbox="allow-scripts"` 和srcDoc承载document。

当前示例：流式过程中显示原始源码和loading，结束后才展示iframe。不是像OpenIntelligentUI那样先逐步绘制自由HTML。它明确要求生产前补CSP/网络/消息校验。示例[模型路由](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/examples/miscellaneous/html-artifact/src/app/api/chat/route.ts)使用Thesys Cloud，核心SDK本地library.prompt/Renderer不必跟随此路由。用现有AILab模型是否能稳定生成同样内容，仍需实测。

### 1.4 流式容错的真实边界

[useOpenUIState](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/packages/react-lang/src/hooks/useOpenUIState.ts)使用createStreamingParser并缓存已完成语句；[Renderer](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/packages/react-lang/src/Renderer.tsx)有组件级错误边界及最后成功children回退。存在这些代码，不等于所有partial输入都保证无闪烁、任意重排都保留状态、或满足HLD的8ms预算，须按版本验证。

当前react-lang源码版本1.0.2；没有采用README宣称的“最多节省67% tokens”推断优于HTML，因为那个对照主要是JSON，不是本轮相同任务的HTML/JS输出。

## 2. OpenIntelligentUI 的真实优势和代价

### 2.1 自由表达是真优势，但不等于无限制

[prompt.py](https://github.com/CopilotKit/OpenIntelligentUI/blob/8391a94c1c3939c679e40ddd7bfc08a9ef690e70/apps/agent/src/prompt.py)直接指导模型编写CSS、HTML、函数与表达式，importmap提供three/gsap/d3/chart.js。适合模型临时创造的物理图解、几何动画、专用计算器等；无需我们为每种画面注册React控件。

但它也有协议要求：参数顺序固定，CSS须先完整，JS分频道且按classic script运行，顶层await无效；依赖须受CSP/importmap允许。不比任意网页拥有更多宿主权限。

### 2.2 流式并非“每token即可交互”

[renderer.tsx](https://github.com/CopilotKit/OpenIntelligentUI/blob/8391a94c1c3939c679e40ddd7bfc08a9ef690e70/apps/app/src/components/generative-ui/open-generative-ui/renderer.tsx)：
- `THROTTLE_MS = 1000`；CSS结束、首HTML、HTML结束、新JS表达式等节点可立即flush，因此不能说所有更新都固定延迟1秒。
- CSS完成前没有可见HTML预览；之后partial HTML经Idiomorph更新preview。
- htmlComplete后销毁preview，创建最终Websandbox。预览到最终不是同一个DOM，不能承诺预览状态自然延续。
- 函数注入、表达式依次执行；早于整个回答完成的交互有可能，但必须等所需HTML/JS/库就绪。
- 这套机制是可借用的实现，不是已证明满足AILab 150ms可见/500轮长会话的性能。大量历史iframe、Three.js/WebGL、定时器、CDN加载可能增加客户端资源，需测离屏卸载、重挂载和状态恢复。

### 2.3 “智能”当前不只是提示词

[visualization_router.py](https://github.com/CopilotKit/OpenIntelligentUI/blob/8391a94c1c3939c679e40ddd7bfc08a9ef690e70/apps/agent/src/visualization_router.py)每个新用户消息前调用Jev，15秒HTTP timeout；[路由契约](https://github.com/CopilotKit/OpenIntelligentUI/blob/8391a94c1c3939c679e40ddd7bfc08a9ef690e70/docs/visualization-routing.md)明确基础表格A2UI、其它图表/地图/计算器走自由HTML，失败会报错而非自动跳过。

所以“所有智能都只是提示词，完整接入不增加调用”对当前项目并不准确。可以重构为主模型自行选工具，但这是我们要实现与评测的变体，不能声称效果等同Jev。额外路由的用量也需纳入AILab账本，失败、超时、取消需定义。

### 2.4 安全与业务契约不能照搬

[frame-content.ts](https://github.com/CopilotKit/OpenIntelligentUI/blob/8391a94c1c3939c679e40ddd7bfc08a9ef690e70/apps/app/src/components/generative-ui/open-generative-ui/frame-content.ts)注入CSP，script允许inline/eval及四个CDN，connect也开放CDN；不等于网络全禁。[sandbox-functions.ts](https://github.com/CopilotKit/OpenIntelligentUI/blob/8391a94c1c3939c679e40ddd7bfc08a9ef690e70/apps/app/src/lib/sandbox/sandbox-functions.ts)验证sendPrompt 1–4000字、openLink HTTPS/可选origin范围。仍需独立检查实际bridge调用授权、用户意图、数据外传和依赖完整性；提示词禁止自动发送不是安全强制。

[架构文档](https://github.com/CopilotKit/OpenIntelligentUI/blob/8391a94c1c3939c679e40ddd7bfc08a9ef690e70/docs/architecture.md)说明checkpoint内存、无跨调用patch及控件状态不自动成为模型上下文。当前依赖[package.json](https://github.com/CopilotKit/OpenIntelligentUI/blob/8391a94c1c3939c679e40ddd7bfc08a9ef690e70/apps/app/package.json)包含多个CopilotKit next预发布版本和Websandbox 1.2.1；这表示需要锁版/升级验证，不等于已认定不稳定。

## 3. 对 AILab 的具体影响

原HLD `agent.delta + Run.summary + 正文openui围栏` 是我们自定的承载协议，不是OpenUI唯一可用协议。换成自由HTML也可设计正文承载，但若照搬OpenIntelligentUI则主要走工具参数/AG-UI activity。

本地 `platform/ailab_platform/llm.py:156` 的stream accumulator只返回text/thinking；tool_calls.arguments在191–196行累计，完整response才转工具调用。Agent loop的on_text接_DeltaWriter，并无现成的工具参数partial→UI事件路径。因此：
- OpenUI结构化路径可复用现有正文delta，并实现正确分块/限额/历史版本。
- 完整移植OpenIntelligentUI工具流需新增partial参数回调、流式UI状态/事件转换与durable最终快照，不只是前端依赖安装。
- 用其renderer但自行设计正文HTML片段可减耦合；但必须自己完成字段拆分、脚本执行时机与重连/恢复，不能再叫“原样完整接入”。

两条路线都不能把模型key放Portal、绕过API授权/额度、让UI直接执行内部工具；分享只读、来源权限、取消续跑、文件访问、历史分页、fork和导出均需与既有业务契约接通。2vCPU/8GiB目标机需测服务端新增路由/runtime的RSS/CPU与模型延迟；客户端另测DOM/iframe/WebGL/后台任务。源码推断不能代替这些测量。

## 4. 到底选哪个

| 用户真正要的结果 | 更适合先用哪条路线 | 原因/边界 |
|---|---|---|
| 风格稳定的数据/业务界面，表单、表格、筛选、注册工具交互 | OpenUI完整目录 + bindings/functions | 复用宿主设计系统和明确权限，现成状态接口；不是自由绘图 |
| 临时创造解释图、模拟器、游戏、复杂视觉工具 | OpenIntelligentUI自由HTML runtime | 已有面向此目标的提示词、预览、JS/bridge链路；承担生成代码质量与隔离成本 |
| 保留AILab架构，同时两类都需要 | OpenUI作编排层，结构化组件 + 隔离HtmlArtifact，或单独自由HTML片段并行 | 两者不互斥；HTML渲染可采用另一个runtime，避免重写整个Agent |
| 直接fork一个新聊天产品 | OpenIntelligentUI整套应用 | 起点完整；不等于完成AILab迁移/兼容 |

**当前选型意见**：依据用户更在意表达自由度，原HLD“只给十几种组件、永不执行生成代码”的设计应改；但尚无证据支持为此淘汰thesysdev/openui。优先比较“保留OpenUI并增设自由HTML通道”和“采用OpenIntelligentUI的自由生成链路”。若只在两者的原生主路径二选一，自由解释/模拟器优先OpenIntelligentUI，业务组件式交互优先OpenUI。

## 5. 尚不能回答的质量/成本问题与验证方案

当前没有同模型、同数据、同prompt任务集的运行结果，不能声称哪个更漂亮、更准确、更快或更便宜。最小同题对照应包含：
1. 销售看板：真实给定数据、筛选、排序和精确数值检查。
2. 交互计算器：空值/非数字/零除/单位、重置、参数回传。
3. 机制解释图：动态变量、SVG/Canvas、手机布局。
4. 3D或地图：依赖失败、降级、交互与来源准确。
5. 多轮修改及刷新恢复：模型是否看到最新UI源码/参数、旧输出是否保留。
6. 多块UI/500轮会话：首可见内容、交互就绪、帧长任务、内存/iframe数量、打印导出。

统一模型/输入条件并报告失败样本、修正次数、输入输出tokens、首可见UI与交互就绪延迟、服务端RSS/CPU和客户端资源。HTML产物路径应单独对齐隔离策略后比较，不能让一方默认CDN全开而另一方离线后直接比较效果。此为后续实验设计，本轮未实施、不自动进入付费评测。

## 连续性

本报告替代前序调研中把“稿子的组件数”当成“OpenUI框架上限”的推断。HLD仍为待重评设计，不授权P0、框架迁移、安装或部署。下一步由用户审阅此比较，决定两条候选的实际运行验证范围，再修订主HLD。


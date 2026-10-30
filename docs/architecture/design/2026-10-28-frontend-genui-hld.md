# AILab Portal 前端重构与 Generative UI — HLD（v4，视觉重构与功能等价，设计待审阅）

> 状态：用户于2026-10-29已授权实施；已有实现及尚未完成/验收的阶段见[集成收尾](../../progress/updates/2026-10-29-frontend-genui-integration.md)。本文是前端重构与 GenUI 的设计主文档，须与 AGENTS、当前交接及已确认决定一起使用；按第 9 节分阶段执行，每阶段有独立验收与闸门，不得跨阶段自动继续。
> 本轮范围（2026-10-29）：用户要求**前端视觉也重构，已有功能保持等价**，采用 [Impeccable](https://github.com/pbakaus/impeccable) 的设计规范。本文将视觉重构独立成可验收工作流；§4.6 是建议方向，尚非已批准视觉稿。仅修订文档，不启动产品实现。
> 已确认决定（2026-10-29）：**保留 thesysdev/openui 作为 GenUI 基础**，主模型结合提示词自主选择表达，不引入 Jev 或独立选择模型。本次仅修订设计，不授权安装、实现、付费验证或部署。
> 能力边界：下文基础目录是 AILab 的示例起点，不是 OpenUI 上限。结构化组件支持流式生成；自由 HTML/CSS/JS 可通过注册 HtmlArtifact 承载，但其官方示例等流结束才渲染，渐进预览和生产隔离仍需单独验证（§6.16）。2026-10-28 固定源码依据见[深度比较](../../reviews/2026-10-28-openintelligentui-vs-openui.md)，不冒充未来版本或运行验收。
> 基线：`fix/full-thinking-rendering@ab0e8d9`。文中 `file:line` 均对照该基线。
> 输入：用户初版《AILab Generative UI HLD》（2026-10-28 对话）。第 3 节逐条说明保留、修改与删除的理由。

---

## 0. 一句话结论

1. **前端重构**：采用 shadcn/ui（Base UI 原语，源码拷入仓库）+ Tailwind CSS v4 + Motion，配合 lucide 图标、Streamdown Markdown、TanStack Query 和 zustand。流畅度的**主因不是动画，是流式渲染管线**（每个 SSE 事件都 setState、非增量事件深拷贝、每个 delta 让所有已挂载轮次重渲染、Markdown 尾部整段重解析）。技术迁移不等于视觉重构：先建立行为/性能基线，再改管线，P2/P5 按 §4.6 重做全站布局层级与视觉系统；已有功能按 §4.8 保持等价。
2. **Generative UI**：保留 `thesysdev/openui`。模型在回答正文中直接写 ```` ```openui ```` 围栏块（OpenUI Lang），Portal 拆成 `Text | UI` 片段，由 `@openuidev/react-lang` 渲染。目录从官方能力和 AILab 设计系统选取、扩展，不限定为十几种组件；需要自由图解或交互工具时，可由同一 OpenUI 体系的隔离 HtmlArtifact 承载（§6.16）。结构化路径**不新增事件类型、模型调用或存储表**：复用 `agent.delta` 与 `Run.summary`。HTML 的渐进预览、执行和导出另有验收，不能套用结构化路径已满足的承诺。表达由主模型自行选择，无 Jev；不采用独立 UI Generator 两段式。

---

## 1. 目标与成功标准

### 1.1 真实目标

| # | 目标 | 可测的成功标准 |
|---|---|---|
| G1 | 动画与流式输出流畅 | 第 10 节性能预算全部达标，且 500 轮会话不劣于当前基线 |
| G2 | 全站视觉重构 | 按 §4.6 的统一视觉系统完成聊天、工作台和辅助页面；桌面/手机、浅/深色及关键状态的真实浏览器证据通过 §4.9 验收，不能以“换了组件库”代替 |
| G3 | 降低维护成本 | 通用控件复用同一原语/角色实现与语义 token；清空 legacy、消除重复控件和跨页样式覆盖；不以 CSS 行数或 Tailwind 类数量代替质量 |
| G4 | 回答中流式输出多个 UI | 同一轮有界的多个 `Text / UI` 片段交错；结构化 UI 在围栏闭合前出现有效组件，Chat/Work 均可用。HTML 的源码流式、可见预览与交互就绪分别按 §6.16 验收 |
| G5 | UI 可靠 | 重连、刷新、历史、fork、分享、打印保留内容与明确降级；单块失败不影响其它内容。结构化路径不执行模型给出的任意 JS/HTML；HtmlArtifact 只在独立隔离环境运行，不访问宿主会话/内部服务。内容恢复不等于临时控件状态恢复（§6.8） |
| G6 | 已有功能等价 | §4.8 的行为清单逐项映射现有代码与测试；原入口、权限、状态流转、快捷键和内容可达性不丢失。视觉对比允许变化，业务断言不能为迁就新界面而删减 |

### 1.2 约束（来自 AGENTS.md 与现状）

- 五个服务的边界不变。GenUI 不进入 Broker、Worker 或 Work 的服务端代码沙箱；HtmlArtifact 的浏览器隔离 iframe 是另一层边界，不等同于 Work 沙箱。
- Portal 不持有模型 key，不调用内部端点。UI 文案用中文。按用户偏好，**不加多余的二级标签和说明文字**。
- 模型只走 OpenAI 兼容的 `/chat/completions`（`catalog.py:24`），没有 structured output，也没有 `response_format`。
- 事件是 best-effort：追加失败只记日志（`agent/loop.py:228-238`）；终态 Run 的事件 30 天后删除（`config.py:197`）。**事件不能作为 UI 的长期来源。**
- 目标机是 2 vCPU / 8 GB 的整栈（服务端）。客户端以本机浏览器为准，并加 4× CPU 节流复验。

---

## 2. 与设计相关的现状事实

| 领域 | 事实 | 位置 |
|---|---|---|
| 依赖 | Portal 只有 next 16 / react 19 / react-dom，没有任何 UI 库；CSS 是 1575 行手写的 `globals.css` | `portal/package.json` |
| SSE | 用 fetch 手动解析 `GET /v1/runs/{id}/events?after=`；重连按 seq 去重；`stream.end` 表示结束 | `lib/api.ts:450-531` |
| 状态 | `chat.tsx` 收到每个事件都调用一次 `setItems(reduceEvent)`，**没有 rAF 合批** | `chat.tsx:118-176` |
| Reducer | 只有同一 step 的 delta/thinking 替换尾部；**其它事件把所有 items 和 steps 全部深拷贝** | `lib/work-process.ts:85-95` |
| 重渲染 | `Turn`、`WorkProcess`、`StepRow` 都没有 memo；回调每次渲染重建，所以每个 delta 都会重渲染所有已挂载轮次 | `chat.tsx`、`turn.tsx` |
| 回答 | 进行中的回答是最后一个 acting 项之后的文本；终态回答是 `run.summary`（只有最后一个 step 的纯文本） | `work-process.ts:242-250`、`turn.tsx:238`、`loop.py:1315,1418` |
| 后端合批 | `_DeltaWriter` 每 0.25 秒或每 120 字落一条事件，所以客户端收到的文字是**成块跳出来的** | `loop.py:59-60,130-186` |
| 长内容 | `ViewportBlock` 让屏幕外内容卸载、保留测量高度；超过 12k 字的 Markdown 按约 6k 字分块，代码围栏不拆开 | `components/viewport-block.tsx`、`lib/markdown-chunks.ts` |
| Markdown | 自研的行解析器，无依赖，不用 `dangerouslySetInnerHTML`；支持 `ailab-file://` 引用、来源 pill、`[n](url)` 角标；不支持数学公式、图片、删除线、表格对齐，也没有代码高亮 | `components/markdown.tsx` |
| 历史给模型 | 最近 12 轮，每轮 `summary[:4000]` **直接按字符截断**；model_context 路径截到 16000 | `routes_internal.py:131-133,181-201`、`routes_model_context.py:81,102` |
| 详情接口 | `result` 只放行白名单里的键 `{previews, thinking, plan, question, resumable, sources}` | `routes_conversations.py:125-129` |
| 来源 | 来源没有 id，用 URL 去重，按首次出现排序，最多 50 条，存在 `result.sources` | `loop.py:74-94,1328-1333` |
| 模型能力 | `capabilities` 只允许 `{"tools","vision"}` | `catalog.py:146-150` |
| E2E | Playwright 用 `get_by_role` 280 处；依赖的类名有 `.turn .md .steps-head .turn-meta .msg .assistant-body .bubble .viewer .work-timeline .preview-pane .file-card` 等；**没有 data-testid** | `platform/e2e/*.py` |
| 单测 | `node --test` 加 `ts.transpile`，只能测没有 import 的模块 | `portal/tests/*.test.mjs` |
| 冻结目标架构 | 前端写的是 "Next.js 16 / React 19 / assistant-ui / shadcn/ui" | `docs/architecture/README.md:92,243` |
| 本机基线 | 500 轮会话只挂载 3 轮正文，DOM 826 个节点，输入到下一帧 14.5–23.3ms | `docs/progress/updates/2026-10-26-full-thinking-rendering.md` |

---

## 3. 对初版 HLD 的审核结论

### 3.1 保留（方向正确）

- 结构化路径由模型生成声明式描述、可信 React 组件执行；此限制不覆盖单独隔离的 HtmlArtifact 路径（§6.16）。
- 回答 = `ContentPart[]`，文字和多个 UI 交错（第 6 节）。
- 保留组件注册表；原十几种组件只作基础示例，正式目录在 P0 对照官方库、表达需求和性能预算确定（§6.6）。
- 交互只通过 Action 白名单；`continue_conversation` 转换成普通用户消息（第 18–20 节）。
- 敏感资源通过平台注册表解析，不信任模型给的 URL（第 17、26 节）。
- UI 失败不等于这一轮失败；可以降级（第 27–28 节）。
- 流式时保留最后一棵合法的树（Last Valid Tree）（第 32 节）。
- 第一版就带 `catalog_version`（第 33 节）。
- OpenUI 只作为适配层，不作为平台的核心协议（Decision 5）。
- 自由 HTML 与结构化组件保持执行隔离，但可同属 OpenUI 编排；不再因组件数量把它一律推到 V3（§6.16）。

### 3.2 修改（初版与代码事实冲突，或有更优解）

| 初版 | 问题 | 本版 |
|---|---|---|
| §7–8、§29、Decision 4：主 Agent 调 `present_ui`，再由独立的 UI Generator 模型生成 | ① OpenAI 兼容的工具调用会**结束当前 assistant 消息**，所以 `present_ui` 之前的文字会按现有规则被归入"过程"，回答被切碎（`work-process.ts:242`）。② 每个 UI 都要**多一轮主模型调用**（完整上下文重读）再加一次生成器调用，回答里有 3 个 UI 就是 6 次额外调用，首字延迟和成本都显著上升。③ `quiet` 调用不流式（`loop.py:537-547`），要流式就得新造一种 writer 和用量计量路径。④ 断点续跑时，生成器的输出需要单独定义幂等与重放语义。⑤ 主 Agent 还得把数据复制进 `present_ui` 的参数，token 并没有省 | **V1 让主模型直接在正文里写 UI 块**：一次调用，真流式，顺序天然正确，用量账本零改动。生成器两段式放进第 13 节"备选方案"，只在评测证明主模型的 UI 质量不足时再启用 |
| §10–12、Decision 3：新增 `agent.ui.start/delta/done` 事件 | UI 源码本来就是模型输出的 token。另开一条事件通道要处理与 `_DeltaWriter` 合批之间的排序、step 边界、双份存储和 Reducer 的第二套状态机 | **UI 块是正文里的围栏**，仍然通过 `agent.delta` 传输。"块开始/结束"由围栏本身表达。Event Sequence = Answer Sequence 依然成立，而且更彻底 |
| §21：Run Event Log 是 UI 的 Source of Truth | 事件是 best-effort，30 天后删除（第 1.2 节） | **`Run.summary` 是持久化的事实来源**，它本来就保存回答全文，现在其中也包含 UI 围栏。事件只用于实时传输和 30 天内的过程回放 |
| §22：另做 UI Snapshot | summary 本身就是终态快照 | 删除 |
| §24：三层验证放在服务端 | 服务端是 Python，OpenUI 的解析器是 TS；在 Python 里重写一遍语法会造成两份规范漂移 | **权威验证在渲染端**（渲染器本来就必须做）；服务端只做轻量的结构检查和指标统计（围栏是否闭合、大小、块数、组件名）。资源归属在 Portal 解析引用时由 API 鉴权兜底 |
| §17：`SourceCard{sourceId}` | 来源**没有 id**，模型也不知道全局编号 | 用模型**确实知道的键**去引用：`SourceCard(url)` 必须与本轮 `result.sources` 中某条 URL 完全一致才渲染，否则降级为纯文本；文件用已有的 `ailab-file://` id；产物用文件名对 `run.artifacts` 解析 |
| §19：Action 种类 | `open_source` 等与组件本身的行为重复 | V1 只保留 `@ToAssistant`（即 continue_conversation）和组件内部的本地状态；打开来源、文件、产物是对应组件的固有行为，不作为通用 Action 暴露 |
| §35：`agent/genui/{generator,prompt,provider,validation}` | 没有生成器，也就没有这几个模块 | 后端只新增 `domain/answer_parts.py`（纯函数）和 `contracts/genui/`（目录描述与生成好的提示词） |

### 3.3 初版缺失、本版补上

1. **多轮编辑 UI**（"把图改成饼图"）：模型历史需要看到上一轮的 UI 源码，而现有历史按 4000 字盲截会把围栏截成半截（§6.10）。
2. **复制、分享、打印、搜索**：每个组件都定义 `toMarkdown` 降级文本（§6.6）。
3. **Work 模式的语义**：回答只取最后一个 step，中间 step 里的 UI 归入过程（§6.3）。
4. **提示词的来源与漂移**：组件目录在 TS 里定义，Python 侧使用的提示词由脚本生成并提交，CI 校验两者一致（§6.4）。
5. **确定性 E2E 与付费评测**：mock 模型输出脚本化的 GenUI；真实模型用评测集量化合法率（§9 P4、P6）。
6. **前端重构本身**：初版没有涉及。第 4–5 节区分管线改造、视觉重构与功能等价，视觉规范、迁移和验收各有交付。

---

## 4. Part A：前端重构

### 4.1 选型

| 库 | 作用 | 选它的理由 | 代价 / 约束 |
|---|---|---|---|
| **shadcn/ui**（Base UI 原语，2026-07 起为默认） | 通用组件：Button、Dialog、Popover、DropdownMenu、Select、Tabs、Tooltip、Sheet/Drawer、Command、ScrollArea、Skeleton、Sonner | 源码拷进 `components/ui/`，**没有运行时锁定**；无障碍（焦点陷阱、Esc、ARIA）由原语负责，可以直接替换现在 10 处手写的 `createPortal`；与冻结目标架构一致 | 需要 Tailwind；生成的组件属于我们的代码，升级时手动合并 |
| **Tailwind CSS v4** | 样式 | shadcn 的前提；token 化；按需生成 CSS，不会无限增长 | 迁移期必须与旧 CSS 共存（§4.5） |
| **Motion**（`motion/react`，配合 `LazyMotion` 和 `domAnimation`） | 进入/退出、布局、手势 | `AnimatePresence` 处理卸载动画；`layoutId` 做 tab 指示条；`drag` 做手机抽屉；`MotionConfig reducedMotion="user"` 统一处理减少动态效果 | 用 `m.*` 加懒加载功能包，首包控制在约 5–15 KB gz（P0 实测） |
| **lucide-react** | 图标 | 替换手写的 55 个 SVG；按名字 tree-shake | 保留 `components/icons.tsx` 作为门面并统一 re-export，AGENTS 里"图标只从 icons.tsx 引入"的规则不变 |
| **Streamdown** | Markdown 渲染 | 为流式而生：自动补全未闭合的粗体、链接、代码块，避免流式时闪烁；按块 memo；GFM 完整（表格对齐、删除线、任务列表）；shiki 高亮和 KaTeX 可懒加载 | 必须关闭原始 HTML、限制链接前缀；用 `components` 覆盖来保留 `ailab-file://`、来源 pill、`[n]` 角标；**保留我们自己的分块和 ViewportBlock 层**；P0 用 40 万字验收，不达标就退回 `react-markdown + remark-gfm` |
| **@openuidev/react-lang**（OpenUI 1.x，锁定精确版本） | GenUI 解析和渲染 | 流式友好（根节点先出现、子节点和前向引用后续补上，未解析的引用直接剔除）；Zod 定义 props；可从组件目录**自动生成提示词**；渲染器不执行 action，只回调 `onAction` | 1.0 于 2026-10-15 发布，比较新，可能变动 → 只在 `components/genui/openui/` 内部 import，外面一层是我们的适配接口（§6.5） |
| **Recharts**（shadcn chart 的底层） | GenUI 图表 | 输出 SVG，打印友好；shadcn 有现成的 chart 主题 | 体积较大，**只在出现图表块时懒加载** |
| **zod** | GenUI props schema | OpenUI 的前提 | — |
| **zustand** | 会话/流式状态 | 用 selector 精确订阅，**只让正在流式的那一轮重渲染**；约 1 KB | 只用于聊天流式状态，不做全局状态 |
| **TanStack Query** | 请求/响应类资源（文件库、管理台、技能、记忆、定时任务、账号） | 自动取消和丢弃过期请求（满足 AGENTS"可被取代的 fetch 必须取消"的规则）、去重、缓存，替换各页手写的 abort 和 seq 逻辑 | **不用于 SSE**，流式仍走 `streamRun` |
| **Vitest** + Testing Library + jsdom | 组件和渲染测试 | 现在的 `node --test` 只能测没有 import 的模块，GenUI 渲染必须测真实组件 | 迁移现有 `tests/*.test.mjs`，统一用一个 runner |

**不采用**：

- **assistant-ui**（冻结架构里提到过）：它的消息/分支/runtime 模型与我们以 Run 为中心的数据模型冲突。我们的 Run 有服务端分页的版本/fork、过程时间线、排队、插话、ask_user、预览，还有已验收的 500 轮卸载机制。ExternalStore 适配层的维护成本高于它带来的收益。用 ADR 记录这次偏离（P0 交付）。
- **OpenUI 的 `react-ui` / `react-headless`**：视觉要与全站 shadcn 统一，聊天状态是我们自己的。
- **虚拟列表库**（react-virtuoso 等）：现有 ViewportBlock 已通过 500 轮验收；换掉会推翻已验证的行为。
- **react-hook-form**：现有表单字段少，没有具体收益。
- **A2UI 作为 V1 格式**：见 §13。

### 4.1.1 shadcn 生态的组件来源（2026-10-28 用户确认）

所有来源都通过 shadcn registry 把**源码**拷进仓库（`npx shadcn add <registry>/<item>`），不作为运行时依赖。因此它们是"组件来源"，不是新的 UI 框架。

```
shadcn/ui（Base UI 原语 + token，唯一的基础层）
  ├─ Origin UI / COSS     常规业务组件：表单、表格、筛选、上传、日期、通知
  ├─ Motion Primitives    微交互与过渡：状态微光、数字滚动、面板切换、分组入场
  ├─ Cult UI              少量高级交互组件（逐个评审后再引入）
  └─ Magic UI             登录/注册或空状态的可选来源；不预设必须加入装饰
```

**在 AILab 中的落点**（实施时按组件逐个确认是否存在；同一种角色只保留一个实现）：

| AILab 位置 | 来源 | 组件类型 |
|---|---|---|
| Composer 附件、文件库上传 | Origin/COSS | 拖放上传、文件列表 |
| 管理台用户/部门/策略/审计/用量 | Origin/COSS | 可排序分页表格、多选 Combobox、筛选栏、日期范围 |
| 定时任务 | Origin/COSS | 日期时间选择、cron 辅助输入 |
| 收件箱、队列 | Origin/COSS | 通知列表、徽标计数 |
| "思考中 / 工作中"状态、步骤运行中 | Motion Primitives | TextShimmer（只在运行时显示，结束后停止） |
| 工作计时、token 用量、GenUI `Metric` | Motion Primitives | AnimatedNumber / SlidingNumber |
| 预览侧栏 tabs、Composer 模式切换、GenUI `Tabs` | Motion Primitives | TransitionPanel，配合 `layoutId` 指示条 |
| 过程步骤展开、来源列表 | Motion Primitives | Disclosure（只用于小容器；大时间线保留 CSS grid 折叠，见 §4.4） |
| 文件卡 → 查看器 | Motion Primitives | MorphingDialog（P2 实测，掉帧则退回普通 Dialog） |
| GenUI 子节点入场 | Motion Primitives | AnimatedGroup（stagger 30ms，最多 6 个） |
| 聊天滚动区上下边缘 | 原生 CSS | 默认无模糊层；确需提示可滚动时用轻量边缘渐变，不遮正文/焦点 |
| 可展开卡片、动画弹出表单 | Cult UI | 候选，逐个评审 |
| 登录/注册、空状态 | Magic UI | 可选静态资产或控件；必须服务任务，不默认加入粒子、光晕、循环背景 |

**引入规则**（写进 `portal/AGENTS.md`，P0 交付）：

1. **只用一套原语**：shadcn 的 Base UI。某个来源的组件如果依赖 Radix，就改写为 Base UI 再引入，否则不引入，避免两套原语同时进包。
2. **只用 MIT 免费层**：Cult Pro、Magic UI Pro、Motion Primitives Pro 等付费层不引入。每个文件头部注明来源、版本或 commit、许可证，例如 `// from motion-primitives/text-shimmer@<sha>, MIT`。
3. **依赖白名单**：motion、tailwind、shadcn 原语、lucide。组件如果引入 gsap、three、canvas-confetti、cobe 等额外动画或图形库，就改写或放弃；确有必要的必须在 PR 里说明体积和收益。
4. **改成我们的 token**：拷入后颜色、圆角、阴影、时长全部换成 `tokens.css` 的变量，删除写死的颜色和 `dark:` 硬编码。
5. **聊天区的性能红线**：
   - 不对正在流式的正文做逐字或逐词动画（TextEffect 这类组件不得用于回答正文，会和 SmoothText、40 万字分块冲突）；
   - 聊天和工作区不放无限循环动画，唯一例外是"运行中"状态的 TextShimmer；
   - 不对大面积区域做 `backdrop-filter` 或 blur 动画；
   - 全部遵守 `reducedMotion="user"`。
6. **一种角色一个实现**：`portal/AGENTS.md` 维护"角色 → 组件 → 来源"表，新增前先查表，不重复引入功能相近的组件。
7. **GenUI 视觉复用设计系统**：组件可以展示、受控本地交互或发送白名单 Action（§6.1），模型不能覆盖宿主 token、传入任意 class/style 或动效参数。AnimatedNumber 只用于离散指标变化，不用于高频 token/计时更新；流式追加不重复播放入场。

> D1 已确认，本轮不重开组件库选择。registry 只是源码来源，不能决定 AILab 的视觉方向；组件接入须服从 §4.6。

### 4.2 目录分层

```
portal/
  app/                      路由（不变）
  components/
    ui/                     shadcn 生成的通用组件（只做外观，不含业务）
    chat/                   Turn、WorkProcess、Composer、Sources、PreviewPane…
    genui/
      index.tsx             适配接口 <GenUIBlock/>（对外唯一入口）
      catalog/              组件目录：每个组件 = schema + React 实现 + toMarkdown
      openui/               OpenUI 适配器（全仓库只有这里 import @openuidev/*）
      actions.ts            Action 白名单与分发
    motion/                 动效原语：FadeIn、Presence、Collapse、SmoothText
    icons.tsx               lucide 门面
  lib/
    stream/                 run-store（zustand）、事件合批、reducer
    answer-parts.ts         回答拆分（纯函数，与 Python 侧共用测试夹具）
    query.ts                TanStack Query client 与 key 约定
  styles/
    tokens.css              设计 token（OKLCH，light/dark）
    legacy.css              旧 globals.css，逐步删除
```

### 4.3 流式渲染管线（流畅度的主因，P1 单独交付）

```
SSE frame ─► streamRun ─► eventBuffer.push(e)
                             │  每个 requestAnimationFrame 一次
                             ▼
                     runStore.apply(batch)   ← reduceEvents(items, batch)，结构共享
                             │
              selector: useRunItems(runId) / useRunTail(runId)
                             ▼
     只有正在流式的 <Turn> 重渲染；其它 Turn 都被 memo 跳过
                             ▼
     <SmoothText> 按帧平滑释放字符 ─► Streamdown（只重解析最后一个块）
```

规则：

1. **合批**：每帧最多一次 store 更新。页面隐藏（`document.hidden`）时改用 `setTimeout(…, 250)` 合批。历史回放（`events/history` 分页）一次性 apply，不逐条处理。
2. **结构共享**：`reduceEvents` 只替换发生变化的 item 和 step，其余保持引用不变，去掉 `work-process.ts:95` 的整体深拷贝。时间线的 key 用稳定 id（`step` 加序号），不用数组下标。
3. **订阅粒度**：`Turn`、`WorkProcess`、`StepRow` 用 `memo`；`chat.tsx` 传下去的回调用 `useCallback` 或通过 store 动作传递，保持引用稳定。
4. **`loadDetail()` 不再在每个状态或产物事件时刷新 20 轮**：只刷新当前 Run（新增或复用单 Run 详情接口），终态时再整体刷新一次。
5. **平滑文字（SmoothText）**：后端每 250ms 或 120 字合批一次，文字会成块跳出。客户端维护 `shown/total`，每帧释放 `max(2, ceil(backlog × dt / 120ms))` 个字符，即**任何积压都在约 120ms 内放完**，平滑但不拖慢。以下情况直接显示全文：历史回放、终态、`prefers-reduced-motion`、页面隐藏、积压超过 4000 字。只作用于当前流式的尾部片段。UI 围栏内部不做逐字释放（§6.5）。
6. **Markdown**：Streamdown 只重解析最后一个块；分块（>12k 字）与 ViewportBlock 保持不变。流式中只有最后一个 chunk 带 `streaming` 标记，这一点不变。
7. **滚动**：保留现有基于 ResizeObserver 的贴底逻辑。HANDOFF 已记录：程序定位用即时滚动，不用 `scroll-behavior:smooth`。

### 4.4 动效体系

| 场景 | 实现 | 规格 |
|---|---|---|
| 新轮次进入 | Motion `m.div`，只作用于页面打开后新到的轮次（沿用现有 `fresh` 语义） | opacity 0→1，y 6→0，180ms，`--ease` |
| 过程折叠/展开 | **CSS grid `0fr→1fr`**（保留现有做法） | 时间线可能有上千个节点，不对大 DOM 做逐帧的 JS 高度动画 |
| 小容器高度（GenUI 块、卡片展开） | Motion `layout` / `height:auto` | ≤ 200ms，仅限节点少的容器 |
| Tab 指示条、模式切换 | `layoutId` 共享布局 | spring（stiffness 500，damping 40） |
| 弹层（Dialog、Popover、Menu） | shadcn/Base UI 自带的 data-state 过渡加 Tailwind 动画 | 120–160ms |
| 手机抽屉（来源、预览） | shadcn Drawer（P0 确认其原语仍在维护）；否则用 Motion `drag="y"` 封装 `Sheet` | 阈值沿用现有的 `DRAG_OPEN=56`、`DRAG_CLOSE=80`；Back 键关闭的行为不变 |
| GenUI 块 | 骨架 → 内容交叉淡入；子节点逐个出现，每个间隔 30ms，最多 6 个 | 骨架按组件类型预留高度，避免布局跳动 |
| Toast | Sonner | — |

统一约束：

- 优先只对 `transform` 和 `opacity` 做动画；上述小容器高度和 CSS grid 折叠是有界例外。在长列表上禁止对 `width`、`height`、`top` 做逐帧 JS 动画。
- 时长 token 统一为 `--t-fast 120ms`、`--t 200ms`、`--t-slow 320ms`（沿用现有值）。
- `MotionConfig reducedMotion="user"` 之外，CSS 动画也响应 `prefers-reduced-motion`；减少动态效果时 shimmer/spinner 改为静态状态图标和文字，不以库配置代替全站覆盖。
- 打印媒体下关闭所有动画，延迟挂载的内容全部渲染（HANDOFF 已有的规则）。

### 4.5 样式迁移（不做一次性大爆炸）

1. **P0 共存**：Tailwind v4 先**不引入 preflight**：`@import "tailwindcss/theme" layer(theme); @import "tailwindcss/utilities" layer(utilities);`。旧 CSS 放进 `@layer legacy`，层顺序为 `legacy < components < utilities`，保证旧页面的样式不变。
2. **token 映射**：P0 先把现有变量等值映射为兼容 token，不能在基础迁移时换色；P2/P5 再按 §4.6 更新 surface、text、accent、status、radius、shadow 等语义 token。目标写进 `styles/tokens.css`，作为 shadcn 的语义变量（`--background --foreground --primary --muted --border --ring --destructive …`）。暗色模式由 `.dark` 类控制，root layout 里已有的内联脚本在首帧前按系统偏好设置它（同时为以后的手动切换留口）。
3. **逐屏迁移**：每迁完一屏就删掉 `legacy.css` 里对应的段（文件里已有 `/* ---------- x ---------- */` 分段标记）。
4. **E2E 钩子类名契约**：§2 列出的被 E2E 依赖的类名（`turn md steps-head turn-meta msg assistant-body user bubble viewer work-timeline preview-pane file-card conv-item lib-row skill-row queue-row composer-box attach-chip mode-toggle viewport-block collapse pane-tab …`）和 `data-mounted` 属性**作为语义钩子保留**在重构后的元素上（`cn("turn", …)`），不带样式。新代码优先让 E2E 用 role/label 定位。这份清单写进 `portal/AGENTS.md`。
5. **收尾**（P5）：legacy 清空后再启用 preflight，并全站人工验收一遍。

### 4.6 视觉重构规范（Impeccable / Operate）

#### 4.6.1 依据、用户任务与设计权限

采用用户指定的 [Impeccable SKILL.md](https://github.com/pbakaus/impeccable/blob/5e745ace7e151feb2a93d360d0281fe39448b857/.cursor/skills/impeccable/SKILL.md)、[Operate](https://github.com/pbakaus/impeccable/blob/5e745ace7e151feb2a93d360d0281fe39448b857/.cursor/skills/impeccable/reference/operate.md) 和 [Operate mode](https://github.com/pbakaus/impeccable/blob/5e745ace7e151feb2a93d360d0281fe39448b857/.cursor/skills/impeccable/reference/mode-operate.md)，固定于 `5e745ace7e151feb2a93d360d0281fe39448b857`（skill 4.8.0）。上游规范提供设计方法，不是组件库或运行依赖；下面数值是 **AILab 的建议设计值**，不是上游强制规定。

- **主要任务**：中文用户持续进行 Chat/Work、查看过程/来源/产物，随后编辑、分支、整理资料。频繁使用时，当前对话、任务状态和可执行动作应直接可辨。
- **模式**：应用壳、Composer、资料库和管理台采用 Operate；回答/来源/文档预览采用 Read 的阅读节奏。登录注册依旧以完成认证为主，不做营销落地页。
- **设计范围**：重新设计外观、空间层级、密度和控件样式；保留产品事实、业务语义、权限、路由与操作能力。不因“极简”删功能，不新增产品宣称、导航类别或新业务流程。
- **设计提案**：以清晰的内容工作台为方向，重做导航、内容、预览三层的视觉关系。保留可辨识的 AILab 名称和靛蓝主色线索，替换现有整页卡片化与通用灰底处理；并非给旧控件换圆角。最终颜色/密度由 P0 代表屏审阅确认，不能把本稿建议记为用户已选视觉稿。
- 本轮只提炼规范到 HLD。当前没有 PRODUCT.md/DESIGN.md；不为文档修订创建另一套事实来源，也不声称已完成 Impeccable 的视觉选稿、实现和渲染复核。实施前再按其 context/init/new-work 流程建立设计资料，引用本项目已有产品/架构记录，避免复制维护。

#### 4.6.2 全站构图与辨识方式

首屏首先是一套可操作的工作空间：导航负责选择上下文，中央负责阅读与输入，右侧负责查证与预览。辨识性落在**回答中的引用与同一预览工作区的对应关系**：点击来源或产物后，原文位置保持，右侧相应 tab 和文内引用显示同一选中态；手机以现有抽屉承接。它使用已有联动能力，不新增一种导航或“详情”产品。

| 区域 | 目标视觉与层级 | 行为边界 |
|---|---|---|
| 应用壳 / 侧栏 | 轻微靛蓝倾向的中性导航底，正文为独立阅读底；导航按现有分组纵向排列，当前会话用浅色填充和清晰字重标识；操作靠对齐与间距分层 | 保留展开/收起、会话/项目列表、搜索、收件箱、账号和管理权限入口；不改成仅靠悬浮发现的图标墙 |
| 对话标题区 | 紧凑一行标题与必要操作；长标题截断后仍能取得全文；运行状态只在需要的位置出现 | 保留手机顶部已有语义，不增加副标题、口号或重复状态条 |
| 回答与过程 | 回答直接置于阅读底；用户消息保留可区分的消息面；过程用细分隔、时间线和状态呈现，展开后正文优先 | 不恢复已取消的回答下方“详情”入口，不再摘要折叠完整思考；折叠/离屏卸载与全文分页不变 |
| Composer | 一个清晰输入面，输入正文优先，附件在上方，模式/技能/模板等工具与发送动作分组；边框与 focus 表达可输入性 | 保留 Enter/Shift+Enter、中文 IME、停止、排队、插话、附件移除与上传失败状态；不把功能藏到不可达位置 |
| 预览与来源 | 一条稳定分界和一层 tab 栏，正文/来源/文件各有清晰滚动范围；避免面板套面板 | 关闭/切换恢复原对话位置，来源页码/文件引用与选中 tab 一致，过期请求不得覆盖当前预览 |
| 资料与管理 | 资料采用可扫描列表，文件需要时显示真实缩略图；管理数据使用表格、筛选和对齐的数字列 | 不把用户表、审计、用量改成装饰 KPI 卡；现有分页、筛选、选择和权限能力保留 |

卡片只用于确有边界的对象、文件或独立交互，不给每段回答、每个设置项和每张表再套容器。阴影只表达浮层高度；常规层级靠底色、留白和细分界。标准按钮、输入、tab、菜单保留熟悉的可操作外观。

#### 4.6.3 Token、中文排版与颜色

| 系统 | 建议规格 | 实施约束 |
|---|---|---|
| 字体 | 延用本机系统中文 sans 栈；代码使用现有 mono 栈 | 不新增在线字体请求；不对中文施加负字距或强制大写 |
| 字级 | 辅助/元信息 12px、标签 14px、回答/输入 16px、分区标题 18px、页面标题 24px；转为 rem token | 固定 rem 层级，不用随视口变化的展示型大标题；12px 不用于正文或关键错误 |
| 行距/行宽 | 回答 line-height 1.75，界面 1.45–1.55；正文目标约 38–44 个中文全角字符宽，外层保留当前 820px 上限 | 正文与代码/表格分开控制；GenUI 可占满聊天列，宽代码/表格局部横滚，不撑开整页 |
| 空间 | 4px 基础步进，常用 4/8/12/16/24/32；正文段间、分区间、页面边距各有 token | 侧栏/表格可紧凑，聊天留足阅读空间；同一角色不得逐屏另写尺寸 |
| 控件/圆角 | 标准控件高 36px，紧凑桌面操作 32px；圆角小 6px、控件 8px、输入/预览面 12px | 手机操作目标至少 44×44px；不要把所有元素都做成 pill |
| 导航/阅读底 | 浅色：导航 `#eef0f7`、阅读 `#fcfcfd`、浮层 `#ffffff`；深色：导航 `#151a29`、阅读 `#10131c`、浮层 `#1b2130` | 建议样本，在 P0 换算为 OKLCH 并审阅；分别定义 hover/selected/border，不靠整体反色 |
| 文字/主色 | 浅色正文 `#182033`，主色继承 `#4f46e5`；深色正文 `#edf0f7`，主色继承 `#7b83ff` | 区分正文、次要文字、占位、禁用；主色用于主动作/选中/焦点，不能同时代表成功或警告 |
| 状态/图表 | success/warning/error/info 独立语义色；图表使用有序 data-series token | 状态有文字或图标，不只靠颜色；系列同时以标签/线型/标记区分，不能把每张图变成独立配色主题 |

> 2026-10-30 用户决定：不用紫/靛蓝，强调色改为墨青（浅 `#0F766E`、深 `#3FD5C0`），中性色改为不带蓝调的石墨灰（浅导航 `#f5f5f3`、阅读 `#ffffff`；深导航 `#141416`、阅读 `#19191b`）。以 `portal/styles/tokens.css` 为准，上表导航/文字/主色两行样本作废；字级、行距、圆角等其余行照旧。

语义 token 至少涵盖 background/panel/overlay、foreground/muted、border/ring、primary、selected、disabled、status、data-series、spacing、radius、elevation、duration。原 CSS 变量只在迁移期作为别名；浅/深主题共享角色，各自独立校验对比度。正文/可操作文字对比度 ≥4.5:1，大字 ≥3:1，焦点和关键控件边界 ≥3:1；样本颜色尚未完成全组合校验，不能记录为已达标。

#### 4.6.4 响应式、状态与无障碍

- **响应式构图**：建议 ≥1280px 使用可展开导航 + 正文 + 打开时的预览列；760–1279px 优先保障正文宽度，可收起导航，预览空间不足时覆盖显示；<760px 保留现有移动侧栏与来源/预览抽屉。以可用容器宽度判断是否可并排，不硬塞三列。导航建议 240–256px、预览建议 360–480px，中央不足 380px 时必须收敛。
- **手机**：覆盖 390×844，并检查 320px 窄屏、横向、软键盘与 safe-area；Composer 跟随现有 visualViewport 契约，聚焦后发送/停止仍可操作。背景不跟随抽屉滚动，关闭回到原焦点/位置；触摸用户不依赖 hover。
- **数据状态**：首次读取用结构稳定的 skeleton；后台刷新保留已有数据；空列表提供一行任务相关文字和已有可用动作，不用装饰图标替代意义。失败在受影响区域给出错误与重试；禁用态须有可访问原因；待确认、额度暂停、排队、取消、终态彼此可辨。不得用 loading 误报成功或隐藏部分结果。
- **控件状态**：每种角色覆盖 default/hover/focus-visible/pressed/selected/disabled/loading/error 中适用状态；原生语义、可访问名称、tab 顺序、Esc、焦点陷阱与返回焦点必须验证。不能只凭 Base UI 声称全站无障碍通过。
- **阅读/辅助技术**：200% 缩放仍能完成操作；正文在窄屏重排，表格/代码可局部滚动。流式正文不按 token aria-live 播报，只对运行状态变化做有节制通知。图表有文字/表格降级。
- **减法边界**：不加多余二级标签和解释；真实表单 label、单位、验证错误、权限/风险提示与必要空状态保留。“让布局解释”不等于只留图标或只用 placeholder。

#### 4.6.5 动效与 GenUI 一致性

动效只服务反馈、状态变化与空间连续性，按 §4.4 使用统一时长；无整页编排入场、无循环背景、无鼠标追光或大面积 blur。运行中 shimmer 可选且有静态替代；长列表、回答和打印以稳定性优先，不能因新 delta 重播进入动画。文件查看器默认普通面板/Dialog；MorphingDialog 只有真实改善定位且通过预算才接入。Magic UI/Cult UI 的可用性不是加入特效的理由。

结构化 GenUI 复用宿主字体、间距、颜色和控件角色；图表、Metric、Tabs 不能自成仪表盘皮肤。HtmlArtifact 是隔离创作内容：宿主只提供版本化主题建议与外框，不强制重写生成内容的 CSS，不承诺它与宿主像素一致；焦点、边界、错误、只读与降级仍由可信外框统一处理。

### 4.7 测试策略

- `npm test` 改为 Vitest。现有 `tests/*.test.mjs` 原样迁移，断言不变。
- 新增：reducer 结构共享（未变化的 item 引用不变）、合批（N 个事件只触发 1 次渲染）、SmoothText（积压在 120ms 内放完、终态立即显示全文）、answer-parts（与 Python 共用夹具）、GenUI 组件 schema 和 `toMarkdown`。
- E2E：现有用例全部保持绿色是每个阶段的硬闸门；新增 GenUI 用例（§9 P4）。
- 性能 E2E：P0 把现有的 500 轮、40 万字测试扩展为输出指标的基准（输入到下一帧、DOM 数、长任务数、流式期间帧时间），每个阶段都和 P0 基线对比。

### 4.8 功能等价清单（视觉重构不可削减）

以下是 P0 需要用实际路由、组件、现有测试补齐的最低清单，不把 HLD 当成完整功能发现。每行记录基线入口/操作 → 新入口/操作、数据与权限结果、自动测试或人工证据；不要求保留旧 DOM 布局。

| 范围 | 必须保持的行为 | 重点回归 |
|---|---|---|
| 会话与消息 | 新建/切换/重命名/删除/置顶、历史分页、搜索、Chat/Work、编辑/重试/fork、版本切换 | 切换取消旧请求，分支模式和历史版本一致，加载更早内容不跳锚 |
| 执行中交互 | 思考/过程、ask_user、停止、排队、插话、等待与额度暂停/继续、失败恢复 | 完整过程连续分页、已确认插话可见、终态不回退、刷新重连无重复 |
| 输入与上下文 | 草稿、附件上传/移除、技能快捷入口与斜杠菜单、模板选择、模型/模式相关控件 | IME 不误发、快捷键语义不变；上传失败不能当作可用文件发送 |
| 来源与产物 | 文内引用/页码、来源列表、文件 tabs、Office/PDF/图片等现有预览、下载 | 正确文件/版本/页码、过期响应丢弃、关闭和手机 Back 返回位置 |
| 资料/项目 | 上传、扫描/解析状态、文件版本、收藏/移动/删除、项目与会话/文件关联 | 分页/筛选/批量操作、权限与失败恢复；不存在的操作不凭 HLD 新造 |
| 辅助工作区 | 技能、记忆、定时任务、收件箱及其现有增删改查/启停/已读能力 | 空/错/加载/过长内容、未读计数、跨页刷新与过期数据 |
| 账号/管理 | 登录注册/退出、账号、部门/用户/策略/用量/审计/品牌/技能及现有配置项 | 角色可见性与 API 鉴权保持，表单校验/保存/失败/分页完整 |
| 分发与兼容 | 分享只读/撤销、复制、打印/PDF、旧 URL、来源/文件链接 | 长回答首尾齐全、非当前分支内容正确、打印无卸载漏页、历史 Run URL 保持兼容 |
| 长内容与主题 | 500轮、40万字、离屏卸载、浅/深色、键盘/触屏 | 不为新视觉全量挂载；主题首帧无闪烁，不丢焦点/编辑状态 |

GenUI 是既有 HLD 规划中的新增能力，不属于“已有功能等价”；P3/P4/P4-H 单独验收。若设计需要变更入口、标签或布局，保留功能可发现性和可访问名称，并同步 locator；不能通过删断言、缩减数据量、放宽性能预算消除回归。

### 4.9 设计交付与验收证据

1. **P0 设计基线**：以当前代码和新采集的桌面/手机、浅/深色截图盘点行为。先确定视觉资料如何复用现有产品文档，再按 Impeccable 建立 PRODUCT.md（引用产品事实）和 DESIGN.md（token/组件规范）；只有实施设计工具需要时才生成相应 sidecar。不把 `context --target HLD` 报告“无视觉实现”误读为项目没有 UI。
2. **代表屏方向稿**：至少涵盖有回答/展开过程/Composer的对话、来源或文件预览、资料列表、带真实列的管理表单/表格，以及移动抽屉。使用标明为合成的数据，覆盖空/错/加载/长内容和浅深主题。所有页面必须体现同一个系统；视觉稿经审阅后才作为 P2/P5 对照。建议 token 并不替代此步骤。
3. **实施后统一 review**：一组关联改动完成后，集中检查桌面 1440×900、390×844、320px 窄屏、浅/深色及键盘状态，修复共同问题再复核；不每个控件改动都重启全套。截图必须打开检查，且配合实际点击、滚动、输入、焦点和 Back 行为，不把静态稿算成功能证明。
4. **交付记录**：列出测试 SHA/源码基线、路由/视口/主题/状态、截图与交互证据路径、§4.8 映射结果、§10 性能实测、未验项。自动测试通过、视觉人工接受、合并、部署分别记录。无完整业务回归、无障碍关键路径和视觉证据，不标记全站重构完成。

---

## 5. Part A 的数据请求统一（TanStack Query）

- 范围：`files/`、`admin/*`、`skills/`、`memory/`、`schedules/`、`account/`、项目、搜索、收件箱等请求/响应类资源。
- 约定：query key 采用 `["files", folderId, cursor]` 这种格式；所有 queryFn 透传 `signal`；变更后通过 `invalidateQueries` 刷新。分页沿用 API 的 keyset 和 `X-Next-Cursor`。
- **不迁移**：`chat.tsx` 的会话详情、SSE 和翻页锚定。它们已有经过验收的 abort + seq 逻辑，与 zustand run-store 绑定。

---

## 6. Part B：Generative UI

### 6.1 核心决策

| # | 决策 |
|---|---|
| D-G1 | GenUI 是回答里的**一种片段类型**，以带语言标记的围栏块形式写在回答正文中；不扩展 Markdown 语法 |
| D-G2 | 以 OpenUI Lang 编排已注册组件，可扩展目录、绑定和函数；HtmlArtifact 可作为注册组件承载自由 HTML/CSS/JS，执行与结构化 React 路径隔离（§6.16） |
| D-G3 | 传输复用 `agent.delta`；持久化复用 `Run.summary`；不新增事件类型和存储表 |
| D-G4 | 主模型通过提示词自主选择文字、结构化 UI 或已准入的 HtmlArtifact 并直接生成；不引入 Jev、单独表达选择模型或 UI Generator 调用 |
| D-G5 | OpenUI 只是适配器；格式由围栏的语言标记表示（`openui`），以后可以并存 `a2ui` 等 |
| D-G6 | 资源只能通过平台注册表引用（来源 URL 精确匹配、`ailab-file://` id、产物文件名） |
| D-G7 | 首期对外能力为 `@ToAssistant` 与受控本地状态/计算；OpenUI 本身还支持绑定、函数、Query/Mutation，但后者须有实际 API 授权与幂等设计才能开放，不是框架不支持 |
| D-G8 | 每个组件必须实现 `toMarkdown` 降级，供复制、分享降级、渲染失败和不支持的客户端使用 |

### 6.2 线格式

模型输出（同一个 `agent.delta` 流）：

````markdown
最近 7 天 GPT 的使用量最高：

```openui
root = Stack([cards, chart])
cards = Grid([m1, m2, m3])
m1 = Metric("GPT", 320, "次")
m2 = Metric("Claude", 210, "次")
m3 = Metric("Gemini", 95, "次")
chart = BarChart("使用量", ["GPT", "Claude", "Gemini"], [Series("次数", [320, 210, 95])])
```

GPT 大约占总量的一半。

```openui
root = Stack([Button("按天拆分", @ToAssistant("把这三个模型最近 7 天的使用量按天拆分"))])
```
````

- 每个围栏是一个独立的 OpenUI 程序，各有自己的 `root`。由于根节点先出现，外壳可以先渲染。
- 语言标记固定为 `openui`。**不在围栏头里写版本**（模型容易写错）；版本由 Run 记录（§6.11）。
- 具体语法以锁定版本的 OpenUI 文档为准；上例按 1.x 文档的 `Name(args)` 写法加位置参数（顺序与 Zod key 顺序一致）。

### 6.3 端到端数据流

```
Agent（Chat/Work 共用）
  system prompt += GenUI 提示词（仅当功能开启且模型有 genui 能力）
  model stream ─► _DeltaWriter ─► agent.delta（文字和围栏混在一起，与现在完全一样）
  step 结束 ─► answer_parts.inspect(content) ─► Prometheus 指标（不产生事件）
  Run 完成 ─► Run.summary = 最后一个 step 的全文（含围栏）；result.genui = {format, catalog, blocks}

API
  会话详情的 result 白名单 += "genui"
  历史给模型 ─► answer_parts.for_history()（围栏感知的截断和占位，§6.10）

Portal
  run-store ─► 回答文本 ─► splitAnswerParts(text)
      → [md, ui(closed), md, ui(open, streaming)]
      md → Streamdown（+ SmoothText）
      ui → <GenUIBlock source streaming catalogVersion runCtx onAction/>
```

**Work 模式**：回答 = 最后一个 step 的文本（现有规则不变）。中间 step 的叙述文本里如果出现 UI 块，在过程时间线里用同一个渲染器显示。提示词要求"UI 只放在最终回答中"。**Chat 模式**：没有工具时只有一个 step，回答就是全文。

### 6.4 组件目录与提示词（单一来源）

- 目录**只在 TS 里定义**：`components/genui/catalog/*.tsx`，每个组件导出 `{ name, props: z.object(...), description, component, toMarkdown, skeleton }`。
- `npm run genui:prompt` 调用 `library.prompt({ preamble, additionalRules, examples })`，输出：
  - `platform/ailab_platform/contracts/genui/openui-v{N}.prompt.md`（交给 Agent 使用）
  - `platform/ailab_platform/contracts/genui/catalog-v{N}.json`（组件名、版本、提示词 sha256；Python 侧用于统计和白名单）
- **漂移校验**：Portal 测试重新生成提示词，必须与提交的文件逐字节一致；Python 测试校验 json 中的 sha256 与 prompt 文件一致。
- 提示词预算：≤ 2,500 tokens，放在 system prompt 的稳定前缀里，以便上游缓存命中。
- 上述预算是首期结构化目录的准入目标，不是把官方全量库或 HTML 规范塞入同一提示词仍能自动满足的事实。P0 记录实际生成大小；若扩目录超预算，收敛任务相关目录或显式修订预算，不能静默删掉用户需要的表达能力。
- 附加规则（`additionalRules`，中文）：
  - 采用下方“表达选择”规则，由模型结合用户目标和上下文主动选择 UI；用户不必输入“生成图表”或组件名。
  - 每个回答最多 4 个块；每块不超过 60 行。
  - 数据必须来自上下文，不得编造。
  - 不写 URL；引用来源时只用已出现过的来源链接。
  - 最终结论必须用文字写出，UI 只是辅助。

#### 6.4.1 表达选择提示词（用户确认借鉴 T3 的提示词驱动方式）

以下是 AILab 的提示词草案，实施时并入生成器的 `additionalRules`，不作为第二份独立运行时提示词。组件语法/schema 仍由目录生成；表达选择规则与目录合计受上述 2,500 token 预算约束。

```text
根据用户要理解或决定的问题，主动选择最容易读懂的表达方式；不要求用户先提出“使用 UI”。
涉及有足够数据的趋势、多个对象对比或排行时，优先用图表或表格帮助判断。
趋势用 LineChart；类别数量或排行用 BarChart；需要逐项核对精确数值或多个属性时用 Table。
只有数据确实构成同一整体且类别较少时才用 PieChart。少量关键指标可用 Metric；状态可用 Badge 或 Progress。
选项只有在能帮助用户采取下一步时才提供 Button，按钮文字应清楚说明将发送什么请求。
用户明确要求文字、表格或某种图表时，优先遵循。结构化目录适合时优先复用；需要自定义图解或模拟器且 HtmlArtifact 已准入时，可自由生成对应页面。未准入时说明边界，不虚构能力。
简单事实、简短解释、单项数值、翻译和普通改写直接用文字。不为装饰增加卡片、图表、Tabs 或按钮。
图表只能使用已有数据；保留单位、时间范围和必要的不确定性说明。数据不足时说明缺口，不凑数成图。
文字负责结论和解释，UI 负责可比较、可检查的信息；避免把同一份数据再完整重复一遍。
让布局表达层次，省去多余的二级标签和说明；必要的单位、图例和数据限制不能省略。
只调用当前目录开放的组件、函数和 action。自由 HTML/CSS/JS 只能放入已准入的 HtmlArtifact，不能注入宿主页面或借此发起未授权外部请求。已有内容足够时不额外增加 UI。
```

这是一套**模型表达策略**：由主模型自主选择，不引入 Jev、关键词路由、单独的 UI 选择模型或模型往返。上述类型映射是优先建议，组件名须与最终目录一起生成；不能遇到“对比”二字就机械出图。流程图/模拟器可通过扩展可信组件或 HtmlArtifact 表达，不把当前示例目录缺少某控件误说成 OpenUI 无法实现。

P6 的 30 条评测提示须覆盖：未明确要求图表但适合可视化、明确要求纯文字、单项数值、缺数据、非整体数据不宜饼图、精确值对照宜表格、重复展示与无用按钮。除语法合法率外，人工检查组件选择是否有助于理解、是否遵循用户表达偏好，以及标签/数据是否准确；沿用“不该出 UI”≤10%与数据一致率100%的门槛。提示词能否带来这些效果仍需评测，不以写入规则视为已实现。

### 6.5 Portal 渲染

**拆分**（`lib/answer-parts.ts`，纯函数）：

- 只识别行首的 ```` ```openui ````，结束标记是同等长度的行首围栏；嵌套在其它代码围栏里的不算。
- 正在流式的最后一行如果是 ```` ``` ```` 的前缀（例如 "```op"），先**暂缓**，等换行后再判断，避免闪出代码块。
- 输出 `{kind:"md"|"ui", text, closed, index}`。块 id 为 `${runId}:${step}:${index}`，由位置决定，稳定不变。
- 与 `markdown-chunks.ts` 协同：UI 块永远自成一个 chunk。

**适配接口**（`components/genui/index.tsx`，对外唯一入口）：

```ts
type GenUIBlockProps = {
  format: "openui";
  source: string;
  streaming: boolean;
  catalogVersion: number;
  ctx: { runId: string; sources: Source[]; artifacts: Artifact[]; readOnly: boolean };
  onAction: (a: GenUIAction) => void;
};
```

**内部行为**：

1. **懒加载**：渲染器、目录和图表依赖拆成独立 chunk（`next/dynamic`），只有出现第一个 UI 块时才加载；加载期间显示骨架。
2. **流式**：把 `source` 交给 OpenUI `Renderer`，设置 `isStreaming`，每帧最多重新解析一次（与 store 合批同频）。
3. **Last Valid Tree**：先验证锁定版本内置的增量解析、局部错误与组件级 last-good 回退，再对确实存在的空白/暂时错误补适配；不重复造一套求值器。目标是暂时错误不抹掉已显示内容，块闭合后再确定最终降级。HtmlArtifact 内容执行使用 §6.16 的独立生命周期，不能把树级回退当成 iframe 状态恢复。
4. **错误处理**：`onError` 只做计数和日志，**不回传给模型**（不做自动修正循环；V1 没有服务端修正通道）。块闭合后仍为空或有致命错误时，显示组件的 `toMarkdown` 降级结果，若降级也不可用，就显示一行"无法显示此内容"，带一个折叠的源码查看入口。
5. **隔离**：每个块包一层 ErrorBoundary，单块崩溃不影响该轮其它内容。
6. **首期不提供 `toolProvider`**：提示词不暴露 Query/Mutation；意外生成时按非法/未开放能力降级，不静默假装执行成功。OpenUI 原生支持它们，后续仅可通过 API 鉴权适配器开放。绑定、纯同步计算函数、本地 action 由正式目录明确注册；未知 action 拒绝并计数。
7. **限额**：每个回答最多渲染 8 个块，每块源码 ≤ 16k 字；超出部分按降级显示。

### 6.6 组件目录：基础示例与扩展规则

下表是原稿的基础示例，包含15种展示组件及 Series/Tab 等辅助结构，**不是正式目录总数，也不是表达能力上限**。固定上游 `1492107c8d24805a19651ad57420156f702bbda5` 的完整 `openuiLibrary` 注册82个条目（包含辅助结构），已有表单、Slider、EditableTable、散点/雷达等图表、Accordion、Carousel、Modal；参见[源码比较](../../reviews/2026-10-28-openintelligentui-vs-openui.md)。这些是静态源码事实，不能当成本项目已接入或质量已验证。

P0 按真实场景选择、复用或适配官方组件，统一 AILab token、中文文案、资源授权与 `toMarkdown`，避免全量打包。需要时增加绑定、受控计算函数或 HtmlArtifact；正式组件名/参数/schema、依赖和状态契约在准入后固化为 catalog v1，不能照抄本表就宣布目录完成。官方包风格/底层原语若与已确认的 Base UI 体系冲突，保留协议和能力并适配实现，不额外引入第二套通用原语。

| 组类 | 组件 | 关键 props（位置参数顺序） | toMarkdown |
|---|---|---|---|
| 布局 | `Stack` | children[], direction?(`col`/`row`), gap?(`sm`/`md`/`lg`) | 依次拼接子节点 |
| | `Grid` | children[], columns?(1–4)；手机端自动单列 | 依次拼接子节点 |
| | `Card` | children[], title? | `**title**` 加子节点 |
| 文本 | `Heading` | text, level?(2–4) | `### text` |
| | `Text` | text, tone?(`default`/`muted`) | 原文 |
| | `Badge` | text, tone?(`neutral`/`success`/`warning`/`danger`/`info`) | `` `text` `` |
| | `Callout` | text, tone?, title? | `> **title** text` |
| 数据 | `Metric` | label, value(number/string), unit?, delta?(number), hint? | `- label：value unit（±delta）` |
| | `Table` | columns[], rows[][]（≤ 200 行；超过时截断并标出总数） | GFM 表格 |
| | `Progress` | label, value(0–100) | `- label：value%` |
| 图表 | `LineChart` | title, categories[], series[] | 转成表格 |
| | `BarChart` | title, categories[], series[], horizontal? | 转成表格 |
| | `PieChart` | title, labels[], values[] | 转成表格 |
| | `Series` | name, values[]（只作为图表的参数） | — |
| 交互 | `Tabs` | tabs[]（`Tab(label, children[])`）；本地状态；打印时展开全部 | 每个 tab 一个小标题 |
| | `Button` | label, action（只允许 `@ToAssistant`） | `- label` |

约束：

- 所有颜色来自 token；图表用固定的分类色板（P4 用 dataviz 规范校验明暗两种模式下的对比度）。
- 基础结构化组件不直接暴露任意 html/style/className 或未校验 URL；资源组件只接受 API 授权的引用。HtmlArtifact 的 document 是明确例外，只流向隔离 renderer，不传给宿主 DOM。
- 数值统一用 `Intl.NumberFormat("zh-CN")` 格式化。

**需要另定业务契约的扩展**：`SourceCard(url)`、`FileCard("ailab-file://…")`、`ArtifactCard(filename)`、`DataRef` 和状态持久化。Select、Form、Slider、交互筛选等不再仅因旧目录没有而一律排到 V2；P0 按任务确定首期范围。HtmlArtifact 按 §6.16 独立准入。

### 6.7 资源引用（V2 的组件，V1 先定规则）

| 引用 | 模型写什么 | 如何解析 | 解析失败 |
|---|---|---|---|
| 网页来源 | 已出现过的来源 URL | 与 `ctx.sources`（流式时按与服务端**同一规则**，即 URL 去重加首次顺序）精确匹配 | 降级为纯文本，不可点击 |
| 资料库文件 | `ailab-file://<file_id>` | Portal 走现有的文件接口，由 API 按当前用户鉴权 | 404 时显示"不可用" |
| 本轮产物 | 文件名 | 与 `ctx.artifacts` 中的 `filename` 匹配 | 不渲染 |
| 数据集（DataRef） | 产物文件名或 `ailab-file://`，加上 sheet 名 | 复用 Worker 已生成的 `derived/{file_id}/sheets.json`（`FilePreview.kind="sheets"`），有界且已缓存，**不新增解析器** | 显示"数据不可用" |

### 6.8 Action

- `@ToAssistant(text)`：Portal 调用 composer 的同一个 `send()`，模式取**当前会话的 composer 模式**。如果当前有 Run 正在运行，就按现有规则进入排队或插话。用户气泡显示 `text`。额度、并发和鉴权全部沿用现有链路，不新增后端端点。
- 只读环境（分享页 `s/[id]`、打印页、旧分支版本）下 `readOnly=true`：按钮显示为禁用，不隐藏，以保持版式。
- 防重复：同一个块的同一个按钮在发送进行中禁用；发送成功后按钮保持可再次点击（与重新输入同一句话等价）。
- 首期临时状态（Tabs 当前页、表格排序、绑定值）只在内存里，刷新回到默认值，这是 AILab 的范围选择。OpenUI 已有 `onStateUpdate` / `initialState` 及消息 context 支持宿主保存恢复状态；若开放持久化，需定义有界数据、版本与 API 授权，不能声称库不支持。HtmlArtifact 控件状态同样不会自动落库或成为模型上下文。
  - 实施记录（2026-10-30）：已按用户要求持久化，接口为 `PUT /v1/runs/{id}/ui-state`，有界 JSON 存在 `runs.ui_state` 列（不放 `result`，避免改写终态结果）。刷新、分享和打印都从保存的状态开始，状态不进入模型上下文。详见 ADR 0028。

### 6.9 后端改动清单（小）

| 文件 | 改动 |
|---|---|
| `catalog.py` | `capabilities` 允许值增加 `"genui"`；`models.toml` 注释同步 |
| `config.py` | `genui_enabled: bool = False`、`genui_max_blocks: int = 8`（1–20）、`genui_max_block_chars: int = 16000`（1k–64k），校验范围 |
| `config.py` / 目录生成与 Portal | P4-H增加独立HTML准入配置（默认关闭）；服务端提示词/能力输出与Portal执行检查一致。块大小、降级、历史版本和只读规则按§6.16固化，不能只隐藏菜单或删除提示词来限制执行 |

> 实施记录（2026-10-30）：`AILAB_GENUI_HTML_ENABLED` 按用户“GenUI 默认启用”的要求默认开启，可显式关闭；关闭时提示词不含 HtmlArtifact，Portal 只显示 fallbackText。
| `contracts/genui/` | 生成的 `openui-v1.prompt.md`、`catalog-v1.json`，以及 `__init__.py`（加载并校验 sha256；Python 中的 `FORMAT="openui"`、`CATALOG_VERSION=1`） |
| `domain/answer_parts.py`（新，纯函数） | `split(text)`、`inspect(text) -> {blocks, unclosed, oversize, components}`、`for_history(text, keep_ui: bool, limit: int)` |
| `agent/prompts.py` | `system_prompt_for` 在 genui 开启且模型具备 genui 能力时追加提示词；Chat 和 Work 都追加 |
| `agent/loop.py` | step 结束时调用 `inspect` 并记录指标；完成时如果 `blocks>0`，写入 `result.genui={"format":"openui","catalog":1,"blocks":n}` |
| `api/routes_internal.py`、`api/routes_model_context.py` | 历史回复改用 `for_history`（§6.10），替换 `reply[:HISTORY_CHARS]` 盲截 |
| 标题生成、会话摘要压缩的输入 | 用 `for_history(keep_ui=False)`，UI 块换成占位文本 |
| `api/routes_conversations.py:128` | 白名单增加 `"genui"` |
| 分享 `ShareView` | summary 原样返回（Portal 端渲染），不改 |
| mock 模型 | 当 prompt 含测试标记时输出脚本化的 GenUI 回答（多块、跨 delta 切开围栏、半截块、非法组件），用于 E2E |

**无迁移**：不新增表或列（`result` 是 JSON）。**用量账本无变化**（没有新增模型调用）。

### 6.10 历史、多轮编辑与降级文本

`for_history(text, keep_ui, limit)` 的规则：

1. **上一轮**（离当前最近的一轮）：`keep_ui=True`，围栏原样保留，这样"把柱状图改成饼图"之类的编辑请求能看到源码。
2. **更早的轮次**：每个围栏替换成 `[界面：BarChart、Metric×3]` 这样的占位，文字保留。
3. 截断永远发生在片段边界上：放不下的 UI 块整块换成占位，绝不切开围栏。
4. Python 和 TS 两侧的拆分规则共用夹具 `platform/tests/fixtures/genui/*.md`，双方测试读取同一组输入并比对输出。

Portal 侧：

- **复制**：复制按钮输出 `toMarkdown` 降级后的纯 Markdown。
- **分享页**：完整渲染，只读。
- **打印 / PDF**：完整渲染，图表输出 SVG；Tabs 展开全部；关闭懒加载，在打印前等待 GenUI chunk 加载完成。
- **搜索**：会话搜索如果索引了 summary，需要对 summary 中的围栏做占位处理（P3 核对搜索实现）。

### 6.11 版本

- `catalog_version` 只记录在 Run 上（`result.genui.catalog`）。正在流式的 Run 使用当前版本。
- **兼容规则**：同一版本内只允许**新增可选 prop 或新增组件**。删除、重命名或改变位置参数顺序必须升版本。
- Portal 同时注册当前版本和上一个版本的目录（旧版本可以是对新版本的映射层）；更旧的版本降级为 `toMarkdown`，再不行就显示源码。
- 提示词文件按版本保留，`openui-v1.prompt.md` 永不修改，只新增 `v2`。

### 6.12 安全

| 威胁 | 防线 |
|---|---|
| 脚本越过执行边界 | 结构化组件与 Markdown 中的 `<script>`、`<img onerror>`、`javascript:` 不执行；只有已准入 HtmlArtifact 的隔离文档可运行生成脚本，不能进入宿主 DOM。自由 HTML 的 CSP/网络/bridge 按 §6.16 验证 |
| 越权访问资源 | 引用只走注册表；文件、产物由 API 按会话主体鉴权（模型写别人的 file_id 只会得到 404） |
| 诱导点击外链 | V1 没有任何外链组件；V2 的 `SourceCard` 只匹配本轮已校验过的来源 |
| Action 滥用 | 首期对外仅开放 `@ToAssistant`，走已有额度/并发/审计；本地绑定与计算不获得服务权限。没有 toolProvider；HtmlArtifact bridge额外执行§6.16的来源、能力与用户意图校验 |
| 资源耗尽（超大块） | 每块 ≤ 16k 字、每回答 ≤ 8 块、表格 ≤ 200 行、图表 ≤ 500 点；解析按帧节流 |
| 纵深防御 | P5 为 Portal 加 CSP（`script-src 'self' 'nonce-…'`，`object-src 'none'`，`frame-ancestors 'none'`），实现方式按 Next 16 文档的 proxy nonce 方案 |

secure profile：GenUI 不引入新的不安全默认值。启用时，若 `contracts/genui` 的提示词 sha256 校验失败则拒绝启动，防止提示词被篡改或不完整。

### 6.13 失败与降级

| 情况 | 行为 |
|---|---|
| 模型没有 genui 能力或功能关闭 | 提示词不出现目录，回答为 Markdown；意外的 openui 围栏只显示源码/降级，不绕过功能或 HTML 执行准入开关 |
| 块流式中途解析失败 | 继续显示 Last Valid Tree |
| 块闭合后非法 | 显示 `toMarkdown` 降级；再不行显示"无法显示此内容"加折叠源码 |
| 回答结束时围栏未闭合（截断） | 结构化路径可做最终容错解析并降级；未完整确认的HtmlArtifact只保留源码/文本降级，不启动生成脚本 |
| GenUI chunk 加载失败 | 显示源码，并提供重试 |
| 单块渲染异常 | 被 ErrorBoundary 捕获，只影响这一块 |

### 6.14 可观测性（标签低基数）

服务端（Agent，在 step 结束时统计）：

- `ailab_genui_blocks_total{mode, outcome=closed|unclosed|oversize|over_limit}`
- `ailab_genui_components_total{component}`：标签取自目录里的组件名，不在目录里的计为 `unknown`
- `ailab_genui_answers_total{mode, has_ui}`：UI 使用率

客户端（Portal → API 批量上报，V1 最小集）：

- `POST /v1/genui/telemetry`：一次最多 50 条 `{outcome: rendered|fallback|error|action, component?, first_render_ms?}`。API 只累加 Prometheus 计数和直方图（`ailab_genui_client_total{outcome}`、`ailab_genui_first_render_seconds`），**不入库、不记录 id**；按用户限速 10 次/分钟。
- 成本：每个块一次计数加一，可忽略。

### 6.15 成本

| 项 | 成本 |
|---|---|
| 服务端 CPU / RSS | 每个 step 一次 O(n) 扫描；没有新进程，没有新依赖 |
| 模型 token | 首期目录提示词预算≤2.5k，可缓存性以提供商为准；输出长度由目录/任务决定，HTML/JS可能远超简单组件。没有 Jev 或独立生成调用；不能用官方相对JSON的token宣传推断相对HTML更省 |
| 存储 | 围栏文字进入 summary，与普通回答相同 |
| 客户端 | GenUI chunk 只在首次出现块时加载（预算见 §10） |

### 6.16 OpenUI 的自由 HTML/CSS/JS 扩展

**保留同一 OpenUI 基础**，注册可信 `HtmlArtifact` 作为自由页面的承载组件，不迁移到 OpenIntelligentUI/CopilotKit，也不需要 Jev。官方[示例](https://github.com/thesysdev/openui/blob/1492107c8d24805a19651ad57420156f702bbda5/examples/miscellaneous/html-artifact/src/html-artifact.tsx)使用 `title` / `document`，document 可含完整 HTML/CSS/JS，最终放入 `sandbox="allow-scripts"` 的 iframe。这证明框架可扩展，不表示本项目已有生产级实现。

- **两种流式必须区分**：结构化 OpenUI Lang 可在生成时出现有效组件；官方 HtmlArtifact 示例只有源码流式，完成后才运行页面。自由 HTML 的渐进预览需要额外适配，不能以源码增长、骨架或动画冒充已实现。
- **设计方向**：外层仍用 openui 围栏与统一组件目录；模型按需选择 HtmlArtifact。AILab 包装器还需提供有界的 `fallbackText`（与 title/document 一起纳入版本化 schema），供复制、搜索、分享和打印失败时使用；不通过另一个模型临时生成降级。正式 schema 和字符限额在 P0 Spike C 固化，遵循配置最大值，不通过 HTML 绕过原块大小限制。
- **执行边界**：HTML/JS 只在独立 iframe 环境运行，不能带 allow-same-origin 或持有模型key、会话cookie、Work沙箱凭据、内部API权限。先定义 CSP、允许的依赖/资源与受限宿主 bridge；不能把 sandbox 属性或提示词当成网络禁用和用户操作授权。
- **CSP 与渐进执行**：`srcdoc` 会继承 Portal CSP，严格 nonce 与生成内联脚本可能冲突。P0 必须选定可验证的隔离文档装载方式；不靠放宽整个 Portal 的 script-src 解决。渐进预览在不完整 markup 上不执行生成脚本，只有完整文档才初始化交互；完整页面更新是否重挂载、控件状态如何处理须明示。
- **生命周期**：生成内容随 summary 保存；刷新可重建文档，后续修改生成新版本，不承诺旧DOM原地patch。只读分享/打印/旧版本不给发送、工具、剪贴板等宿主写能力。纯本地交互和对外副作用分开，bridge不因frame发送消息就视为用户授权。
- **交付/资源**：关闭或卸载销毁iframe与任务；离屏暂停/恢复需避免历史页面继续运行定时器/WebGL。导出允许有声明的静态/文本降级，不能承诺任意Canvas/3D在PDF中原样恢复。记录首预览、交互就绪、客户端CPU/内存/iframe数量、依赖离线失败和服务端成本，不套用结构化chunk/RSS预算。

这是同一选型下需要设计、Spike和独立验收的能力，不一律推到V3，也不因本次文档更新自动开放执行。**若只有结构化路径通过，必须明确交付边界，不能标记自由HTML已完成。**

---

## 7. Chat 与 Work 的差异汇总

| | Chat | Work |
|---|---|---|
| 提示词 | `CHAT_MODE_NOTE` + GenUI | Work 系统提示 + GenUI |
| 回答 | 全文（单个 step） | 最后一个 step；中间 step 的 UI 显示在过程里 |
| 数据来源 | 上下文、网页、资料库，数据内联在块里 | 同 Chat；V2 可以通过 DataRef 引用沙箱产出的 CSV/XLSX（经 Worker 预览） |
| Action | `@ToAssistant` 以当前 composer 模式发送 | 同左 |

---

## 8. 模块边界

- GenUI 涉及 Agent（提示词、指标）、API（历史、白名单、遥测）和 Portal。**Broker、Worker、Work服务端沙箱零改动**；浏览器HtmlArtifact隔离独立实现（V2 的 DataRef 只读取 Worker 已有的产出）。
- `contracts/genui` 只放生成的产物和常量，不 import `models.py`。
- 全仓库只有 `components/genui/openui/` 一处 import `@openuidev/*`，替换格式时只动这个目录。

---

## 9. 实施阶段

每个阶段：在独立分支上做，小步提交，单独评审；阶段末尾按 AGENTS 更新 `current.md` 和当天的 update 文件。**下一阶段需要用户授权才能开始。**

### P0 基础与 Spike（闸门）

1. 安装依赖并锁定精确版本：tailwindcss v4、@tailwindcss/postcss、shadcn init（Base UI）、motion、lucide-react、streamdown、@openuidev/react-lang、zod、recharts、zustand、@tanstack/react-query、vitest、@testing-library/react、jsdom。动手前先读 `node_modules/next/dist/docs/` 中与 CSS、proxy 相关的说明。
2. 按 §4.5 实现 Tailwind 与旧 CSS 的共存，生成兼容 `styles/tokens.css`；此时**全站像素级不变**（现有 E2E 全绿，再加截图对比）。另按 §4.8–4.9 完成行为盘点、代表屏方向稿与目标 token 规范，审阅通过后供 P2/P5 使用，不能直接把兼容 token 当成新视觉交付。
3. 测试 runner 迁移到 Vitest。
4. 建立性能基准脚本，记录**基线**：500 轮、40 万字、流式 2000 字的场景，指标包括输入到下一帧的 p95、DOM 数、>50ms 长任务数、流式期间帧时间 p95（加 4× CPU 节流复验）、chat 路由首包 JS gz。
5. Spike A：Streamdown 渲染 40 万字（配合现有分块）对比自研解析器，要求性能不劣于自研的 10%，并且引用、pill、角标都能通过 `components` 覆盖实现。不达标就改用 `react-markdown + remark-gfm`。
6. Spike B：锁定 OpenUI 版本，用多个目录组件验证跨 delta 切块、围栏闭合前有效组件可见、追加时身份稳定、增量解析与 last-good 回退；再验证绑定/计算/状态接口、目录扩展、`library.prompt()` 体积和懒加载大小。产出正式首期目录，不把原示例数量作为上限。
7. 交付 ADR：`0019-portal-ui-stack.md`（采用 shadcn/Tailwind/Motion，不采用 assistant-ui）和 `0020-generative-ui.md`（本文第 6 节的决策）。
8. 更新 `portal/AGENTS.md`：依赖规则、E2E 钩子类名契约、图标门面、§4.1.1 的 registry 引入规则和"角色 → 组件 → 来源"表。在 `components.json` 中登记 Origin/COSS、Motion Primitives、Cult UI、Magic UI 的 registry 地址，并逐个核对许可证（只用 MIT 免费层）。同步 `/AGENTS.md` §8 中"no UI library"这一条。
9. Spike C：对照官方 HtmlArtifact 验证自由 HTML/CSS/JS 承载；明确源码流式、渐进预览和交互就绪三个时刻。固化 document/fallbackText 限额、版本化 schema、CSP装载、网络/依赖策略、bridge权限和生命周期；验证截断不执行、隔离、重建/修改、只读、打印降级及CPU/内存成本。若准入未完成，记录阻塞与后续动作，不能当作 OpenUI 不支持或宣布 HTML 路径已完成。

**闸门**：Spike A、B 达标，基线与正式首期目录已记录，兼容层全站无视觉变化，目标视觉方向与功能等价清单已审阅；Spike C 的验证结果和自由HTML交付边界必须审阅明确。C未通过时可单独评审结构化路径，不自动带入HTML执行。

### P1 流式管线（只改性能，不改视觉）

- 按 §4.3 实现第 1–5 条：run-store、rAF 合批、结构共享 reducer、memo 与稳定回调、单 Run 刷新、SmoothText。
- 验收：§10 中的流式指标全部达标；E2E 全绿；新增单测（引用保持不变、合批、平滑文字）。

### P2 聊天界面重构

- 先按 §4.6 的已审阅方向重构应用壳、导航、聊天阅读区、Composer 与预览空间层级；不能只替换组件标签或保留旧卡片布局换配色。动效按 §4.1.1/§4.6.5 需要选择，不批量安装效果组件。
- Turn、WorkProcess、StepRow、Composer（模式切换、附件、技能、模板）、Sources（含手机抽屉）、PreviewPane、QueueStrip、对话菜单、fork 菜单、版本切换、Markdown（换成 Streamdown）、文件卡，全部改用 shadcn、Tailwind 和 Motion，并保留钩子类名。
- 删除 `legacy.css` 中的对应段。
- 验收：§4.8 中本阶段全部功能等价，按 §4.9 集中做真实浏览器视觉/交互/主题/无障碍检查；E2E 全绿；500 轮与 40 万字回归不劣于 P1 的数值。

### P3 GenUI 后端（可与 P2 并行）

- 实现 §6.9 的全部改动，以及 Python 侧的 `answer_parts` 和共享夹具；暂时提交一个手写的占位提示词文件（P4 用生成的版本替换）。
- 测试：围栏切分（嵌套代码、半截块、未闭合、超限）、`for_history` 不切开围栏、上一轮保留与更早轮次占位、白名单放行 `genui`、标题和摘要输入中没有 UI 源码、capability 校验、配置范围、sha256 校验、指标标签有界。
- 验收：Ruff 通过；SQLite 和 PG 全套通过；迁移漂移测试无变化。

### P4 GenUI Portal

- 实现 `answer-parts.ts`（共享夹具）、`GenUIBlock` 适配层、OpenUI 适配器、P0确定的正式目录（含 schema、实现、`toMarkdown` 和骨架）、Action、只读模式、打印与分享、复制降级、遥测上报、`genui:prompt` 脚本与漂移测试。绑定/计算依所选目录落地，不把框架已有能力全部重写。
- E2E（使用 mock 模型）：
  - 一个回答中有 3 个块与文字交错，按顺序流式出现；
  - SSE 中途断开后重连，结果与不断开相同；
  - 刷新和历史加载与流式时一致；
  - 半截块显示骨架，非法块显示降级，其余内容不受影响；
  - `@ToAssistant` 发送新消息，运行中点击则进入排队；
  - 分享页只读，打印包含图表 SVG；
  - Work 模式中间 step 的 UI 显示在过程里，最终回答的 UI 显示在正文；
  - 手机 390×844 下 Grid 自动变单列；
  - 注入用例（script、onerror、javascript:）只显示为文本。
- 验收：以上用例全部通过；GenUI chunk 只在出现块时加载；§10 的 GenUI 预算达标。

### P4-H 自由 HTML 承载与渐进预览（独立准入）

- 在 OpenUI 目录内实现 §6.16 的 HtmlArtifact，复用正文delta/summary；在P0 Spike C确认的装载方式中隔离执行，不引入独立Agent或Jev。
- 验收必须覆盖：不完整document无生成脚本执行、可见预览与交互就绪时刻、更新/刷新重建、消息来源/权限检查、网络限制、错误降级、手机、分享只读和打印。独立测试500轮/多个HTML块的iframe与任务释放；报告服务端与客户端成本。
- 本阶段需单独实施授权。未通过或未授权时提示词与目录均不开放HtmlArtifact，仅显示源码/降级；不把结构化P4通过算作此阶段完成。
- 实施记录（2026-10-30）：用户已授权并实施。装载方式为同源 `app/frame/[kind]` 路由加 `sandbox="allow-scripts"`，文档经 postMessage 投递；书写中用 preview（只放行壳脚本 hash），闭合后用 run（`default-src 'none'`，无网络）。截断的块不执行；页面导航被父页 `frame-src` 拦截后停止；离开视口卸载。bridge 只回传高度，不开放其它能力。详见 ADR 0028。

### P5 其余页面、收尾与安全

- 按 §4.6 将其余页面纳入同一视觉系统；业务表单、表格、上传、日期等优先取自 Origin/COSS。认证/空状态以完成任务为先，不预设 Magic UI 装饰。
- P2 已迁移的应用壳/侧栏统一复核；⌘K（换成 shadcn Command）、文件库、项目、技能、记忆、定时任务、账号、管理台、登录注册迁到新组件，请求/响应资源按 §5 接入 TanStack Query。
- 清空 `legacy.css`，启用 preflight，加 CSP。
- 验收：§4.8 全清单与 §4.9 全站视觉/主题/无障碍证据完整；E2E 全绿；全套 Python 测试、Portal 测试/构建、Ruff 通过；首包 JS 不超预算。清空 legacy 后再检查打印与所有未改页面，不能以 CSS 行数下降代替验收。

### P6 真实模型评测与灰度（需付费授权）

- 评测集 `platform/evals/genui/`：30 个提示（对比、趋势、排行、选项、不应该出 UI 的普通问答、多轮"改成饼图"），在每个准备开启 genui 的模型上运行。
- 指标与门槛：闭合块合法率 ≥ 95%；"不该出 UI 却出了"≤ 10%；多轮编辑成功率 ≥ 80%；数据与上下文严格一致率 100%（人工核对：块合法且显示正确、整份回答无错误陈述；编辑须完成请求，控件/HTML 保留原门槛）。正确手写数字或块外复述仅作诊断；无依据的事实断言、数据矛盾、夸大差距或错误 UI 派生值均失败，明确标记的推测除外。
- 达标后才给该模型的 `models.toml` 加上 `genui`，并把 `genui_enabled` 设为 true。不达标就调整提示词（升级为 v2 提示词文件）；如果仍不达标，评估 §13 的两段式备选方案。

### V2（另行授权）

V2 的业务扩展范围：`SourceCard`、`FileCard`、`ArtifactCard`、`DataRef`（复用 sheets.json），以及组件状态持久化（拟 `PUT /v1/runs/{id}/ui-state`，有界 JSON，存在 `result.ui_state` 中）。表单、筛选、Timeline等按P0正式目录确认，不再因为原例子没有而统一延期；HtmlArtifact按P4-H验收，不视作只能V3实现。

---

## 10. 性能预算与验收（本机测量，并以 4× CPU 节流复验；不冒充目标机的结论）

| 指标 | 预算 |
|---|---|
| 500 轮会话：输入到下一帧 p95 | ≤ P0 基线 × 1.1（参考值 14.5–23.3ms） |
| 500 轮会话：DOM 节点数 | ≤ P0 基线 × 1.2（参考值 826） |
| 40 万字回答的首尾全文与滚动 | 现有断言全部保持 |
| 流式（约 40 tok/s，共 2000 字） | 主线程 >50ms 长任务数为 0；帧时间 p95 ≤ 16.7ms（4× 节流下 ≤ 33ms）；每帧 store 更新 ≤ 1 次；非流式轮次的渲染次数为 0 |
| 文字可见延迟 | 后端 delta 到达后，最多 150ms 内全部可见 |
| chat 路由首包 JS（gz） | ≤ P0 基线 + 80 KB |
| GenUI 懒加载 chunk（gz） | 渲染器加目录 ≤ 60 KB；图表单独一个 chunk，≤ 110 KB |
| GenUI 首个骨架 | 围栏头到达后的下一帧出现 |
| GenUI 块解析 | 16k 字的块，单次解析 ≤ 8ms（本机） |
| HtmlArtifact | P0-C记录首预览/交互就绪、包体、iframe/任务数量和客户端CPU/内存，并审定预算；P4-H据此验收，不能沿用结构化解析指标冒充HTML运行成本 |
| 服务端 | 不新增后台任务；Agent 每个 step 一次 O(n) 扫描；RSS 无可测变化 |

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| OpenUI 1.0 很新，API 可能变动 | 锁定精确版本；只在适配器目录内 import；Spike B 为准入；退路是同一接口下实现一个自研的 JSONL 格式 |
| DeepSeek 等模型写 UI 的质量不稳定 | 按模型逐个开启 capability；P6 评测设门槛；有 Last Valid Tree 和降级兜底 |
| 模型滥用 UI | 提示词规则；评测"不该出 UI"比例；指标监控 UI 使用率 |
| Tailwind 与旧 CSS 冲突 | cascade layer 加暂不启用 preflight；P0 要求像素级不变 |
| E2E 大面积失效 | 钩子类名契约；每阶段都以 E2E 全绿为闸门 |
| 首包体积增长 | Motion 用 LazyMotion；图表和 GenUI 懒加载；shiki 和 KaTeX 懒加载；每阶段对比预算 |
| Streamdown 在超长文本上的性能 | 保留分块和 ViewportBlock；Spike A 为准入；退路是 react-markdown |
| 历史中的 UI 源码增加 token | 只完整保留上一轮，更早轮次用占位 |
| 多个 registry 风格不一、重复造轮子、混入 Radix 或额外动画库 | §4.1.1 的规则：单一原语、只用 MIT、依赖白名单、改成我们的 token、一种角色一个实现；PR 评审时对照"角色表" |
| 新视觉变成“换库换皮”或删掉已有操作 | §4.6 统一构图/排版/token；§4.8 逐项功能等价，§4.9 真实浏览器证据；不以代码行数宣告成功 |
| 炫技动效拖慢聊天 | §4.1.1 第 5 条的性能红线；每阶段对比 §10 预算 |
| shadcn 抽屉原语的维护状态 | P0 确认；退路是 Motion drag |

---

## 12. 待用户决定

| # | 问题 | 默认建议 |
|---|---|---|
| D1 | 组件库选型 | 已确认：shadcn/ui（Base UI）为基础，Origin/COSS、Motion Primitives、Cult UI、Magic UI 作为 registry 来源（§4.1.1） |
| D-G | GenUI基础与表达选择 | 2026-10-29已确认：保留thesysdev/openui；主模型自主选择表达，不引入Jev。自由HtmlArtifact按§6.16/P4-H独立准入；这不等于实施授权 |
| D2 | 视觉重构范围与方向 | **范围已明确**：全站视觉重构，已有功能等价，遵循 Impeccable；§4.6 为建议视觉系统。P0 审阅代表屏与 token 后确定最终方向，不再以“沿用旧视觉”作为默认 |
| D3 | 不采用 assistant-ui（偏离冻结架构，需要写 ADR） | 不采用 |
| D4 | GenUI 首批开启的模型，以及 P6 付费评测的额度 | 先评测当前默认模型；额度由你确定 |
| D5 | 阶段顺序：P2（界面）与 P3（GenUI 后端）是否并行 | 并行 |
| D6 | V1 是否需要 `SourceCard` | 不需要（来源面板已有），放 V2 |

---

## 13. 备选方案与否决理由

| 方案 | 否决理由 | 何时重新考虑 |
|---|---|---|
| `present_ui` + 独立 UI 生成模型（初版） | 每个块多 1 次主模型往返加 1 次生成器调用；回答被工具调用切碎；需要新的流式和计量路径；续跑语义复杂 | P6 评测中主模型合法率 < 95%，并且换便宜的专用模型确实更好 |
| 独立的 `agent.ui.*` 事件 | 重复了 delta 的职责；排序、合批和双份存储都要额外处理 | 将来出现**不由模型文本产生**的 UI（例如工具直接产出的卡片）时，再加一个 `agent.ui` 事件，复用同一个渲染器 |
| A2UI 作为 V1 格式 | JSON 格式 token 更多；协议以 surface 和消息为单位，适合跨应用的 agent 协议，与"嵌在文本流中"不匹配；v1.0 仍是 RC | 需要与外部 Agent 或第三方客户端互通时；OpenUI 有 `@openuidev/a2ui`，届时在适配层加一种格式即可 |
| 用 OpenIntelligentUI 整套替换 OpenUI | 用户已确认保留OpenUI；自由HTML可经HtmlArtifact扩展，替换整个应用还需适配Agent/Run/计费与工具参数流 | 只有实际评测证明OpenUI扩展无法满足要求时另行评审；其renderer/提示词可作参考 |
| 任意 React/JS 直接运行于 Portal 宿主 DOM | 破坏会话与业务权限边界 | 不采用。隔离HtmlArtifact是§6.16的设计路径，不在此否决 |
| Jev 或独立表达选择模型 | 主模型已可通过提示词自主选择；额外调用增加费用、时延和失败路径 | 仅当同题评测证明主模型选择长期不达标且独立路由收益明确时再评审 |
| 自研 DSL | 解析器、流式容错和提示词生成都要自己维护 | OpenUI 的 Spike 失败时作为退路 |
| assistant-ui 全量接入 | 见 §4.1 | 如果将来放弃以 Run 为中心的数据模型 |

---

## 14. 非目标（V1）

- 宿主DOM内执行模型代码、无限制网络/URL/工具权限、任意React工程依赖安装；隔离HtmlArtifact是P4-H独立准入项，不属于此处一概排除的能力。
- 组件/HTML控件状态持久化、DataRef及新的业务写入契约（属于后续扩展）；本地绑定/计算与表单组件不等于业务写入。
- 自动修正循环（把解析错误回传给模型）。
- 手动的深色/浅色切换（只预留接口）。
- 部署、上线和远端发布（另行授权）。

---

## 15. T3 Code Visual replies 源码调研（2026-10-28）

调研固定在公开仓库 `pingdotgg/t3code@5211acfe95a0c6d82a60e2524b5e46b47fe67a3e`；仅静态阅读源码和官方文档，未运行上游。用户所说的 visualize / intelligent UI，在本次核对的文档中对应 **Visual replies**，另有独立的 **MCP Apps**；不假定这两个称呼是上游正式模块名。

### 15.1 实际机制与 streaming 边界

- Agent 被提示在趋势、对比等回答中主动生成 HTML，先 `html_preview` 后 `html_render`。这由 [orchestrationInstructions.ts](https://github.com/pingdotgg/t3code/blob/5211acfe95a0c6d82a60e2524b5e46b47fe67a3e/packages/provider-core/src/server/orchestrationInstructions.ts) 中的运行指令驱动，没有在这条路径中发现独立的 UI 生成模型。
- 两个工具都接收**完整、自包含的 HTML 字符串**（最多 512,000 字符），允许内联 CSS/JavaScript；不是组件目录 DSL。[工具 schema](https://github.com/pingdotgg/t3code/blob/5211acfe95a0c6d82a60e2524b5e46b47fe67a3e/apps/server/src/mcp/toolkits/html/tools.ts)。
- `html_preview` 在 headless Chrome 中运行页面，返回截图、内容高度、console；Agent 可以据此修正。`html_render` 执行 prepare，内联本地图片、注入 bootstrap，将 HTML 写成附件，测量多宽度高度后返回 `{attachmentId,title,height,heights?}`。[服务实现](https://github.com/pingdotgg/t3code/blob/5211acfe95a0c6d82a60e2524b5e46b47fe67a3e/apps/server/src/htmlRender/HtmlRender.ts)。这是同一 Agent 的工具工作流；不等同于初版 HLD 的专用 UI Generator 模型，但会增加工具往返和可选预览修正成本。
- 前端用附件 URL 加载 `HtmlRenderDocument`；首次加载后固定 URL，避免重新签发 URL 导致页面重载。[HtmlRenderFrame.tsx](https://github.com/pingdotgg/t3code/blob/5211acfe95a0c6d82a60e2524b5e46b47fe67a3e/apps/web/src/components/chat/HtmlRenderFrame.tsx)。
- 因而这条链路是**完整页面发布后展示**，没有这里所需的 token → 部分组件树增量协议。页面自身可以用 JS 做交互、动画和局部更新；不能由此称为模型生成过程中的 streaming UI。也不能从此推断整个 T3 Code 没有其它流式能力。

### 15.2 交互、安全与成本

- iframe 是 `sandbox="allow-scripts allow-forms"`，刻意不加 `allow-same-origin`，保持 opaque origin。宿主先检查 `event.source === frame.contentWindow`，再处理高度、链接、图片和快捷键消息；需要用户交互的消息另有焦点/userActivation 检查。[BrowserDocumentFrame.tsx](https://github.com/pingdotgg/t3code/blob/5211acfe95a0c6d82a60e2524b5e46b47fe67a3e/apps/web/src/components/files/BrowserDocumentFrame.tsx)。这是执行生成代码的隔离方案，可供§6.16参考；iframe sandbox本身不等于禁止网络访问，不能直接沿用结构化路径的安全假设。
- bootstrap 在首次绘制前注入主题，后续用 `ui/notifications/host-context-changed` 同步 token；用 ResizeObserver 和 `ui/notifications/size-changed` 回传真实内容高度。[主题与 bridge](https://github.com/pingdotgg/t3code/blob/5211acfe95a0c6d82a60e2524b5e46b47fe67a3e/packages/shared/src/htmlRender.ts)。服务拒绝 vh 类单位以避免自适应高度反馈循环；客户端高度优先于服务端测量，避免字体差异产生嵌套滚动。
- 每张本地图片上限 10 MiB，内联后页面上限 25 MiB；Chrome 并发限制为 2，发布测高超时 6 秒，测高失败仍可发布。官方文档说明首次下载浏览器约 120 MB。这些是源码限制/文档描述，不是 2 vCPU/8 GB 的实测容量；不应把 headless Chrome 加进 AILab API/Worker 来复刻此功能。
- **MCP Apps 是另一条链路**：工具结果携带 app，app 可调用自身服务器工具、向会话发消息，并有独立确认及生命周期要求；官方文档称当前 Codex 支持。这不能自动赋予上述 HTML replies 调工具或发送消息的能力。[官方 Visual replies / MCP Apps 文档](https://github.com/pingdotgg/t3code/blob/5211acfe95a0c6d82a60e2524b5e46b47fe67a3e/docs/user/html-renders.md)。

### 15.3 对本 HLD 的采用结论（2026-10-29 更新）

1. 保留OpenUI；结构化流式和自由HtmlArtifact是同一基础下的不同执行路径。参考T3的HTML能力时，按§6.16/P4-H独立设计和验收鉴权、网络、资源与导出，不再用“永不执行生成代码”描述所有GenUI。
2. 借鉴主题 token、手机宽度检查、真实内容高度、稳定挂载身份和发布前视觉检查。OpenUI 在主文档内渲染通常由自然布局处理高度，不需要为了模仿 T3 额外套 iframe。
3. 用户已确认借鉴提示词驱动的“智能”：主模型主动选择表达，不引入Jev；见§6.4.1。原十几组件只是示例，正式目录与HtmlArtifact开放能力按P0与后续准入结果生成提示词，不能把它误说成OpenUI上限。
4. P0 Spike B 应显式断言：在围栏尚未闭合、回答尚未结束时，已出现有效组件；追加 delta 不卸载已有组件，最后合法树能跨暂时解析失败保留。只有 skeleton、文字流式或页面内动画均不算通过。具体 OpenUI API/容错能力仍需按锁定版本实际验证，本调研不替代 Spike。

## 参考

- OpenUI：https://github.com/thesysdev/openui ，Renderer：https://www.openui.com/docs/openui-lang/renderer ，组件定义：https://www.openui.com/docs/openui-lang/defining-components
- OpenUI 1.0 发布说明（第三方报道，需对照官方 changelog）：https://aicoder.com/news/news-20261016-openui-1-0-generative-ui-custom-actions
- A2UI：https://github.com/a2ui-project/a2ui ，https://a2ui.org/specification/v1.0-a2ui/
- shadcn/ui 默认改用 Base UI：https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default
- Cult UI（MIT 免费层；Pro 为付费许可，不引入）：https://github.com/nolly-studio/cult-ui
- Motion Primitives（仓库 MIT；Pro 不引入；引入前核对具体组件的 LICENSE）：https://github.com/ibelick/motion-primitives
- Origin UI（第三方文章；改名 COSS、改用 Base UI 的说法需在 P0 对照官方站核实）：https://21st.dev/blog/origin-ui-components
- shadcn 生态 block/registry 汇总（第三方，仅作选型参考）：https://adminlte.io/blog/shadcn-ui-block-libraries/
- Magic UI：P0 引入前补充官方仓库与许可证链接


---

## 16. 补充：发送过程、紧凑输入框与浮动顶部操作（2026-10-29）

### 16.1 范围、参考与已确认方向

本节为用户试用反馈的**追加设计**，原有 §0–15 及参考内容保持不变。不更换 OpenUI/组件选型、不重写原分阶段计划；本轮只记录并细化需求，**不授权产品实现、安装依赖、模型调用或部署**。后续前端重构按本节补充 P0/P2/P5 的设计和验收；涉及发送准备的服务端合同须一并设计，不能仅用动画模拟完成。

用户提供的两张手机截图用于描述交互问题，不作为产品已验收的目标视觉稿，也不复制含个人会话/地址的原图进公共仓库：

- `1000089105.jpg`：圈出空白 Composer 仍有多行 placeholder、滚动条及多排工具的占高，以及左上角操作区的大面积白色留白。该截图未展示附件，**不能据此确定现有附件进度条位于左上角还是右上角**。
- `1000089107.jpg`：参考紧凑的一行输入胶囊及顶部独立悬浮控件组。借鉴的是紧凑、可扩展和 floating 布局，不照搬其它产品的功能、品牌、权限或语音入口。

用户已明确三个方向：发送反馈不等待文件准备；输入框随文字量扩展和收缩；手机与桌面的顶部操作均采用浮动布局。下文位置、行数、像素和动效时长是设计建议，须在代表屏/真实设备上收敛，不能写成已测结论。

### 16.2 发送立即反馈，模型等待必要准备完成

**“发送动画不阻塞”与“允许模型提前回答”是两个不同合同。** 点击发送后，消息立即以待发送外壳进入对话，文字、附件顺序和文件身份固定；消息入场、页面滚动、输入下一条草稿均不等待上传/扫描/解析/OCR。只有本条消息要求的附件全部准备成功，且完成当前模式/模型的准入，才能进入模型回复阶段。

1. 选择文件后先建立本地附件项和稳定临时身份；可在选择后提前上传以减少等待，也允许点击发送后继续上传。已开始的任务被消息接管，不重新上传一遍。发送按钮不因仍在上传或解析而全局禁用；无模型、权限不足、空消息等其它合法限制仍按现有规则处理。
2. 点击发送时冻结本条文字、模型、模式、附件列表/顺序与发送标识，立即显示用户消息并清理为下一条草稿。尚未获服务端确认时不能显示为已可靠接收；原始草稿/可重试材料不能因入场动画就丢弃。
3. 逐文件继续上传、扫描、必要解析及模式准入。过程展示挂在这条消息的附件上，不另加整页加载遮罩、不把输入框整体锁住，也不提前显示“模型思考中”。
4. 全部附件达到本条消息的必要准备条件后，才申请/派发模型执行；准备失败时不创建一次收费模型调用。不以动画播放完成作为任何业务触发条件。
5. 用户可继续编辑/提交后续消息；实际派发遵守已有会话排序、排队与插话合同。后续消息若依赖尚未成功的文件不得越过准备门槛，也不能让同一消息重复触发回复。

**准备完成的含义须按用途区分**：共用安全扫描和必要原生处理；Chat 还须完成可读正文、必要 OCR、完整性与输入预算检查，超限明确提示选范围或切 Work；Work 仍以文件引用/索引和按需工具读取为主。Work 的准备门槛不扩大为“全文注入模型”或“把整份PDF/PPT全部渲染/OCR”。支持视觉的图片直接走能力分流，非视觉模型才补必要 OCR。选择/上传阶段允许提前计算，但不等于该内容已经进入回答模型。

### 16.3 附件进度与整条消息失败

**建议统一位置**：每个附件保留文件图标/缩略图，图标**右上角的紧凑状态位**展示小进度条或等待标记，文件名与点击区域不被覆盖；图像、办公文件和多文件采用同一定位规则。P0 用桌面/手机样稿确认最终只保留一种进度样式，不左右随机摆放；这里是候选设计，不是对现有位置的断言。

- 上传有真实字节总量时显示真实进度；只有服务端确认的页数/阶段完成量才能显示解析/OCR百分比。未知总量时显示不定进度，不能用计时器伪造从0到100%，也不能上传100%就宣称文件已解析。
- 进入扫描、解析或OCR时，进度位继续表达“尚未就绪”，必要阶段名可通过附件状态/辅助技术取得；成功后结束进度动画，失败后同一位置切为错误标记。不得只靠颜色区分。
- **任意一个附件上传或必要处理失败，整条消息下方固定显示“发送失败”和“重试”。** 保留所有附件及其各自状态、原顺序和文字；不得静默丢掉失败文件，把剩余文件作为原请求发送给模型。无文件的消息提交失败沿用同一消息级反馈。
- 不给用户堆叠多个全局toast；失败文件自身提供可定位、简短且可操作的原因，消息级只保留必要的失败和重试入口。输入预算/格式不支持应解释如何恢复，不能和临时断网混成一个无信息错误。
- 一个文件失败后，已在处理的其它附件允许有界完成并缓存；本条消息继续停留失败态，不因其余文件后来成功就自行触发模型。重试期间不删除用户消息、不重复播放整条入场。
- **重试只续传/重做失败或失效阶段**；已成功上传/扫描/解析的文件与缓存复用。重复点击、重连、晚到旧进度都不能重复建文件/派发模型或把较新状态覆盖回旧状态。
- 被拒绝的恶意文件、永久不支持格式等不能无限自动重试；保留“重试”入口和明确原因，恢复路径需允许用户显式移除/替换后重新提交，记录附件集合的变更，不能暗中少发一个文件。

建议消息/附件阶段用于解释和验收，最终字段及存储映射由 P0 对照现有 Run/文件/队列合同确定：

| 对象 | 阶段 | 展示与派发条件 |
|---|---|---|
| 附件 | 等待上传 → 上传中 → 扫描/必要解析 → 就绪；任一步可失败 | 单文件真实进度或不定进度；成功文件可复用，失败可定位 |
| 消息 | 本地待提交 → 准备中 → 可派发 → 回复中/完成 | 用户消息先出现，全部必要附件就绪并通过准入才进入模型阶段 |
| 消息 | 任一必要阶段失败 → 发送失败 → 用户重试 → 准备中 | 原消息保留；重试新的发送尝试，不能复活已有终态Run或重复已确认调用 |

### 16.4 稳定身份、恢复与后台边界

- 一条待发送消息具有稳定客户端发送标识；每次重试有独立尝试号，文件上传仍用各自上传幂等键。服务端接收、附件关联/ordinal、准备状态与派发权威由API保存，不把React状态、动画回调或摘要当业务账本。
- 本地占位项在API确认后原位对齐服务端身份；必须防止“服务端已收到但响应丢失”导致用户重试生成两条消息或两次模型调用。准备阶段与Run执行终态分开，不把服务端终态改回running。
- 恢复连接/刷新后查询服务端已确认的准备状态。浏览器本地原件已经丢失、且服务端未完整收到时，明确要求重新选择文件；不能把文件名相同视为同一份文件，也不宣称浏览器关闭后仍能续传本地未上传字节。
- 取消、切会话、删除文件/会话或更换模型时，取消或忽略旧请求并重新校验归属、版本与预算；晚到成功不能在已经取消/删除的消息上启动模型。重试不能绕过扫描、权限、配额或模式限制。
- 沿用API唯一业务DB writer、业务变化与派发outbox、短事务及既有服务凭据边界。不得在等待上传/解析/OCR/模型时持有业务事务或用户锁；上传与处理并发均有界，重试不得制造无界任务。
- P0明确复用现有准备/文件/队列合同还是需要新增持久化的待发送状态，并补齐迁移与恢复设计后再实施。此需求涉及端到端编排，不能仅把当前 `canSend` 的等待条件删掉后就认为完成。

### 16.5 Composer 按内容自然伸缩

1. **空态紧凑**：没有文字、附件及显式上下文选项时，桌面和手机均采用一行输入面；短placeholder建议“发消息…”或现有语义对应的短句，不用多行示例撑高。空态无内部纵向滚动条，单纯聚焦不自动撑成大框。
2. **渐进扩展**：正文从1行随实际换行/自动折行增长，建议最多约4行；粘贴长文达到上限后在正文区内部滚动，不继续挤压回答区。候选空态整体高度约56–72px，正常视口下展开态尽量控制在可视高度约四分之一内；数值按字体、工具和软键盘样稿确认，不是硬编码验收结论。
3. **删除即收缩**：删除、撤销、清空、发送后均按剩余实际内容回落，回到1行后恢复紧凑态，不保留上一次长文高度；placeholder不参与决定展开行数。自动测高先允许收缩再读取真实内容高度，更新只影响Composer，不触发整段历史重新布局/渲染。
4. **工具与附件有界**：模式、模型、附件、技能、模板、停止/发送等能力保持可达。常用操作与发送保留在紧凑布局，低频控件可放现有语义的“更多”菜单；不靠减小触控热区压缩高度。带附件时允许增加独立附件带，超过建议1–2行则在附件区有界滚动/展开，不能无限推高输入面。
5. **键盘和输入法**：保持Enter/Shift+Enter、中文IME、选区及粘贴语义；不要在组合输入期间触发发送或跳动光标。结合既有visualViewport和安全区处理软键盘，不用重复padding把底部垫高。软键盘后的极小视口优先保证至少一行正文、必要操作和触控可达，不能机械套用四分之一高度把控件裁掉。
6. **状态稳定**：生成中/停止、重试、排队或附件进度不应让整个Composer反复增减一排。缩放/旋转/字号变化重新测高；用户上翻历史时，Composer伸缩维持阅读锚点，不强行滚到消息底部。尊重reduced-motion，避免每个字符触发昂贵layout动画。

### 16.6 顶部控件浮动，消除整条空白占位

- 手机和桌面聊天/工作画布都改为**独立浮动控件组**。侧栏开关靠左上，新建/通知或更多操作按现有语义分组，可在右上独立成组；最终分组在P0代表屏确认，不恢复整条固定白色header来承载几个图标。
- 背景围绕控件组自身收边，可使用轻量实色/阴影表达浮层；不以横跨全宽的大白底、统一大顶部padding或大面积blur占据阅读区。正文可滚动到控件下方，控件间空白区域不拦截正文点击/选取/滚动；点击热区建议至少44×44 CSS px。
- floating不等于遮挡正文：首屏第一行、锚点定位、跳转目标和键盘焦点必须可完整看到。顶部只保留实际控件/安全区必要避让，结合局部布局与scroll-padding/scroll-margin处理，不把现有整页固定大padding原样叠加回来。
- 手机按visualViewport、安全区和横竖屏定位，软键盘/浏览器工具栏变化不产生双重顶部空白。桌面按主画布位置和展开侧栏/预览栏宽度定位，不与标题、来源tab或输入框互相覆盖。
- 侧栏、预览、菜单和模态有统一层叠/焦点规则：预览/模态覆盖聊天时隐藏或禁用底层浮动按钮，焦点留在当前交互面；关闭后正确恢复。保留新建、侧栏、收件箱等入口及其权限/未读语义，不因截图中只见两个胶囊就删除功能。

### 16.7 纳入原重构阶段的交付与验收

本节在未来获实施授权后纳入 §9，**本轮不启动任一阶段**：P0补消息准备/恢复合同与代表屏，P2同时交付Composer、应用壳和消息反馈，P5复核全站视觉/功能等价。若服务端合同需扩展，须作为P2依赖明确排期；不能先发布“动画成功”，再补真正的模型派发门槛。

| 场景 | 必须取得的证据 |
|---|---|
| 慢上传/慢解析、多文件混合大小 | 点击发送后下一帧/短入场内出现用户消息；输入和页面可操作，各附件进度持续更新；后端调用记录证明准备成功之前模型调用数为0 |
| 任一文件上传、扫描、解析或必要OCR失败 | 消息下方“发送失败 / 重试”；原文字、附件及顺序保留，模型调用为0；错误与失败文件可对应 |
| 重试、重复点击、响应丢失/重连 | 成功文件不重复上传/解析；仅一个逻辑消息和一次有效模型派发；旧尝试进度不能覆盖新状态 |
| Chat/Work与图像能力分流 | 两模式都不阻塞消息入场；Chat完整性/预算、Work按需引用、vision/OCR和每轮独立图片限制仍符合文件方案 |
| 刷新、取消、切会话、删除或替换附件 | 恢复已确认状态；本地原件丢失明确要求重选；被取消/删除的准备任务不能晚到后派发模型；不更改终态Run |
| 空输入、1/4/20行、长粘贴再删空 | 桌面/手机空态紧凑无空滚动条；达到上限后内部滚动；删除/撤销/发送后收缩，模式/模型/附件等入口可达 |
| 手机软键盘、旋转、放大文字及桌面预览并排 | 无重复安全区padding、全页横向溢出、光标/发送键遮挡；输入框不持续吞掉正文，保持阅读锚点 |
| 浮动控件与侧栏/预览/模态 | 无整条多余白色占位；第一行、焦点和锚点可见；点空白能操作正文，底层按钮不穿透当前模态 |
| 动画、性能、无障碍 | reduced-motion与键盘/读屏状态可用，明确区分准备和回复；进度仅局部更新，长会话/40万字/4×CPU节流下仍遵守§10，不新增按文件数线性增长的高频全局轮询 |

建议把“发送点击→消息首次可见”单独测量，候选目标≤100ms（不含约定短入场结束时间），与上传/扫描/解析总耗时、模型首token延迟分别报告；实际弱机/节流预算由P0冻结。截图证明布局，真实输入/网络故障和服务端派发记录证明行为，不能互相代替。当前仅完成文档补充，所有本节产品实现与上述运行验收均未执行。

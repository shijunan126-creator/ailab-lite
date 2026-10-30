# 自由生成 GenUI 开源候选（2026-10-28）

> 后续深度源码核对已补充[OpenIntelligentUI vs thesysdev/openui](2026-10-28-openintelligentui-vs-openui.md)：OpenUI本身已有HtmlArtifact扩展示例，不能从本稿“目录有上限”推断必须更换框架。以下为初筛建议，不覆盖深度比较的修正。

## 目标与检查边界

用户认为十几种固定组件限制表达，要求先搜索可自由生成布局、图表、示意图及交互工具的较大开源方案。只调研，不安装、不付费、不实施、不替换现有服务。本次阅读公开官方文档及仓库 main 内容，属于当日动态快照，未固定所有候选 SHA，未运行候选。项目工作树仍为 `fix/full-thinking-rendering@ab0e8d9`，已有文档和 `.DS_Store` 保留。

关键修正：自由 HTML 与流式展示并不互斥。可以先增量展示 markup，再延后执行脚本；“必须等整页生成完才能展示”过于绝对。需区分 markup 可见时间、脚本执行时间、交互就绪时间。

## 1. Generative Frame：优先考察的嵌入式渲染库

- [官方介绍](https://www.assistant-ui.com/generative-frame/docs)、[README](https://github.com/assistant-ui/assistant-ui/tree/main/packages/generative-frame)、[package.json](https://raw.githubusercontent.com/assistant-ui/assistant-ui/main/packages/generative-frame/package.json)。MIT；本次 main 声明版本 `0.0.8`，不是成熟度保证，也不冒充 npm 最新版本。
- 自由 HTML/SVG，支持 CSS/JS；另有组件 spec 模式，但不是必须采用的入口。核心不依赖完整 assistant-ui 聊天框架，React/Vue/assistant-ui 为可选适配。
- `createWidget → write(chunk) → end()`；按动画帧合并，增量 morph 页面，代码完整后才按顺序执行脚本。提供主题同步、错误/console/空白诊断、截图、提示词和可选 repair loop。
- 对 AILab 的推断：可将既有模型流中提取的 UI 源码片段交给 `write`，无需为了渲染器迁移整个 Agent/Run/聊天框架。但片段协议、持久化、重连、历史重放、分享和打印仍需我们设计和实测。
- [安全/自托管](https://www.assistant-ui.com/generative-frame/docs/security)：默认 Safe Content Frame 通过外部 shim 独立站点隔离。自托管同等站点隔离不是简单替换一个 URL，文档要求 public suffix、wildcard DNS/TLS 和匹配 shim。`opaqueOrigin:true` 可跳过外部 shim，但 srcdoc 继承宿主 CSP，内联脚本与严格 nonce CSP 有冲突，进程隔离也较弱。
- 网络策略默认禁止 fetch/XHR/WebSocket，但允许若干 CDN 资源，不能称完全离线或绝对无法外传。bridge 权限由宿主 callback 决定。服务端渲染常驻资源可能较少，但本轮未测包体、CPU、RSS或恶意脚本卡顿，不能给性能结论。

## 2. CopilotKit OpenIntelligentUI / Open Generative UI：优先参考的完整体验

- [仓库](https://github.com/CopilotKit/OpenIntelligentUI) MIT，页面约4k stars（当日显示，仅表示关注规模）；[官方功能文档](https://docs.showcase.copilotkit.ai/claude-sdk-python/generative-ui/open-generative-ui)。
- 可自由生成 HTML/CSS/JS，通过 `generateSandboxedUi` 和 CopilotKit runtime 转成 activity 流；包括3D解释、地图、计算器等示例。原生组件和自由 UI 共存，由 agent 提示词/路由选择。
- [架构](https://github.com/CopilotKit/OpenIntelligentUI/blob/main/docs/architecture.md)：字段顺序为 `initialHeight → placeholderMessages → css → html → jsFunctions → jsExpressions`。先预览 iframe 渐进展示，再初始化最终 Websandbox iframe。历史输出是独立快照，没有跨调用任意 DOM patch，控件状态不会自动成为模型状态。
- Next.js + CopilotKit + Python Deep Agent/LangGraph/AG-UI，不是直接拷一个 React 组件就接入现有栈。当前仓库 README 还要求模型 key 与 Jev 可视化路由 key；开源代码不等于默认端到端完全无外部服务。是否绕过路由及接入现有模型，需单独验证，不在此承诺。
- 仓库架构文档明确 checkpoint 是进程内存；不能照搬为 AILab durable Run 契约。建议先参考产品、提示词和流式 renderer，不直接替换 AILab 后端。

## 3. LibreChat Artifacts + Sandpack：大型聊天产品参考

- [LibreChat](https://github.com/LibreChat-AI/LibreChat) MIT，页面约46.4k stars（当日显示）；[Artifacts 官方文档](https://www.librechat.ai/docs/features/artifacts)。
- 支持生成 React、HTML、SVG、Mermaid、Markdown，后续对话修改；Normal/shadcn/Custom 提示词模式，回答流式期间产物持续更新。
- [Sandpack](https://github.com/codesandbox/sandpack) Apache-2.0，是通用浏览器代码预览/编辑底座，不是自己决定何时生成 UI 的 Agent。官方仓库约6.3k stars（当日显示）。适合希望支持 React 与更完整前端依赖的产物。
- 默认使用 CodeSandbox CDN/bundler；LibreChat 文档提供自托管配置，且 React/framework 与静态 HTML 需要分别配置 bundler URL，否则部分内容仍走公网。新增 bundler 部署与客户端编译成本需测。
- 更适合借鉴完整产物侧栏、预览/源码、导出和多轮编辑。具体不完整源码如何增量运行、是否重挂载和保留交互状态，不能只从“支持 streaming”推断，需要读实现并运行。

## 4. 排除与下一步

- [block/summon](https://github.com/block/summon) 虽方向相近，但仓库已归档且规模小，不作为优先依赖。
- 更大的预定义组件目录仍有目录上限，不解决本轮自由表达目标。不要将 OpenIntelligentUI 与原 HLD 的 OpenUI Lang 混为同一项目。
- 优先深查 Generative Frame 的独立接入；以 OpenIntelligentUI 校对体验和提示词；以 LibreChat/Sandpack 作为 React 富产物备选。这是调研建议，不是已选型。
- 下一步先确定自由 UI 的产物边界，才重写 HLD：正文内多块、历史恢复/版本编辑、脚本执行/网络/宿主 bridge、依赖自托管、打印导出、失败降级。然后用相同的示意图、交互计算器和数据面板比较实际效果与性能，任何安装/实现/付费验证仍待用户授权。

本轮只进行了文档/仓库检索及记录检查，未运行 Python/Portal/浏览器或候选运行时。主项目能力状态没有变化。

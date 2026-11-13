# Portal

Portal 是浏览器里的界面与同源入口：负责页面、发送队列、SSE 流式渲染、GenUI、样式和文件查看器。它不持有模型密钥，不执行业务授权，不解析不可信文件，也不执行用户沙箱；这些分别属于 [API](api.md)、[文件](files.md) 和 [沙箱](sandbox.md)。

## 代码地图

| 要改的东西 | 位置 |
|---|---|
| 页面路由、页面级组合 | `platform/portal/app/(app)/`；登录注册在 `platform/portal/app/login/page.tsx`、`platform/portal/app/register/page.tsx` |
| 全局外壳、侧栏、搜索、账号菜单 | `platform/portal/components/app-shell.tsx::AppShell` |
| 会话页、发送、跟随流、侧栏查看器 | `platform/portal/components/chat/chat.tsx::Chat` |
| 输入框、附件、技能、模板、排队与插话 | `platform/portal/components/chat/composer.tsx::Composer` |
| 单轮消息、过程、回答、分支/重试/编辑 | `platform/portal/components/chat/turn.tsx::Turn` |
| 回答中的 Markdown 与 GenUI 分片 | `platform/portal/components/chat/answer.tsx::Answer` |
| 侧栏标签、预览、来源、网页阅读器 | `platform/portal/components/chat/preview-pane.tsx::PreviewPane` |
| 发送队列 | `platform/portal/lib/outbox.ts` |
| 附件上传与扫描轮询 | `platform/portal/lib/uploads.ts` |
| SSE 读取与重连 | `platform/portal/lib/api.ts::streamRun` |
| SSE 合批 | `platform/portal/lib/stream/batcher.ts::createBatcher` |
| 事件折叠成过程与回答 | `platform/portal/lib/work-process.ts::reduceEvent` |
| API 类型与请求封装 | `platform/portal/lib/api.ts` |
| 会话缓存与分页合并 | `platform/portal/lib/conversation-cache.ts`、`platform/portal/lib/conversation-pages.ts` |
| 回答拆分、复制降级 | `platform/portal/lib/answer-parts.ts` |
| GenUI 控件状态保存 | `platform/portal/lib/ui-state.ts` |
| 浏览器内 QuickJS 与 `client.call` | `platform/portal/lib/js/client-calls.ts`、`platform/portal/lib/js/runner.ts` |
| GenUI 组件目录 | `platform/portal/components/genui/catalog/schema.ts` |
| GenUI 渲染适配 | `platform/portal/components/genui/index.tsx::GenUIBlock`、`platform/portal/components/genui/openui/block.tsx` |
| GenUI 契约生成 | `platform/portal/scripts/genui-prompt.ts` |
| 服务端渲染的文档查看器 | `platform/portal/components/preview/office-view.tsx::OfficeView` |
| 查看器 iframe 与 CSP | `platform/portal/components/sandbox-frame.tsx::SandboxFrame`、`platform/portal/lib/frame.ts::framePolicy` |
| 设计 token | `platform/portal/styles/tokens.css` |
| 级联层次 | `platform/portal/app/globals.css` |

目录职责：

- `app/`：路由处理器与页面。`app/(app)/` 下的页面共享 `AppShell`；`app/api/` 是浏览器唯一可达的 API 入口；`app/frame/[kind]` 是不透明源文档框架；`app/office-viewer/` 与 `app/pdfjs/` 是带版本号的静态资源路由。
- `components/`：`chat/` 是会话主体，`genui/` 是生成界面，`preview/` 是文件与文档查看器，`library/` 是资料库组件，`admin/` 是管理页；其余是跨页共享控件。
- `lib/`：浏览器请求、状态、流、上传、查看器协议和纯函数。服务端专用代码在 `lib/server.ts`，不得被客户端组件导入。
- `styles/`：`tokens.css` 只放语义 token；`base.css` 是元素与通用控件；`shell.css`、`chat.css`、`library.css`、`pages.css` 是分屏样式；`app.css` 和 `genui.css` 是后加载的重建样式。`app/globals.css` 先声明 `theme < base < components < utilities`，再按 `base`、`shell`、`chat`、`library`、`pages`、`app`、`genui` 的顺序导入组件层样式。

路由入口：

| 路由 | 页面或处理 |
|---|---|
| `/`、`/c/[id]`、`/p/[id]` | 新会话、已有会话、项目内新会话；都由 `platform/portal/components/chat/chat.tsx::Chat` 承担 |
| `/files` | 资料库；页面级组合在 `platform/portal/app/(app)/files/page.tsx` |
| `/s/[id]`、`/print/[id]` | 只读分享、打印导出；分别复用 `Answer` 与查看器 |
| `/runs/[id]`、`/schedules`、`/skills`、`/memory`、`/account`、`/admin` | Run 详情与各管理/设置页 |
| `/login`、`/register` | 独立于 `AppShell` 的登录注册页 |
| `/api/auth/*`、`/api/v1/*` | 会话建立/清除与同源 API 代理 |
| `/frame/[kind]`、`/office-viewer/*`、`/pdfjs/*` | 文档框架外壳与版本化查看器资源 |

## 关键流程

### 发送一条消息到流式回答

1. 输入框提交后，`Chat` 冻结当前分支修订、模式、模型、技能、模板和附件顺序，调用 `enqueue` 写入 `useOutbox`；消息立即以待发送形态显示，输入框不等待上传。
2. 附件在选中时已由 `startUpload` 开始上传，发送过程用 `settle` 等待每个附件的上传与扫描结果；只有全部附件可用才继续，缺文件时整条消息失败，不带缺损附件发出。
3. `process` 在需要时调用 `/v1/chat/prepare`，再按创建顺序等待同一会话里更早的待发消息（`turnOf`）。
4. `dispatch` 用 `client_send_id` 提交：新会话走 `/v1/conversations/start`，已有会话走 `/v1/conversations/{id}/messages`；运行中则走 `/v1/conversations/{id}/queue`，并在 `conversation_busy` / `conversation_idle` 之间回退。
5. 返回的 Run 进入会话详情，`platform/portal/components/chat/chat.tsx::follow` 建立 `AbortController` 和 `createBatcher`，由 `streamRun` 读取 `GET /v1/runs/{id}/events?after=seq`。
6. `streamRun` 按 `seq` 去重并在断线后从最后一个事件续读；`stream.end` 结束本次流。每个动画帧（页面隐藏时每 250ms）把积压事件一次性交给 `setItems`，用 `reduceEvent` 折叠，保持未变 item 与 step 的引用。
7. `run.status`、`artifact.created` 只合并刷新该 Run；`preview.updated` 进入侧栏预览；`client.call` 交给浏览器执行；终态事件先 flush 再重新读取会话详情。
8. `Turn` 从 items 与 Run 终态回答中拆出过程与正文，`Answer` 把正文拆成 Markdown 与 `openui` 块；流式文字由 `useSmoothText` 平滑放出，过程中与终态各自遵守同一 reducer。

### 一次预览与查看

1. 文件或交付件先由 `FilePreviewBody` 判断格式与状态；可原生打开的 Word、Excel、PowerPoint、PDF 走 `OfficeView`，其余走 `PagedPreview`。
2. `OfficeView` 先请求文档索引与精确版本；可原生显示时，它把查看器 bundle、样式、文档字节和主题通过 `postMessage` 交给 `app/frame/office`，查看器只在该不透明源框架中运行。
3. 原生查看器不可用、超过 `MAX_NATIVE_BYTES`、索引失败或渲染失败时，回退到服务端分页预览：页图用 `DocumentPages`，表格用 `Sheets`，文本或图片用对应视图。
4. Work 预览返回单独的 PDF 时用 `PdfDocument`（`react-pdf`）；它从 `app/pdfjs` 取 CMaps、标准字体、Wasm 与 ICC，路径带 PDF.js 版本，可长期缓存。
5. 侧栏 `PreviewPane` 为每个文件保留独立标签；来源与网页阅读器各占一类标签。网页阅读器走 API 的同源渲染代理，不使用 Portal 的 CSP 框架。
6. `platform/portal/app/frame/[kind]/route.ts::GET` 只提供四类框架外壳与各自 CSP；文档内容仍由 `SandboxFrame` 在握手后用 `postMessage` 送入，因此生成页、交付 HTML 和 Office 查看器不会继承 Portal 页面的策略。
7. `platform/portal/office/build.mjs` 从 `vendor/office-suite/upstream/office-runtime` 打包查看器，`platform/portal/app/office-viewer/[version]/[...path]/route.ts::GET` 按 `manifest.json` 的版本提供不改写的资源；`platform/portal/app/pdfjs/[version]/[kind]/[name]/route.ts::GET` 只服务 Portal 自己的 `react-pdf` 预览。
8. `platform/viewer-runtime/package.json` 固定的是服务端 `viewer-reader.ts` 的 Node 依赖，随 API 镜像构建；浏览器不加载它，Chat/Work 的文档索引仍由 [文件](files.md) 与 [沙箱](sandbox.md) 负责。

### GenUI 从目录到回答

1. `catalog/schema.ts` 用 zod 定义组件、位置参数、`opts`、分组和给模型的说明；`catalog/components.tsx` 提供 React 实现，重计算放在 `catalog/data.ts`，Markdown 降级在 `catalog/markdown.ts`。
2. `scripts/genui-prompt.ts` 从 schema 生成 `platform/ailab_platform/contracts/genui/openui-v2.index.md`、`openui-v2.guide.md` 和 `catalog-v2.json`；Agent 只读取这些生成文件。
3. 模型在回答里写 ` ```openui ` 围栏；`splitAnswerParts` 只认行首围栏，普通代码围栏里的同名文本仍是代码。
4. `GenUIBlock` 限制块大小，按需加载解析器与目录，先在流式期间保留最后一棵可解析的树；解析失败或整块为空时显示源码降级，单个组件失败不拖垮其它内容。
5. `OpenUIBlock` 把源码转成程序，用目录解析、求值并渲染；控件写入 `$变量`，表达式就地重算；`@ToAssistant` 通过 `GenUIHost.send` 走与输入框相同的发送队列。
6. 控件、页签、排序与图例状态按 Run 的块序号保存到 `Run.ui_state`；`saveBlock` 合并后延迟提交到 `PUT /v1/runs/{id}/ui-state`，分享与打印只读同一份状态。
7. `HtmlArtifact` 是唯一在 iframe 中运行的生成页面：书写阶段只显示 markup，围栏闭合后才允许脚本运行；离开视口会卸载框架。

## 不变量

Portal 的 CSP、样式层级、E2E 钩子、流式合批、发送队列、GenUI 生成和测试规则见 [platform/portal/AGENTS.md](../../platform/portal/AGENTS.md)，这里不重复。以下只列本模块的数据模型与状态生命周期带来的约束。

- 浏览器只请求同源 `/api`。`app/api/v1/[...path]/route.ts` 从 httpOnly cookie 读取会话令牌并在服务端加 `Authorization`，不代理 `/internal`；写请求必须同源。
- Portal 不把会话令牌或密码放进 HTML、URL、localStorage 或客户端 bundle。钉住它的测试：`platform/e2e/test_portal.py::test_the_session_token_and_password_are_invisible_to_page_scripts_and_to_the_bundle`。
- 只有全部附件就绪才派发；失败重试复用已就绪文件与同一个 `client_send_id`。钉住它的测试：`platform/portal/tests/outbox.test.ts::a lost send response retries with the same id and ready files, without another upload`。
- 同一会话的待发消息按 `created` 顺序派发；分支修订在提交时冻结，等待上传与重试都不能改用更新的修订。
- SSE 事件必须按 `seq` 去重、断线续读，并在中止时取消 reader。钉住它的测试：`platform/portal/tests/api.test.mjs::a dropped event stream resumes after the last event, without repeats`。
- 刷新、分页与流共用同一份过程投影：`ProcessView` 与实时 SSE 必须折叠成相同 items。钉住它的测试：`platform/portal/tests/work-process.test.mjs::ProcessView V1 API projection stays identical to the live SSE display`。
- GenUI 块之间不共享变量；每回答最多 8 块、每块最多 32000 字符，表格最多 200 行，图表最多 500 点。
- 派生数字由组件从原始数据计算，模型不提供手算结果；复制、打印与画面必须来自同一份计算。
- 来自模型的文本按文本渲染。除 `HtmlArtifact` 的隔离框架外，不执行模型生成的 HTML、脚本或事件处理器。
- `HtmlArtifact` 只在 `genui-preview` / `genui-run` 中运行，且只能回传高度；它不能访问 Portal cookie、存储、网络或会话。
- GenUI 控件状态的持久副本只在 `Run.ui_state`；页面内存只是尚未回写的最新值。它是界面偏好，不进入模型上下文，也不能改变回答或业务授权。

## 怎么扩展

### 加一个页面或弹窗

1. 页面放到 `platform/portal/app/(app)/<route>/page.tsx`，自动获得 `AppShell`；顶级页面同时在 `platform/portal/components/app-shell.tsx::AppShell` 的 `side-nav` 加入口。
2. 数据请求走 `platform/portal/lib/api.ts::api`，在 effect 中建立 `AbortController` 并在清理函数里取消；需要登录失效跳转时依赖 `redirectIfUnauthorized`。
3. 弹窗优先复用 `platform/portal/components/library/modal.tsx::Modal`；从固定侧栏打开的浮层按 AGENTS 的 `createPortal` 规则挂到 `document.body`，并处理 Escape、焦点返回和点击遮罩关闭。
4. 新页面若参与会话列表或项目状态，使用现有事件与缓存（`conversationsChanged`、`projectsChanged`），不要复制一套全局状态。
5. 加浏览器测试到 `platform/e2e/`，覆盖入口、刷新后的状态与权限失败；不要只测组件快照。

### 加一种 SSE 事件或 API 类型

1. 在产生事件的 API/Agent 代码中写入事件类型与 payload；事件字段属于公共契约时，同时改 `platform/ailab_platform/contracts/` 或 `schemas.py`，并在 `platform/portal/lib/api.ts` 镜像同名字段与类型。
2. 若事件出现在过程时间线，在 `platform/portal/lib/work-process.ts::reduceEvent` 增加分支和用户可读文案；保持未变 item 的引用，不修改输入数组。
3. 若事件有页面级副作用（打开预览、刷新单个 Run、标题、客户端调用），在 `platform/portal/components/chat/chat.tsx::follow` 处理；不要把副作用塞进 reducer。
4. 若它是新的过程事件，更新 `platform/tests/fixtures/process_view_v1.json` 或 `platform/tests/fixtures/process_lifecycle_v1.json`，让 Python 过程快照与 Portal 折叠结果继续同构。
5. 在 `platform/portal/tests/` 增加 reducer 测试；若涉及断线、去重或取消，再加 `platform/portal/tests/api.test.mjs` 的流测试。

### 加一个 GenUI 组件

1. 在 `platform/portal/components/genui/catalog/schema.ts` 定义组件：名称、描述、zod props、`META` 的 group/notes/example，并加入 `DEFINITIONS`；同步 `platform/portal/components/genui/catalog/limits.ts::COMPONENT_NAMES`。
2. 在 `catalog/components.tsx` 或专用文件实现渲染；高级图表与图示放懒加载文件。需要计算的派生值加到 `catalog/data.ts`，复制降级加到 `catalog/markdown.ts`。
3. 若组件可交互，用 `useStateField` 且有稳定的 state key；状态只允许 API 能保存的标量与 `{value, componentType}`。
4. 运行 `cd platform/portal && npm run genui:prompt`，提交 `platform/ailab_platform/contracts/genui/` 下重新生成的三个文件；不要手改。
5. 增加渲染、复制与错误形状测试到 `platform/portal/tests/genui-components.test.tsx` 或 `genui.test.tsx`；契约漂移由 `genui-contract.test.ts` 拦截。

### 改样式

1. 先按 AGENTS 的 token、字体和层级规则确定新样式放在哪一层；需要新 token 时同时补浅色与 `.dark`。
2. 重建一个区域时，删除它替换掉的旧规则（`base.css`、`shell.css`、`chat.css`、`library.css`、`pages.css`），不要留下两套同时命中的样式。
3. 由模板拼出的类名（如 `tone-${kind}`）在源码里直接 grep 不到，删除前先搜前缀和 `file-tile.tsx` 的映射。

### 改动 E2E 依赖的类名或文案

1. 以 `platform/e2e/` 的实际选择器和中文标签为准；AGENTS 里的清单只是入口，不复制成第二份常量表。
2. 类名、角色或标签一改，在同一变更中更新 `platform/e2e/`，不要留下靠文案兼容的双路径；改版后跑对应的浏览器测试。

## 配置

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_API_BASE` | Portal 服务端访问 Platform API 的地址；只给路由处理器与 proxy，不能暴露到浏览器 | `http://127.0.0.1:8080` |
| `AILAB_STANDALONE` | 为 `1` 时 Next 输出 standalone，供镜像运行 | 未设置 |
| `OFFICE_RUNTIME_SRC` | `office/build.mjs` 读取的上游查看器 UI 源码目录 | `vendor/office-suite/upstream/office-runtime` |

平台服务自身的完整配置见 `platform/ailab_platform/config.py`；GenUI 的总开关与块上限由 API 的 `AILAB_GENUI_*` 决定，Portal 只按 `/v1/auth/me` 的 features 与契约渲染。

## 相关决策

- [ADR 0027 Portal 前端栈、流式管线与非阻塞发送](../architecture/adr/0027-portal-ui-stack-and-send-flow.md)：确定 Next/React、Tailwind 层级、CSP、zustand 发送队列、SSE 合批和侧栏原生查看器。
- [ADR 0028 回答内 Generative UI](../architecture/adr/0028-generative-ui-openui.md)：确定 `openui` 围栏、OpenUI 渲染、控件状态、`HtmlArtifact` 沙箱与复制/打印降级。
- [ADR 0029 GenUI 组件目录 v2 与按需披露](../architecture/adr/0029-genui-registry-v2.md)：确定组件目录、派生量由组件计算、索引/指南按需读取和目录版本规则。
- [ADR 0023 平台文件读取执行边界与并发](../architecture/adr/0023-system-document-reader-and-concurrent-capabilities.md)：说明浏览器查看器背后的系统读取边界。
- [ADR 0026 办公内容覆盖与持久文件等待](../architecture/adr/0026-document-visual-coverage-and-file-waits.md)：说明文档等待状态与预览/读取覆盖语义。

## 已知的坑

见 [HANDOFF · Portal](../../HANDOFF.MD#portal)。Portal 的长期规则见 [platform/portal/AGENTS.md](../../platform/portal/AGENTS.md)。

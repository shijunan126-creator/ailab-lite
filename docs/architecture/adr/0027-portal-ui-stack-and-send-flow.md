# ADR 0027：Portal 前端栈、流式管线与非阻塞发送

状态：本地实施（分支 `feat/frontend-genui`），依据 [前端/GenUI HLD](../design/2026-10-28-frontend-genui-hld.md) §4、§16。偏离冻结目标架构中的 assistant-ui。

## 决定

- **样式**：2026-10-30 起 legacy 层清空（HLD P5）。层序 `theme < base < components < utilities`，Tailwind preflight 在 base 层。旧样式按区域并入 `styles/base|shell|chat|library|pages.css`，与重建的 `app.css`、`genui.css` 同在 components 层，按此顺序加载，同一套特异性与先后规则决定胜负，不再靠“重建层整体压过旧层”。并入规则：
  - 迁移前在它命中的每个元素上都被重建规则覆盖的旧声明删除；源码中不再出现的类删除（由模板拼出的类名如 `tone-${kind}` 保留）。
  - 同层后，针对同一元素的更具体规则胜出，例如开关和复选框的圆角、列表行字重；以前被通用的 `button` 规则无意盖掉，现在恢复原意。
  - 旧规则压过重建组件的个别情况，改回重建值或并入 `app.css`，例如侧栏链接。
  - 原来只靠旧文件内先后顺序成立的覆盖，改为显式复合选择器，例如 `.modal.search-dialog`。
  - preflight 去掉而 Portal 依赖的浏览器默认（列表标记、行内图标、标题粗体、各类输入框外观）在 `base.css` 顶部补回。
- **CSP**：`proxy.ts` 为每个页面生成 nonce：`script-src 'self' 'nonce-…' 'strict-dynamic'`、`object-src 'none'`、`frame-ancestors 'none'`、`frame-src 'self' blob:`、`connect-src 'self'`；`style-src` 允许 inline（React 的 style 属性）。root layout 读取 nonce 后所有页面动态渲染：页面原本就依赖会话 cookie，增加的只是每次整页请求一次 layout 渲染（毫秒级）。生成页面与交付的 HTML 文件不再用 srcdoc（会继承父页 CSP），由 `app/frame/[kind]` 下发各自的策略（ADR 0028）。PDF.js 关闭 `isEvalSupported`。浏览器端 JavaScript（`run_js`）需要 WebAssembly，`script-src` 另加 `'wasm-unsafe-eval'`（只放行 Wasm 编译，不放行 `eval`）。
- **图标**：`components/icons.tsx` 改为 lucide 门面，导出名不变；只从这里引图标。
- **状态**：zustand 只用于跨路由存活的发送队列和上传状态（`lib/outbox.ts`、`lib/uploads.ts`）。会话详情、SSE、翻页锚定保持原有 abort + seq 逻辑。
- **测试**：`npm test` 改为 Vitest；旧 `node:test` 用例通过 `tests/node-test-compat.mjs` 原样迁移，组件测试按文件声明 jsdom。
- **浏览器 JavaScript**：`quickjs-emscripten-core` + `@jitl/quickjs-wasmfile-release-sync`（0.32.0，精确锁定）。用途：模型请求的小计算在用户浏览器的 Web Worker 里跑，不占服务端 CPU；首次使用才加载约 0.5 MB Wasm。内存上限靠传入固定 `maximum` 的 `WebAssembly.Memory`（该构建的 `setMemoryLimit` 不生效），超时靠中断处理器加 Worker `terminate()`。
- **不采用 assistant-ui**：其消息/runtime 模型与以 Run 为中心的版本、过程、排队、插话、ask_user 和 500 轮卸载机制冲突。

本轮**未引入**（没有使用点就不加依赖）：Streamdown（自研 Markdown 已满足 40 万字分块、`ailab-file://`、来源 pill；替换需先做 HLD Spike A 对比）、Recharts（GenUI 图表用自绘 SVG，见 ADR 0028）、Motion、shadcn/Base UI 组件、TanStack Query。各自在对应屏幕重建且有具体行为需要时再引入。

## 流式管线

SSE 事件按帧合批（`lib/stream/batcher.ts`，隐藏页 250ms）后一次折叠进 items；reducer 结构共享，未变化的 item/step 保持引用；`Turn`、`StepRow` memo，Chat 传给轮次的回调用 `useStable` 保持身份，非流式轮次在 delta 期间不重渲染。运行中的 `run.status/artifact.created` 只刷新该 Run（`GET /v1/runs/{id}`，300ms 合并），终态再整体刷新会话。`useSmoothText` 把后端 250ms/120 字的成块文字在约 80ms 内均匀放出（加上按帧合批，delta 到达后 150ms 内全部可见，HLD §10）；终态、隐藏页、减少动态效果或积压超过 4000 字直接显示全文，openui 围栏内源码不逐字释放。

## 非阻塞发送（HLD §16）

点击发送即冻结文字、模式、模型、附件顺序，用户消息立刻以待发送形态出现在会话中，输入框清空可继续输入。附件从选择时就开始上传（XHR 真实字节进度）与扫描轮询；发送不等待它们。`lib/outbox.ts` 在全部附件就绪、Chat 准备（`/v1/chat/prepare`）通过后才创建会话/消息；任一附件失败整条消息显示“发送失败 / 重试”，不少发文件、不调用模型。重试只重做失败的上传或检查，已就绪文件复用。消息创建带 `client_send_id`，响应丢失后重试由服务端返回同一 Run，不会产生第二次派发。同一会话的待发消息按发送顺序派发；运行中发送走既有排队/插话合同，`conversation_busy`/`conversation_idle` 互相回退。

边界：待发送状态只在浏览器内存（跨路由保留，刷新后丢失）；服务端尚无“已接收但未派发”的持久状态，刷新后需要用户重新发送，本地已上传完成的文件仍在资料库。若需要跨刷新恢复，再按 HLD §16.4 增加服务端准备状态。

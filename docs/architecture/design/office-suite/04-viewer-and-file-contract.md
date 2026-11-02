# 04 侧栏、选区和文件合同

[总览](README.md)。目标是迁移四格式 viewer 的完整交互能力，Chat/Work 共用；不是只复用一个“查看”按钮，也不把侧栏变成 Office 原位编辑器。制作修改由 Work 沙箱完成，更新后侧栏展示新版本。

## 复用与替换边界

上游根 `$UPSTREAM/office-runtime/`：

- `ui/viewers/{DocxView,XlsxView,PptxView,PdfView}.tsx` 和 `types.ts`：优先复用 reveal/goTo/capture/find/selection/page/zoom 逻辑及测试。
- `ui/shell/`、`ui/main.tsx`、`ui/panel.css`：复用必要的选择、目录、缩略图、搜索、标记等实现；融入 AILab 现有侧栏布局，删除被替换的重复交互。
- `ui/host.ts`、`rpc.ts`、`assets.ts`、`windowStorage.ts`：通过明确适配层替换 MCP Apps host、上游 surface、workspace 路径与本地文件资源；不是原封不动部署 stdio server。
- `src/domain/anchor.ts`、`src/app/documents.ts`：保留定位语法和状态语义，API 映射文件身份；与 reader 使用相同 parser revision。

Portal 候选接入点为 `components/chat/preview-pane.tsx`、`components/preview/file-viewer.tsx` 及新的隔离 office frame。按格式 lazy load；遵循既有 `/frame`/sandbox-frame、CSP 和 trusted host bridge，不能把不可信文档或生成 HTML 作为同源 srcdoc。viewer 代码是可信静态包，文档内容不是指令。

frame 不持模型/服务凭据，不直连 `/internal`；父 Portal 只通过已授权 same-origin API 获取有界文件/派生物，并使用窄的消息协议传给指定 frame。跨 frame 校验 source window、view/session nonce、消息类型/长度/版本；不信任任意 postMessage，iframe opaque origin 时也不能仅比较 origin 判授权。禁用文档宏和自动外链加载，外链按现有导航策略处理。

## 文件与位置身份

文档引用候选结构：`file_id + file_version + content_revision/derivation_id + locator + quote_hash`；物理页定位另带 render_revision，quote 为有界引用片段，不是授权依据。Word p/t、Excel sheet/range、PPT slide/shape、PDF page 的精确支持按 parser/viewer 映射能力声明，不能伪造 object_id。

Work 尚未交付的文件使用 `run_id + workspace_revision + authorized_path + content_hash`；路径只允许当前租约工作区，UI 只取得受控渲染/内容绑定。交付后替换为正式 file_id/版本。旧版本引用不自动指向最新 head；同名重建必须生成新内容身份并取消旧加载/截图。

对于 Word 分页和 XLSX 打印页，结构 anchor 与 LO 物理页不等价：原生 viewer 选择映射使用 parser 的结构身份，服务端截图使用 render 版本。不能确认对应关系时返回无法精确定位，不能猜测页码或高亮错误位置。浏览器原生 Office 预览与 LO/PDF 版式可能不同，最终交付视觉验收以实际导出版本为准。

## 模型工具映射

建议用少量固定 schema：复用 `read_library_file`/`view_attachment`，新增固定 document-view 操作接口承载 open/get/reveal/capture；操作枚举短且稳定，不把整个 MCP tool 清单动态插入请求。最终命名与 schema 在实施前定稿并进入 recorded-request 测试。

| 上游 操作 | AILab 适配目标 | 行为与失败合同 |
| --- | --- | --- |
| open_document | 获取元数据/outline＋追加打开侧栏命令 | API 核对文件权限、版本和扫描状态；原生读取不依赖 UI 在线 |
| read_document/search_document | read_library_file 的 anchor/query/cursor | 复用派生索引、原生/OCR区分、coverage；游标绑定版本，错误版本拒绝 |
| show_in_viewer | 有授权的 reveal 命令/引用标记 | 给精确 file/version/targets/quote，UI 滚动并高亮；ack 失败不能声称用户已看到 |
| get_view | 指定 view_id 的有界查看状态快照 | 多文件/多客户端存在歧义时返回候选；无状态或过期明确 unavailable，不猜“这个” |
| capture_view | 在线 viewer capture 或受限服务端范围渲染 | 区分当前实际视口截图与源文件范围渲染；后者不能冒称用户当前所见 |
| 用户选区引用 | outbox 文档引用对象 | API 复核源文本/版本/范围，再绑定用户消息；不只提交一段可伪造文本 |
| 本地文件导入/列表 | 既有 upload/library/download API | 扫描、配额、归属、大小和项目策略继续生效；不开放宿主文件列表 |

状态读取与发送截图都遵守 mode/model capability；非视觉模型只能获得文字和定位，图表语义标未验证。Chat 不因 capture 获得任意 exec，平台渲染仍是可信受限作业。

## 选区发送与查看状态

用户选区时记录 view_id、文档精确版本、locator 和有限文本；点击“引用到对话”才进入 `lib/outbox.ts` 的统一消息发送/排队/重试路径。API 校验引用范围并登记 MessageFileRef/来源依赖；选区变成消息的一部分后冻结，不随用户后续滚动变动。排队消息、插话、retry/edit、fork、分享均需继承各自精确引用，不在出队时重新取“当前选区”。

Chat 新增显式范围准备路径：选区问答只注入该范围和必要相邻结构、coverage；用户仍可普通附件发全文，原有完整准入规则保留。整篇总结不能因为打开选区而默认只读该段；任务范围由消息明确决定。大选区分页并显示只读范围，不把全文 prepare 绕过为静默截断。

用户滚动/换页/选中位置只更新短 TTL 查看状态，不逐事件落 RunEvent、不推给模型、不增长历史。`get_view` 仅在模型显式调用时取快照并追加结果；新消息中用户说“这里”时可附发送瞬间的 view 引用快照。浏览器只是状态来源，不是权限权威。

查看状态按 user/conversation/client/view 绑定，view_id 不由模型任意选择别人的；多端多视图不能靠全局 last-write-wins。UI ack 和截图通过 request_id+revision 关联，超时或版本已切换则旧响应忽略；所有等待有 deadline，刷新/关闭/断线触发不可用。

## 无浏览器、恢复和删除

定时任务、后台 Work、用户离线时：原生 read/search、文件制作、服务端渲染和视觉验收继续工作；get_view 明确 unavailable，reveal 可保存待展示命令但不声称已显示。`capture_view` 若请求“当前视口”则不能无声替换成第一页；请求指定文档范围可使用服务端渲染并标明来源。

reveal 命令需要 API 授权、attempt fencing、稳定 command_id 和 outbox/SSE；只发送有界目标，不把实时选区全部写进事件。重放不重复生成文件或抢占用户其他文档；旧版本命令遇到新版本显示明确版本对象，不能高亮新内容。

来源删除/撤权/回收站/分享访问沿用现有 API 围栏：禁止再次读取、清理截图和引用副本的可见性；运行上下文按既有 access_revoked 重建。截图是派生文件，有内容 hash、TTL、所有者和引用保留，不能无主地留在 workspace/对象存储或作为普通最终产物交付。

## 性能限制

上游 `ui/assets.ts:readFileBytes` 虽分块传输，最终仍分配完整 ArrayBuffer；`LoadedDoc` 还持有 bytes，viewer/worker 可能再复制。不能宣称已经流式或恒定内存。按格式设置明确的浏览器 native-view 大小/页数/单元格准入，ArrayBuffer 用 transfer/共享受控引用避免额外完整复制，超过上限走已有服务器分页预览并标明原生选区/对象定位不可用。

普通大文件仍能有界读取和查看，但只有在声明范围内通过 native 四格式交互，才能称完整交互 parity。切后台卸载重 viewer，保留轻量位置；关闭时释放 worker、object URL 和二进制缓存。解析/渲染按同文件版本复用，首屏不下载四套 viewer 或全页图片。新查询有界/索引，UI 高频状态不写大表；资源测量见[05](05-delivery-and-acceptance.md)。

## 接口定稿（2026-11-02，P3/P4 共同依据）

定位语法沿用 runtime anchor：docx `p:N[-M]`、`t:N[/rRcC]`；xlsx `Sheet[!A1[:B2]]`；pptx `slide:N[-M]`、`slide:N/shape:ID`；pdf `page:N[-M]`，1-based，API 用同版本 runtime 的 `check()` 规范化。`version` 指文件内容 hash（`content_revision`），与 `render_revision` 分开。

| 接口 | 合同 |
| --- | --- |
| `GET /v1/files/{id}/document` | 文档查看索引（Worker 用 runtime reader 生成并缓存为 `derived/{file_id}/viewer.json`，按 content hash＋parser revision 复用）：`{status: ready\|pending\|failed\|unsupported, file_id, version, parser_revision, kind, title, bytes, units, outline[{at,label,level}], blocks?[{at,text}]（仅 docx，有界）, native: bool, reason?}`。pending 时 Portal 轮询；`native=false` 表示超过原生查看准入（字节/页/单元格上限，P3 定值并测量），Portal 回退现有分页预览。分享页走分享身份的同形接口。字节仍由现有 `/v1/files/{id}/content` 流式提供。 |
| Work 未交付文件 | `GET /v1/runs/{run_id}/workspace/document?path=` 与 `.../workspace/content?path=`：仅当前用户、当前租约工作区、授权路径；返回同形索引及 `version=content_hash`、`workspace_revision`；交付后 Portal 切换到正式 file_id。 |
| 消息文档引用 | 发送体新增 `doc_refs: [{file_id, version, at, quote}]` 或 `[{run_id, path, version, at, quote}]`（每条 quote ≤4000 字，最多 8 条），与 `file_ids` 同一 outbox/client_send_id 路径。API 校验读取权限、版本存在、`at` 合法、quote 与该范围正文规范化后匹配，存入新表（不复用 `message_file_refs` 唯一键）；retry/edit/fork/排队继承冻结引用。Agent 收到的用户消息以追加文本块呈现引用（文件名、定位、引文），Chat 仅准备该范围＋必要相邻结构，不触发全文 prepare。 |
| 查看状态 | `PUT /v1/conversations/{id}/view`：`{view_id, file_id, version, at, selection?{at,text}}`，Portal 去抖（≥500ms）上报，短 TTL（10 分钟）临时存储，不写 RunEvent/业务大表；`DELETE` 于关闭侧栏/切走。按 user+conversation+view_id 隔离。 |
| 固定工具 `document_view` | Chat/Work 同 schema，`op` ∈ `open\|reveal\|get_view\|capture`。open/reveal：`{file_id\|path, targets?[{at,quote?}], note?}`，API 授权后经 outbox 发 `document.reveal` 事件 `{command_id, file_id?\|path?, filename, version, targets, note?}`，结果只表示“已发送”，不声称用户已看到。get_view：返回该会话活跃 view 快照；多个返回候选，无/过期返回 `unavailable`。capture：`{file_id\|path, at, source: viewport\|render}`；`render` 用服务端受限渲染（复用派生页图，按 file_hash+render_revision+范围缓存）；`viewport` 发 `client.call {call_id, kind: "capture_view", input: {view_id, at?, max_width?}, timeout_ms, deadline}`，Portal 通过现有 client-calls claim/result 回传（成功 value 为 `{image, mime_type:"image/png", width, height, at, version}` 对象，≤2 MiB decoded，绑定 view_id/version），Agent 等待有 deadline（≤20s），超时返回 `unavailable`，不改用第一页冒充。截图作为有 hash/TTL/所有者的派生物，以图片引用进入上下文与 checkpoint。 |

### 前端实现带来的修订（2026-11-02，以此为准）

- **引用两种目标**：`doc_refs` 每项为 `{file_id, version, at, quote}` 或 `{run_id, path, version, at, quote}`（Work 未交付文件；`path` 与该 Run 预览/工作区里的文件名一致）。轮次输出 `doc_refs` 时每项附 `filename`，Portal 在用户消息下渲染为可点击的引文，点击即在侧栏打开并定位。
- **`document.reveal` 载荷**：`{command_id, file_id? | path?, filename, version, targets[{at, quote?}], note?}`；`path` 为工作区文件名，对应发出事件的 Run。Portal 按 `command_id` 去重。
- **视口截图复用 `client.call`**，不新增 capture_request 事件与上传端点：Agent 发 `client.call {call_id, kind: "capture_view", input: {view_id, at?, max_width?}, timeout_ms, deadline}`；只有正显示该 `view_id` 的标签页会认领（其余标签页不 claim），结果经现有 `POST /v1/runs/{run_id}/client-calls/{call_id}/result` 回传：成功 `{ok: true, value: {image: <PNG base64>, mime_type: "image/png", width, height, at, version}}`，base64 ≤ 2 MiB×4/3（超出时 Portal 自动缩窄重拍），失败 `{ok: false, error}`。API 校验 `version` 与 view 状态一致、图片大小与格式，再作为派生物进入上下文；超时与无人认领沿用 client.call 合同返回 `unavailable`。

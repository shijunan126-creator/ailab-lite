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

Work 尚未交付的文件使用 `run_id + authorized_path + version(content_hash)`；路径只允许当前租约工作区，UI 只取得受控渲染/内容绑定。交付后替换为正式 file_id/版本。旧版本引用不自动指向最新 head；同名重建必须生成新内容身份并取消旧加载/截图。

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

在线续期使用当前可见Office视图的低频有界快照，不新增后台渲染、模型消息或数据库事件。前端交互去抖上报；静止时约每3分钟续期，页面隐藏停止，恢复可见重新同步，关闭/卸载清理。后端仍保持10分钟TTL以回收失联视图。同一已授权工作区快照的重复续期只复核会话/Run归属及运行状态；首次、过期或快照变化走完整验证。`get_view`/capture使用前仍核对工作区当前版本，续期不把旧选区升级为已验证的新版本。

续期写入有服务端顺序合同：Portal为一次会话绑定生成独立view_id，切会话、释放后重开或新frame使用新身份，capture registry同步切换；所有实际PUT携带递增sequence。API在授权/快照前登记token/序号，授权后只允许最新token在120秒内提交；DELETE原子关闭并保留600秒有界记录，不以客户端abort/第二次DELETE推断服务器已停止。内存锁与Redis Lua语义一致；活跃16、总generation记录128，满额拒绝新身份且不驱逐有效围栏。sequence不进入get_view或模型上下文，旧客户端省略时仅按接收顺序兼容。记录无业务表或逐事件模型消息。


普通主输入框不自动附当前选区：模型需按viewer技能指导调用`document_view(op="get_view")`；显式“添加到对话”或“提问”则携带冻结doc_refs，优先于后来变化的查看状态。指导必须使用实际固定工具名，不能继续保留早期planned:P3/P4禁用说明。

查看状态按 user/conversation/client/view 绑定，view_id 不由模型任意选择别人的；多端多视图不能靠全局 last-write-wins。UI ack 和截图通过 request_id+revision 关联，超时或版本已切换则旧响应忽略；所有等待有 deadline，刷新/关闭/断线触发不可用。

## 无浏览器、恢复和删除

定时任务、后台 Work、用户离线时：原生 read/search、文件制作、服务端渲染和视觉验收继续工作；get_view 明确 unavailable，reveal 可保存待展示命令但不声称已显示。`capture_view` 若请求“当前视口”则不能无声替换成第一页；请求指定文档范围可使用服务端渲染并标明来源。

reveal 命令需要 API 授权、attempt fencing、稳定 command_id 和 outbox/SSE；只发送有界目标，不把实时选区全部写进事件。重放不重复生成文件或抢占用户其他文档；旧版本命令遇到新版本显示明确版本对象，不能高亮新内容。

来源删除/撤权/回收站/分享访问沿用现有 API 围栏：禁止再次读取、清理截图和引用副本的可见性；运行上下文按既有 access_revoked 重建。截图是派生文件，有内容 hash、TTL、所有者和引用保留，不能无主地留在 workspace/对象存储或作为普通最终产物交付。

## 性能限制

### 当前预览与渲染路径

| 入口 | 执行位置和复用 |
| --- | --- |
| 聊天四格式原生侧栏 | Worker受限子进程生成/缓存viewer索引，Portal读取原件，在隔离frame内按格式绘制；不是把每页从LibreOffice渲染沙箱取回。超准入/索引失败走既有分页回退。上传正文解析仍可使用Broker系统沙箱，与浏览器绘制是不同工作。 |
| 文件库/分享 | 四格式FilePreviewBody优先复用隔离原生viewer，索引/脚本失败或超准入才挂载PagedPreview并请求preview；无会话时隐藏提问/引用且不上传查看状态，正文仍可选中复制。Word/Excel的物理分页引用直接走分页，PDF页/PPT slide可原生定位。 |
| 分页回退 | API幂等outbox→Worker；Word/PPT/PDF等页面走Broker按用户复用渲染沙箱，结果存derived缓存。原生四格式普通library文件不再在ready/交付时提前排分页作业；显式回退、OCR/视觉页请求和模板缩略图仍保留服务端生成及复用。legacy格式继续既有有界预生成。 |
| Work正在制作的实时预览 | Broker只扫描变化文件并发kind=native的源身份/版本通知，不自动复制/解析/导出，也不占用render调度或触发预热。原生通过workspace/document、content打开；原生不可用时才请求workspace/preview，在当前Run沙箱按不可变快照生成PDF/页图/表格，并按内容hash＋格式＋renderer/镜像版本缓存。交付后切正式file_id入口。 |
| view_workspace_file / review_artifact | 当前Work沙箱按指定范围受限渲染，后者另返回结构证据，质量由模型判断；不是借用文件库的按用户渲染租约。 |
| 视觉附件/OCR、服务端capture | 文件页图优先复用派生预览；缺失范围按既有作业请求。render capture只支持可证明物理页映射的范围；工作区范围在当前沙箱渲染并按内容hash缓存。viewport capture由当前浏览器截图，不启动服务器渲染。 |

索引ready/native只代表准入，不代表浏览器renderer已成功。隔离frame的load/prepare和renderer onError经构建适配发送带nonce、revision的终态failed；宿主只处理当前window/nonce/revision，释放frame/下载/截图注册后进入既有授权回退。普通警告和旧文档错误不触发导出。原件访问拒绝、版本/字节数变化、网络错误或加密明确显示错误，不把它们当兼容性问题启动服务器渲染。详见[审查修复](../../../progress/updates/2026-11-03-office-review-fixes.md)。

这些路径共享权限/版本/资源限制和部分渲染脚本，尚非单一执行流水线。加载反馈可以共用UI组件，不应为了动画触发额外解析或重复导出。

上游 `ui/assets.ts:readFileBytes` 虽分块传输，最终仍分配完整 ArrayBuffer；`LoadedDoc` 还持有 bytes，viewer/worker 可能再复制。不能宣称已经流式或恒定内存。按格式设置明确的浏览器 native-view 大小/页数/单元格准入，ArrayBuffer 用 transfer/共享受控引用避免额外完整复制，超过上限走已有服务器分页预览并标明原生选区/对象定位不可用。

普通大文件仍能有界读取和查看，但只有在声明范围内通过 native 四格式交互，才能称完整交互 parity。切后台卸载重 viewer，保留轻量位置；关闭时释放 worker、object URL 和二进制缓存。解析/渲染按同文件版本复用，首屏不下载四套 viewer 或全页图片。新查询有界/索引，UI 高频状态不写大表；资源测量见[05](05-delivery-and-acceptance.md)。

## 接口定稿（2026-11-02，P3/P4 共同依据）

定位语法沿用 runtime anchor：docx `p:N[-M]`、`t:N[/rRcC]`；xlsx `Sheet[!A1[:B2]]`；pptx `slide:N[-M]`、`slide:N/shape:ID`；pdf `page:N[-M]`，1-based，API 用同版本 runtime 的 `check()` 规范化。`version` 指文件内容 hash（`content_revision`），与 `render_revision` 分开。

| 接口 | 合同 |
| --- | --- |
| `GET /v1/files/{id}/document` | 文档查看索引（Worker 用 runtime reader 生成并缓存为 `derived/{file_id}/viewer.json`，按 content hash＋parser revision 复用）：`{status: ready\|pending\|failed\|unsupported, file_id, version, parser_revision, kind, title, bytes, units, outline[{at,label,level}], blocks?[{at,text}]（仅 docx，有界）, native: bool, reason?}`。pending 时 Portal 轮询；`native=false` 表示超过原生查看准入（字节/页/单元格上限，P3 定值并测量），Portal 回退现有分页预览。分享页走分享身份的同形接口。字节仍由现有 `/v1/files/{id}/content` 流式提供。 |
| Work 未交付文件 | `GET /v1/runs/{run_id}/workspace/document?path=` 与 `.../workspace/content?path=`：仅当前用户、当前租约工作区、授权路径；返回同形索引及 `version=content_hash`；交付后 Portal 切换到正式 file_id。 |
| 消息文档引用 | 发送体新增 `doc_refs: [{file_id, version, at, quote}]` 或 `[{run_id, path, version, at, quote}]`（每条 quote ≤4000 字，最多 8 条），与 `file_ids` 同一 outbox/client_send_id 路径。API 校验读取权限、版本存在、`at` 合法、quote 与该范围正文规范化后匹配，存入新表（不复用 `message_file_refs` 唯一键）；retry/edit/fork/排队继承冻结引用。Agent 收到的用户消息以追加文本块呈现引用（文件名、定位、引文），Agent 仅追加冻结引用正文；不触发全文 prepare，额外相邻结构需模型另行读取。 |
| 查看状态 | `PUT /v1/conversations/{id}/view`：`{view_id, file_id? | run_id+path, version, at: string|null, selection?{at,text}}`，Portal 去抖（≥500ms）上报，短 TTL（10 分钟）临时存储，不写 RunEvent/业务大表；`DELETE` 于关闭侧栏/切走。按 user+conversation+view_id 隔离。 |
| 固定工具 `document_view` | Chat/Work 同 schema，`op` ∈ `open\|reveal\|get_view\|capture`。open/reveal：`{file_id\|path, targets?[{at,quote?}], note?}`，API 授权后经 outbox 发 `document.reveal` 事件 `{command_id, file_id?\|path?, filename, version, targets, note?}`，结果只表示“已发送”，不声称用户已看到。get_view：返回该会话活跃 view 快照；多个返回候选，无/过期返回 `unavailable`。capture：`{file_id\|path, at, source: viewport\|render}`；`render` 用服务端受限渲染（复用派生页图，按 file_hash+render_revision+范围缓存）；`viewport` 发 `client.call {call_id, kind: "capture_view", input: {view_id, at?, max_width?}, timeout_ms, deadline}`，Portal 通过现有 client-calls claim/result 回传（成功 value 为 `{image, mime_type:"image/png", width, height, at, version}` 对象，≤2 MiB decoded，绑定 view_id/version），Agent 等待有 deadline（≤20s），超时返回 `unavailable`，不改用第一页冒充。截图作为有 hash/TTL/所有者的派生物，以图片引用进入上下文与 checkpoint。 |

### Work按需分页回退（2026-11-03）

`GET /v1/runs/{run_id}/workspace/preview?path=`复用workspace/document的API归属、会话、运行中状态与Broker路径/租约验证。Broker先以拒绝符号链接、文件大小和变化检查的快照固定版本，再按内容hash、扩展名、render脚本/镜像版本缓存；命中仍先核对当前源身份。返回既有Preview的kind/pdf/pages/data及数字version，通过既有owner-bound run previews资产端点读取。只在原生失败/超准入的回退组件挂载时请求，切版本/关闭取消旧请求，切版本先清除旧画面。缓存和资产随既有Run预览生命周期回收，无业务表/迁移/新事件类型。渲染沿用受限工作区，队列等待2秒、导出65秒超时、快照上限20MiB和输出上限不放宽；更大活动文件明确不可用，交付后可按文件库准入处理。原生渲染不消除正文解析、索引、下载带宽、Work视觉检查和既有listener预热的成本。

### 前端实现带来的修订（2026-11-02，以此为准）

- **引用两种目标**：`doc_refs` 每项为 `{file_id, version, at, quote}` 或 `{run_id, path, version, at, quote}`（Work 未交付文件；`path` 是授权工作区源路径，如 `outputs/draft.docx`；与显示名 `filename` 分离）。轮次输出 `doc_refs` 时每项附 `filename`，Portal 在用户消息下渲染为可点击的引文，点击即在侧栏打开并定位。
- **`document.reveal` 载荷**：`{command_id, file_id? | path?, filename, version, targets[{at, quote?}], note?}`；`path` 为授权工作区源路径，对应发出事件的 Run；显示名另用 `filename`，原生打开不等待分页预览生成。Portal 按 `command_id` 去重。
- **视口截图复用 `client.call`**，不新增 capture_request 事件与上传端点：Agent 发 `client.call {call_id, kind: "capture_view", input: {view_id, at?, max_width?}, timeout_ms, deadline}`；只有正显示该 `view_id` 的标签页会认领（其余标签页不 claim），结果经现有 `POST /v1/runs/{run_id}/client-calls/{call_id}/result` 回传：成功 `{ok: true, value: {image: <PNG base64>, mime_type: "image/png", width, height, at, version}}`，base64 ≤ 2 MiB×4/3（超出时 Portal 自动缩窄重拍），失败 `{ok: false, error}`。API 校验 `version` 与 view 状态一致、图片大小与格式，再作为派生物进入上下文；超时与无人认领沿用 client.call 合同返回 `unavailable`。

整合实现补充：XLSX选区引用以TSV承载值和原始公式；后端从同一runtime的授权范围解析结果中移除阅读用表头/行号，保留单元格内容。公式两端均引用原表达式，不把浏览器临时计算的未缓存值当作源文件内容。Work原生源路径通过preview.path保留，reveal可在分页预览完成前建立原生标签；没有源路径的历史预览沿用分页回退。

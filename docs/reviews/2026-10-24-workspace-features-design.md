# 2026-10-24 工作区功能设计：项目说明、品牌套件、通知、排队/插话、分享、资料库、移入项目

用户授权（2026-10-24）：实现下列 7 项（不做“管理员用量看板”），主 Agent（Claude）负责前端、整体架构与审核，后端交 Codex `gpt-6.1-sol` medium；测试通过后提 PR 并合并。**未授权部署。** 基线 `main@74e66c0`。

本文件是三个后端分支与 Portal 的共同契约。改契约先改这里。

## 现状（核对过代码）

- 会话移入项目已有：`PATCH /v1/conversations/{id} {project_id}`，Portal 会话“⋯”菜单可选项目。缺的是“文件跟着走”和产物卡片上的“移到项目”。
- 产物本就是资料库文件（`purpose=library`），落在会话所在项目；非项目会话落在“全部文件”。批量移动 `POST /v1/files/batch {action:"move"}` 已有。
- 扫描版 PDF 的 OCR 已有（T19）：READY 事务内排队，`meta.ocr`、`meta.ocr_text_key`；`read_library_file` 已读取 OCR 文本。需核实 OCR 文本带页标记、页码引用可用。
- 正在回答时发送：`start_run` 内 `require_idle` 拒绝。Portal 回答中禁用发送。
- PPT 技能是 JSON→pptx 生成器（`images/sbx-office/skills/pptx/scripts/build.py`），已支持 `theme.primary/accent` 自定义；没有母版、logo、字体。
- 品牌/通知/分享/排队均无。

## 迁移分配

三个分支并行，开发时各自 `down_revision = "0024"`；集成时由父 Agent 依次改链为 0025←0024、0026←0025、0027←0026，并做双库升/降/再升回归（见 HANDOFF“独立迁移分支合并”）。

| 分支 | 迁移 | 内容 |
|---|---|---|
| A `feat/projects-brand` | `0025_project_instructions` | `projects.instructions` |
| B `feat/inbox-queue` | `0026_inbox_queue` | `notifications`、`queued_messages` |
| C `feat/shares-library` | `0027_shares` | `shares`、`share_recipients` |

品牌、文件摘要不需要迁移（`platform_settings`、`files.meta`）。

---

## 1. 项目说明（A）

- `projects.instructions TEXT NOT NULL DEFAULT ''`，≤ 4000 字。`ProjectOut` 增 `instructions: str`；`PATCH /v1/projects/{id}` 接受 `{instructions}`（可与 `name` 同时或单独）。
- Agent bootstrap 增 `project_instructions: str`（仅当会话属于项目且非空）。系统提示在记忆之前插入一节“项目说明（用户为本项目设定，适用于本项目全部对话）”。平台规则优先；说明不能授予工具或越权。
- 冻结：首轮组装上下文时进入 checkpoint 的 messages；续跑不重读。定时任务在项目内同样生效。
- Portal：项目页顶部“项目说明”卡片：无内容时一行占位“添加项目说明”，点开为文本框，失焦/保存键提交。

## 2. 公司品牌套件（A）

数据：`platform_settings["brand"]`

```json
{"enabled": true, "name": "公司名", "colors": {"primary": "#1F4E79", "accent": "#E0A526"},
 "font": "思源黑体", "logo_file_id": "fil_…", "pptx_template_file_id": "fil_…", "docx_template_file_id": "fil_…",
 "version": 3, "updated_at": "…"}
```

- 资产文件 `purpose="brand"`（不进资料库列表、不计个人配额），上传者为管理员；扫描同上传流程。替换时旧资产保留到新资产 READY 后再删除（不出现空窗）。
- Worker 在受限子进程分析 pptx 模板：幻灯片尺寸、主题色、主题字体、版式名列表、母版数，写 `meta.brand`；失败则资产不可用并给出原因。docx 模板只校验可打开并列出样式名（≤ 50）。logo 仅 PNG/JPEG/SVG ≤ 2 MiB，SVG 由 Worker 栅格化成 PNG 后使用。
- 平台管理员接口（scope 同 `/v1/admin/settings/context`）：
  - `GET /v1/admin/brand` → 上述设置 + 每个资产 `{file_id, filename, status, size, analysis, thumbnail_url}`
  - `PATCH /v1/admin/brand` `{enabled?, name?, colors?, font?}`（颜色 `#RRGGBB` 校验；字体 ≤ 60 字）
  - `POST /v1/admin/brand/assets/{kind}`（`kind ∈ logo|pptx|docx`，multipart `file`）→ 资产 DTO，status 先为 pending
  - `DELETE /v1/admin/brand/assets/{kind}`
  - `GET /v1/admin/brand/assets/{kind}/content`、`/thumbnail`（管理员预览）
- 运行：Work 模式且 `enabled` 时，Agent bootstrap 带 `brand {version, name, colors, font, assets:{kind: file_id}}`；租约开始时 Broker 把 READY 资产注入 `/workspace/.brand/`（`logo.png`、`template.pptx`、`template.docx`、`brand.json`）。内部授权：brand 资产对任何运行可读，但只能经此注入路径，不能被 `read_library_file`/`copy_library_file` 读取。版本号冻结在 run 的 `runtime_meta.brand_version`。
- 技能：
  - PPT 生成器：存在 `/workspace/.brand/brand.json` 时默认套用（deck.json `"brand": false` 关闭）——颜色覆盖主题主/强调色、字体用于标题和正文、logo 放在封面和内容页角标；有 `template.pptx` 时以其母版为底（尺寸、背景、主题字体），生成器自己的版式画在模板的空白版式上，删除模板自带幻灯片。
  - SKILL.md（pptx、xlsx）与 Work 系统提示：说明品牌目录、默认使用、用户明确要求时才不用；Word 报告用 `Document("/workspace/.brand/template.docx")` 并沿用其样式。
  - 需重建 `sbx-office` 镜像；PR 写明镜像变化与成本。
- Portal：管理 → 新标签“品牌”：开关、名称、两个色块选择、字体、三个资产槽（上传/替换/删除，显示缩略图或分析出的版式与主题色）。

## 3. 完成通知与收件箱（B）

- 表 `notifications`：`id, user_id, kind, title(≤200), conversation_id, run_id, share_id, created_at, read_at`。索引 `(user_id, created_at, id)`、未读部分索引 `(user_id) WHERE read_at IS NULL`。每用户保留最近 200 条（插入时裁剪，同事务）。
- `kind`：`run.succeeded`、`run.failed`、`run.waiting`（等待用户回答 ask_user）、`schedule.succeeded`、`schedule.failed`、`share.received`。
- 何时生成（与 Run 状态变更同一事务；`transition_run` 的调用方或其钩子内）：
  - 定时任务发起的 Run（start 时 `options.schedule_id`）进入终态 → `schedule.*`；
  - 其他 Run 进入 `succeeded/failed` 且 `finished_at - created_at ≥ settings.notify_after_seconds`（默认 60）→ `run.*`；
  - 任一 Run 进入等待用户输入 → `run.waiting`（同一 Run 只一条）；
  - 分享给我（C 调用 B 的 `create_notification(db, …)`；集成前 C 暂以 no-op 适配，集成时接上）。
  - `canceled` 不通知。
- 接口：
  - `GET /v1/notifications?cursor=&limit=` （limit ≤ 50）→ `{items:[Notification], next_cursor, unread}`
  - `GET /v1/notifications/unread` → `{unread: int, latest_at: datetime|null}`（只走索引，供 30 s 轮询）
  - `POST /v1/notifications/read` `{ids?: [str], all?: bool, conversation_id?: str}` → `{unread}`
  - `Notification = {id, kind, title, conversation_id, run_id, share_id, created_at, read_at}`；`title` 为会话或任务标题。
- Portal：侧栏用户区上方“通知”铃铛 + 未读数；弹层列表（未读加粗，点击跳转并标为已读，“全部已读”）。轮询 `/unread` 30 s，仅页面可见时；`latest_at` 变化时，若页面隐藏且已授权浏览器通知，则显示系统通知（权限只在用户第一次打开收件箱时请求）。打开某会话时对该会话 `read {conversation_id}`。

## 4. 回答进行中继续发消息：排队与插话（B）

- 表 `queued_messages`：`id, conversation_id, user_id, prompt, file_ids JSON, model, options JSON(mode/skill), steer bool, state, after_run_id, run_id, created_at, updated_at`。`state ∈ queued|steered|started|canceled`。索引 `(conversation_id, state, created_at, id)`。每会话最多 5 条 `queued`。
- 接口：
  - `POST /v1/conversations/{id}/queue` `{prompt, file_ids, model, mode, skill, steer}` → `QueuedMessage`。只在活动叶 Run 处于活跃状态（queued/running/waiting）时接受；否则 409 `{detail:"对话空闲，请直接发送"}`。文件、技能、配额校验同 `start_run`。
  - `PATCH /v1/conversations/{id}/queue/{qid}` `{prompt?, steer?}`（仅 `queued`）
  - `DELETE /v1/conversations/{id}/queue/{qid}`（→ canceled）
  - `POST /v1/conversations/{id}/queue/resume` → 启动下一条（用于暂停后）
  - `ConversationDetail.queue: [QueuedMessage]`，`ConversationDetail.queue_paused: bool`
  - `QueuedMessage = {id, prompt, file_ids, model, mode, skill, steer, state, created_at}`
- 派发（同一事务经 outbox，不在提交后补发）：活动叶 Run 进入 `succeeded` 时，取最早一条 `queued`（含未被领取的 steer）调用 `start_run(locked=True, commit=False)`，`parent_run_id` = 该 Run，置 `started`/`run_id`。一次只派一条，下一条等新 Run 结束。Run `failed/canceled` 或用户切换版本/编辑使活动叶改变 → 队列暂停（`queue_paused=true`），由用户“继续”或删除。会话删除 → 全部 canceled。
- 插话（steer）：Agent 每次模型调用前，若心跳响应带 `steer_pending: true`，调用 `POST /internal/runs/{id}/steer/claim`（scope agent，绑定 run_id）原子领取全部 `steer=true AND state=queued` 的消息（→ `steered`，`run_id`=当前），以用户消息追加到 checkpoint 的 messages，并写事件 `agent.steered {queued_id, prompt}`。checkpoint 保证只注入一次；续跑不重复领取。Run 结束前未被领取的 steer 消息按普通排队派发。插话内容随该 Run 进入之后各轮的历史（`history_messages` 在该 Run 的 prompt 之后按序加入）。
- 只在 `running` 状态领取。Run 处于 `waiting`（ask_user）时，提问仍走原回答流程，插话与排队消息都只排队，不当作对提问的回答。
- Portal：回答中输入框保持可用；发送键变“排队”图标，Alt/⌥+Enter 或排队条上的“立即插入”设为插话。输入框上方贴边显示排队条（每条一行：内容、插入/编辑/删除），暂停时显示“继续”。插话在当前 turn 的过程里显示为“补充：…”。

## 5. 内部只读分享（C）

- 表 `shares`：`id, owner_id, kind(conversation|file), conversation_id, through_run_id, file_id, audience(people|everyone), title, created_at, updated_at, revoked_at`；`share_recipients(share_id, user_id)` 主键二者，索引 `(user_id, share_id)`。
- 接口：
  - `POST /v1/shares` `{kind, conversation_id?, file_id?, audience, user_ids?}` → `Share`。会话分享快照为当前活动路径（`through_run_id` = 活动叶，须为终态）；文件分享固定该文件版本。
  - `GET /v1/shares?scope=mine|received&cursor&limit` → `{items, next_cursor}`
  - `GET /v1/shares/for?conversation_id=|file_id=` → 我对该对象的有效分享（菜单显示状态）
  - `PATCH /v1/shares/{id}` `{audience?, user_ids?, refresh?: true}`（refresh 把快照推进到当前活动叶）
  - `DELETE /v1/shares/{id}` → 撤销
  - `GET /v1/shares/{id}/view?cursor` → `{share, owner:{display_name}, title, turns:[{id, prompt, answer, created_at, mode, artifacts:[ShareFile], attachments:[{filename}]}], next_cursor}`（每页 ≤ 20 轮，路径 ≤ 500）
  - `GET /v1/shares/{id}/files/{file_id}/content|preview|preview/{name}|thumbnail`：仅快照内 Run 的产物或被分享的文件本身。
  - `GET /v1/users/lookup?q=` → `[{id, username, display_name}]`（≥1 字、≤ 8 条、仅活跃用户、前缀匹配，排除自己）
  - `Share = {id, kind, conversation_id, file_id, audience, recipients:[{id, display_name, username}], title, url:"/s/<id>", created_at, updated_at, through_run_id}`
- 授权：所有者总可见；`everyone` 对任何已登录活跃用户可见；`people` 仅接收人。撤销、对象删除/进回收站、所有者停用 → 404。只读：不分享思考、工具步骤、输入附件内容（只显示文件名）、记忆。审计日志记录创建/修改/撤销。新接收人收到 `share.received` 通知。
- Portal：会话“⋯”菜单与产物卡片“分享…”→ 弹窗：受众（指定同事 / 所有同事）、同事选择（输入查找）、复制链接、更新到最新、撤销。`/s/<id>` 只读页（与打印页同风格的轮次渲染 + 产物卡片可预览/下载）。侧栏“分享给我”入口放在收件箱内（通知跳转），不加新导航项。

## 6. 资料库：摘要、OCR 核验、就文件提问（C）

- 摘要：库文件 READY 且可提取文本（解析文本或 OCR 文本）≥ 200 字时，API 作业用 `ModelPolicy.summary_model()`（quiet、关闭思考若模型声明支持）生成 ≤ 120 字摘要，写 `meta.summary {status: done|failed|skipped, text, model, at}`；`call_id = summary:<file_id>` 幂等，计入文件所有者用量并冻结计价。只取前 ~6k token 输入。OCR 稍后完成且原先无文本时再生成一次。平台设置 `platform_settings["library"] {summaries: true}`，管理员可关闭（管理 → 上下文页加一个开关，归 A/C 契约：C 提供 `GET/PATCH /v1/admin/settings/library`）。
- `search_library`/`list` 工具结果附带摘要（≤ 120 字），帮助模型选文件。
- OCR 核验：扫描 PDF → OCR 文本保留 `=== 第N页 ===` 等现有页标记，`read_library_file(pages=…)` 可按页读，页码引用跳页正确；若不满足，在原因处修。
- “就这个文件提问”：纯 Portal——文件查看器与文件卡片菜单“提问”→ 新对话（文件所在项目内）并附上该文件、聚焦输入框。

## 7. 移入项目时带上文件（A）

- `PATCH /v1/conversations/{id}` 增可选 `move_files: bool`（仅与 `project_id` 同时有效）：把该会话所有 Run 的产物（本人、库文件、未删除）按 lineage 移到目标项目（`move_lineage`，重名按现有规则改名），同事务。响应 `ConversationOut` 增 `moved_files: int | null`。
- Portal：会话菜单选项目后，若会话有产物，菜单项下给二级确认“同时移动 N 个文件”（默认勾选）；产物卡片“⋯ → 移到项目”走已有批量移动。

---

## 共同约束

- 每个新列表接口有界/分页；新查询有索引并在 SQLite 与 PG 上 `EXPLAIN`。指标标签低基数。
- 新的平台设置与 `AILAB_PROFILE=secure` 检查：品牌资产必须经扫描；`notify_after_seconds` 在 config 校验范围（10–3600）。
- 不新增依赖，除非 PR 写明成本；Worker 分析在既有受限子进程中执行。
- Portal 只用 `app/globals.css` 令牌，图标来自 `components/icons.tsx`，弹窗/菜单 `createPortal`，可被取代的请求可取消。

## 成本预估（需在 PR 中以实测更新）

- 通知：每次长任务终态 1 次插入 + 裁剪（按索引），轮询 `/unread` 每用户 30 s 一次的索引计数。
- 排队：终态事务内 1 次索引查询；插话仅在心跳标记时多 1 次内部调用。
- 分享：查看按页读取快照路径（≤ 20 轮/页）。
- 摘要：每个库文件一次便宜模型调用（输入 ≤ 6k token、输出 ≤ 200 token），计入用户用量；可关闭。
- 品牌：模板分析每次上传一次；每个 Work 租约注入 ≤ 3 个文件（模板通常 < 5 MiB）。

## Portal 对契约的补充（主 Agent，集成时由各分支对齐）

- 分享：每个所有者对同一对象只有一个有效分享。`GET /v1/shares/for?…` 返回 `Share | null`；对已有有效分享的对象再 `POST /v1/shares` 返回该分享（按请求更新受众/接收人），不建第二个。文件分享的 `GET /v1/shares/{id}/view` 带 `file: ShareFile`、`turns: []`。`ShareFile.download_url`/`thumbnail_url` 为分享作用域的 API 相对路径（`/v1/shares/{id}/files/{fid}/…`），Portal 加 `/api` 前缀。
- 排队：会话空闲时 `POST …/queue` 的 409 `detail` 含“空闲”，Portal 据此改为直接发送；其余 409/422 显示原因。`RunDetail.steered: [{id, prompt}]`（按领取顺序），刷新后仍显示该轮的插话。
- 资料库设置：`GET/PATCH /v1/admin/settings/library` → `{summaries: bool}`。
- 品牌：资产上传 multipart 字段名 `file`；资产 DTO 的 `thumbnail_url` 为 API 相对路径（无则 null）；`analysis` 键：`theme_colors: ["#RRGGBB"]`、`fonts: {major, minor}`、`layouts: [str]`（pptx）、`styles: [str]`（docx）、失败时 `error: str`。
- 移入项目：`PATCH /v1/conversations/{id}` 带 `move_files`；`ConversationOut.active_run_id` 已有，Portal 用于分享“更新到最新”。

## A 分支对契约的修改

- **SVG 暂拒绝**：logo 上传只接受 PNG/JPEG（≤ 2 MiB），Worker 校验真实格式并归一化为 PNG。当前 Worker 依赖没有 SVG 栅格化器；Pillow 不解码 SVG。为不增加原生渲染依赖及其内存/攻击面，本轮返回 422，不把 SVG 原文注入沙箱。
- **旧资产物理留存**：READY 切换和 DELETE 立即移除旧资产的有效设置指针，后续运行使用新设置；旧不可变文件暂留，供已冻结 `runtime_meta.brand` 的运行续跑。立刻物理删除会破坏冻结/续跑契约；现有文件 GC 不跟踪这种跨用户品牌引用，本轮不新增无索引的全历史 Run JSON 扫描。旧版本/被覆盖的候选资产暂不自动回收，管理员反复替换会增加平台磁盘占用；父任务需明确后续引用生命周期/回收策略，不能当作已有 GC 验收。
- **有界资产**：每个 PPTX/DOCX 模板 ≤ 10 MiB，仍受现有 `max_upload_bytes` / `max_parse_bytes` 的较小值约束；归一化 logo ≤ 2 MiB、≤ 1600 万像素。每个 Work 租约最多三个资产、合计 ≤ 22 MiB（加 ≤ 64 KiB 的 brand.json），Broker 下载与 provider 写入都执行字节上限。
- **后台候选与有效版本分离**：GET 返回的 `assets[kind]` 显示最新 pending/rejected 候选（无候选时显示有效资产），顶层 `*_file_id` 始终是当前 READY 有效资产。上传不递增有效版本；成功分析激活、设置 PATCH、资产 DELETE 才递增。模板暂不生成缩略图，`thumbnail_url=null`；logo 提供 WebP 缩略图。

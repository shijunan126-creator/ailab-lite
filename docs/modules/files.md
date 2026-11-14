# 文件

文件模块管理资料库中的不可变文件版本：上传、安全扫描、按需解析与 OCR、派生资源缓存、Chat/Work 的附件输入、交付件与版本、资料库列表和回收站。它不负责页面与查看器 UI（[Portal](portal.md)）、沙箱租约和 `/workspace/inputs` 注入（[沙箱](sandbox.md)）、模型上下文与请求前缀（[Agent](agent.md)）、业务授权与配额账本（[API](api.md)）。

## 代码地图

| 要改的东西 | 位置 |
|---|---|
| 上传、预签名上传、完成上传 | `platform/ailab_platform/api/routes_files.py::upload_file`、`::presign_upload`、`::complete_upload` |
| 上传操作幂等与内容复用 | `platform/ailab_platform/api/upload_identity.py::replay`、`::reusable`、`::receipt_for` |
| 文件、交付、上传收据模型 | `platform/ailab_platform/models.py::FileObject`、`::UploadReceipt`、`::Artifact`、`::RunDelivery` |
| Worker 消费文件队列 | `platform/ailab_platform/worker/main.py::handle_message`、`::_handle` |
| 扫描、类型/压缩包安全检查、缩略图 | `platform/ailab_platform/worker/pipeline.py::_process_file`、`platform/ailab_platform/worker/safety.py::inspect` |
| 受限解析子进程 | `platform/ailab_platform/worker/isolate.py::execute`、`platform/ailab_platform/worker/parsers.py::extract_path` |
| 文本/代码解析 | `platform/ailab_platform/worker/plain_text.py::prepare` |
| Office/PDF 系统解析 | `platform/ailab_platform/api/native_preparation.py::request_native`、`platform/ailab_platform/worker/native_preparation.py::process_native`、`platform/ailab_platform/worker/office.py::read`、`platform/ailab_platform/broker/documents.py::DocumentReader` |
| 原生索引与文档块 | `platform/ailab_platform/api/document_sources.py::index_document`、`platform/ailab_platform/api/document_content.py::manifest`、`::unit_bytes` |
| Chat 输入准入与冻结计划 | `platform/ailab_platform/api/document_input_plan.py::prepare`、`::input_files`、`platform/ailab_platform/domain/document_input_plan.py::choose_inline` |
| 模型看到的附件内容 | `platform/ailab_platform/api/attachment_input.py::input_files`、`platform/ailab_platform/agent/prompts.py::user_prompt` |
| OCR 范围、缓存与执行 | `platform/ailab_platform/api/document_ocr.py::request_scope`、`platform/ailab_platform/api/routes_document_ocr.py::step`、`::execute_image`、`platform/ailab_platform/worker/document_ocr.py::process` |
| 预览请求、调度与生成 | `platform/ailab_platform/api/preview_requests.py::request_preview`、`platform/ailab_platform/api/preview_dispatch.py::dispatch_preview_jobs`、`platform/ailab_platform/worker/preview.py::process_preview` |
| 预览、缩略图与页图读取 | `platform/ailab_platform/api/routes_files.py::download_thumbnail`、`::get_preview`、`::preview_asset` |
| 派生容量账本 | `platform/ailab_platform/api/derived_assets.py::reserve`、`::finish`、`::publish_writer`、`::evict_oldest`、`platform/ailab_platform/derived_writes.py::AccountedWrites` |
| 交付提交 | `platform/ailab_platform/api/routes_internal.py::_commit_artifact`、`platform/ailab_platform/broker/service.py::LeaseManager.commit` |
| 项目与文件移动、星标 | `platform/ailab_platform/api/routes_projects.py::delete_project`、`platform/ailab_platform/api/routes_files.py::patch_file`、`platform/ailab_platform/library.py::move_lineage` |
| 版本、移动、回收站 | `platform/ailab_platform/api/routes_files.py::file_versions`、`::restore_version`、`::batch_files`、`platform/ailab_platform/library.py::trash_lineages`、`::purge_lineages` |
| 资料库与文件夹列表 | `platform/ailab_platform/api/routes_files.py::list_files`、`::list_file_folders`、`platform/ailab_platform/api/routes_library_tools.py::library_query` |
| 原生查看索引 | `platform/ailab_platform/api/routes_files.py::get_document`、`platform/ailab_platform/api/document_view.py::index`、`platform/ailab_platform/worker/viewer.py::process_viewer` |
| 浏览器截图 | `platform/ailab_platform/api/viewer_captures.py::save`、`platform/ailab_platform/models.py::ViewerCapture` |

## 关键流程

### 上传到安全可用
API 负责接收与记账，Worker 负责扫描；解析只在 Worker 的受限子进程或 Broker 的渲染沙箱里发生。

1. `POST /v1/files`（或 `POST /v1/files/presign` 加 `POST /v1/files/{id}/complete`）在用户锁内检查项目归属和配额；请求体按 1 MiB 块流式写入临时文件并计算 SHA256，超过 `max_upload_bytes` 立即 413（`platform/ailab_platform/api/routes_files.py::upload_file`）。
2. 带 `client_upload_id` 时，`UploadReceipt` 按请求指纹重放；没有 `parent_id` 且 purpose 为 `library` 时，同一用户、项目、原始文件名、MIME、大小和 SHA256 的 READY head 由 `platform/ailab_platform/api/upload_identity.py::reusable` 复用并返回 `reused=true`，只有 `disposition=copy` 跳过复用。
3. 新文件行是 `FileStatus.PENDING`，`stage_job(db, "file", file_id=...)` 与文件行在同一事务进入 outbox（`platform/ailab_platform/api/jobs.py::stage_job`）。
4. Worker 领取 job，把原件下载到临时文件并调用 Scanner 端口；`infected` 直接置 `rejected`，`unavailable` 有界重试后写 `scan_exhausted`，扫描失败绝不放行（`platform/ailab_platform/worker/main.py::_handle`、`platform/ailab_platform/worker/pipeline.py::_process_file`）。
5. 普通资料库上传带 `input_lifecycle=2`：扫描 clean 后只做类型、压缩包和摘要/大小校验（`platform/ailab_platform/worker/safety.py::inspect`），通过即 `READY`。此时正文、页图、OCR、缩略图都是 `absent`；Work 可以立即取原件，Chat 读取时才进入下一节的按需准备。扫描通过不等于解析完成（`platform/ailab_platform/worker/pipeline.py::_process_file_impl`）。
6. 旧任务、模板、品牌和技能导入没有这个标记，继续走 Worker 受限子进程或 Broker 系统沙箱里的解析，见下节。

### 按需解析与 OCR
1. Chat 发送或显式读取时，`platform/ailab_platform/api/native_preparation.py::request_native` 为文件建 native job；`native_kind` 根据安全扫描确认的实际类型决定是否支持。PDF 先 probe，需要全文时才改 full；超过 `max_parse_bytes` 或 Chat 的 20 MiB 上限直接 413。
2. Worker `process_native` 领取；文本/代码在受限子进程里流式读取（`platform/ailab_platform/worker/isolate.py::execute`、`platform/ailab_platform/worker/plain_text.py::prepare`）；Office/PDF 经 Broker 短生命周期 render 沙箱产出受限 tar，Worker 只做大小、SHA256、条目形状和逐块摘要校验（`platform/ailab_platform/worker/native_preparation.py::unpack`；旧整包解析走 `platform/ailab_platform/worker/office.py::read`）。
3. Native 结果写成 `derived/{file_id}/office/{job}-{attempt}/manifest.json` 和 `text.txt`，旧整包解析写成 `derived/{file_id}/office/{job}/...`；API `platform/ailab_platform/api/native_preparation.py::complete` 在用户锁内发布对象，并把 manifest 拆成 `FileDerivation` 与 `DocumentBlock`（`platform/ailab_platform/api/document_sources.py::index_document`）。
4. 解析失败只把 `representations.native_text` 置 failed，不改变文件的安全 READY，也不返回空正文。
5. OCR 不在上传或解析里自动发生。Chat 的显式 `read_document(text_source="ocr")` 由 `platform/ailab_platform/api/document_ocr.py::request_scope` 建 `chat_ocr` operation；Work 的工作区图片由 `platform/ailab_platform/api/routes_workspace_ocr.py::prepare` 建 `workspace_ocr`，两者共用 `ocr` lane 和执行器。Worker `platform/ailab_platform/worker/document_ocr.py::process` 准备页图或裁剪并逐步提交，推理都在 API（Chat 走 `platform/ailab_platform/api/routes_document_ocr.py::execute_image`，Work 走 `platform/ailab_platform/api/routes_workspace_ocr.py::status`）；文件页结果按 `(file_id, source_version, 引擎指纹, selector)` 唯一存入 `DocumentOcrUnit`。
6. 页面图来自渲染并记录在 `RenderedPage`（版本 `poppler-1280-v1`）；Word/Excel 的物理页由渲染决定，不能用结构锚点代替页号。

### 首次打开预览
1. `GET /v1/files/{id}/preview` 先读 `file.meta.preview`。未生成或旧记录需要补扫时，`platform/ailab_platform/api/preview_requests.py::request_preview` 写 pending manifest、`preview_job_id` 和 outbox；job 仍存在时复用同一个 job。失败的预览不会自动重试。
2. Worker `process_preview` 先取 `ailab:preview:{file_id}` 锁和进程内单渲染槽，重复消息不会第二次渲染；同一文件版本的成功预览只生成一次，只有失败、补扫或元数据失效后才产生新 job。图片、文本和本地表格在受限子进程完成，Office/PDF 交给 Broker render 任务。
3. 生成物写到 `derived/{file_id}/preview/{job_id}/` 并逐页发布；API `platform/ailab_platform/api/routes_internal.py::publish_preview_page`、`::complete_file_preview` 校验 job、页号、key 前缀和容量账本后才写 manifest、`RenderedPage` 和文件依赖唤醒。
4. `GET /v1/files/{id}/preview/{name}` 只服务 manifest 列出的名字，返回 `Cache-Control: private, ... immutable` 与由内容摘要和派生版本算出的 ETag；缩略图同理。轮询预览状态只读元数据，不读对象存储。

### Chat 与 Work 的文档输入
1. Chat 在创建 Run 前（`POST /v1/chat/prepare` 或发送入口）由 `platform/ailab_platform/api/document_input_plan.py::prepare` 按 `client_send_id` 冻结输入计划：每份文件的 `file_id`、`sha256`、版本、kind、可用表示、token/图片数和是否 inline。计划随后写入 `Run.options.document_input_plan`。
2. 计划只做有界准入：单份/合计 20/40 MiB，正文总量取 24K token 与模型输入预算 20% 的较小值，内联视觉页合计最多 8 张；`platform/ailab_platform/domain/document_input_plan.py::choose_inline` 从最大的整份文件开始移出 inline，不截断正文、不偷偷少发页。模型不支持工具而又有文件不能 inline 时明确拒绝。
3. 文件没有可用 native 索引时触发 native job 并返回 `chat_attachment_processing`；视觉页未就绪时请求页图。准备使用持久 operation 和 600 秒绝对截止。
4. 装配请求时 `platform/ailab_platform/api/attachment_input.py::input_files` 复验计划与当前 `sha256`、native 索引 revision、页图 digest；版本或派生 revision 变化返回 409 让用户重发。`platform/ailab_platform/agent/prompts.py::user_prompt` 只把计划中的正文、导航和图片部件追加到用户消息。
5. Work 不冻结 inline 计划：`platform/ailab_platform/api/attachment_input.py::input_files` 只给原件身份与 `/workspace/inputs` 位置，模型用沙箱内 office 技能按需读取；只有图片附件在模型支持 vision 时按需准备 `display.webp`。Chat 文档工具 `read_document`、`search_document`、`view_document` 不进 Work（`platform/ailab_platform/domain/document_tools.py::permitted`）。

### 交付件与两阶段提交
1. 模型把文件写到 `/workspace/outputs` 后调用交付；Broker `LeaseManager.commit` 只接受该目录内的普通文件，先复制到 `staging/{lease_id}/{uuid}/{name}` 并计算大小和 SHA256。沙箱侧细节见 [沙箱](sandbox.md)。
2. Broker 调 `POST /internal/artifacts`，携带以 `run_id:` 开头的稳定 `X-Delivery-Id`；API 是唯一写库方。
3. `platform/ailab_platform/api/routes_internal.py::_commit_artifact` 要求 Run 正在运行，校验 staging 前缀、大小、SHA256 和扫描结果，再复制到 `runs/{run_id}/{file_id}/{filename}`，写 `FileObject(READY)`、`Artifact`、`RunDelivery`、`artifact.created` 并排缩略图。
4. `(run, delivery_id)` 是交付幂等键：同 id 重试返回原结果，内容不同返回 422。同名输出槽只在当前可见祖先链上匹配 `output_key` 或 `artifact_filename`；内容相同复用同一 `file_id`，内容改变分配新 `version`。`filename` 是资料库显示名，`meta.artifact_filename` 保留工作区原名。
5. 任何拒绝都会删除 staging；数据库提交后对象只在明确失败时删除，响应丢失不能删已提交对象。

### 资料库、回收站与版本
1. `GET /v1/files` 只列 `is_head=1`、未删除、未清除的文件；传 `ids` 时按精确版本查询，历史引用仍能定位旧版本，但不把旧版本混进浏览列表。
2. 分页用 `limit` 加 `cursor`（游标是上一页最后一个文件，按排序字段与 id 做严格小于），`offset` 只用于兼容；`GET /v1/files/folders` 按项目聚合，默认筛选下还单独返回空项目。
3. 删除文件或项目是软删除：写 `deleted_at` 和 `purge_at`，项目删除还写 `trashed_from_project_id` 与 `trashed_project_name`。回收站占配额，只有 `purge` 或到期清理才删对象和行；`platform/ailab_platform/api/reaper.py::purge_expired_trash` 分批处理到期项。
4. 版本历史保留行和引用；超过 `max_file_versions` 的旧版本变成 tombstone。`restore-version` 把旧版本字节复制成新文件身份，只继承扫描/安全等不可变事实，派生资源按新身份重新准备；旧版本引用不会自动指向新 head。

### 原生查看与页图
1. `GET /v1/files/{id}/document` 默认只返回打开元数据（类型、大小、版本、是否可原生），不启动解析任务；`?structure=true` 才查 `viewer.json` 或排 `viewer` job。
2. `platform/ailab_platform/worker/viewer.py::process_viewer` 用固定版本 Node reader（源码 `platform/ailab_platform/sandbox_tools/viewer-reader.ts`，依赖固定在 `platform/viewer-runtime/package.json`，路径由 `viewer_reader_path` 指定）生成 `derived/{file_id}/viewer/{PARSER_REVISION}/{job}.json`；reader 有 96 MiB heap、192 MiB RSS、18 秒和 4 MiB 输出上限。
3. `platform/ailab_platform/contracts/document_view.py::NATIVE_BYTES`、`::NATIVE_UNITS` 决定文件是否允许原生打开；超限时原生打开不可用，只能走分页渲染，不伪造结构。`viewer.json` 的 `PARSER_REVISION` 必须与前端和引用校验一致。
4. 服务端页图与浏览器截图是两套来源：页图由渲染任务产生并记 `RenderedPage`；`platform/ailab_platform/api/viewer_captures.py::save` 保存用户当前视口截图（`ViewerCapture`，不可重建、随消息引用保留），两者不能互相冒充。

## 不变量

- 文件版本不可变：任何字节变化都产生新的 `FileObject` 和 `version`；`file_id` 加 `sha256` 是引用、缓存和交付身份的锚点，旧版本不跟随 head。
- `READY` 只表示安全可用，不表示已解析。缺失的正文、页图、OCR 必须是 `absent`/`unavailable`，不能返回空串冒充已读。
- 文件归属的派生对象只能写在 `derived/{file_id}/` 下；Run 级预览和截图用 `previews/{run_id}/`、`viewer/{run_id}`，同样受账本约束。写入前 `platform/ailab_platform/api/derived_assets.py::reserve`，按实际字节 `platform/ailab_platform/api/derived_assets.py::finish`，业务元数据与发布在同一事务。没有账本记录的字节、没有对象的 ready 元数据都不允许。钉住：`platform/tests/test_accounted_derived_writes.py::test_every_storage_write_reserves_before_io_and_cleanup_never_deletes_published_bytes`。
- 派生容量由单行 `DerivedBudget` 账本串行化；超过高水位时按创建时间淘汰可重建的 `cache` 到低水位。`native`、`ocr`、截图和仍被 pin 的对象不淘汰；淘汰先失效业务元数据，再删字节。代码：`platform/ailab_platform/api/derived_assets.py::evict_oldest`、`::collect_batch`。
- 同一文件版本的预览/解析由持久 job、claim 和文件锁去重；重复请求复用已发布 manifest，失败不会自动无限重试。钉住：`platform/tests/test_file_previews.py::test_worker_preview_concurrent_messages_render_once`、`::test_failed_never_auto_retries_and_delete_cleans_every_derivative`。
- 预览轮询不读对象存储；资产 ETag 由内容摘要与派生版本算出，命中 `If-None-Match` 返回 304。钉住：`platform/tests/test_file_previews.py::test_preview_poll_never_reads_storage`、`platform/tests/test_library.py::test_thumbnail_response_caching_ownership_and_cleanup`。
- 扫描失败永远不放行；`infected` 直接 rejected，`unavailable` 只能重试到上限后隔离。钉住：`platform/tests/test_file_quarantine.py::test_upload_is_pending_and_unusable_until_scanned`、`platform/tests/test_upload_safety.py::test_safety_only_upload_has_no_parser_prewarm_or_derivative_writes`。
- OCR 只按显式页或区域发生，推理只在 API；缓存键包含源版本、引擎指纹和 selector，在途 operation 不能被查询替换。钉住：`platform/tests/test_ocr_lifecycle.py::test_active_selection_is_idempotent_and_conflicts_are_explicit`。
- Chat 输入计划在发送身份上冻结；重试复用同一计划，源版本或派生 revision 变化返回 409，不在历史里改写已发送内容。前缀缓存规则见 [Agent](agent.md)。钉住：`platform/tests/test_document_input_plan.py::test_preparation_identity_binds_source_and_model_and_adopts_legacy_preflight`。
- 每个用户在同一 document lane 只有一个待执行重活，全局等待上限 32；所有等待使用绝对截止，不因重复请求续期（`platform/ailab_platform/api/document_operations.py::request`、`platform/ailab_platform/api/document_preparation.py::start_budget`）。
- 一次交付只接受 Run 运行期的 `/workspace/outputs` 普通文件；`(run, delivery_id)` 幂等，内容不同 422，staging 必须清理。钉住：`platform/tests/test_artifacts.py::test_retry_after_staging_was_already_consumed_is_idempotent`、`::test_changed_content_is_a_new_artifact`。
- 服务端不把完整文件读进内存：上传按块写临时文件，Worker 下载到临时文件后按流扫描/解析，交付用 8 MiB spool 边哈希边扫描；原件下载和预览资产都流式返回并在 `finally` 关闭。
- 资料库列表必须有界并走索引；新增过滤条件没有对应索引就是缺陷。钉住：`platform/tests/test_library_agent_tools.py::test_library_search_query_uses_user_index`。

## 怎么扩展

### 加一种文件格式
1. 类型表：`platform/ailab_platform/contracts/file_types.py::TEXT_SUFFIXES`、`::OFFICE_SUFFIXES`，预览分类 `platform/ailab_platform/contracts/file_preview.py::preview_kind`，安全检测 `platform/ailab_platform/worker/safety.py::inspect`（OOXML 还要加入 `OOXML` 表），MIME 纠正 `platform/ailab_platform/contracts/file_types.py::DETECTED_MIME`。
2. 解析：旧路径在 `platform/ailab_platform/worker/parsers.py::extract_path` 加分支（只在受限子进程执行）；Chat 的原生表示要在 `platform/ailab_platform/api/native_preparation.py::native_kind`、`platform/ailab_platform/worker/office.py::KINDS` 和 `platform/ailab_platform/broker/documents.py::KINDS` 接同一种 kind。只加类型表不加 parser，文件会 READY 但永远 `native_format_unsupported`。
3. 预览：`platform/ailab_platform/worker/preview.py::process_preview` 的本地分支或 Broker 渲染器要能产出 manifest 约定的 `p-*.png` 或 `sheets.json`；`platform/ailab_platform/contracts/file_preview.py::FilePreview` 会拒绝不符合约定的名字。
4. 原生查看：加入 `platform/ailab_platform/contracts/document_view.py::NATIVE_BYTES`、`::NATIVE_UNITS`，并确认 reader 能输出该格式的 `units` 与 `anchor`；不加入就只能走分页回退。
5. 测试：至少覆盖 `platform/tests/test_upload_safety.py`（类型、压缩包、伪造扩展名）、`platform/tests/test_file_previews.py`（manifest 与资产）、`platform/tests/test_document_view.py`（原生准入）；真实沙箱和真模型场景放 `platform/scenarios/`。

### 加一种派生资源
1. 选稳定 key：`derived/{file_id}/{kind}/{generation}/...`，generation 用 job id、attempt 或内容摘要，不能只用时间戳。
2. 写：Worker 用 `platform/ailab_platform/derived_writes.py::AccountedWrites`，API 自己用 `platform/ailab_platform/api/derived_assets.py::reserve` → 写对象 → `platform/ailab_platform/api/derived_assets.py::finish`，Broker 用 `platform/ailab_platform/derived_writes.py::AccountedRunWrites`；不要直接调 `storage.put_*` 绕过容量。
3. 发布：把 manifest 或指针写进 `file.meta` 或新表时，同一事务调 `platform/ailab_platform/api/derived_assets.py::publish` 或 `::publish_writer`；被替换的 key 用 `platform/ailab_platform/api/derived_assets.py::retire`，不要立即删可能仍被读的字节。
4. 失效与回收：`file.meta` 字段要加入 `platform/ailab_platform/api/derived_invalidation.py::invalidate` 的字段列表；不可重建的资源（浏览器像素）设 `reconstructible=False` 并 `pin` 到消息，否则容量压力下会被 `platform/ailab_platform/api/derived_assets.py::evict_oldest` 淘汰。
5. 删除：`platform/ailab_platform/library.py::delete_file_rows` 已按 `derived/{file_id}/` 前缀清理；新资源如果另有数据库行，必须在那里加删除，否则留下孤儿行。
6. 测试：`platform/tests/test_derived_assets.py::test_reserve_settle_replacement_and_idempotent_completion`、`platform/tests/test_accounted_derived_writes.py` 覆盖新写入方；补容量拒绝和失败补偿用例。

### 改文档输入给模型的方式
1. 改 `platform/ailab_platform/api/document_input_plan.py::prepare` 的准入和 `::input_files` 的装配；计划是发送身份的一部分，不能改成请求时再算一次。
2. 新内容只能追加在用户消息或工具结果里；不要改 system 消息、工具 schema 或已发送消息来塞文件内容，否则整段前缀缓存失效。规则见 [Agent](agent.md) 与 [ADR 0030](../architecture/adr/0030-prefix-stable-model-requests.md)。
3. 变更必须同时改 `platform/ailab_platform/api/document_input_plan.py::validate_frozen` 的校验项并加 recorded-request 测试；只改 `platform/tests/test_document_input_plan.py` 里的纯函数不够。
4. 视觉页改动要同时考虑 `vision_input_tokens`、8 页内联上限，以及非工具模型只能整份 inline 的拒绝路径。

### 改资料库列表查询
1. 用户浏览改 `platform/ailab_platform/api/routes_files.py::list_files`，Agent 搜索改 `platform/ailab_platform/api/routes_library_tools.py::library_query`；两条查询的权限、排序和索引不同，不能只改一条。
2. 新过滤条件要配 `FileObject` 的索引；部分索引的谓词必须与查询里的常量完全一致。SQLite 上 `content_type LIKE 'image/%'` 要用 `literal_column("'image/%'")` 才能命中 `ix_files_live_images`，绑定参数不行。
3. 保持 `(排序字段, id)` 的稳定游标；文件列表 `limit` 默认 50、最大 200，文件夹列表默认 100、最大 200，`offset` 兼容和 `ids` 精确版本查询不要混用。
4. 在 `platform/tests/test_library_agent_tools.py::test_library_search_query_uses_user_index` 的 EXPLAIN 列表里加新查询，并补 `platform/tests/test_library.py::test_filters_order_search_and_pagination` 或 `platform/tests/test_library_namespaces.py::test_folder_pagination_empty_projects_images_and_search` 的行为断言。

## 配置

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_MAX_UPLOAD_BYTES` | 单次上传上限 | 50 MiB |
| `AILAB_MAX_PARSE_BYTES` | Worker 解析上限；更大的文件只保留元数据 | 20 MiB |
| `AILAB_MAX_ARTIFACT_BYTES` | 单个交付件上限 | 100 MiB |
| `AILAB_MAX_ARCHIVE_BYTES` | 资料库批量下载包上限 | 1 GiB |
| `AILAB_LIBRARY_QUOTA_BYTES` | 每用户资料库配额（含回收站） | 20 GiB |
| `AILAB_MAX_FILE_VERSIONS` | 每条文件血脉保留的版本数 | 10 |
| `AILAB_TRASH_DAYS` | 回收站保留天数 | 30 |
| `AILAB_DERIVED_BUDGET_BYTES` | 全平台派生资源预算 | 1 GiB |
| `AILAB_DERIVED_CACHE_DAYS` | 可重建缓存的 TTL | 30 |
| `AILAB_OCR_MAX_PAGES` | 一次显式 OCR 的页数上限 | 20 |
| `AILAB_PARSE_MEMORY_MB` | 受限解析子进程内存上限 | 192 |
| `AILAB_PARSE_TIMEOUT_SECONDS` | 受限解析子进程墙钟上限 | 60 |

完整清单见 `platform/ailab_platform/config.py::Settings`。

## 相关决策

- [ADR 0011 单机文件扫描隔离与资料库按需预览](../architecture/adr/0011-worker-previews.md)：上传只扫描，重型预览按需生成并缓存到 `derived/`。
- [ADR 0018 文件 OCR 与对话多模态输入](../architecture/adr/0018-file-ocr-draft.md)：推理在 API，OCR 由 outbox job 触发，OCR 文本不覆盖原生文本。
- [ADR 0023 平台文件读取执行边界与并发](../architecture/adr/0023-system-document-reader-and-concurrent-capabilities.md)：解析在短生命周期系统 render 租约中，Worker 只编排与校验。
- [ADR 0025 Work 标配 Office 读取核心](../architecture/adr/0025-work-office-reader.md)：Work 在自己沙箱里按需读取原件，不依赖系统索引。
- [ADR 0026 办公内容覆盖与持久文件等待](../architecture/adr/0026-document-visual-coverage-and-file-waits.md)：原生、OCR、视觉是三种覆盖维度，等待通过 `file_dependency` 恢复。
- [ADR 0032 单次使用的预热渲染沙箱（部分被生命周期指南取代）](../architecture/adr/0032-prewarmed-single-use-render-sandboxes.md)：渲染槽位与同用户复用规则。
- [ADR 0034 安全原件就绪与按需派生](../architecture/adr/0034-safety-ready-and-lazy-native-input.md)：`input_lifecycle=2`，安全 READY 不等于解析完成。
- [ADR 0030 前缀稳定的模型请求](../architecture/adr/0030-prefix-stable-model-requests.md)：文档内容只能追加，不能改写已发送的请求。

## 已知的坑

见 [HANDOFF · 文件](../../HANDOFF.MD#文件)。

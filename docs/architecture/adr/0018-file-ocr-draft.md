# ADR 0018 草案 · 文件 OCR 与对话多模态输入

状态：accepted（本地实现），2026-10-23。T19子任务已在a1547fe集成，4f9f3cb补齐多模态与运行恢复契约；[最终验收](../../reviews/2026-10-23-t07-runtime.md)。真实付费vision未验。

## 决定

1. **推理只在 API**。API 是持 catalog 密钥的可信服务，OCR 调用走现有 `ModelTransport`。Worker 不持模型凭据，只做扫描、受限 PDF 页分析并驱动 API 任务；Broker 不参与推理。
2. **OCR 由 outbox 消息触发，不在扫描消息里同步执行**。API 在把文件 READY 并且 `ocr=pending` 的同一事务中调用 `stage_job(kind="ocr")` 写入版本化JobEnvelope，由Worker领取后处理并先ACK API、后ACK传输。Worker 处理该消息：`prepare` → （PDF）预览渲染 → `run`。失败只让消息不 ACK，由 API 持有的任务状态决定续跑，READY 文件不会被标成 `processing: error`。旧做法在扫描消息内同步调用，429 或预览未完成会把就绪文件标错，已删除。
3. **渲染复用既有 preview 管线**。PDF 的低文字页经 API 记录的 `preview_job_id`（pending 预览）由 Worker `process_preview` 渲染，走现有 Broker `job=preview` 绑定。无假 Run。图片使用扫描时已生成的 `display.webp`（最长边 2048px），不新增渲染。
4. **状态机**：`file.meta.ocr` 为 `pending → running → done | failed`，`unavailable` 仅来自配置。每次迁移都是持用户锁的检查加写入。原因码显式：`vision_not_configured`、`text_pdf`、`page_limit`、`size_limit`、`deadline`、`interrupted`、`quota_exceeded`、`usage_rejected`、`timeout`、`preview_unavailable`、`pdf_analysis_unavailable`、`normalized_image_unavailable`、`empty_ocr_text`、`ocr_failed`。上游错误体不入库。
5. **计费归属文件所有者**。每页一个调用 `call_id = ocr:<file_id>:<page>`，`subject=file_job`，`mode=ocr`，`run_id=NULL`，`file_id` 非外键。额度与 Chat/Work 同一用户池，沿用预留 → 调用 → 结算及过期回收。已存在的 call_id 不会再次推理（`call_already_dispatched`），避免重投时重复计费或重复调用。
6. **账本契约**：`ReserveUsageIn` 增加 `subject`（`run|file_job`）与 `file_id`。`run` 需要 `run_id`，只能由 Agent 预留；`file_job` 需要 `file_id`、`mode=ocr`、不能带 `run_id`，只能由 API 内部 OCR 服务创建。HTTP 的 reserve/settle 对 `file_job` 返回 403。
7. **迁移 0018**：`model_usage` 与 `usage_reservations` 是无入向外键的叶表，SQLite 上通过 batch 放宽 `run_id` 为可空，不重建 files/runs/users。降级时 `file_id` 暂存到旧 `run_id`（金额与所有者保留），再次升级按 `mode=ocr` 恢复 `file_job`。
8. **文本落点**：OCR 结果写入 `derived/{file_id}/ocr.txt` 并记为 `ocr_text_key`，不覆盖 Worker 的原生 `text_key`。`_attachment_texts` 合并两者，所以混合 PDF 的原生文字与扫描页文字都会给模型。设计稿写的是“写回 `text_key`”，这里有意偏离，理由是覆盖会丢失原生提取结果。
9. **对话多模态**：`agent.multimodal.input_content` 决定每个附件的输入形式。vision 模型收到受控 `image_url` 部件（`data:` URL，`detail: high`，来自 `display.webp`，上限 2 MiB）；否则使用 OCR 文本；两者都没有则给出显式的“图片未能读取（OCR <状态>）”，绝不静默为空。`has_image_part` 为真时，候选模型必须限定为 vision。
10. **预算**：`TokenEstimator` 不再把 base64 当文本计数。每张图按 `vision_input_tokens`（默认 4096）预留，并用实际 usage 校准。这是保守估计，不是厂商精确 tokenizer。

## 默认上限与成本

- `ocr_max_pages=20`：限的是**低文字页数**，不是文档总页数。文字足够的长文档不需要 OCR，不会因总页数被拒。
- `ocr_max_bytes=20 MiB`，`ocr_page_max_bytes=2 MiB`，`ocr_min_page_chars=40`，`ocr_max_output_tokens=4096`，`ocr_deadline_seconds=1800`。单次 OCR 的模型总时间为 `llm_total_timeout_seconds`，缺省回落到 `llm_timeout_seconds`（120 秒）。
- **进程级单槽**：一个 API 进程同一时间只跑一个 OCR，忙时 429，Worker 保持消息未 ACK 并重投。多个 API 进程之间没有全局准入，单机一个 API 进程是本决定的前提；横向扩展需要调度适配层的全局准入，未实现。
- **PDF 分析**只在配置了 `ocr_alias` 时运行，在受限解析子进程里逐页提取文字，低文字页数超上限即提前停止。成本约等于一次额外的 PDF 文字提取，受子进程 CPU/墙钟限制。目标机耗时未测。
- 无新依赖。

## 迁移与输入契约

正式链为0014→0016→0017→0018→0019，双库升/降/再升及漂移通过；不再使用子任务临时改链。AgentLoop已接input_content，并按图片限制vision候选；checkpoint存图片文件引用，恢复时重新授权读取。Worker files:ocr scope已同步ADR0002。

## 未验证与边界

- 真实付费 vision 未调用，全部 HTTP 测试使用本地可控的 OpenAI 兼容上游。
- 目标机的 CPU、RSS 与渲染耗时未测。
- Worker 与 API 必须同时升级。旧 Worker 不会处理 `kind=ocr` 信封，旧 API 不会排队 OCR。
- Portal 没有 UI 改动。

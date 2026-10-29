# 当前代码证据与开源参考

> 实施状态（2026-10-29）：用户已授权本方案实施，覆盖下文历史“只设计”限制；不授权push/PR/merge/部署。OCR只计量、不计费、不设用户累计配额，资源隔离仍保留。当前实现、验收及剩余缺口以[实施记录](../../../progress/updates/2026-10-29-file-context-core-validation.md)和[验收逐项记录](../../../reviews/file-context-2026-10-29/acceptance.md)为准。本文其余历史状态不代表当前完成度。

状态：设计修订稿，未实施。基线 `main@e71a60f`。本轮明确Chat预处理、Work自主操作、图片按视觉能力分流、RapidOCR与昇腾NPU优先；正文中的资源与大小数值均为待验收候选。

[总览与模块入口](README.md)。

## 当前实现证据与缺口

以下是本轮静态代码核对，不代表部署环境或付费模型质量验收。

[源证据清单](../../../reviews/2026-10-29-file-context-source-audit.json)记录本地文件SHA256、关键函数行号和第三方固定提交/源码hash。可从清单定位核对下表；未将第三方代码复制进仓库。

| 事实 | 当前代码依据 | 含义 |
| --- | --- | --- |
| 普通上传默认 `purpose=library` | `api/routes_files.py:81`；`api/routes_runs.py` 校验输入文件 | 不需要重做一个文件存储库；需要补引用和内容读取 |
| 文件已有 `id/sha256/lineage_id/version/parent_id/is_head` | `models.py:FileObject` | 引用绑定精确 FileObject；禁止悄悄跳到同 lineage 最新版 |
| Word 提取段落和表格；PDF 只提取前20页 | `worker/parsers.py:_docx/_pdf` | 不能宣称读取完整文档、Word 内嵌图或可靠页码 |
| 单附件3万、合计6万字符直接拼入本轮消息 | `api/routes_internal.py:_attachment_texts`；`agent/prompts.py:user_prompt` | 长文默认消耗大；前缀截断可能漏掉相关尾页 |
| 配置 `ocr_alias` 后图片/PDF READY 自动排 OCR | `worker/pipeline.py:187–216`；`api/routes_internal.py` READY patch | 上传意图与识别意图耦合，可能和正常看图重复花费 |
| PDF 低文字页标准是少于40字 | `worker/ocr.py:low_text_pages`；`config.py` | 不是可靠性判定；封面误判、页眉掩盖扫描正文都可能发生 |
| OCR 推理在 API，使用视觉模型并逐页记账 | `api/routes_ocr.py:recognize/_run_ocr` | 本轮未发现专用 PaddleOCR 后端；不得记为已部署专用 OCR |
| 资料搜索只查文件名；读取返回已提取文字 | `api/routes_library_tools.py:library_query/read_library` | 已有分页/选页，但没有文档内容检索与图片重读闭环 |
| 历史上下文只取 prompt/reply/status/steers | `api/routes_model_context.py:model_context` | 没有当轮输入附件清单；未压缩也可能失去文件上下文 |
| 跨轮摘要明确传旧摘要与新增轮次 | `agent/loop.py:_assemble_context` 的 `compact()` | 用户提出的滚动合并机制**已经存在**，应改进而不是另起一套 |
| 摘要游标有 ancestry 校验及 CAS | `api/routes_model_context.py:save_model_summary` | 有正确基础；缺更细来源、版本和删除/并发门禁 |
| 本轮压缩会读取前次摘要，排除 `image_url` 后总结 | `agent/loop.py:_compact_work_context` | 不会把原图变成可恢复的无损文字；原始本轮输入保留又可能持续占用预算 |
| 本轮压缩输入超长时头尾截取 | 同上 `max_chars` | 中间记录可在未整理前丢失，不适合当作完整消费进度 |
| 图片 token 单独估算；checkpoint 存引用，恢复再读图 | `model_policy.py:TokenEstimator`；`adapters/runtime.py:checkpoint_state`；`agent/loop.py:_restore_images` | “图片完全没考虑”不准确；缺的是生命周期和按需上下文装配 |
| 沙箱已有 pypdf/Poppler/Office 读写与转换工具 | `images/sbx-office/Dockerfile`；`market_skills/pdf` | Work 能操作原文件；Chat 不需要为读取文本启动用户沙箱 |
| skill正文按需，但目录和tool schema整批提供 | `agent/prompts.py:system_prompt/system_prompt_for`；`agent/tools.py:tools_for_turn/tools_for_chat` | 已有load_skill，不是完整能力搜索；需小入口和动态schema |
| 普通删除是回收站；彻底清理有持久对象删除意图 | `api/routes_files.py:delete_file`；`library.py:trash_lineages/purge_lineages/delete_file_rows`；`api/reaper.py:drain_object_deletions` | 不能把204当物理擦除；新增来源/索引/快照合同尚未实现 |
| 每次重新上传创建新ID和file job | `api/routes_files.py:upload_file` | 同ID可复用既有派生物，不等于重复上传也会自动复用 |
| API已有capabilities，前端类型/菜单未接 | `catalog.py:ModelSpec.public`；`portal/lib/api.ts:ModelInfo`；`components/chat/model-menu.tsx` | 复用API能力声明，补UI，不再新增第二个能力真值 |
| 附件数量上限50，未单独限5图 | `schemas.py:RunCreate/QueuedMessage`；`portal/lib/api.ts:MAX_ATTACHMENTS` | 5图为新设计规则；文档内页图另计请求预算 |

既有 `tests/test_model_gateway_context.py::test_200_round_budget_and_incremental_summary` 验证了增量摘要的预算、游标和重复装配不再调用；`tests/test_long_runs.py::test_current_run_context_compacts_whole_tool_groups_and_keeps_original_input` 验证了工具组和原输入保留。**本轮未运行这些测试；它们也没有证明连续多次压缩后的附件回查或事实保真。**

### 对“第二次怎么压缩”的精确回答

当前跨轮实现：第一次把 `旧summary=""` 和新增 `prefix` 交给摘要模型；保存 `through_run_id=prefix[-1].id`；后续读取该摘要，并只取游标后的历史，再传 `{"summary": old, "turns": new_prefix}`。摘要作为一个合并结果替换，不累积多份摘要文本。每一批摘要成功保存后才推进边界。

当前本轮实现：第一次摘要作为 system 消息放回 messages；第二次遍历待整理 messages 时会包含它。这个行为是间接的，没有独立 typed previous-summary 字段，且头尾裁剪可能影响输入完整性。方案统一为显式旧摘要、输入区间和提交协议。


## 开源参考：固定提交、可借鉴处及边界

检索日期2026-10-29。下列源码通过 GitHub API 解析分支到 SHA 后读取 raw 文件；不是只读搜索摘要。不复制第三方源码，不因此新增这些项目的依赖。若实施时引入代码或模型，另核对当时版本及 LICENSE/模型许可证。

| 参考 | 本轮实际核对 | 借鉴 / 不照搬 |
| --- | --- | --- |
| [LangMem summarization，48e3c11](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/short_term/summarization.py#L31) | `DEFAULT_EXISTING_SUMMARY_PROMPT`、`RunningSummary`、输入准备与工具配对 | 旧摘要＋新消息、消费 ID、预算、完整 tool 组；不引入 LangGraph，不照搬无限增长 message ID set |
| [OpenCode compaction，b6c43a5](https://github.com/anomalyco/opencode/blob/b6c43a524265496086fc0fdd83180bfa47d4d22e/packages/core/src/session/compaction.ts#L164) | `SUMMARY_TEMPLATE/buildPrompt`；`flattenAndDropOldest`；近期上下文/媒体省略 | 有结构的任务摘要、新指令更新旧状态、近期原始记录、安全余量；媒体被省略并不证明图片事实保留，不复用其删除策略 |
| [Docling provenance，5582916](https://github.com/docling-project/docling-core/blob/55829160b6eb17b6796fb6649e9b43bcce94cb01/docling_core/types/doc/common/reference.py#L205)；[文档概念](https://docling-project.github.io/docling/concepts/docling_document/) | 文档条目、页面/图片引用、`ProvenanceItem(page_no,bbox,charspan)` | 原文件与结构化块分开、定位到来源；本版只做所需子集，不承诺 Word 的物理页码，不部署完整重型布局流水线 |
| [PaddleOCR PP-OCRv5，dab3fe3](https://github.com/PaddlePaddle/PaddleOCR/blob/dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5.en.md) | mobile/server 对比、CPU benchmark、测试条件 | 专用 OCR 候选基线，不宣称是最新/最优模型；CPU可运行不等于小内存或单页很快，必须目标机实测 |

Paddle 官方这份 benchmark 的 mobile CPU 配置峰值主存也约2GB、CPU占用接近十核，不能拿其页耗时套到2vCPU机器。完整服务可远超模型权重大小。最新文档入口已发生版本变化；实施前固定候选模型/引擎/线程并实测，不写浮动 `latest`。

OpenAI 的[文件输入](https://developers.openai.com/api/docs/guides/file-inputs)和[compaction](https://developers.openai.com/api/docs/guides/compaction)可作非开源补充：直接文件输入与检索是不同路径，原生 compaction 也不是普通文字摘要。本文不声称掌握 ChatGPT 内部附件或压缩实现。

## 本轮新增开源核对

| 项目与固定提交 | 核对内容 | 本设计采用/边界 |
| --- | --- | --- |
| [RapidOCR 80b427d provider](https://github.com/RapidAI/RapidOCR/blob/80b427d69bfc5b91a12913394875910296c6f315/python/rapidocr/inference_engine/onnxruntime/provider_config.py) | CANN检测、provider列表、缺CANN时CPU回落；config.yaml与推理session | 用CANN路线加平台required-backend校验；源码Apache-2.0，具体模型hash/许可实施时固定；并非已测NPU |
| [PydanticAI a70042d ToolSearch](https://github.com/pydantic/pydantic-ai/blob/a70042d5664e87960478428cdfe2710fbcc6d97d/pydantic_ai_slim/pydantic_ai/capabilities/_tool_search.py) | native与本地search_tools、deferred schema和发现历史 | 本地搜索适配私有模型；不引入框架；MIT |
| [OpenHands 47bbc5e Skill](https://github.com/OpenHands/software-agent-sdk/blob/47bbc5e8392c4c75b7f77a1da38bed4a9f53d6a5/openhands-sdk/openhands/sdk/skills/skill.py) | metadata/正文分层、trigger与按需正文 | 不照搬全量目录或关键词触发全文；MIT |
| [MiniMax文档技能 60aaae5](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-xlsx/SKILL.md) | Word先预览/分析、Excel先结构发现、任务分类和reference拆分；PDF偏生成 | MIT可作模式参考；不照搬其.NET/Node/Chromium依赖，不声称它已实现我们的长文硬限制 |
| [Anthropic PDF技能与独立许可 dbd4588](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/pdf/LICENSE.txt) | PDF/DOCX/XLSX/PPTX公开流程和许可证 | 文档技能标Proprietary；本轮只核对常见流程，不移植正文/脚本，不归为可直接复用的MIT技能 |

RapidOCR的[引擎文档](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/how_to_use_infer_engine/)有NPU配置，ORT官方[CANN EP说明](https://onnxruntime.ai/docs/execution-providers/community-maintained/CANN-ExecutionProvider.html)明确面向昇腾。它们证明存在支持路径，目标卡驱动、CANN/ORT版本、算子与资源仍需验收。旧CPU benchmark不能替代RapidOCR/CANN性能。

仓库冻结架构`02-模型服务层设计.md`的OCR NPU预留和`08-Skills与沙箱环境设计.md`的平台OCR工具方向已核对；只作为设计来源，不推断实际已部署或改写冻结副本。源证据JSON另记录本轮本地文件及第三方固定提交hash，不包含临时下载路径或密钥。


## ZCode与内置技能的直接复用研究

新增的具体上游文件、许可差异、Office Runtime能力/内存边界及接入路径统一维护在[09上游复用计划](09-upstream-reuse.md)。[本轮源证据](../../../reviews/2026-10-29-builtin-skills-source-audit.json)记录ZCode `3c5b227`、Claude Code `58d6da1`及MiniMax `60aaae5`实际下载源码hash和完整tree元数据。仅研究/改plan，没有导入第三方产品代码或安装运行依赖。

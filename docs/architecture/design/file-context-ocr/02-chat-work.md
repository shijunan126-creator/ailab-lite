# Chat预处理、Work自主操作与输入预算

2026-10-29 实施补充：正文/OCR/视觉覆盖与自动文件等待的最新合同见[ADR 0026](../../adr/0026-document-visual-coverage-and-file-waits.md)，本轮验收见[修复记录](../../../progress/updates/2026-10-29-document-reading-reliability.md)；下方旧状态为历史设计。

> 实施状态（2026-10-29）：用户已授权本方案实施，覆盖下文历史“只设计”限制；不授权push/PR/merge/部署。OCR只计量、不计费、不设用户累计配额，资源隔离仍保留。当前实现、验收及剩余缺口以[实施记录](../../../progress/updates/2026-10-29-file-context-core-validation.md)和[验收逐项记录](../../../reviews/file-context-2026-10-29/acceptance.md)为准。本文其余历史状态不代表当前完成度。

状态：设计修订稿，未实施。基线 `main@e71a60f`。本轮明确Chat预处理、Work自主操作、图片按视觉能力分流、RapidOCR与昇腾NPU优先；正文中的资源与大小数值均为待验收候选。

[总览与模块入口](README.md)。

## Chat / Work 文件处理

### 两种模式分别装配，共用原文件与派生物

所有上传先通过流式字节限制、内容识别、安全扫描。基础结构/类型信息可预处理，但是否生成完整可读内容、是否OCR、是否渲染由mode和任务决定。下面的限制同时由API执行；Portal只做提前提示，不能靠前端限制安全。

**Chat路径**：上传或资料库重引 → 复用/生成确定版本的chat_readable包 → 模型相关预算检查 → 准备完后开始本轮回答。包的状态独立于文件READY，取`pending/ready/partial/too_large/unsupported/failed`。不要求模型自己先调用office工具，也不允许在准备中发一次模型回答后悄悄补上另一份输入。

chat_readable是按格式生成的正文，不是先付费生成的摘要：

预处理超出短等待窗口（候选15秒）时附件保持“处理中”，返回job状态而不启动模型；总准备deadline候选120秒，到期给明确失败/转Work选项，禁止浏览器无限等待或Agent轮询空转。准备完成的包绑定`file_ids + selector + attachment_revision`；模型发送前对最新revision复核。已有资料库处理结果可跳过这些计算，不能为了“预处理”再跑一次OCR。

| 格式 | 预处理给模型的内容 | 不可假装包含 |
| --- | --- | --- |
| DOCX | 标题路径、段落、列表、Markdown表格/单元格定位 | 不自动包含页码/嵌图语义，嵌图需要视觉或OCR；修订/批注规则明确标记 |
| PDF | 按物理页编号的文字/表格块；无可靠文字的所需页经RapidOCR | 图表语义、完美阅读顺序、已跳过页不能冒充全文 |
| XLSX/CSV | sheet名、有效范围、表头、限定单元格的类型/值；必要时显示公式与缓存状态 | 无缓存值不当空值；不自动重算、执行宏、展开全部长表 |
| PPTX | slide序号、标题、文字和表格；与问题相关的页图在图像预算内另发 | 不自动展开全部媒体；图表无数据时标记需视觉/Work |
| 图片 | vision模型直接图像部件；非vision模型预先OCR文本 | OCR不包含颜色/空间/人物/图表语义 |

首次整份文档无法在Chat读取预算内完整提供时，返回明确的`chat_attachment_too_large`，建议Work或用户显式选页/章节/范围。若用户选范围，包必须注明“仅这些范围”，不能自动裁到前N页。Chat可用网页/历史等少量工具，但不依赖多轮文档Agent循环弥补超限；全文总结超限不转成隐式top-k总结。

**Work路径**：上传或资料库重引 → 给Agent原文件ID、大小/结构与已有处理索引 → 搜索文件能力、按需加载skill → 选择读现成派生内容/沙箱库提取/按页转图/平台OCR/修改原文件。默认不注入附件全文，不预先OCR整个扫描PDF。无tools能力的模型不适合自主Work，启动前明确拒绝该组合，建议换支持工具的模型或Chat。

同一file_id从Chat切Work，只改变装配策略，原文件SHA和已有派生物不变；Work已OCR的页可供之后Chat复用，仍须重新通过Chat规模与模型预算。模式切换不自动创建新文档版本，也不把旧附件悄悄重新附加本轮。

旧`.doc/.xls/.ppt`仅在Broker受限Office转换支持且预算允许时转换，否则unsupported；不能往API/Worker安装Office。加密文件不明文传密码给模型，不解析则返回encrypted；首版可明确要求用户上传解密副本。

### 对图片的处理

本轮图像在vision模型第一跳直接看；非vision模型由平台先OCR成文本，不能把任务交给模型自行调用补救。历史图像默认只携带必要引用。每次上下文装配根据当前任务选择是否保留图像输入，避免每个工具轮都重复带不再相关的图像。任务要求两图对比时两张都必须在该次请求中，不能用两份摘要替代。

新 `view_library_image(file_id,page?,region?,detail)` 在Chat/Work均可用。Agent调用后：API检验run/来源/文件版本 → 返回受限派生资产描述 → Agent通过已授权服务通道获取bytes → 把图片作为后续模型请求的类型化图像部件加入。**仅返回图片URL字符串或JSON给模型，不等于模型已看到图像。**

工具结果先完成原有tool_call/result配对，再添加附带来源标签的图像观察消息；provider adapter负责合法消息格式。checkpoint仍存精确引用，发送前重查权限再hydrate。文字模型查看图像时自动转受限OCR文字路径，工具返回`representation=ocr_text`；若请求本质是图像语义则返回`vision_required`，不能静默假装看图成功，也不能悄悄切更贵模型。

图片观察记录 `{file_id,version,region,observed_at_run,claim,uncertainty}` 可由已发生的模型回答整理，不能为每个上传额外调用描述模型。精确颜色、位置、数字问题需重新看图；OCR只解决文字，不替代语义图像理解。

### 统一读取工具与返回契约

复用并扩展既有工具，避免增加一堆同义名称：

| 工具 | 输入与行为 | 默认有界返回 |
| --- | --- | --- |
| `search_library` | query、scope=conversation/project/library、cursor；先查明确引用再扩大当前授权范围 | 10条、最大20；文件ID/名/类型/短片段/精确版本；不返回路径、密钥或全文 |
| `read_library_file` | file_id、selector=pages/blocks/heading/sheet_range、query可选、cursor、max_tokens | 默认2000、最大6000估算token；返回段落和locator、覆盖区间、missing、truncated、next_cursor |
| `view_library_image` | 精确文件、page或图片、region、detail | 默认从少量所选页/区域开始；每图≤2MiB；数量按当前模型实际图像能力和剩余token/像素/字节预算决定，不套用输入框每轮5张的限制 |
| `extract_text` | file_id、选页/区域、force_ocr=false、输出格式txt/json | 返回job_id/status/progress；已完成时返回派生ID/文字文件，不把整份OCR文本塞入tool结果 |
| `copy_library_file` | 保留现有Work工具，使用精确file_id | 仅授权工作区路径；不重复上传或重新解析 |

API返回typed locator，不让Agent构造存储key。用户单独“提取文字”与Agent工具走同一业务API；不同caller分别检查用户会话与服务scope。请求上限、分页cursor包含file/derivation/version绑定与完整性校验。`max_tokens`是估算上限，实际请求仍经统一estimator和余量检查；分页不能截坏UTF-8或表格行，超大行拆单元格区间并标记。

HTTP落点：保留 `/internal/runs/{run_id}/library/search` 与 `/library/read`，新增同前缀 `/library/view-image` 和 `/library/extract-text`；公开入口用 `POST /v1/files/{file_id}/extractions`、`GET /v1/file-extractions/{job_id}`、`POST /v1/file-extractions/{job_id}/cancel`。这些都是拟议接口。内部路由校验service scope、run attempt及当前文件授权；公开路由使用用户session，Portal同源代理不可转发internal。读取/查看结果另带 `file_id,version,derivation_id,source_digest,representation,status,coverage,next_cursor`。不存在或无权限统一404；合法但未完成返回202和job_id；参数范围错误422；队列满429+Retry-After；已取消/过期job返回稳定终态原因，不返回上游错误正文。

PDF `coverage`同时表达总页数、已处理页、已返回页和缺失/不支持页；Word用结构块覆盖，未知页数不填伪造数值。已处理但因token预算未返回的部分与根本未处理的部分必须区分。统一错误码包含 `source_unavailable/cursor_stale/processing/unsupported/vision_required/parse_resource_limit/ocr_timeout/quota_exceeded`，不可用不是空正文。

新job kind必须同步 `contracts/jobs.py`、producer、claim授权、业务终态判断、Worker分派/ACK/reaper和重投测试；不能只给outbox塞一个陌生kind。进度事件只发job_id、done/total/status，不携带正文或资产签名URL；客户端收到事件后按权限读取结果，轮询可取消并忽略旧响应。

扫描页未就绪时 `read_library_file` 返回 `processing` 和已可用块/缺失页，不返回假空白。Agent可调用extract_text触发或复用job；同步等待有短截止时间，长任务交回durable handle。Chat具备平台文件job调用能力，不因此获得exec或沙箱权限。用户可取消，切页取消前端等待不会误删原文件。

### 最小UI

普通上传和资料库选择保持统一。输入框附件卡片代表“本轮引用”，不额外用多层状态说明占空间；处理中/失败在需要时显示。文件菜单增加“提取文字”；弹窗只保留文件、选页、开始/取消；完成后复制、下载TXT、发送到对话。高级OCR实现、CPU/NPU、缓存hash不进入普通用户流程。OCR任务进度和失败可恢复，不新增常驻侧栏栏目。

### 大文件准入与5张图规则

下表是首版候选策略，配置由API下发，待样本定版；不是代码已有的限制。当前upload上限50MiB、parse上限20MiB，输入附件数量允许50，未实现独立图片5张限制。

| 资源 | Chat候选准入 | Work候选准入 |
| --- | --- | --- |
| 原文件字节 | 单文件≤20MiB且解析成功 | 原上传硬上限50MiB保留；>20MiB不在Worker全解析，走受限sandbox按需操作 |
| PDF/PPT页数 | 默认≤40页；更多仅接受用户明确选定≤40页范围 | 不按40页拒绝原文件；按范围解析/渲染，总job与每批限额仍执行 |
| DOCX长度 | 不依赖不可靠的Word页数；以解压大小/块数/提取token和实际模型余量判定 | 原文件可接受，先结构索引；按标题/段落/表格切块，受限遍历不一次打印全文 |
| 首次Chat正文包 | 全部附件合计≤`min(8192,0.35B,当前输入实际剩余预算)`；全文或明确选中范围均须完整 | 不自动喂全文，通常只给小型元数据/可用派生索引 |
| Excel长表 | 完整待回答范围必须放得下；超限要求用户选sheet/range或转Work | read-only分块、流式计算、必要重算；不无限展开有效范围或样式空单元格 |
| Chat扫描OCR | 需要OCR的选定页≤10，排队/执行有deadline；超出建议Work | 分页持久进度，每job候选≤20页；更长由上层计划分job，不一口气识别整个文件 |
| 独立上传图片 | 本轮最多5张，两模式一致；保留现有全部附件数上限并独立校验图片数量 | 同左；需要更多可分轮上传，但不让全历史图自动累积入上下文 |
| 文档页面/裁剪视觉输入 | 仅在视觉需求与预算内选页，不默认展开整份文档 | Agent按任务选择页/区域和批次；数量由模型能力与实际预算决定，不固定5图 |

输入框每轮最多附加5张独立图片，按内容识别的实际类型校验；用户后续可以继续附图，不设置整段对话累计5张的上限。PDF/PPT是文档附件，其页面/内嵌图不计入这5张；历史图片重新查看、工具返回的页图/裁剪图，以及实际模型请求的图像数量，也不受这个输入框数量限制。模型请求另按provider真实图像上限与剩余token、像素、字节预算验限，预算不足则选范围或分批，不能因为第6张就拒绝。只有确实超过对应模型/资源限制时才返回`image_budget_exceeded`，并说明实际限制。

百页Word在上传前未必能知道真实页数：传输阶段按字节拒绝413，安全解析后再做Chat准入。文件已存资料库则保留，标`chat_unavailable_too_large`，不把存储成功伪装成Chat已能读，也不私自删除用户文件。前端发送按钮等待明确准入结果；API启动run前再次按所选模型预算验。不支持解析、超时、ZIP炸弹分别返回稳定错误，不用“太长”掩盖安全失败。

已预处理文件换小窗口模型要重做输入准入，通常只重装配，不重跑OCR。用户选择范围生成的是chat_view，缓存键包含selector；不能用一个小范围结果覆盖完整派生文档。

### PDF/PPT“全量展开”的准确边界

避免的是默认同时做“整份逐页渲染＋所有页图一次性放进模型上下文”。例如用户只问第80页，先定位并处理相关页；用户要求整份视觉审阅，就建立覆盖计划，按预算分批读取，直至全部页面完成。用户明确要整份转换/识别时，也可以逐页生成全部派生结果并缓存，这不等于把全部结果一次性喂给模型。

文件解析/转换的处理范围、缓存覆盖范围、一次模型请求的输入范围是三个独立范围。禁止用“全量展开”笼统限制前两者；也不能把默认小批次写成不可调整的硬上限。Chat仍须通过本轮预处理/输入准入，超限显式选范围或转Work，不在Chat偷偷启动长期办公循环。

### 模型视觉能力与最少提示

API的`ModelSpec.public()`已经返回`capabilities`，复用这个字段补齐前端ModelInfo类型和ModelMenu显示，不新增重复vision真值；不从模型名称猜。当前缺口在前端类型与展示。

- 模型列表中支持vision显示一个视觉icon（有accessible label/tooltip“支持视觉”）；不支持显示无视觉状态icon/tooltip，避免每行长说明。
- 仅当本轮有图片且所选模型不支持vision时，在输入区出现一次短提示：**“当前模型不支持视觉，图片将通过 OCR 转为文字。”** 纯文字/普通Word附件不重复显示。
- OCR不可用时提示“当前模型无法读取图片，请切换支持视觉的模型。”，阻止假空白图像输入；图像语义问题不以OCR替代已看图。
- 处理中只在附件卡显示进度/失败；正常完成不播报OCR服务、NPU、缓存、格式转换等后台细节。

切模型时取消或忽略过期的准入/预处理响应；后台OCR已有任务可以继续缓存，但发送前根据最新模型与附件revision重新选表示。带图请求fallback只能选vision模型；不能半途回落文字模型且不重建OCR输入。需要改成OCR路径时应在尚未输出前重新装配并按真实调用记账，不能在已输出后拼接另一种模型回答。


## Token与检索预算

### 装配顺序与上限

先计算 `B = min(floor(0.70 * context_tokens), context_tokens - reserved_output_tokens)`，保留现有保守输入上限，使用当前候选模型真实能力及必要工具schema计算。小窗口模型不能直接套大窗口参数。

Chat装配是当前规则/必要schema、当前消息/准入后的可读包、有效摘要和近期交互；Work装配是当前规则/活跃schema、当前消息/小型附件索引、有效摘要/近期交互和本次按需读取结果。文件索引、摘要和读取共享B，不能各自单独认为有一整个窗口。

候选限额：摘要≤`min(2500, 0.10B)` tokens；附件元数据≤`min(800, 0.05B)`；Chat首次文档内容≤`min(8192, 0.35B)`；保持近期至少2个完整assistant/tool组，能多留就按剩余预算保留。Work当前用户必需输入超预算时分页/分批；Chat则明确要求选范围或转Work，不自动裁切或开始多轮办公工具循环。任何模式都不能删除用户约束强行发出。

图片按provider已知尺寸/patch/tiles规则计数，有实现的token计数API可优先使用；其余沿用保守估算和实测校准，不能把base64字符按正文算，也不能永远宣称每图精确4096token。显示缩略图、OCR图和模型高细节图是不同派生物；小字需要按需裁剪，不能只靠现有2048边显示图保证OCR质量。

预算算例（纯公式推算，不是模型计费实测；固定prompt/schema/历史过大时可用附件预算还会减少）：

| 上下文 / 输出预留 | B输入硬预算 | Chat附件正文候选上限 |
| --- | --- | --- |
| 8192 / 2048 | 5734 | 2006 |
| 16384 / 4096 | 11468 | 4013 |
| 32768 / 8192 | 22937 | 8027 |
| 204800 / 8192 | 143360 | 8192 |

这使小窗口模型更早拒绝长附件，但不会因为模型给了200K就自动塞几十万字。图片需要从同一B里扣除；输入框每轮5张只是用户附加数量限制，不代表模型请求固定最多5张，也不承诺小窗口一定能同时看5张高分辨率图。

### 去重和来源账本

单次请求对 `(file_id,derivation_id,locator,representation)` 去重，允许同一页的文字和必要图像同时出现，但计入两者成本。删除旧工具大输出前保存可重读locator。来源账本记录已读块/页、hash、读取run和是否仍在当前窗口；“已读”不等于“仍然记得全文”。

默认不每轮注入全库文件名；仅本轮附件、当前任务选定文件及与最近指代有关的少量历史文件。完整历史引用按游标查message_file_refs，分支只查祖先路径，不能用全会话最新时间猜“刚才那份”。不确定两个同名候选时澄清，不能随意选最新。

### 文档内检索

首个可用版本先做确定性页/章节读取，再做有界词法检索，不依赖embedding。SQLite采用FTS5、PG采用等价规范化词项索引；中英文分词策略必须统一（候选CJK bigram＋英文word），不能直接用PG英文tsvector当中文检索。

索引对象为全部可用文本块；索引构建由Worker给出受限词项，API分批写库。SQL中的授权过滤在排名/limit前，不允许检索跨用户后再隐藏。大文件索引生成是有界后台job；缺索引时返回 `index_pending`，可按显式页读取，禁止退回无界全文件扫描。fts5运行时不可用时显式关闭内容检索，不默默声称已支持。分数只表示相关性，不表示文字正确。

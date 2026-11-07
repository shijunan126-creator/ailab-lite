# 文件读取流程调查：结构导航、视觉与OCR

基线：fix/ocr-lifecycle-pr@87315188，PR160合并产品树main@fdc0a05；2026-11-05静态源码核对。授权为研究与方案修订；未改产品、未装依赖、未运行模型/浏览器/性能或全套测试。并入侧聊最新用户方向：Work排除Chat三个文档工具，主/子任务共享当前Work沙箱。最终方案统一见[设计](../architecture/design/2026-11-05-chat-document-input.md)。

## 结论与源码证据

| 事实 | 核对位置 | 对设计的含义 |
|---|---|---|
| ZCode已有open_document/read_document/search_document；open返回文档描述、outline与定位语法 | vendor/office-suite/upstream/office-runtime/src/adapters/server.ts:61、:204、:233 | 前文说read/search这两个名字并非上游工具不准确；AILab新设计是同名file_id适配合同，不是直接装上游服务就已接通 |
| 上游viewer已有先open→read/search→需要布局/空文字时capture的操作流程 | vendor/office-suite/upstream/pdf-plugin/skills/pdf-viewer/SKILL.md:17 | 可借鉴操作流程；capture_view依赖已打开的面板，不能照搬为无人浏览器时的后台渲染 |
| ParsedDocument有units/outline/read/search，AILab补contentInfo(has_text,visual) | vendor/office-suite/runtime/src/domain/model.ts:23 | 结构清单来自解析器，不要求文档自带目录；outline可为空，仍可按页/段落/表格/sheet定位 |
| runner先完整readFile并调用parser，再遍历anchor产文字块/manifest；PDF parser顺序getTextContent/getOperatorList，最多索引3000页；XLSX先加载整个工作簿 | vendor/office-suite/runtime/runner.ts:12、vendor/office-suite/runtime/src/adapters/parse/pdf.ts:64、xlsx.ts:125 | 模型端分页不是解析成本按页发生。不能声称现有runtime已提供常数成本快速全篇概览；大文件partial必须保留，真按范围解析需要扩适配器 |
| manifest记录outline/coverage/units/quality/visual_units，不输出可寻址的图片资源目录 | vendor/office-suite/runtime/runner.ts:39 | 可复用结构/覆盖合同；“内嵌图直接看”需要新增资源枚举/安全提取映射，不能声称已有 |
| PPT picture解析目前只输出descr或(image)，chart为占位；不是图像语义 | vendor/office-suite/runtime/src/adapters/parse/pptx.ts:86 | 图题/alt不等于已经看图，更不保证识别图内文字；文字与视觉覆盖独立 |
| 生成器navigation在共用入口写read_library_file/view_attachment/extract_text，Work随后还收到office-read指引；模块过滤仅Chat去Work段 | vendor/office-suite/build.py:170、platform/ailab_platform/agent/skill_modules.py:18 | 冲突根因在模式入口和可加载资源。须从生成源修，不手改生成SKILL，不改上游操作内容来凑模式 |
| office-read在Work镜像，超时60s、V8 heap160MiB；工作区查看在当前lease中渲染 | images/sbx-office/office-read:20、platform/ailab_platform/broker/workspace_view.py:1 | Work无需为了读/看再租系统reader/render沙箱，技能资源不构成额外沙箱 |
| Work并行子任务共享父run沙箱，当前允许列表仍有read_library_file | platform/ailab_platform/agent/subtasks.py:1、:41 | 模式隔离需覆盖主任务/子任务/发现/执行拒绝；并行工作目录分开，Office转换不能无限并行 |
| 现有页图路径Office→LibreOffice PDF→pdftoppm；PNG/JPEG直接读，PDF直接栅格化 | platform/ailab_platform/sandbox_tools/render_preview.py:112、view_workspace_file.py:75 | PDF不是所有读取的必经格式，只是当前整页排版渲染后端。提取原生文字/已有图片资源不需先转PDF |
| OCR现有独立执行器接有界图片推理，Work本地没有因此自动获得OCR引擎 | images/rapidocr/executor.py:130、:158 | Work若需OCR，要有当前lease绑定的受限图片/裁剪到现有执行器适配；不安装重复权重、不向沙箱发执行器密钥、不额外租平台render沙箱。此入口尚待实现 |

## 推荐固定流程

复用同一“发现表示→选择范围→原生文字/图像/OCR→检查覆盖”的读取方法，按模式提供不同操作说明；不往system堆平台实现细节。Chat只开放受限解析/搜索/看图/OCR表示，Work在原沙箱使用Office技能；不提供任意exec作为Chat只读能力。

概览给真实可得结构，不保证完整语义目录：身份/版本、页或块数量（未知则未知）、已有标题、文字/图像/OCR可用性、已检查范围、下一导航cursor与可选操作。无目录用稳定页号/段落号/表格号/sheet/range；未检查项标unknown。概览不做全篇OCR/全页栅格化，不额外调用描述模型；已缓存解析可直接使用，首次解析必须承认其真实成本。轻量probe与选范围解析是新适配工作，不是现成能力。

混排不能按“这页有文字→只读文字”分流：has_native_text/has_raster_or_vector_visual/ocr_coverage分别记录。原生文字可能只有页眉，图片内仍有文字；即使有原生文字也允许对页/区域显式OCR。空文字不证明是扫描件，可能是图表/路径文字/空白，模型可先看页再决定。

模型选择read_document(text_source=native/ocr)或view_document；默认native读取不隐式触发OCR。search只搜可用文字，报告未覆盖；模型需要扩大扫描文字检索时显式read(ocr)再search。保留3个独立工具，不增加第四个OCR名字。

表示选择：原生文字直接解析；已验证可独立提取的嵌入位图可直接看/OCR，但只证明该位图，不包含排版/叠字/图表组合；页/区域关系需要渲染；图片不转PDF，PDF不重转，Office整页图继续复用现有PDF后端。嵌图提取只作有支持映射时的可选路径，未实现时返回不支持/用整页图，不从裸ZIP路径猜来源。

## 验收与剩余证据边界

必须补带页眉的扫描混排、可疑/旧文字层、无outline、部分索引、原生chart/组合图形、同文件重复读取、Work主/子任务工具隔离与工作区OCR样本。测冷/热概览、范围解析、页图/OCR时延、CPU/RSS与输入token；当前无数值性能证据，不能承诺“概览很快”或“直接提图必然便宜”。

## 当前系统沙箱池职责核对（同日补充）

此处是87315188代码与默认配置，非运行中容器盘点。本环境私有AILab-local符号链接目标不可读，未据历史部署推断当前数量。

- DocumentReader._read使用LibraryRenderer._spec与manager.acquire_render；解析与渲染是同一profile=render池的不同job。config默认reserved_slots=1、prewarm=1、768MiB，不是两类各一个。闲置可回收，容器身份可替换；同owner可复用，跨owner旧idle需回收/重建，不保证跨用户直接复用同容器。
- 池内业务工作：四格式原生文字/结构解析与manifest/块导出；Office转PDF并缓存；PDF/Office页面栅格化供预览及模型视觉；为OCR准备选定页图（不执行OCR推理）；PPT模板缩略图；需要系统渲染的表格预览（转换/公式重算及有界sheets.json，普通原生XLSX/CSV/TSV预览通常由Worker本地处理）。业务之外还做Office预热、任务临时文件清理、健康检查与回收。
- Work工作区view/review/fallback虽持有render_activity调度票据，但使用现有Work lease，不调用acquire_render另租系统沙箱。排队域不能当容器数量。
- Worker的图片缩放/缩略图、文本预览、部分表格预览、原生viewer产物、模板/品牌/技能包解析以及PDF低文字分析使用受限子进程，不各分配Broker沙箱。OCR、扫描、网页crawler/egress是独立服务/进程，不属于这一个Office系统池。
- 历史ADR0023的专用sbx-document-reader镜像仍在仓库；当前config.document_sandbox_image没有实际读取调用点。不能因存在镜像/配置名就认为有额外reader容器。真实运行数量必须查当前部署与lease/provider状态，本轮未完成该盘点。

源码：broker/documents.py:37、broker/render.py:35/:113/:315、broker/service.py:549/:598/:670/:780/:1039、worker/preview.py:79、worker/isolate.py:23、config.py:221。

## 浏览器减负调查（同日补充）

- 已有能力：Portal office/build.mjs打包四格式原生viewer；office-view.tsx:187通过page-tool调用capture_view；lib/office-views.ts按view_id注册在线查看器；lib/js/client-calls.ts:17领取请求、截图缩放到2MiB预算并回传结果。有版本、限额、claim、超时协议，可复用，无需新建浏览器任务系统。
- 现有后台依赖：office-view.tsx:101的useDocumentIndex等待服务端index ready；worker/viewer.py:derive运行viewer-reader.ts生成结构索引。host.ts:93的read/search等server tool调用通过桥接回平台，不是已有浏览器全文检索。api/preview_requests.py仍有eager preview路径。因此“浏览器能画文档”不能推断无系统reader/render成本。
- 建议优先在线原生预览＋复用当前页截图、撤普通上传自动导出，并拆打开viewer与全文索引的依赖；完整索引/OCR仍走原路径。OCR前移须新增下载权重、WASM/Worker、设备资源/兼容与恢复路径，当前未证明能简化，第一阶段不做。
- 本轮仅静态调查；未改Portal、未运行浏览器或性能测量。完整设计合同在设计第2.5节，不另存重复性能报告。

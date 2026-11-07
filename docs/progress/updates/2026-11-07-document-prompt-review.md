# 文件输入：提示词与调用逻辑终审修复

用户在提PR前要求复核Chat/Work提示词（缩小范围、去冗余/冲突、不让模型绕路）和整条调用逻辑，并授权做全部修复与真实模型测试；不push/PR/合并/部署。分支`feat/document-input-lifecycle`，基线`7d446f9c`（承接[真实模型workflow记录](2026-11-07-real-model-document-workflows.md)），本轮未fetch。

## 根因与修复

1. **提示词绕路**：V2 Chat指南要求先`resolve_attachment`/`navigation_only`，与“附件正文已给出”冲突，模型每轮多一次目录调用；viewer指南重复整段workflow，夹带测试语料措辞（“五行文字”“奇偶页”）和其他格式规则。`vendor/office-suite/build.py`改为每种格式只给本格式事实，带正文直接回答、文字用read、视觉问题才view；工具契约只写在工具描述里一次。系统提示删去与动态恢复事实重复的inputs规则，回答范围/交付总结各缩成一句（genui 2000字预算回到范围内）。
2. **PPT区域参数试错**：服务端重编码的页图`image_revision`是JPEG摘要，区域校验却比对RenderedPage摘要，模型无法从返回值得到正确身份，只能猜。`view_document`结果每个文件直接给`region_ref`（capture用`image_id`，规范页用`page`），工具描述要求原样填入；revision失配附可执行的下一步。
3. **参数错误不可恢复**：本地无pydantic判别式，422只有笼统文案，模型盲重试。`TextSelector`按`kind`判别；Agent在调用API前本地校验，返回字段级`fix`，不回显参数值。view失败改为普通工具结果而非ToolError。
4. **Work复制失败**：模型把资料库文件复制到`/workspace/work`，Broker 422被吞成通用错误。V2描述明确复制到`/workspace/inputs/`、path可省略；422返回路径规则。
5. **多页OCR批次必失败**：执行器在可能失败前按`X-Ocr-Operation`登记回执并拒绝重复；平台对同一operation的每页都用operation id，第二页恒409→`ocr_failed`。首次改成按`image_index`仍失败——每步只重建剩余图片，序号恒为0。最终按`operation:claim_token:selector摘要`标识每次派发的每张图（legacy页循环按页）；再投递换claim token，不复用已登记身份。
6. **既有回归**：`file_work.finish`的kind白名单把文件处理job当作superseded（`47d9c757`引入）；改为围栏本operation自己的job kind。

## 验证（基线`7d446f9c`+本轮改动）

- Ruff check/format通过（646文件）。完整Python套件（SQLite，最后一次OCR身份修复前）退出码0，进度输出计2561通过/237环境跳过；最终OCR身份修复后重跑OCR/文档9个相关文件122通过/6跳过。未跑双库PostgreSQL/Redis、E2E、Portal build（未改Portal）。
- 新增/调整回归：录制前缀（新规则文字、移除旧规则）、view `region_ref`、参数字段级反馈、revision失配提示、copy路径反馈、执行器拒绝重复id的fake、执行id每图每派发唯一。
- 真实DeepSeek Flash（`actual_backend`全部`deepseek-flash`），独立VM 2CPU/6GiB，真实ClamAV/Docker Office/CPU RapidOCR，公开API驱动，无Portal：30轮/134次settled调用。

| 场景 | 修复前（上一记录/首轮） | 本轮 |
| --- | --- | --- |
| 目录先行调用 | 每个Chat场景1次`navigation_only` | 0 |
| Word表格首轮 | 8.9s | 6.2s |
| PDF指定页首轮 | 11.9s | 9.3s |
| Excel数据首轮 | 36.9s | 11.4–11.9s |
| PPT视觉两轮 | 每轮1次region失败 | 0失败，22.1/15.1s |
| Work Word两轮 | 各1次失败，41.0/26.4s | 0失败，28.3/22.1s |
| 扫描件2页OCR | `ocr_failed`后逐页重试 | 0失败，2次执行器200，24.4s |

追问首请求暖缓存96.97%–99.16%（13次），均≥95%。时间均为单次观察。

## 剩余边界修复（同日第二轮）

用户要求把上面列出的剩余边界“能修的都修”。基线为`191fcdba`加本轮未提交改动。

1. **小PDF视觉unknown**：只要页面有矢量就判unknown，带表格线的小PDF因此不内联。改为按pdf.js路径几何分类（vendor补丁011）：跳过只裁剪的`endPath`，坐标轴线条算rules，填充框算shading（仍unknown），曲线或斜线才算drawing。改后带线小PDF直接内联，首轮7.3s、零读取（上一轮9.3s加一次read）。
2. **Excel工作表→页映射**：Calc导出PDF时每张打印表一个书签，渲染据此记录各表首页（`render_sheet_pages`，绑定源版本）。新增`view_document`的`kind=sheet`按表名看页，结果附`sheet_to_page_mapping`；Work的`view_workspace_file`返回`sheet_pages`。空表和隐藏表没有页，返回`sheet_not_rendered`。首次看页时学习书签的渲染job挂在`render_job_id`依赖边上，不会被当孤儿取消。
3. **PPT像素文字归属**：块标签带形状在页面上的百分比范围（补丁012，例如`@x3-73% y13-69%`），模型据此判断渲染图里的文字落在哪个形状内。inline页图标签带`region_ref`，后续区域查看不用再猜revision。PPT视觉归属3/3正确（修前1/4）。
4. **搜索绕路**（终跑发现）：`search_document`每次只扫100块，400段文档末尾的词需要4次调用，模型中途改道，用了25次调用、52s。改为按单次工作量限界：最多2000块、100万字符、100个存储对象。canonical文本一次读完；legacy逐块文本和OCR页仍不超过100个对象。复测两次：4–5次调用，11.1–12.7s。
5. **Work exec用dash**（终跑发现）：Docker后端和后台helper用`/bin/sh`，在Debian镜像里是dash，模型写的`<(...)`报语法错误；OpenSandbox execd本来就用bash，提示词也要求pipefail。三处后端统一为bash。复测4轮Work Word都没有shell错误；唯一一次失败是模型自己写的`assert`拦下了错误假设，下一步已修正。
6. **Work目录**：镜像和mock预建`/workspace/work`，`office-read`自动创建父目录，去掉先失败再mkdir的绕路。
7. **本地对象目录并发首开**（PostgreSQL/Redis套件里偶发失败）：切WAL需要独占锁，SQLite对此立即返回BUSY、不等busy_timeout，16线程首开时200次里失败7次。改为只对这一步有界重试，400次压测0失败。

验证（代码head `766ccfcc`）：
- Ruff通过。PostgreSQL/Redis双库完整套件退出码0，2739通过、69环境跳过。SQLite完整套件2567通过、240跳过、1失败；失败的是定时任务合并派发测试，在`766ccfcc`修复（不是本分支引入：套件跨过09:00 HKT时，前面测试建的任务到期被一并派发），单独重跑通过。容量淘汰加入后在最终代码上重跑SQLite完整套件：退出码0，2566通过、242跳过；淘汰相关的派生账本测试在SQLite与PostgreSQL都通过，PostgreSQL双库完整套件没有在淘汰改动后重跑。E2E（mock沙箱）194通过、15跳过。未改Portal，`npm test`（258个测试）与`npm run build`都通过。
- 真实DeepSeek Flash（`actual_backend`全部为`deepseek-flash`），独立VM：终跑12个场景，追问首请求缓存命中96.92%–99.42%（11次）。搜索与bash修复后，literal-search和Work Word各复测2次，0平台失败，追问缓存97.17%–99.21%（4次）。时间均为单次观察。
- 只配置了DeepSeek Flash的凭据，没有做多模型评估。

8. **派生缓存容量满时不淘汰**（用户追问临时文件生命周期）：账本预算满了只会让写入方得到507，可重建的缓存要等30天到期。新增容量淘汰：使用量超过高水位（90%）时，维护循环按创建时间从旧到新淘汰可重建的`cache`，直到低水位（75%），沿用到期回收的认领流程（先使元数据失效、再删对象、成功后释放字节）。原生文本、OCR、截图、有pin的、15分钟内新建的都不动。核对了渲染缓存PDF：经`AccountedWrites`登记为`cache`，随文件删除按前缀清理。顺序用创建时间而非最近使用，因为读取不写账本；要做LRU需另加读取路径的合并写入，未做。测试覆盖淘汰顺序与保护、元数据失效、删除失败时仍占用字节、`EXPLAIN`命中`ix_derived_assets_captures`（SQLite与PostgreSQL）。

不改的两项：回答偏长或外推，系统提示里已有一句“只回答本次问的内容”，本轮回答都没超出范围，不再加重复规则；两页OCR文字相同时再看图核对，属于合理核验。

## 剩余边界

- 含填充框的小PDF仍判visual unknown，不内联（填充可能是图表）。Excel只映射打印表，没有书签时返回`sheet_page_mapping_unavailable`，模型改用页码查看。
- 模型偶有小失误：Excel区域行名标错一次；Excel第二轮看过页后有时还做一次OCR。按表名做OCR没有实现。
- 未覆盖多模型、非合成业务文档、Portal浏览器路径与NPU/S3/OpenSandbox真后端。
- 运行产物（计划、事件、回答、计量）只在仓库外临时目录，结束后删除。

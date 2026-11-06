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

## 剩余边界

- 含矢量线条的小PDF视觉状态unknown，按设计不内联页图，模型仍可能主动看页核对；Excel工作表→渲染页映射未知，模型需排除法定位。
- 回答仍可能偏长或从局部推广（如PPT未看页）；两页OCR文字相同时模型会再看图核对，属合理但多一步。
- 未覆盖多模型、非合成业务文档、PostgreSQL/E2E、Portal浏览器路径与NPU/S3/OpenSandbox。
- 运行产物（计划、事件、回答、计量）只在仓库外临时目录，结束后删除。

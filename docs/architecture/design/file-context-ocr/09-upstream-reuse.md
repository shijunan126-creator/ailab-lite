# Office Runtime与平台内置skills复用计划

> 实施状态（2026-10-29）：用户已授权本方案实施，覆盖下文历史“只设计”限制；不授权push/PR/merge/部署。OCR只计量、不计费、不设用户累计配额，资源隔离仍保留。当前实现、验收及剩余缺口以[实施记录](../../../progress/updates/2026-10-29-file-context-core-validation.md)和[验收逐项记录](../../../reviews/file-context-2026-10-29/acceptance.md)为准。本文其余历史状态不代表当前完成度。

状态：**仅plan，未实施**。用户明确要求研究后修改plan；本轮没有授权导入产品代码、安装依赖、构建或部署。2026-10-29核对上游固定提交，当前本地产品基线仍为`main@e71a60f`。[总览](README.md)；能力发现合同见[03](03-capability-discovery.md)。本文是上游选择、具体路径及接入安排的唯一维护处。

## 1. 选择结论

优先复用 **ZCode Office Runtime的读取/搜索/定位核心＋对应viewer skills**，保留上游模块和测试结构，用薄适配层接AILab文件授权、缓存与工具，不重新手写四套同类解析器。完整桌面插件/UI不随核心一起搬入Portal。办公生成/编辑规范可补充MiniMax MIT skills；Claude Code的公开材料作为行为参考，不作为可直接搬用的办公运行时。

上游“测试过”不等于已覆盖我们的多用户服务、沙箱限制或大文件预算。接入验收是针对这些差异，不是重新开发解析算法。**更快、更省资源目前未证实，先测同等工作量。**

## 2. Office Runtime到底是什么

ZCode多个办公插件共用的TypeScript运行库：上接`open_document/read_document/search_document/show_in_viewer/get_view/capture_view`等工具和查看面板，下接Word/PDF/Excel/PPT解析器。它维护结构化文档、页/段落/表格/单元格位置、读取游标、当前查看位置，以及按path/revision缓存的解析结果。

skill是模型读取的操作说明；runtime是实际完成解析、读取、搜索和面板协作的代码。读取核心和viewer UI可以分阶段接入；AILab已有文件预览、对象存储和权限系统，不需要再启动一套ZCode桌面或把本地workspace路径暴露给模型。

## 3. 已读取的固定源码与直接路径

ZCode固定提交：`3c5b227649371507ddc4eb46ac5133afe6db1cab`。以下每个链接均指向本轮实际读取文件，不是待搜索的目录名。

| 复用对象 | 上游路径 | 用途/接入判断 |
| --- | --- | --- |
| 共用引擎定义 | [office-runtime/package.json](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/office-runtime/package.json) | MIT、Node≥24；确认引擎依赖与模块入口，不能当纯Python库导入 |
| 工具/返回契约 | [office-runtime/src/contract.ts](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/office-runtime/src/contract.ts) | 文档类型、locator/outline、错误与读取预算；外层换AILab文件身份 |
| 读取与会话状态 | [office-runtime/src/app/documents.ts](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/office-runtime/src/app/documents.ts) | createDocumentOwner、revision缓存、read/search/view；不照搬跨用户path缓存 |
| 定位语法 | [office-runtime/src/domain/anchor.ts](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/office-runtime/src/domain/anchor.ts) | Word段/表格、PDF页、PPT页/shape、Excel范围；优先保留语法并映射file_id |
| Word解析 | [office-runtime/src/adapters/parse/docx.ts](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/office-runtime/src/adapters/parse/docx.ts) | 标题、段落、表格按结构读取与定位 |
| PDF解析 | [office-runtime/src/adapters/parse/pdf.ts](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/office-runtime/src/adapters/parse/pdf.ts) | PDF.js文字/页码、search和分页；空文字页交平台OCR |
| Excel解析 | [office-runtime/src/adapters/parse/xlsx.ts](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/office-runtime/src/adapters/parse/xlsx.ts) | 工作表、单元格/范围和公式/缓存信息 |
| Word操作说明 | [docx-viewer/SKILL.md](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/documents-plugin/skills/docx-viewer/SKILL.md) | open/read/search、段落表格anchor、用户选择与正文的关联 |
| PDF操作说明 | [pdf-viewer/SKILL.md](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/pdf-plugin/skills/pdf-viewer/SKILL.md) | 页范围读取、搜索和按需看图；保留上游流程，替换工具/路径 |
| Excel操作说明 | [xlsx-viewer/SKILL.md](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/spreadsheets-plugin/skills/xlsx-viewer/SKILL.md) | sheet/范围定位和读取 |
| PPT操作说明 | [pptx-viewer/SKILL.md](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/presentations-plugin/skills/pptx-viewer/SKILL.md) | slide/shape读取和按需图像 |
| 技能加载器 | [core/src/tool/handlers/skill.ts](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/handlers/skill.ts) | 加载正文、展开技能根目录变量；作为适配依据，不必搬整个Agent框架 |
| 技能索引装配 | [core/src/context/sections/skills.ts](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/context/sections/skills.ts) | description预算；不能照搬全量技能目录来代替03的按需搜索 |

上游`office-runtime/src/adapters/parse/pptx.ts`、`domain/model.ts`、`domain/text.ts`及OOXML/文件适配辅助文件在完整tree中已确认存在；实施导入时带齐其依赖闭包与原测试，不仅复制一个SKILL.md。它们不在本轮逐文件深读清单，不能记为已验证行为。

### 办公创建/编辑的补充对象

MiniMax固定提交`60aaae52bb2af8162732751a4332f62a5fef518b`，以下路径本轮已读：

- [minimax-xlsx/SKILL.md](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-xlsx/SKILL.md)、[scripts/xlsx_reader.py](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-xlsx/scripts/xlsx_reader.py)、[references/read-analyze.md](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-xlsx/references/read-analyze.md)：结构发现/读取/编辑路线可复用；reader用pandas整表加载，不直接用于大表流式入口。
- [minimax-docx/SKILL.md](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-docx/SKILL.md)、[scripts/docx_preview.sh](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-docx/scripts/docx_preview.sh)：可移植审阅/编辑规范；主体方案依赖.NET OpenXML SDK，不能不测成本就把.NET全装到lite镜像。
- [minimax-pdf/SKILL.md](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-pdf/SKILL.md)、[scripts/merge.py](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-pdf/scripts/merge.py)：生成/填表/合并可复用，不把偏生成的技能当完整PDF读取引擎。
- [pptx-generator/SKILL.md](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/pptx-generator/SKILL.md)、[references/editing.md](https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/pptx-generator/references/editing.md)：读/编辑/生成的路线与规范。上游markitdown命令是工具调用方法，不是本平台已安装该依赖的证据。

## 4. 许可与Claude Code核对结果

- ZCode [仓库LICENSE](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/LICENSE)为Apache-2.0，[office-runtime/LICENSE](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/office-runtime/LICENSE)为MIT。viewer skills未看到独立相反许可，按仓库许可证记录来源；runtime保留其MIT声明。
- ZCode docx/pdf/xlsx主要生成技能的frontmatter仍写Proprietary，但各自LICENSE.txt写Apache-2.0。本轮记录这处冲突，首批采用许可明确的runtime/viewer部分；生成技能不能只看仓库根就整包复制。可优先采用MiniMax明确MIT版本，所需格式对应子目录声明/脚本头仍需随导入核对。
- MiniMax根及minimax-docx子目录许可已读取，均MIT；复制时保留声明并记录改动，不用“参考”二字掩盖实际代码复制。
- Claude Code官方[README](https://github.com/anthropics/claude-code/blob/58d6da1e34d6cd1c6379dcbf568bd66f5f9b6102/README.md)与[LICENSE.md](https://github.com/anthropics/claude-code/blob/58d6da1e34d6cd1c6379dcbf568bd66f5f9b6102/LICENSE.md)已读。该固定tree不是可直接导入的完整办公运行时来源，根许可为all-rights-reserved/商业条款。官方[skills文档](https://code.claude.com/docs/en/skills)介绍内置/按需技能，但公开机制不等于对应内部实现已开源。
- `anthropics/skills`的docx/pdf/xlsx/pptx有独立Proprietary许可，见既有证据模块；不从泄露/第三方镜像获取内部skill作为可分发依赖。本方案已有可用的ZCode/MiniMax复用对象，不需要押在这条路径上。

## 5. 平台内置能力，用户不可管理

本次讨论的技能归属是`platform-internal`。随平台镜像固定版本发布，用户没有启用、禁用、编辑、删除、导入这些技能的入口；不进入`/v1/skills`列表、输入框技能按钮、市场或用户资料库。现有个人/可选技能机制保持独立，不能把这些内置能力注册进同一个可切换目录再靠前端隐藏。

建议内部命名空间`platform.document.read`、`platform.pdf.read`、`platform.spreadsheet.read`、`platform.presentation.read`；内部registry与公开skill catalog分离，不能被用户同名skill覆盖。内部可搜索的metadata只有服务端和Agent可读，目录不全量进prompt；按mode、模型能力、MIME/任务发现并加载相关正文。平台运维可通过版本发布治理，不属于用户个人配置。

Chat主要由平台确定性预处理，内部规范约束“当前可读包的范围/来源、何时需要视觉”，不让Chat承担多轮Office操作；Work按发现的内置skill使用同一runtime工具或沙箱操作。真正依赖viewer面板的get_view/show_in_viewer/capture_view在Portal适配前不能假装可用；初期保留上游技能结构，只删去/替换平台未提供的工具指令。

## 6. 接入计划与最小适配

以下都是未来实施步骤，本轮不执行：

1. **固定源与导入清单**：候选`vendor/zcode-office-runtime/`保留runtime源码/依赖闭包/测试/MIT；内部viewer技能候选`platform/ailab_platform/internal_skills/`保留上游结构和Apache声明。记录repo、commit、file hash、license、patch清单。不要直接修改上游源来嵌入AILab业务权限。
2. **工具契约映射**：open→文档元数据/outline，read(anchor,cursor)→read_library_file，search→文档内检索。模型仅持`file_id+版本+locator`，薄适配把授权文件绑定到运行目录；上游path anchor不直接当宿主路径。读完的coverage/next_cursor回到既有引用/缓存合同。
3. **受限执行**：优先复用Broker现有文件作业/render租约承载可信Office runner，Worker只编排/提交派生物。sbx-office候选加入固定Node24和最小runtime依赖；Chat可使用平台内部解析作业，不让Chat模型获得用户exec。不得把Node全量解析放到API线程或直接突破Worker192MiB解析预算。
4. **同一读取核心服务两模式**：Chat由平台调用runtime生成可读包、做准入再发模型；Work通过工具调用runtime的read/search/定位，也可在沙箱操作原文件。优先复用已有结果，不为了套runtime再次OCR。使用Broker槽位的冷启动/排队必须计入Chat首文件延迟；若不满足快交互目标，先优化执行适配与缓存，不能宣称默认已更快。
5. **OCR仍独立**：runtime提供PDF原生文字和页面定位；无可读文字时接平台RapidOCR/CANN服务。上游技能里本地安装OCR、自动联网安装环境、直接cat大XML的流程要替换成既有平台工具/限制，不能整段照做。
6. **上线替换边界**：按格式验证后替换现有Python原生文本提取路径；预览、文件生命周期、用户授权保持既有业务权威。新后端失败返回明确原因，不隐式回旧解析器掩盖故障；回滚是部署/版本回滚，不是每请求双跑。是否引入viewer UI另列实现项，不为读取核心重复开发一套Portal。

资源路径选择需要先证明上游依赖闭包可在当前镜像运行：它不是可直接pip安装的库，package还标private。建议保留源代码构建产物，避免未来靠未发布npm包。不能把全部devDependencies（面板React、Playwright等）原封不动装入读取runner；按静态依赖闭包构建，保留实际使用的解析库与许可证。

## 7. 当前源码可见的性能边界

本轮已读到：Node≥24；PDF用PDF.js并复制输入bytes，初始化可索引最多3000页；DOCX读取XML后构建块数组；XLSX通过ExcelJS装载workbook。createDocumentOwner缓存最多6份解析文档、4个文件handle，按path/fingerprint命中。**分页返回只限制输出，不代表解析过程流式/常量内存。**

这些比现有简单解析提供更统一的定位/搜索能力，但不能直接证明更快。多用户服务不能仅按workspace路径缓存，也不能让6份大文件缓存挤满共享服务；首次接入使用受限任务生命周期，持久复用由平台文件版本缓存负责，之后再按真实成本决定是否保留热执行器。

## 8. 接入前对照与验收

比较同一批PDF/DOCX/XLSX/PPTX，双方提取**相同页/表/范围**；旧Python仅读PDF前20页，不能与新runtime读完3000页直接比耗时。至少区分冷进程/冷解析、同进程缓存命中、平台持久缓存命中三组。

记录整体墙钟、解析CPU、进程树/容器峰值RSS、源文件与派生磁盘、Node依赖/镜像增量、Chat首答时延、查第80页/段落/单元格耗时、内容覆盖和定位正确性。样本含小文件、100页扫描/文字/混合PDF、长Word/表格、公式缓存缺失Excel及含多媒体PPT；中文路径/重命名/不同用户同名与删除在途也要测试。

复用上游原测试证明未破坏其行为，再增加AILab薄适配合同：API权限、run/file绑定、游标/版本、资源限额、错误语义、OCR回调、缓存复用/删除；内部skill不得出现在任何用户管理接口，用户同名skill不能覆盖，恢复后仍加载已固定版本。上游测试通过不能替代这些合同。

验收结论分别给出功能和性能：若统一anchor/读取能力合格但冷启动较慢，应如实记录并优化执行层，不臆称全面更快；若无法适配目标资源预算，不把功能接通当容量验收。实施后仍按AGENTS跑完整组测与UI相关真实浏览器，当前仅检查plan与源码证据。

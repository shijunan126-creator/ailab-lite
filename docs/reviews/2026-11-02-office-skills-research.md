# 上游办公插件与 AILab 基础技能调研

后续：用户已要求先出完整迁移 plan，见[模块计划](../architecture/design/office-suite/README.md)。本文保留源码证据，不作为实施授权。

范围：用户要求研究本机 上游 packages 的 plugins/skills，解释当前四个通用基础技能为何显得简略。本轮只读源码并记录结论，未授权产品实施、发布或部署。

基线：AILab `fix/source-pill-alignment@c033664c4d9d5837283193f44fbd9ce2ede306e9`，含进入任务前已有未提交改动；上游 本地干净 `main@a60ba6a7e5a06742270ca605f5d2cc2766afa7e1`。AILab vendored runtime 固定 `3c5b227649371507ddc4eb46ac5133afe6db1cab`，不把两版差异当作同一版本行为。源码根 `$UPSTREAM`。未查询部署态。

## 结论

AILab 目前复用的是 上游 Office Runtime 读取核心及 viewer 指导，办公创建/编辑主要由本地自写生成器提供；没有完整采用 上游 的制作技能包。四个基础技能实际是“共享读取约束＋格式专属简单制作流程＋基础检查”，不能等同 上游 四个完整插件。用户感到制作能力偏简单，有源码依据；这不证明真实输出一定差于 上游，尚无同模型对照实验。

品牌颜色、字体、logo 和模板是独立资产输入，能约束风格，不能替代编辑、排版、分析和质量检查流程。问题不在于四个入口数量，而在于每个入口背后的任务路由和执行资源深度。

## 上游 绝对路径与用途

以下均为本机实际存在的路径。阅读顺序建议：四个制作入口 → 对应任务/场景模块和脚本 → viewer/runtime → 技能加载机制。

| 绝对路径 | 用途 |
| --- | --- |
| [$UPSTREAM/documents-plugin/skills/docx/SKILL.md]($UPSTREAM/documents-plugin/skills/docx/SKILL.md) | Word 制作入口；按新建、编辑、排版、批注、读取选择路线。 |
| [$UPSTREAM/documents-plugin/skills/docx/routes]($UPSTREAM/documents-plugin/skills/docx/routes) | Word 五条任务路线；edit.md 说明既有文档修改与样式保留。 |
| [$UPSTREAM/documents-plugin/skills/docx/scenes]($UPSTREAM/documents-plugin/skills/docx/scenes) | Word 场景规范：公文、报告、合同、学术、简历、试卷、文案。 |
| [$UPSTREAM/documents-plugin/skills/docx/references]($UPSTREAM/documents-plugin/skills/docx/references) | 封面、目录、公式、图表、OOXML 与排版参考。 |
| [$UPSTREAM/documents-plugin/skills/docx/scripts]($UPSTREAM/documents-plugin/skills/docx/scripts) | 文档编辑/批注辅助库，以及 postcheck.py 等检查脚本。 |
| [$UPSTREAM/spreadsheets-plugin/skills/xlsx/SKILL.md]($UPSTREAM/spreadsheets-plugin/skills/xlsx/SKILL.md) | Excel 制作入口；按创建、编辑、分析、转换、财务等任务路由。 |
| [$UPSTREAM/spreadsheets-plugin/skills/xlsx/scenes]($UPSTREAM/spreadsheets-plugin/skills/xlsx/scenes) | 数据分析、财务、编辑、转换、VBA 等具体操作规范。 |
| [$UPSTREAM/spreadsheets-plugin/skills/xlsx/engines]($UPSTREAM/spreadsheets-plugin/skills/xlsx/engines) | 图表、视觉设计及 VBA 参考模板。 |
| [$UPSTREAM/spreadsheets-plugin/skills/xlsx/quality/pipeline.md]($UPSTREAM/spreadsheets-plugin/skills/xlsx/quality/pipeline.md) | Excel 构建与验收流程说明。 |
| [$UPSTREAM/spreadsheets-plugin/skills/xlsx/xlsx.py]($UPSTREAM/spreadsheets-plugin/skills/xlsx/xlsx.py) | Excel CLI：重算、公式审计、模式扫描、结构读取、数据透视、图表检查、验证。 |
| [$UPSTREAM/presentations-plugin/skills/pptx/SKILL.md]($UPSTREAM/presentations-plugin/skills/pptx/SKILL.md) | PPT 设计、pptxgenjs 示例、模板制作及既有 PPT 保样式修改；本技能的主要说明集中在此文件。 |
| [$UPSTREAM/pdf-plugin/skills/pdf/SKILL.md]($UPSTREAM/pdf-plugin/skills/pdf/SKILL.md) | PDF 制作入口；按报告、创意排版、学术和文件处理选择路线。 |
| [$UPSTREAM/pdf-plugin/skills/pdf/briefs]($UPSTREAM/pdf-plugin/skills/pdf/briefs) | 报告、海报、简历、学术及既有 PDF 处理的场景流程。 |
| [$UPSTREAM/pdf-plugin/skills/pdf/typesetting]($UPSTREAM/pdf-plugin/skills/pdf/typesetting) | 分页、溢出、字体、配色、封面、图表和几何布局规范。 |
| [$UPSTREAM/pdf-plugin/skills/pdf/scripts]($UPSTREAM/pdf-plugin/skills/pdf/scripts) | PDF 处理、HTML 转 PDF、排版与质量检查脚本。 |
| [$UPSTREAM/documents-plugin/skills/docx-viewer/SKILL.md]($UPSTREAM/documents-plugin/skills/docx-viewer/SKILL.md) | Word 面板读取、搜索、定位与用户引用指导。 |
| [$UPSTREAM/spreadsheets-plugin/skills/xlsx-viewer/SKILL.md]($UPSTREAM/spreadsheets-plugin/skills/xlsx-viewer/SKILL.md) | Excel 面板工作表/单元格读取与定位指导。 |
| [$UPSTREAM/presentations-plugin/skills/pptx-viewer/SKILL.md]($UPSTREAM/presentations-plugin/skills/pptx-viewer/SKILL.md) | PPT 面板幻灯片读取与定位指导。 |
| [$UPSTREAM/pdf-plugin/skills/pdf-viewer/SKILL.md]($UPSTREAM/pdf-plugin/skills/pdf-viewer/SKILL.md) | PDF 面板分页读取、搜索与查看指导。 |
| [$UPSTREAM/presentations-plugin/agents/visual-judge.md]($UPSTREAM/presentations-plugin/agents/visual-judge.md) | 视觉验收 Agent 配置：读取页面 PNG，逐页报告通过/失败及问题；其余三个办公插件也各有该文件。 |
| [$UPSTREAM/documents-plugin/.plugin/plugin.json]($UPSTREAM/documents-plugin/.plugin/plugin.json) | Word 插件装配清单，声明 skills、agents、MCP server 和 UI surface；其余三个插件同名相对路径用途相同。 |
| [$UPSTREAM/office-runtime/src/app/documents.ts]($UPSTREAM/office-runtime/src/app/documents.ts) | 四格式共用的文档读取、搜索、缓存及查看状态逻辑。 |
| [$UPSTREAM/office-runtime/src/adapters/server.ts]($UPSTREAM/office-runtime/src/adapters/server.ts) | 将文档能力暴露为 MCP 面板工具。 |
| [$UPSTREAM/office-runtime/src/adapters/parse]($UPSTREAM/office-runtime/src/adapters/parse) | DOCX/XLSX/PPTX/PDF 原生内容解析器。 |
| [$UPSTREAM/core/src/context/sections/skills.ts]($UPSTREAM/core/src/context/sections/skills.ts) | 将技能名称、描述和路径装配为模型可见目录。 |
| [$UPSTREAM/core/src/tool/handlers/skill.ts]($UPSTREAM/core/src/tool/handlers/skill.ts) | Skill 工具入口：加载正文、展开技能目录变量、返回模型上下文。 |
| [$UPSTREAM/adapters/src/skills/index.ts]($UPSTREAM/adapters/src/skills/index.ts) | 技能发现、元数据解析、禁用过滤和正文读取实现。 |
| [$UPSTREAM/adapters/src/skills/roots.ts]($UPSTREAM/adapters/src/skills/roots.ts) | 决定从用户目录、项目目录及额外根目录查找技能。 |

注意：这些是上游源码路径，不是 AILab 沙箱内可直接调用的安装路径。AILab 现有复用代码位于 `vendor/upstream-office-runtime/`，四基础技能位于 `images/sbx-office/skills/platform.docx/`、`platform.xlsx/`、`platform.pptx/`、`platform.pdf/`。

## 上游结构与执行顺序

四个插件的 `.plugin/plugin.json` 均声明 skills、agents、stdio MCP server 与侧栏 UI surface。插件打包的是不同职责：

1. Skill metadata（name、description、when_to_use）进入技能目录，模型按用户任务选择。
2. `Skill` 工具通过 adapter 加载 `SKILL.md` 正文，返回技能基目录；支持 `plugin:skill`，展开 `${ZCODE_SKILL_DIR}` / `${CLAUDE_SKILL_DIR}`。正文不会自动执行脚本，也不自动附带所有参考文件。
3. 制作技能要求模型按任务继续读取 route/scene/reference，再用 Bash 等执行包内脚本或生成代码。
4. Viewer 技能通过 MCP 调用 open/read/search/show/get_view 等，与 UI 面板和用户引用协作；四格式共享 office-runtime。
5. 四插件均含 `agents/visual-judge.md`：要求读取预先渲染的 PNG，以逐页 pass/fail 和具体证据报告问题；描述将其定位为一次视觉验收，避免主模型重复检查。它是可调度评审配置，不代表安装插件就会自动执行，也不是生成引擎。

源码入口（相对上述 上游 根）：

- `core/src/context/sections/skills.ts`：目录默认预算 20,000 字符、单描述 250 字符；超预算退到名字/路径，不能误说是严格 token 总上限。
- `core/src/tool/handlers/skill.ts`：普通技能正文 100,000 bytes；dynamic-workflows 200,000 bytes，截断有标记。
- `adapters/src/skills/index.ts`、`roots.ts`：发现/加载、禁用过滤、命名空间、目录解析；默认合并 `.upstream/skills` 与 `.agents/skills`，插件可增加 resolved roots。
- `office-runtime/src/adapters/server.ts`：面板相关工具；`office-runtime/src/app/documents.ts`：文档读取及状态。

## 四种格式的实际差距

| 格式 | 上游 制作技能资源 | 当前 AILab 基础技能与缺口 |
| --- | --- | --- |
| Word | `documents-plugin/skills/docx`：create/edit/format/comment/read 五路线；学术、报告、合同、简历、试卷、公文、文案七场景；封面设计、目录、公式、图表参考；OOXML 编辑/批注辅助库；postcheck | `platform.docx` 主要用 JSON + python-docx 新建标题、段落、列表、表格和图片。未提供同等完整的原件修订/批注、分节目录页码和场景路线。模型可能自行写代码，但基础技能没有可靠指导和配套工具链。 |
| Excel | `spreadsheets-plugin/skills/xlsx`：新建、编辑、分析、格式转换、复杂/简单财务、VBA、批处理路线；图表/设计参考；`xlsx.py` 实现 recalc/audit/scan/inspect/pivot/chart-verify/validate | `platform.xlsx` 已有公式、格式、图表、品牌、JSON/脚本两种路径和 LO 重算检查，不能说完全缺质量检查。缺少上游同等深度的任务路线、公式模式审计、财务建模和数据透视指导。 |
| PPT | `presentations-plugin/skills/pptx`：设计规范、详细 pptxgenjs API 示例、模板制作、既有 PPT 保样式修改；模板文本容量预算、group 递归、run 级替换等具体编辑规则 | `platform.pptx` 主路径是 deck.json + 自写版式生成器，有主题、图标、可编辑图表、模板/品牌、几何检查和按需看图。可稳定生成常见页面，但指导会把任务导向有限版式；缺少任意复杂布局和原件精细编辑的完整路线。 |
| PDF | `pdf-plugin/skills/pdf`：report/creative/academic/process 路线，报告、海报、简历、学术、转换/填表等；ReportLab/HTML/LaTeX 按场景选型；分页、溢出、封面、图表规范及 QA 脚本 | `platform.pdf` 明示路径主要为合并、拆分、提取文本，check 只检查可读性、页数和 page box。PDF 原创制作指导与上游差距最明显；其他库或模型临时编程的能力不能当作已封装的技能合同。 |

实读了四个制作 SKILL、viewer 示例、manifest、visual-judge、Word edit 路线、Excel quality pipeline/CLI 命令实现入口、AILab 四个 SKILL/registry/prompts 和相关生成/检查脚本。未逐行审计所有 scene/reference 或执行上游脚本。

## 依赖关系补充

依赖分为“插件读取/面板”和“制作技能脚本”两条链。四插件 package.json 均通过 workspace 依赖 office-runtime，并调用其 scripts/build.mjs 生成各自 dist/server.mjs 和 UI；这不意味着制作脚本也全部通过 Office Runtime 完成。

| 层 | 主要依赖 | 负责什么 |
| --- | --- | --- |
| 技能编排 | 模型、Skill 加载器、Read/Bash 等工具、完整技能目录 | 选择任务路线、读取参考、运行代码；SKILL.md 本身不会执行 |
| 读取/面板核心 | Node ≥24、office-runtime；ExcelJS、PDF.js、JSZip、fast-xml-parser | 原生内容解析、结构定位、搜索、缓存；不是完整生成/编辑引擎 |
| 面板与协议 | MCP SDK/ext-apps、Zod；React、docx-preview、react-xlsx、pptx-renderer、PDF.js；上游 UI 宿主 | 工具协议、文件展示、用户引用与查看状态；只复用制作脚本时可不带整套 UI |
| Word 制作 | Node 的 docx（docx-js）、Python/包内 OOXML 辅助库、defusedxml；部分功能用 python-docx、matplotlib、Pandoc | 新建、保样式编辑、批注、图表、文本抽取 |
| Excel 制作 | Python、openpyxl、xlsxwriter、包内 xlsx.py；分析路线按需用 pandas 等 | 生成/修改表格、公式/图表、数据透视和审计 |
| PPT 制作 | Node 的 pptxgenjs；编辑用 python-pptx；部分流程用 sharp、Playwright、markitdown | 新建自由布局、编辑原件、处理素材及抽取内容 |
| PDF 制作 | ReportLab、pypdf、pikepdf、pdfplumber、PyMuPDF；HTML 路线为 Node/Playwright/Chromium/pdf-lib；学术路线为 Tectonic/LaTeX | 分别完成报告排版、文件处理、创意 HTML 排版和学术排版 |
| 转换/视觉验收 | LibreOffice、Poppler、中文字体；视觉模型/评审 Agent | Office→PDF、Excel 重算、PDF→PNG、检查最终版面 |

依赖不是每个任务全部必需。HTML/Chromium、LaTeX 和特定分析库按路线需要；LO 在 Office 转换/重算路径需要。Node ≥24 是 office-runtime 的要求，不能泛化为所有 Python 制作脚本要求。package.json 主要描述插件 runtime/UI 构建依赖，Python/系统制作依赖还须核对技能正文、env_setup 和脚本 imports，不能只看 npm 清单。

核对入口：上述五个 package.json、office-runtime/scripts/build.mjs、三格式 env_setup/env_check.sh、PDF html2pdf-next.js 的 require 以及各制作 SKILL。此次未安装依赖、未运行构建或制作脚本，不声称这些依赖已全部存在于 AILab 镜像。

## 为什么此前只接了一部分

既有[上游复用计划](../architecture/design/file-context-ocr/09-upstream-reuse.md)明确分开“统一读取核心”和“办公制作技能”。当时先接 runtime/viewer，并记录生成技能许可冲突，制作可另选明确许可来源。当前 `vendor/upstream-office-runtime/UPSTREAM.json`、`skills/NOTICE.md` 与四个 SKILL 的 adapted-from 声明吻合这个边界。

因此“已经用 上游”在读取层成立，不能推导制作层也已完整复用。当前 Agent 仍为 AILab Python AgentLoop，并非整套 上游 CLI/plugin host；复制 plugin manifest 不会自动获得其 MCP、UI 或 agent 调度。

当前生成与验收也有两个具体限制：

- Word `build_docx.py` 从 `Document()` 新建，固定字体和基础布局；基础制作路径不会自动获得保留原文复杂样式的编辑能力。
- Word `check.py` 对发现的问题输出 WARN 并返回 0；PDF `check.py` 不判断内容顺序和视觉布局。工具 exit 0 不能证明满足用户要求。PPT/Excel 已有更具体的检查，需逐项对比复用，不能全部推倒重写。

## 上游不能照搬的部分

- 当前本地四个制作技能的 frontmatter 写 Proprietary，同目录 LICENSE.txt 写 Apache-2.0，冲突仍存在；导入前要确认一致的可分发版本和文件来源，不能笼统按根许可证判断。
- Word/Excel/PPT 含缺 LO 就强制下载/安装、用户宿主目录和跨平台 setup；AILab 沙箱无网、依赖由平台镜像治理，应移到镜像/构建边界，不让模型临时装整套环境。
- 上游 file-citation、绝对路径、MCP 面板指令要映射为 AILab file_id/version、工作区路径、read/view/deliver；不能把宿主路径或未实现工具暴露给用户 Agent。
- Excel 说明存在“Python 验证时 TOTAL/SUM 可写固定值”的例外，与 AILab 活公式目标冲突；PDF 说明存在用中文替换日文字符的规则，不能作为可靠保真策略引入。上游内容更丰富不代表每条规则正确。
- XLSX pipeline 要求逐 sheet 完整 LO 重算/审计，多 sheet 会增加重复成本；视觉评审额外增加模型调用和渲染。上游 visual-judge 指定本机账号模型，AILab 不能直接沿用身份配置。
- PDF/PPT 的大入口本身仍可精简、拆模块；“渐进加载”不是所有格式都已经理想实现。复用应保留有用流程、代码、测试和来源，不以全文长度或文件数量判断质量。

## 建议的后续方案（未实施）

保留四个基础入口，把每个入口改成任务路由：读取 / 新建 / 编辑 / 排版 / 分析或转换，按任务追加加载对应完整模块；保留目录、脚本、资源及原测试，避免只搬 SKILL 主文。

优先补 Word 原件编辑和分页/目录、公文等场景；补 PDF 从零制作；再把 Excel 的深审计和专业分析、PPT 的自由布局与精细编辑接到现有路径。JSON 生成器保留为简单任务快捷路径，不要求所有复杂任务都挤进 JSON schema。

AILab 权限、文件版本/交付、OCR、模式隔离和前缀缓存继续由平台负责。Chat 只获得读取/分析相关模块，Work 才获得脚本和制作模块；新增模块作为工具结果追加，已读完整当前模块可复用，权限撤销/压缩按既有边界处理。

效果验收需要同模型、同素材、同任务对照现有与候选技能：Word 保样式修改/目录分页、Excel 活公式/数据透视、PPT 模板定点改页/PDF 输出、PDF 报告和表单。同时记录成品正确性、视觉问题、任务完成率、token/工具调用、总时长、峰值 RSS、镜像增量；静态源码研究不能证明效果或性能提升。

## 本轮验证和下一步

仅核对源码、版本、文档链接与本轮文档差异；未改产品/测试/配置，未运行测试、构建、浏览器、真模型或部署。本轮没有新增能力状态，故不更改 status-matrix。下一步由用户决定是否把上述建议展开成逐模块适配设计；不能从调研结论自动开始替换基础技能或接入完整插件框架。

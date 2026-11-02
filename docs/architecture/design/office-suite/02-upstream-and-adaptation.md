# 02 原文、能力和依赖迁移

[总览](README.md)。上游包根为 `$UPSTREAM`；下表给出完整源路径。能力按四办公插件逐项迁移，不用自写简版替换原始操作知识。

## 来源与完整能力清单

| 上游绝对路径 | 引入对象/能力 | 运行归属 |
| --- | --- | --- |
| `$UPSTREAM/documents-plugin/skills/docx/` | 原始 SKILL、routes 五路线、scenes 七场景、references 排版/封面/目录/数学/图表、scripts OOXML 编辑/批注/postcheck | Work 制作/编辑；Chat 可读取适用的审阅知识，不能运行脚本 |
| `$UPSTREAM/spreadsheets-plugin/skills/xlsx/` | 原始 SKILL、scenes 创建/编辑/分析/转换/财务/VBA/高级、engines、templates、quality、xlsx.py 全命令 | Work；Chat 原生表格读取、公式解释和分析指导 |
| `$UPSTREAM/presentations-plugin/skills/pptx/` | 原始设计与 API 说明、模板制作、既有 PPT 精细编辑、保样式/容量规则 | Work 制作/编辑；Chat 阅读和审阅 |
| `$UPSTREAM/pdf-plugin/skills/pdf/` | 原始 SKILL、briefs 全路线、configs、typesetting、references、scripts：制作/填表/拆合/转换/排版/QA | Work；Chat 读取/审阅，不获得脚本执行 |
| `$UPSTREAM/documents-plugin/skills/docx-viewer/SKILL.md` | Word 结构读取、搜索、引用、查看定位 | Chat＋Work |
| `$UPSTREAM/spreadsheets-plugin/skills/xlsx-viewer/SKILL.md` | 工作表/范围读取、搜索、选区关联 | Chat＋Work |
| `$UPSTREAM/presentations-plugin/skills/pptx-viewer/SKILL.md` | slide/shape 读取、查看及引用 | Chat＋Work |
| `$UPSTREAM/pdf-plugin/skills/pdf-viewer/SKILL.md` | 分页读取、搜索、查看及引用 | Chat＋Work |
| `$UPSTREAM/presentations-plugin/agents/visual-judge.md` | 单次视觉验收原始角色说明；其余三包有同名配置，去重同内容但保留来源 | Work 只读评审；Chat 可按显式审阅任务看图，不增加制作 |
| `$UPSTREAM/office-runtime/` | src 读取/search/anchor/session、ui viewer/shell、字体/worker/静态资源、必要构建脚本与测试 | 受限 reader＋Portal；不启用本机任意文件访问 |
| `$UPSTREAM/documents-plugin/.plugin/plugin.json` | 四插件装配语义参考，其他三包同名位置 | 转成平台能力描述；不另启动整个 上游 CLI/plugin host |

“完整”还要求：四格式打开/目录/缩放/搜索/选区/定位高亮/当前查看状态/截图；用户模板、字体、公式、图表、原件保留、批注修订、格式转换、视觉验收与失败报告。原生格式能力和外部应用执行能力分开：例如 VBA 生成/保留可迁移，Linux 沙箱并不因此能运行 Windows Excel COM；登记为外部环境前提，不能写成已支持执行。

实施 P0 生成**机器可读能力清单**（不是运行输出）：每个源文件、CLI 子命令、工具、route、UI 功能对应迁移目标、依赖、验收用例和状态。任何省略项必须列出原因及替代合同；不得仅把表格中的“主要流程”做完就标记全量完成。所有脚本 import、外部命令、动态引用和文件相对路径都须闭包核对。

## 原文保存与适配方式

候选源布局 `vendor/upstream-office/{upstream,patches,tests}/`，保留原目录层级、许可证、提交与逐文件 hash；这是实施候选路径，本轮不创建 vendor。构建产生适配资源，放入平台读取层与 `/opt/skills/platform.<format>/` 只读资源树，沿用四基础身份和 index 校验，不注册为可关闭的广场技能。现有 vendor reader 先 rebase AILab patch，再和新 UI 共用同一 parser/anchor 版本；不同时长期运行两套不一致解释器。

原文、适配差异与最终模型输入可逐段对照。只给模型最终适配的单份文本，不同时注入原文＋翻译＋补充长解释。保留上游英文原文，无需整包翻译；产品控件用中文。

| 必要适配 | 规则 |
| --- | --- |
| Skill/Read/Bash | 映射 `load_skill`/模块读取/`exec`/`exec_wait`；保留操作流程，修改工具名字、根目录变量和调用语法 |
| 原始文件路径 | 平台文件先按 file_id/版本授权；Work 用 copy_library_file 后获得沙箱路径。UI 用文档绑定身份，不能把宿主绝对路径交给模型 |
| viewer 工具与 file-citation | 映射平台读取、位置引用、reveal、截图和交付；不输出用户无法识别的 上游 citation 标签 |
| setup/install/network | 环境脚本留作来源审查，运行指令改为预装检查/明确缺依赖。依赖在镜像构建安装；素材/包下载走平台服务，OCR 走现有接口 |
| 默认输出/备份 | 原件保持只读，修改生成新版本；资料库已有版本与交付幂等是权威，不执行上游宿主旁边默认 backup 的存储约定 |
| 品牌/模板 | 用户明确规格→本轮模板→已启用品牌→上游默认设计；尽量复用原设计配方，不能把全部输出重新约束到本地 JSON 固定布局 |
| 自动评审 | 原视觉标准和逐页证据保留；换掉上游账号/模型字符串，执行权由平台收窄，统一计量与预算 |
| 明显保真冲突 | 禁止为验证方便把必需活公式写死、替换用户日文为中文、要求沙箱联网安装等。每条以显式 patch 和回归登记；不能悄悄改写整个技能 |

外部用户文档始终是不可信数据；上游技能即使要求 unzip/XML 也只能在受限沙箱、经大小/压缩比/输出预算准入后执行，不覆盖平台安全规则。

## 依赖闭包与运行位置

| 能力 | 主要依赖 | 放置与边界 |
| --- | --- | --- |
| 原生读取 | Node≥24、ExcelJS、PDF.js、JSZip、fast-xml-parser | 复用 Broker 系统 reader 和 Work office-read；API 不解析 |
| Word | docx-js、Python/OOXML 辅助库、defusedxml，部分流程用 python-docx、matplotlib、Pandoc | Office Work 镜像；检查 scripts/document.py 及模板齐全 |
| Excel | openpyxl、xlsxwriter、pandas 等、xlsx.py；LO 重算 | Office Work；宏运行能力按目标环境单独声明 |
| PPT | pptxgenjs、python-pptx、sharp，部分流程 markitdown/Playwright | Office Work；不因复制说明就宣称 npm 包可解析 |
| PDF 常规 | ReportLab、pypdf、pikepdf、pdfplumber、PyMuPDF 及具体脚本闭包 | Office Work；独立核对各许可证与二进制库 |
| HTML/创意排版 | Node、Playwright、Chromium、pdf-lib、字体 | 受限沙箱按需启动浏览器，固定镜像预装；无用户宿主浏览器依赖 |
| 学术排版 | Tectonic/LaTeX、所需宏包/字体缓存 | 离线可用构建，运行时不自动联网补包；缺包明确返回 |
| 转换和视觉 | LibreOffice、Poppler、字体、视觉模型 | 复用已有渲染队列/租约，避免与预览重复渲染 |
| 侧栏 | React、docx-preview、react-xlsx、pptx-renderer、PDF.js、workers/字体资源 | Portal 隔离 frame，按格式 lazy load，替换上游 host/RPC |

先记录各路线依赖、镜像增量和许可证，再分层构建。完整依赖可通过共享 Office 镜像的独立层复用；不要增加常驻 Chromium/Node/LaTeX 服务。若完整版不满足 2 vCPU/~8GiB 和现有沙箱限额，再设计明确的作业资源档位；不能无测量默认提高限额，或以 lite 名义无声删除重功能。

许可门槛仍未解决：制作 SKILL frontmatter 为 Proprietary，而目录 LICENSE.txt 为 Apache-2.0；UI/runtime 第三方组件和 Python PDF 库也有各自条款。P0 必须确认实际可分发版本和所需义务，必要时取得上游澄清。计划不因此缩小目标，但在来源未明确前不实施复制/分发。原始视觉角色提到系统级 delivery protocol，本次在已查路径未定位到完整独立正文；实施前必须找到可用源或明确列出 AILab 适配流程，不能宣称已搬运未取得的隐藏提示词。

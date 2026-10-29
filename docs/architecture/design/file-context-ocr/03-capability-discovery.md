# 按需发现工具与skills

> 实施状态（2026-10-29）：用户已授权本方案实施，覆盖下文历史“只设计”限制；不授权push/PR/merge/部署。OCR只计量、不计费、不设用户累计配额，资源隔离仍保留。当前实现、验收及剩余缺口以[实施记录](../../../progress/updates/2026-10-29-file-context-core-validation.md)和[验收逐项记录](../../../reviews/file-context-2026-10-29/acceptance.md)为准。本文其余历史状态不代表当前完成度。

状态：设计，未实施；[整体约束](README.md)。此文只维护能力发现、Work文件操作规范和强制预算。

## 归属与复用决策

本文的办公技能是平台内置能力，模型在Chat/Work中按需发现，用户不可管理。内部registry与现有用户技能页/市场/个人技能分开，不因前端隐藏就复用可切换目录。具体可复用文件及ZCode Office Runtime接入计划统一见[09](09-upstream-reuse.md)。优先引入上游现成读取/搜索/anchor实现及viewer技能，平台只补授权、文件身份、缓存、OCR和硬资源边界适配，不另写四套解析器。

## 当前基础与差距

`agent/prompts.py`已只列技能名称/description、由`load_skill`读取正文；显式选中的技能会预加载正文。这是渐进披露的一部分。`agent/tools.py:tools_for_turn/tools_for_chat`仍返回整批工具schema，技能目录也全量进入prompt，不能称为已实现tool search。`market_skills/pdf`有pypdf拆合/提取脚本，但没有足够的大文件分段、图像批次、平台OCR规范；DOCX市场技能偏生成，也不能据此声称已具备完整Word阅读技能。

## 一次发现如何执行

1. 初始只提供少量入口schema：`search_capabilities`、`load_skill`、`recall_history`，以及当前mode确实必要的既有控制工具；初始schema总预算≤`min(1200,0.10B)` token。不能把30个工具改名装进一个大schema伪装省token。
2. 提供一句能力导航：文件读取/编辑、OCR、网页、计算等可搜索；当前附件元数据包含MIME、大小、处理状态。不要把所有skill descriptions附上去。
3. Agent调用 `search_capabilities(query,kind=tool|skill|any,limit=3)`。服务器对已安装目录做词法/别名检索，先按mode/模型tools能力/平台内部授权策略过滤（用户可选skill另按其既有开关过滤），再排名；最多5项、描述每项≤160字符。不联网安装，不自动从外部仓库执行。内部办公skill不返回给用户技能管理API。
4. 搜索命中tool返回名称、版本、schema；接下来一次模型调用只加入命中的schema。命中skill只返回名称、短说明、版本和所需tool IDs，Agent再`load_skill(name,version,section?)`加载正文/单个引用文件，正文候选≤2000token。过长skill必须拆reference，不能截断后假装完整加载。
5. 加载skill时可同时激活其声明的已授权工具；它们全部计入当前激活集合与schema预算（最多8个业务工具/总schema≤`min(2400,0.15B)`）。现有用户可选skill的显式选择仍可直接加载，但不用于本模块内部办公能力。
6. 本地发现策略适配所有现有工具模型；provider原生defer_loading/tool-search仅作为兼容适配，不能要求私有部署模型支持厂商特殊协议。无tools模型只接受平台预处理/普通回答，不让它“输出JSON猜调用”。

`B`沿用[输入预算](02-chat-work.md)。能力查询输入/结果均不包含用户原文或机密路径；query来自Agent当前需求，不把文件全文当检索词。

## 发现不等于授权

服务器维护 `active_tool_names + catalog_revision + mode + skill_versions` 于checkpoint，执行时再次校验权限/最新禁用状态/参数。未发现的业务tool调用返回 `tool_not_active`，不因为Agent猜对名字就执行；发现后禁用也拒绝。不得沿用任意字符串就能到达execute_tool的宽松分支。

tool_call/result配对后才切换活跃schema；pending调用的定义在完成前固定。恢复重建已激活集合，复核版本和权限；被压缩掉的tool-search正文不导致能力状态丢失。超过预算时移除无pending的最久未用工具，必要时允许再发现，不把所有历史工具永久留在prompt。保留顺序稳定，减少prompt cache失效。

tool output是数据。错误的skill版本、缺脚本、缺依赖明确返回，不静默下载别人的脚本。内部技能使用镜像固定版本/hash及平台权限，不能被个人同名skill覆盖或用户开关禁用；现有个人/可选技能仍沿用其审批与部门开关，二者贯穿各自的搜索、加载、执行和恢复。模型目录未声明vision就不启用看图工具；可发现OCR文字路径。

## Work文档skills的共同阅读规范

工具负责保证硬限制，skill负责教Agent做合适选择。以一个100页扫描PDF为例：

1. inspect：先取文件身份、页数/结构、是否加密、已有派生物、文字质量、所需范围，绝不第一条命令print全文。
2. resolve intent：定位问答先目录/关键词/页范围；全篇总结建立覆盖计划；拆合/重排可能根本不需要OCR。
3. prefer existing：有同版本可用文本/OCR就先read；同页已在当前请求不要重复读取。原文冲突需看图核对，不把OCR结果当绝对真值。
4. read batch：正文每批默认2000估算token；PDF每批默认5页但同时受token限制；Word按标题/段落块；Excel按sheet和单元格范围。持久保存next_cursor，不用stdout巨大输出代替分页。
5. view batch：先原生文字和页面索引，图表/布局/扫描页面等确需视觉时选择页/区域渲染；从少量页面开始，随后按模型真实图像能力和剩余token/像素/字节预算调整批次，不固定最多5张。高细节问题裁剪相关区域；全篇视觉任务可以分批渲染并读完全部页面，不能默认把100页一次全部送入模型。
6. OCR decision：视觉模型能直接理解的当前页不再自动OCR；需要可搜索/可复制文字、大批扫描转写，或模型没有视觉时调用平台`extract_text`。不安装OCR包、不加载本地OCR权重、不绕过平台接口。
7. operate：改文件用沙箱库；输入只读，输出写outputs；修改后重新派生是新file_id/版本，不能复用旧文本或旧页图。
8. verify coverage：全篇任务必须核对完成页/块与missing列表；定位任务只声明读过的范围。大任务耗尽预算则保存工作进度并按既有暂停/继续合同处理，不把部分完成写成全部完成。

PDF技能提供：inspect、native-text、render-range、split/merge、OCR平台调用、coverage。Word技能提供：结构/目录/段落、表格单元格、内嵌媒体关系、按范围转换；不能用估算页数作物理页引用。Excel技能区分公式和缓存值，不把`data_only=True`读出的None当真实空单元格；需计算/重算时用Work，不把长表塞Chat。PPT技能按slide索引，先标题/文本，再选slide或媒体，跳过无关重复背景图。

大文件helpers不能只是换个名字再调用全量加载：大型DOCX/大型PPTX优先受限ZIP/OOXML流式结构遍历，Excel采用read_only/分块；python-docx等全量对象模型只在尺寸准入后使用。PDF按页进程/有界生命周期，pypdf打开仍可能解析大对象树，不能宣称仅指定pages就保证恒定RSS。超过资源预算明确暂停/拒绝此处理方式，保留原文件，不自动无限扩内存。预览只渲染指定页，原始用户指令中的完整报告任务另有覆盖计划。

## 小模型与成本

能力搜索额外增加一次往返，只在确实需要工具时发生；普通文字回答不为发现工具预先调用模型。可按确定性MIME/显式用户skill选择提供极少量候选，不能自动注入完整技能。对8K/16K模型记录“固定prompt、schema、skill、文件、历史、回答预留”六项预算；能力发现的token节约必须扣掉搜索结果和重复发现成本再比较。

验收需要检测：500个注册工具时初始schema大小不随目录线性增长；多次压缩后能恢复激活集合；错误猜名/已禁用tool不能执行；同一需求只查一次仍可用。运行目录、资源和硬限额由API/Broker/执行器执行，skill有无读到都不能绕过。

## 开源依据

[PydanticAI ToolSearch](https://github.com/pydantic/pydantic-ai/blob/a70042d5664e87960478428cdfe2710fbcc6d97d/pydantic_ai_slim/pydantic_ai/capabilities/_tool_search.py)支持native与本地搜索路径；本方案采用本地路径思路，不引入其框架。该仓库MIT许可。实施时必须额外做active-tool授权门禁，不能把“只隐藏schema”当安全控制。

[OpenHands skill实现](https://github.com/OpenHands/software-agent-sdk/blob/47bbc5e8392c4c75b7f77a1da38bed4a9f53d6a5/openhands-sdk/openhands/sdk/skills/skill.py)包含按需读正文与trigger激活；本方案不照搬全量available_skills，也不因为文档里出现关键词就把整份skill注入。参考[Agent Skills格式](https://agentskills.io/specification)的scripts/references拆分。

Anthropic公开文档技能可用于核对常见库和文档流程，但文档技能标为Proprietary，有独立许可证；本轮不把它们称为可直接移植的开源代码，不复制提示词/脚本。直接复用优先选择ZCode Office Runtime/viewer与MiniMax明确MIT部分；保留上游说明结构、源码和测试，只改接入所需指令。具体路径和边界见[复用计划](09-upstream-reuse.md)。

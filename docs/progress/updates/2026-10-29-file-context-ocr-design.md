# 2026-10-29 文件、OCR与多代上下文：模块化设计交付

## 范围与基线

用户授权设计与开源研究，未授权产品实施、迁移、付费调用、推送、PR或部署。基线是本会话已fetch的`main@e71a60f8b4e0ea139b12be80d14cb38a627a2db7`，当前`docs/file-context-ocr-design-20261029`。原主目录、本地交接分支与旧未跟踪证据保留；本轮未复查远端CI或个人部署。

用户后续明确Chat常见文档预处理、Work自主工具/skills，图片按vision分流/输入框每轮最多附加5张独立图片，后续轮次可继续，tool-search、缓存和删除；指定RapidOCR，硬件纠正为昇腾NPU优先。原CPU候选方向已由这条新要求取代。

## 交付与变更

[总览](../../architecture/design/file-context-ocr/README.md)链接9个独立模块：消息定位、模式/预算、能力发现、压缩、RapidOCR、生命周期、交付验收、源码证据。原单篇文档只保留入口，不另存冗余长副本。每种信息只在对应模块维护，详细设计不复制到current/HANDOFF。

主要补齐：稳定消息身份和附件顺序/分支歧义；Chat准备包与超限拒绝；Work大文件分批/选页/图像预算；动态工具schema和skill正文；RapidOCR CANN/required backend及现有OCR NPU卡位差异；同ID复用/重复上传私有缓存复制；回收站与彻底删除、DB/派生对象/摘要/checkpoint/sandbox快照谱系。

[源码证据清单](../../reviews/2026-10-29-file-context-source-audit.json)包含20个本地文件与26份固定提交外部源码记录。新增核对RapidOCR `80b427d`、PydanticAI `a70042d`、OpenHands `47bbc5e`、MiniMax skills `60aaae5`，并核对各自许可证；Anthropic公开文档技能独立许可证为Proprietary，不作为可直接移植的开源脚本。未将第三方源码、模型或临时下载路径写入项目。

现状核对：有滚动摘要/图片checkpoint、skills正文按需、同file派生读取与持久对象删除重试，但工具schema仍整批注入；普通删除为回收站；重新上传新建FileObject/job；API已有capabilities而前端未接；无新设计的完整消息定位/专用OCR/清理合同。源码“有能力”与真实运行成功分开。

## 当前检查与边界

当前整体检查通过：8模块与总览/旧路径入口、186个本地链接、3个JSON示例及围栏配对、20份本地源码指纹、26份外部固定提交记录、4个预算算例、29个不重复验收场景编号、跨模块Chat/Work/NPU规则和`git diff --check`。这些是文档/静态证据检查，29场景本身尚未执行；不沿用上一版159链接/9源码的计数。

产品代码、Portal、依赖和迁移未改；未启动服务、未运行Python/Portal/浏览器套件、未调用付费模型、未执行RapidOCR/NPU、未实测识别质量、token节约或资源容量。上游RapidOCR缺CANN会回CPU是源码事实；设计required-backend尚未实现，不能称已经防止降级。

冻结架构已有OCR NPU预留和Agent调用OCR方向，但原模型不同；本轮只写替换关系和后续ADR要求，不改写冻结副本或重分卡。8K/16K/32K预算表只是公式算例，大小/页数/准备deadline/NPU并发指标都是候选。

## 下一步

用户逐模块讨论并定版候选门槛、OCR积分和目标NPU配置，再明确实施范围。依赖和验收看[交付模块](../../architecture/design/file-context-ocr/07-delivery.md)，不自动开工或发布。

## 后续澄清：输入框限制与PDF处理范围

用户明确纠正：5张只限制输入框每轮附加的独立图片，不限制模型单次输入、历史图片、文档页或裁剪。已移除先前误加的请求≤5图规则。PDF/PPT不默认“整份渲染并一次性回灌模型”；按任务需要允许逐批完整审阅，也允许逐页转换全部页面并缓存。新增I01验收，用实际允许超过5图的模型合同验证不误拒；这是未执行的设计验收项。

本次澄清后检查：模块内相对链接、JSON示例、围栏及29个唯一验收编号通过；旧模型请求5图硬上限表述已清除，`git diff --check`通过。未改产品源码、未运行产品套件。

## 后续研究：优先复用Office Runtime与内部skills，只改plan

用户要求立即研究ZCode/Claude Code等可挪用来源并补具体路径；明确技能是平台内置、模型按需加载、用户不可管理。用户随后再次纠正只写/改plan，不实施。曾误建的`feat/zcode-office-runtime`是空分支，已切回设计分支并撤销；没有产品源码修改、安装依赖、构建或测试业务栈。

新增[09复用plan](../../architecture/design/file-context-ocr/09-upstream-reuse.md)：ZCode Office Runtime（MIT）定位/读取/搜索核心和viewer技能为优先，MiniMax MIT办公编辑规范为补充，逐文件说明路径/依赖/许可。ZCode部分生成技能frontmatter与Apache LICENSE不一致，按实际范围记录；Claude Code官方tree不是完整办公运行时来源且商业许可，不从泄露源挪用。明确Node24/全量解析/缓存的性能边界，未来先同范围测量，再做薄适配，未预设更快。

[新增源码证据](../../reviews/2026-10-29-builtin-skills-source-audit.json)固定ZCode `3c5b227`、Claude Code `58d6da1`、MiniMax `60aaae5`，共40份已读文件及未截断tree元数据。没有把上游源码复制到产品仓库。当前实施状态仍为未实施，下一步由用户讨论定版plan。

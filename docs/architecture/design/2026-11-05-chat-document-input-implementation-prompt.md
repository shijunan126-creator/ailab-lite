# 文件输入方案实施任务（供用户复制给实施AI）

以下正文是待用户发送的任务提示词；保存此文件不等于本线程开始实施，也不代表授权push、PR、合并或部署。

---

请在AILab lite中实施“安全入库、Work按需操作、Chat按需准备及浏览器减负”方案。此任务授权本地产品实现、必要迁移、回归与真实验收；不要只再出一份plan。不要push、创建PR、合并、部署、操作现有测试栈或清理用户业务数据；必要测试使用隔离的临时栈/数据库并清理自己的资源。常规技术选择按下面方案推进，不逐项重复请求确认。遇到真正无法满足的约束，给出具体证据，不擅自扩大范围。

先读取本仓库AGENTS.md、docs/progress/current.md、HANDOFF.MD及/Users/michael/.codex/RTK.md；涉及Portal还要读取platform/portal/AGENTS.md。核对实际目录、branch、HEAD、status、remote和最新main，在隔离实施分支开展工作，保留所有其他工作区和未提交修改。

本任务的完整设计资料目前位于：
/Users/michael/Developer/ailab-lite/.worktrees/ocr-lifecycle-pr/

其中部分文档尚未提交，普通git checkout不一定能拿到。先从上述来源读取并把本任务设计、研究、缺口报告原样带入实施分支，再按最终实现维护；不要顺带复制该旧工作区的其他dirty产品修改。若路径不可用，搜索确切文件名；无法取得设计原文时明确缺失，不凭聊天摘要编造实现规格。设计SHA只是调查基线，不以旧fix分支代替最新main。

必读（相对上述资料目录；实施分支中保持对应相对路径）：
1. docs/architecture/design/2026-11-05-chat-document-input.md：唯一当前实施规格，完整读取。第8.1节是最后复核澄清；第5节为建议配置初值，实施采用这些默认值并验证范围，不反复问用户；OCR沿现有90秒默认/120秒上限，不能沿用早先180秒说法。
2. docs/reviews/2026-11-05-document-reading-workflow-research.md：已核对源码、上游复用、系统池职责、浏览器依赖及尚未实现边界。
3. docs/reviews/2026-11-04-chat-visual-coverage-followup.md：已复现的部分缓存完整性缺口及最小回归方向。
4. docs/progress/README.md、docs/progress/roadmap.md、docs/architecture/status-matrix.md：读取相关范围，保留其他暂停项目，不能自动推进别的路线图。
5. docs/architecture/adr/0026-document-visual-coverage-and-file-waits.md、0030-prefix-stable-model-requests.md、0033-context-epoch-band-process-snapshot.md：旧视觉/等待合同、前缀及压缩恢复约束。历史规则与本次设计冲突处按本次设计更新ADR；新编号检查现有序列，不预占编号。

实施核心：
- 普通上传只安全入库；安全可用与原生文字/页图/OCR的派生状态拆开。取消普通输入附件自动全文解析、索引、转PDF、页图、OCR和Office预热；专用导入与用户成品的必要合同不能误删。
- Work主任务和并行子任务共享当前工作沙箱，Office读取/编辑/转换/渲染走自己的runtime/skills。排除Chat平台文档接口的发现和执行入口，不是按同名字符串禁止ZCode能力。保留查找/复制资料库原件、交付和工作区查看。工作区OCR通过lease绑定、受限图片/区域调用现有OCR服务，不外泄密钥、不安装重复权重，不另租系统render沙箱。
- Chat新run用read_document、search_document、view_document。read默认native，OCR用text_source=ocr明确选择页/区域；search只搜可用文字并报告未覆盖，view不隐式OCR。混排的文字/视觉/OCR覆盖独立，不因页眉/隐藏文字层跳过图像内容。原生读搜、原图不必转PDF；Office整页排版可复用现有PDF中间表示。
- 先提供有界真实导航，无目录就用稳定locator，未知标unknown。小文件按24K token与输入预算20%取小、最多8图装配，超出按需取，每次最多4图。取消文件物理总页数准入，保留字节/结构/时间/内存/累计工作量预算。现有整包parser不得假称已按范围加载。
- 优先复用浏览器原生viewer和现有capture/client-call；首开不等待不必要全文索引。按需回传正确版本的页/区域截图；原子claim/generation控制后备，防重复发布与无界双算。关闭/离线/超时有后备；Work后备使用自己的沙箱。保留精确引用核验，客户端结果不是安全扫描或权限证明。
- 固化“导航→选范围与表示→读/搜/看/OCR→核对覆盖”的必要方法，放按模式skill/工具说明；不要往system堆TTL、队列或引擎等后台策略，不新增已读页数UI。技能从vendor/office-suite/build.py修改并重新生成，审查viewer/引用模块/主子任务允许列表，不手改生成SKILL，不删减上游必要操作内容。
- 旧run固定旧工具目录、语义和prefix，新目录只给新run。恢复/压缩/重建/文件等待/撤权/删除/换版本全链路校验，禁止为省事重写已发消息。
- 服务端和浏览器性能、去冗余、容量准入、完整资源清理必须与新功能同批实现。遵守设计第6.1、7.1、7.2节：临时文件正常结束立即清，崩溃有持久补偿；Canvas/bitmap/Blob/objectURL/Worker/流/监听器/计时器/请求Map均有释放；消息/checkpoint必要截图只保留一份规范资产，最后引用释放再删。缓存TTL/代际回收、原子预留、预算回填、删除墓碑与并发迟到写入一起处理。绝不能靠提高内存、无限重试、定期重启或跳过测试掩盖泄漏。

执行方法与验收：
先按设计第8节形成代码落点、状态/工具schema、资源生命周期和分阶段验收清单；核对现有实现后直接推进，无需另等一次计划批准。先冻结样本、设备、冷/热基线及性能门槛。不要扩大为全量迁移上游插件、浏览器OCR/全文索引、图片语义搜索或自建排版引擎；支持不全的内嵌图提取用明确整页/区域后备，不偷偷丢内容。

围绕根因成组实现，集中review和最终检查，不每修一个问题就重复整套冒烟。数据库迁移、索引、outbox/claim/取消/容量预留须适配SQLite和PostgreSQL，必要时做EXPLAIN；服务边界和现有授权不能绕过。

完成仓库要求的Ruff、完整Python套件、相关双库/Redis与E2E合同、Portal测试/build及真实浏览器检查；请求构造变化必须有录制前缀测试与真实模型检查。至少验证四格式/扫描混排/无目录、Chat三工具与Work模式隔离、旧run恢复、浏览器截图与后备/多tab/版本变化、两用户争用、取消/崩溃/磁盘或存储故障、删除/TTL/引用恢复、50轮资源稳定性。检查实际日志/对象/行/资源计数，不用mock-only通过冒充真实验收。真实模型缓存目标≥95%，冷启动与大批新输入单独报告。移动设备、模型或某后端不可用时如实列未测边界，不声称整体完成或凭空报数。

最终维护current.md、必要dated update、相关status-matrix/ADR和HANDOFF陷阱，每类事实只留一处。原始日志/heap/trace放仓库外临时目录或CI artifact；清理自己产生的临时资源，保护用户原件、成品、既有业务数据和其他修改。

交付时给出：实现清单与代码基线；各关键验收/性能和清理结果；旧run/迁移/剩余风险；未测或阻塞项；尚未push/PR/合并/部署的明确状态。工作必须完成到可审阅，不能用“还可以继续”代替必要实施。

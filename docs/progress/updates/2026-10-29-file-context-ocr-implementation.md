# 2026-10-29 文件上下文与平台办公能力实施

> 后续已迁入AILab lite内规范工作树，并完成实际DeepSeek/RapidOCR及三项缺口，见[核心补完记录](2026-10-29-file-context-core-validation.md)。下文为前轮历史基线，不作为最新未完成列表。

本地接入与冻结基线集中验证完成；完整设计不能仅凭合同测试标记全部完成。分支`docs/file-context-ocr-design-20261029`，HEAD/base`e71a60f8b4e0ea139b12be80d14cb38a627a2db7`加未提交实现；原设计、源审计及旧记忆证据保留。用户授权实施，不授权push/PR/merge/部署。本轮没有提交或远端写操作，迁移只在隔离测试库执行。

产品/测试/镜像源文件的固定基线见[tested-source.json](../../reviews/file-context-2026-10-29/tested-source.json)，811个文件的摘要为`4d987f559f75fdccf5ee0437e19b8cd40d9b1723a6026a42071cfe45c6147eaa`。这是含dirty实现的源清单，不冒充Git提交SHA或构建发布签名。

## 实现范围

- 固定ZCode `3c5b227649371507ddc4eb46ac5133afe6db1cab`，保留MIT读取核心、原始viewer/Apache声明、源hash、测试与patch清单；最小解析依赖与独立reader镜像，没有桌面/完整UI/OCR权重。修正上游超大单元分页截断和宽表列遗漏。
- Office Runtime属于平台，执行在Broker短生命周期系统沙箱，和用户Work租约分离。Worker只编排、校验归档和提交派生物；API保持业务DB唯一writer。文档/作业绑定、尝试claim、独立命名空间、提交前复验和归档finally清理，不回落旧解析器掩盖失败。多用户扩展约束详见[ADR0023](../../architecture/adr/0023-system-document-reader-and-concurrent-capabilities.md)。
- 迁移0035/0036：消息附件ordinal/上传来源/版本，已应用消息位置与queue alias，原生派生物/块/页/locator，逐页OCR缓存、summary frame/source、删除epoch与持久清理游标。历史回填分批，不从上传完成时间猜提交顺序。
- Chat先共享预处理/非vision图片OCR、再完整性与预算准入；partial/超限明确拒绝，不截前缀假装全文。Work默认只注入引用/必要索引，按需工具读取、选页看图、复制原文件和编辑交付。图片使用catalog capabilities；输入框每轮独立图片5张限制没有扩展到历史图、文档页或provider请求。
- 私有`platform.*`办公skills与用户skill管理分离，模型按需搜索/加载，用户同名skill不能覆盖；初始小schema集合、最多8个激活工具、动态schema预算、固定版本checkpoint恢复。失败/未发现/禁用能力不越权执行。
- 跨轮摘要使用旧摘要＋尚未覆盖prefix、epoch/CAS和不可变来源frame；本轮执行摘要连续分块并逐段checkpoint消费游标，保留工具配对/原始用户约束/已确认副作用。跨轮每第8代或conflict条目引入有权限原文片段的有界重校准；真实模型语义保真仍未验。
- RapidOCR专用执行器接口：固定源码、预置模型hash/来源/许可、required backend、三个实际session/provider、禁用静默CPU EP回退、节点profiling。CPU必须显式配置。只计量页/引擎/后端/耗时，不积分计费、不设用户累计配额。推理不在API解析不可信文件，不在用户沙箱装OCR权重；远端调用期间不持DB事务/用户锁。
- 同ID跨模式复用；重复上传仅同owner原生派生物复制新私有命名空间，复制在Worker及事务外、提交再验来源。回收站与彻底删除分开；在线来源栅栏覆盖历史原文、摘要、模型bootstrap、事件/预览、dream/向量回填、晚到Agent写入和checkpoint恢复；后台分批清理索引、派生对象、summary、工作区、思考/工具事件、记忆快照与交付结果正文，保留确认身份和独立交付文件。
- Portal模型视觉图标、附件准备的过期响应取消、两处文件菜单共用提取入口、PDF页范围、处理/失败/下载。用户技能页和市场保持独立。

## 验证与证据

逐项覆盖及缺口见[07矩阵验收记录](../../reviews/file-context-2026-10-29/acceptance.md)。最终冻结基线SQLite1450通过/103跳过、0失败；[日志](../../reviews/file-context-2026-10-29/sqlite.txt)。PostgreSQL1498通过/55跳过、0失败；[日志](../../reviews/file-context-2026-10-29/postgres.txt)。五服务真实浏览器120通过、0失败/跳过；[日志](../../reviews/file-context-2026-10-29/browser.txt)。Ruff419文件、服务导入、技能清单/hash、结构golden通过；依赖锁检查通过且本轮Python依赖未变。上游15项通过，production npm audit当前0条；[真实reader](../../reviews/file-context-2026-10-29/real-reader-tests.txt)四格式/两并发合同通过，核对断网、cap_drop、no-new-privileges与1CPU/320MiB限制，租约释放后无残留容器。

最终浏览器包含最新删除栅栏，Portal42项与生产构建通过（[测试](../../reviews/file-context-2026-10-29/portal-tests.txt)、[构建](../../reviews/file-context-2026-10-29/portal-build.txt)）。[桌面](../../reviews/file-context-2026-10-29/file-extraction-1280.png)/[390px](../../reviews/file-context-2026-10-29/file-extraction-390.png)截图已逐一检查，无横向溢出。浏览器使用真实Portal/API/Worker/Agent/Broker进程和Playwright；模型、沙箱和OCR服务未替代真实硬件质量验收。

## 性能与成本

[同范围冷/热/缓存对照](../../reviews/file-context-2026-10-29/performance.md)及[原始JSON](../../reviews/2026-10-29-office-performance.json)。当前小样本Office冷启动约189–371ms，旧Python约121–207ms；Mac冷RSS约82–160MiB，旧约32–35MiB。没有更快的结论。新读取器输出更多结构、公式/备注，词项对照只是排查明显遗漏，不是正确率/布局保真。

真实Linux系统容器小fixture全路径约1.67–2.23s/份，cgroup内存峰值约38–76MiB，CPU累计约0.41–0.89秒；有Barrier与采样成本，不是纯解析。专用镜像逻辑Size约114.9MiB，业务镜像不增加Node。持久缓存样本是本地对象文件读取，不能称API/S3延迟；没有拿旧前20页和新全文作速度比较。

## 失败历史与修复

早期工具目录断言、Malformed预览fixture的显式错误合同、队列位置autoflush、尝试版本化manifest进程加载不一致均已集中修复。测试模型在发现工具后重复等待导致浏览器假超时，已修脚本。OCR表在前批运行时新增导致迁移drift，已作最终冻结复验，不作为最终失败保留。一次命令PATH误用Mac旧Python使部署脚本3项缺tomllib，固定3.12后联合回归通过。最后SQLite清理用例只等5批，新增120页对象队列尚未轮到目标；改为检查真实持久意图并在有界多批内完成，不跳过清理或放宽产品预算。原失败日志留在本轮/private/tmp路径，最终只归档无敏感内容的结果。

## 未完成与风险

- 真实Ascend型号/驱动/CANN/ORT wheel/模型组合、节点执行profile、OCR CER/WER/关键数字和表格准确率、首次图编译/显存不足、30分钟稳定负载均未验。当前没有NPU或预置OCR模型；源码/HTTP合同不能冒充NPU成功。
- 真实模型8K/16K/32K输入、超过5图能力、500轮10代摘要保真、真实中文复杂办公样本和全文答案质量未验。Chat候选200k字符/200扫描页、20页作业批次及执行器槽位只是开发保护参数，不是验证过的产品容量。
- 跨轮重校准有界片段会随输入预算缩减；尚无跨frame持久的未校准来源队列，本轮执行摘要也不做完整原文重建。删除采用保守整frame失效，不是保留所有无关条目的精细重建。这些是设计实现边界，不伪称质量已验。
- 不同file_id重复上传的OCR页尚未复制复用；同file_id跨模式可复用。复杂PDF旧错层质量分类、公式无缓存值/嵌图语义需后续代表样本验证。
- 面向数百人架构依赖PostgreSQL、持久队列/claim、共享对象存储与有界执行器；未做数百人容量/Chat排队TTFT/目标2vCPU8GiB整栈压测。Broker组内有限并发已验，完整跨节点Broker调度/路由未在本轮实现或验收。
- 旧备份恢复须在开放服务前应用删除墓碑；尚无跨备份自动应用器，不承诺删除已经送达浏览器/外部模型的数据。真实S3/OSB/ClamAV组合和生产保留政策未验。

本轮专属Colima虚拟机/磁盘/镜像、测试PG/Redis及五服务均已停止或删除，最终端口检查见[环境清理](../../reviews/file-context-2026-10-29/cleanup.txt)。最终源清单逐文件复核无变化。个人部署、现有用户数据和主目录工作树未操作。下一步应先完成上述设计缺口及目标环境/真实模型验收，再另行决定发布；无本轮远端发布授权。

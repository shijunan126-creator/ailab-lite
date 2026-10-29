# 开发路线图

2026-10-29 已授权并完成本机体验部署：固定dirty快照、保留数据0033→0036、实际四格式/OCR/DeepSeek/浏览器通过；不等同远端合并或目标NPU/数百人验收。[当前部署边界](updates/2026-10-29-file-context-local-deployment.md)。


2026-10-29 核心后续：已完成本机可做的校准队列、OCR副本复用和备份墓碑应用，使用现有DeepSeek与真实RapidOCR验核心链路；当前剩余目标NPU/质量/容量等验收以[最新记录](updates/2026-10-29-file-context-core-validation.md)为准。当前工作树在项目`.worktrees/file-context-ocr`，不再沿用Memory临时目录命名。


## 2026-10-29 文件上下文与OCR（本地核心接入已验，完整方案仍有缺口）

实施顺序按[07模块](../architecture/design/file-context-ocr/07-delivery.md)的文件关系→共享派生物→上下文→OCR→联合验收推进。用户最新授权覆盖设计阶段限制，不授权push/PR/merge/部署。平台内部办公能力在Broker系统沙箱执行，与用户技能/Work沙箱独立；架构以数百人扩展为目标，本机不做容量承诺。OCR只计量、不计费、不设累计用户配额。代码与验证、外部条件及尚未实现的设计项统一见[实施记录](updates/2026-10-29-file-context-ocr-implementation.md)；当前下一步以[current](current.md)为准。

## 2026-10-28 记忆系统完善

用户授权对前轮记忆审查的全部缺陷及架构/体验缺口自主实施，完成验证后提PR并修复直到merge，不包含部署。保持五服务/双库和已取消临时对话的决定；0034持久工作进度、统一可见性/作用域、来源/删除/时间合同和每调用计费已经实现，本地组测完成，精确状态与下一步见[current](current.md)，设计见[ADR0022](../architecture/adr/0022-memory-consistency-and-scoped-recall.md)，验收见[报告](../reviews/2026-10-28-memory-system-hardening.md)。不据旧路线自动发布其他功能。

## 当前主线（2026-10-25）

用户明确目标：单机上的体验、调度、性能与可靠性优先，同时保持未来扩机的业务契约和适配边界。已授权全量审查、直接修复、完整验证、提PR并合并；不包含部署。以已确认问题和实测成本决定改动，不按目标图补组件。具体范围、当前基线和下一步仅见[current.md](current.md)，本轮验收见[交付记录](updates/2026-10-25-single-node-review.md)；下文旧分支授权均为历史。

更新：2026-10-23。目标是在 2 vCPU / 约 8 GiB 单机上可靠完成 Chat、Work、文件处理与多轮产物编辑；未来扩机只替换 adapter/部署，不重写授权、账本、文件交付和 Portal 协议。

当前执行范围见 [current.md](current.md)。**用户已于 2026-10-23 恢复剩余工作实施，优先 F8，并行推进独立项**；实现细节与估算见 [剩余工作设计](../architecture/design/2026-10-23-remaining-work.md)，不重复维护状态。各 T 项当前完成度只在 [状态矩阵](../architecture/status-matrix.md) 更新。

## 阶段与验收目标

| 阶段 | 任务 / 解决的问题 | 依赖与进入条件 | 退出标准 |
|---|---|---|---|
| 当前交付 | T21 持续文档；已完成能力集成 main 并提 PR | 已有功能与真实组件成果；保留未完事项 | 集成冲突语义解决；全量 Python、Portal build、部署静态检查和浏览器回归通过；PR 可审阅且风险明确 |
| 1 | T09 事务 outbox 全面化、T08 Agent 有界并发与 Chat 保留槽 | 用户恢复开发；先约定队列信封与 attempt/ACK 契约 | 提交后杀进程、Redis 清空、重复投递均收敛；同会话串行；Work 排队不阻塞 Chat；双库测试与目标机成本记录 |
| 1 并行 | F8 Broker 注入/交付/快照流式化 | 用户恢复开发；API/客户端/provider 统一有界流接口 | 文件大小增长不导致 RSS 线性增长；并发大文件在 384 MiB 限额内；取消/失败清理临时文件、关闭流；记录吞吐、磁盘和 RSS 曲线 |
| 2 | T07 checkpoint、ask_user、续跑、Agent 租约心跳 | T09 信封与 attempt；既有 T01/T05/T06 | 每步杀 Agent 后安全续跑，不重复确认交付/不可重放工具；旧 attempt 写入被拒；等待用户释放槽与沙箱；完整浏览器 E2E |
| 3 | T14 输出前模型降级与上下文预算；T15 会话分页 | 恢复/计费契约稳定；两项可独立实施 | 首字超时/429/坏上游有界降级，已输出不重播，账本记实际后端；200 轮不超上下文预算；500 轮详情首屏双库 p95 <150 ms，并测浏览器翻页 |
| 4 | T16 可观测性、告警和混合负载 | 上述运行链路稳定 | TTFT/排队/outbox/未对账可观测，无用户级标签；2 vCPU / 8 GiB 上记录混合负载 p95、各 Pod RSS/CPU/磁盘与限额，不以单轮通过声称并发容量 |
| 5 | T19 OCR/多模态；T20 Skills 治理与评测 | T14 能力声明；公司模板、样本、许可输入 | 扫描件/照片可读且计费归属正确，模型不可用有明确状态；技能 manifest/哈希与镜像一致，策略可测试，真实产物 golden 验收 |

当前本地结果：F8、T07/T08/T09/T14/T15/T19已集成并完成统一基线验收；T16已有五服务mock混合基线，T20治理与真实Docker证明完成。真实联合容量、付费vision与业务golden仍按边界保留，详见[最终报告](../reviews/2026-10-23-t07-runtime.md)。下表阶段为验收目标，不表示任务均未开始，也不授权自动发布。

## 集成与发布待办

以下与“功能已实现”分开跟踪；本轮只记录，不擅自扩展开发范围。

| 问题 / 影响 | 下一步与完成条件 |
|---|---|
| CI 与测试完整性（2026-10-24 已授权） | 固定源码 MinIO/独立双库保留；新增范围/门禁回归、契约防 skip、生产依赖导入、E2E 诊断与每周真实组件；本地验证与远端待验边界见 [本轮记录](updates/2026-10-24-ci-test-completeness.md) |
| main 新增 SearXNG 未覆盖此前目标机实测 | 固定镜像与搜索服务密钥配置；真实搜索、不可用路径和 Agent-only 网络访问验收；重测全栈 CPU/RSS/延迟。当前 request 100m/128 MiB、limit 500m/512 MiB 是预算，非实测 |
| lite hostNetwork 的公网边界未验 | 在目标宿主验证 8081/8090 仅节点/集群可达，公共入口不能访问 `/internal`；保留实际防火墙/探测证据 |
| 真实组件组合仍有边界 | 验证真实 OSB Office + S3 联合部署、签名重载、长期并发、生产规模备份 RPO/RTO；企业 IdP/付费模型按可用凭据与授权单独验收 |
| 设计中的迁移号 0014–0017 仅为规划 | 实施前检查当前 Alembic head，按真实顺序分配并在双库升/降/再升；不能照抄旧设计号 |

## 范围边界与决策

沿用 [ADR 0001](../architecture/adr/0001-single-node-scope.md)：不做多机调度、跨节点租约、闲置沙箱复用、部门额度池和分布式事件总线。没有承诺日期或预先指派新 agent。目标/依赖变化时更新本页，具体决策写 ADR，实施与验收结果写 dated update。

## 2026-10-24 新增授权：用户记忆与技能编辑

按用户账号持久记忆、可编辑前端与 Chat/Work 工具；参考 Hermes 有界记忆/技能维护逻辑。验收以隔离双库、权限/重投/CAS、真实 Portal/API/Agent/Worker 浏览器链路和成本为准，见[审查](../reviews/2026-10-24-memory-skills.md)。前端最多两个 Claude Opus 5.5 Medium 并发。仅本地实现，不沿用旧任务的发布授权。

## 2026-10-24 当前授权：Chat fork / retry 后端

本工作树只负责后端契约、0022迁移、并发/权限/存储与双库验收，Portal由父Agent处理。本轮不沿用旧发布授权；本地提交后交父Agent集成，不推送、不建PR、不部署。具体契约、边界和成本见[后端记录](updates/2026-10-24-chat-fork-retry-backend.md)。

## 2026-10-24 后端子任务：定时任务与页码引用

范围为每用户五个任务、事务调度、Worker缓存页边界、项目索引与引用prompt；本地提交后交父Agent合入，不启动其他路线项或发布。验收、迁移0024接线和前端归属见[交付记录](updates/2026-10-24-schedules-citations.md)。


## 2026-10-24 PPT 模板后端子任务

用户授权公司/个人/单轮PPT模板后端，不包含Portal/发布。`feat/ppt-templates-backend` 已实现并本地双库/相关五服务验证，未合并/部署；父任务下一步集成Portal、确认联系图视觉边界，再另行授权0028迁移与sbx-office重建。精确验收、成本、未验边界见[交付记录](updates/2026-10-24-ppt-templates-backend.md)，接口见[设计](../reviews/2026-10-24-ppt-templates-design.md)。


PPT缩略图最新授权改为sandbox预览流水线，撤回平台镜像/Worker Office渲染；ready同事务登记一次preview outbox，首屏派生480px JPEG切换URL，失败保留草图与模板可用性。父任务下一步集成并在真实sandbox环境验收；不需要为此给平台镜像安装Office。当前双库结果和边界见[本轮记录](updates/2026-10-24-ppt-template-thumbnail-rendering.md)。


## 2026-10-24 模板缩略图UI修复与合并授权

用户授权主Agent修复公司/个人模板真实缩略图自动更新，完成验证后提PR并合并至main；包括此前已验收但未合main的模板集成。独立preview_status、有界可取消轮询及两条UI路径的成功/失败回归已本地通过；未授权再次部署。结果与边界见[本轮记录](updates/2026-10-24-template-preview-refresh.md)。

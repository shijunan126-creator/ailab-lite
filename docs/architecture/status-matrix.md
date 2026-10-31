# 单机能力状态

2026-10-31 技能/办公/记忆/QuickJS 本地实施（`feat/skills-memory`，未合并）：四个不可关闭的基础办公技能、句中多技能且不强制Work、后台技能草稿与目录树编辑、回到底部、浏览器QuickJS `run_js`、增量记忆整理与解释报告、稳定提示前缀；迁移0040–0043。真实模型质量、供应商缓存命中与设备开销未验，见[记录](../progress/updates/2026-10-31-skills-memory.md)。

2026-10-30 队列发送补齐0038持久ID与唯一索引，派发/插话/取消后同请求重放201，跨请求/会话/直接入口复用409；旧JSON不回填，未改响应schema。[验证与边界](../progress/updates/2026-10-30-queue-send-idempotency.md)。

2026-10-29 首次预览后台预热与发送幂等已本地实施：文件 ready/已扫描产物交付在原事务预排不超过20MiB的办公/PDF预览，完整OCR页可复用，`client_send_id`用户级唯一索引防重复Run/派发；保持文件库旧预览合同。成本、冷/缓存边界和验证见[记录](../progress/updates/2026-10-29-preview-send-idempotency.md)，未部署。

2026-10-29 办公内容/OCR与等待修复已在独立分支实施，覆盖PDF/DOCX/PPTX/XLSX的文字与视觉事实、缓存/任务复用、持久等待、自动恢复及Chat/Work分流；本地最终验证见[本轮记录](../progress/updates/2026-10-29-document-reading-reliability.md)。未据此声明已部署、视觉模型质量/NPU或数百人验收。

2026-10-29 fork交叉链路已修复并更新本机API/Portal：消息/附件消费位置、工具读取删除依赖、摘要来源和校准游标随分支迁移，重新打开恢复正确模式，用户切换保持独立。267核心及7真实浏览器通过；旧分支已丢失关系需重fork，完整容量/NPU边界不变。[审查记录](../progress/updates/2026-10-29-fork-context-review.md)。

2026-10-29 上下文策略已统一：Chat/Work历史和Work执行使用完整模型窗口85%触发，输出按当前剩余空间计算且请求/账本一致；本机API/Agent更新、管理配置和浏览器通过，168核心回归通过。满窗口真实模型/数百人容量未验。[策略/证据](../progress/updates/2026-10-29-context-85.md)。

2026-10-29 来源预览与读取恢复对齐：静态可恢复失败时使用已有隔离浏览器HTML快照，失败显示中文HTML；精确授权API/Agent、CSP/public-only边界保留。力扣真实正文通过，知乎两个样本仍受原站拒绝，未宣称全网或数百人容量。[记录](../progress/updates/2026-10-29-reader-preview-403.md)。

2026-10-29 已授权并完成本机体验部署：固定dirty快照、保留数据0033→0036、实际四格式/OCR/DeepSeek/浏览器通过；不等同远端合并或目标NPU/数百人验收。[当前部署边界](../progress/updates/2026-10-29-file-context-local-deployment.md)。


2026-10-29 核心后续：工作树已迁入项目`.worktrees/file-context-ocr`；真实DeepSeek5次成功压缩（另1次schema拒绝已计量）、实际RapidOCR CPU识别与重复上传缓存/独立删除通过。已补跨frame校准持久队列、OCR私有页复制和API墓碑恢复应用器；核心84/21跳过、Ruff/导入通过，未重复前轮全套。真实NPU/代表性质量及数百人容量未验。详见[最新记录](../progress/updates/2026-10-29-file-context-core-validation.md)，不据下方历史未完成表判断当前状态。


2026-10-29 文件上下文/平台办公能力核心接入已本地实现/验证：固定ZCode核心和私有viewer工作流、Broker系统reader、原生块索引/附件身份、Chat准入/Work按需、tool/skill发现与恢复、摘要来源栅栏和RapidOCR专用执行接口已接线。迁移0035/0036仅在隔离库验证，未部署。OCR只计量、不计费；真实NPU/CANN、OCR质量、真实模型多代保真、数百人容量和跨节点Broker调度未验，不把实现合同等同生产验收。逐项缺口与证据见[验收记录](../reviews/file-context-2026-10-29/acceptance.md)及[实施记录](../progress/updates/2026-10-29-file-context-ocr-implementation.md)。下文历史阶段记录不覆盖本节或推断部署。

2026-10-28 记忆完善已本地实现（`feat/memory-system-hardening@ac7e6cc`，base`0659101`，尚未合并）：统一模型视图/作用域、持久分段进度与claim、短事务/每调用账本、完整副本删除、追加纠正/时间状态及记忆来源管理已接线；0034双库迁移。完整组测SQLite1451/102跳过、PG1499/54、浏览器118/0跳过、Portal42/build通过，最后局部锁/缓存修改另做双库专项，精确head仍由PR CI验证。真实答案质量/付费模型/GPU embedding、目标机并发不冒充已验。见[实施及边界](../reviews/2026-10-28-memory-system-hardening.md)。下方9类审查缺陷是修复前基线。

2026-10-28 记忆架构审查（固定main `0659101`）：工作上下文、账号事实、BM25/可选向量历史检索与nightly框架已存在，附件完整DoD未满足；项目作用域、事实context compiler、完整删除谱系与质量基线仍缺。既有65项记忆测试通过，13个合同探针12失败/1对照通过，归并9类缺陷（7 P1/2 P2），包括可见性/写入资格、游标、来源、冲突原子性和SQLite模型等待写锁。仅审查未修复，不据此声称生产就绪；真实模型/PG并发/整栈容量未验。[审查与证据](../reviews/2026-10-28-memory-architecture-review.md)。下方“PR96待评审”等旧状态已由实际main合并代码取代，本轮不推断个人部署。

2026-10-26 持续reader发现PID1孤儿回收及启动取消活driver泄漏，PR96已实现tini/独立进程树监督/1GiB单页准入。最终候选166/166正常读取、两组50次启动故障及隔离探测通过，无OOM/僵尸；按用户要求停止全量复测并交付PR，不合并部署。线上仍cc247a2，任意网页容量与新全样本成功率未验，见[初步证据](../progress/updates/2026-10-26-crawler-process-reaping.md)。

2026-10-26 PR92/93/94合并后main CI均成功；reader补齐Linux/ARM64镜像、真实k3s双向网络隔离、7项browser/proxy契约及2vCPU/8GiB组件短测。整次调用超时修复已经PR95合并main`cc247a2`并部署；个人Compose已接入并通过隔离/真实Agent及桌面手机验收，目标VM短测完成，长期容量仍未验，详见[当前验证](../progress/updates/2026-10-26-merged-reader-validation.md)。

2026-10-26 网页来源侧栏：PR83已合main `438bd63` 并部署；多网页标题/独立关闭、最近4文档保留、原版静态隔离与正文提取边缘缺陷修复，合并main八job成功与部署后真实登录/桌面手机两标签/CSP/数据保留验收通过；验证与动态站点边界见[本轮记录](../progress/updates/2026-10-26-web-source-tabs.md)。

2026-10-26 管理文件去重、思考默认折叠/按需完整渲染、Portal standalone与按变更部署：本地15项浏览器/Portal41/build/SQLite1270通过，未发布；成本与验收边界见[本轮记录](../progress/updates/2026-10-26-admin-thinking-deploy.md)。
2026-10-25 本机部署：固定main `72901e3`已通过保留数据的0028→0029→0030迁移，真实来源桌面/390×844、模型DOCX/预览、3分钟exec_wait、取消、问答继续与受控额度暂停/UI继续通过；26分30.89秒真实模型+runsc工作、8阶段各180秒计算/报告交付，观察26次短claim续期、投递1次、workspace最多/最终1对象、无stall误判/OOM/重启。九服务RSS合计中位1930.21MiB，本轮余额减少¥0.16。完整1–4小时/真实额度重置、强制重复检测、真实手机和硬件链路稳定性仍未验；[本轮记录](../progress/updates/2026-10-25-deploy-long-runs.md)区分当前检查、探针失败与历史证据。下文旧“未部署”仅代表当时阶段。

2026-10-25 来源与长任务最终验收：集成`feat/long-runs-sources@a677a5f`，最终`feat/long-runs-final@38a71f8`。来源面板/Run来源、v2预算/续租/统一exec/重复检测、额度暂停和review3已集成；补齐checkpoint/result快照引用原子同步、真实tmpfs helper安装、SIGKILL监督进程的孤儿组清理。Docker/runc、Docker/runsc、OpenSandbox/runc+dns+nft各400次exec：209.262/259.915/1012.291秒，四次采样均5 PID/0 Z；真实合同共9项通过。SQLite1248/132跳过、PG+Redis1335/45跳过、Portal38/build、E2E98/0跳过及Ruff通过。精确边界/清理见[最终记录](../progress/updates/2026-10-25-long-runs-sources.md#final-real-sandbox-validation)；未合并/未部署，真实1–4小时模型、整栈目标容量、手机仍待验。

2026-10-25 长任务v2审查修复：`feat/long-runs-v2-fixes`基于集成`8a2bfed`（含Portal `a4fa0b6`），补齐有界checkpoint日志/旧格式恢复、exec头尾与传输字节预算、静默wait排除、运行状态事件和无Agent句柄列表时的租约全量清理。本地验收及残余边界见[统一记录](../progress/updates/2026-10-25-long-runs-sources.md#long-runs-v2-review-fixes)；不代表真实长时模型、目标机容量或上线验收。

2026-10-25 最终来源/长任务集成：`feat/long-runs-sources@0caa73b` + `feat/quota-pause-gc@65b47e0`，base `origin/main@09599a0`。来源面板/Run来源、长任务预算/租约/后台exec与日/月额度暂停已集成，本地全套最终复核见[统一交接](../progress/updates/2026-10-25-long-runs-sources.md#最终整树复核与额度暂停gc修复)。GC非终态保护集keyset分页、0030(status,id)索引，精确引用快照保留；真实1–4小时模型、真实sandbox后台exec、目标机RSS与真机手机待验。未合入main/未部署；下文子分支“未集成”均为历史阶段。

2026-10-25 Run 来源持久化：`feat/run-sources@41da18f` 本地实现整轮网页来源去重/50条上限、checkpoint恢复、终态结果与详情输出；未集成Portal或合入main，验证边界见[交付记录](../progress/updates/2026-10-25-run-sources.md)。

2026-10-25真实沙箱补验（PR #54已合并main `a646ba3`，未部署）：真实Docker/runsc 9项、OpenSandbox/runc+dns+nft 9项、PG16+Redis+OpenSandbox五服务浏览器7项通过，均0跳过；SQLite/PG全Python1178/1224通过（跳过81/35）。head `16245a7` 的8项CI全通过。修复Docker到期竞态与OpenSandbox DNS-only字面IP绕过，模型/扫描仍为测试替身，不能推导Kubernetes/gVisor或付费模型联合验收。见[本轮记录](../progress/updates/2026-10-25-real-sandbox-acceptance.md)。

2026-10-25 单机全量审查：已实现恢复前预算复核、0029调用部门快照、详情批量读取、历史版本精确查找、完整已确认插话和Portal异步生命周期修复；本地SQLite1170/PG1216/E2E98及Portal35/build通过（跳过/覆盖率/基线见交付），[PR #49](https://github.com/shijunan126-creator/ailab-lite/pull/49) head `0dc2884` 八项检查全部通过，已squash合入main `d11f86e`，合并树与测试head一致；未部署。范围与实测成本见[审查](../reviews/2026-10-25-single-node-review.md)，以[交付记录](../progress/updates/2026-10-25-single-node-review.md)的最终检查head和验收边界为准。下文旧状态保留历史证据，不能覆盖本条。

2026-10-24 PPT模板集成：`feat/ppt-templates-portal`，部署固定`b2c8394`，测试`e905790`，未合main。空库0028、三类镜像冷构建/源码核对、真实Docker/runsc sandbox首屏480px JPEG（公司/个人）、有模板/无模板真实PPT、原生母版/版式和示例页剔除、queue/steer/share/inbox及PPTX/DOCX浏览器预览通过；SQLite1123/PG1208/E2E88通过。**Portal自动缩略图切换已修复并合入main，待部署**：独立preview_status及有界可取消轮询，公司/个人延迟成功、失败回退和503重试4项浏览器回归通过；本轮完整SQLite1123/118、Portal33/build通过，详见[修复记录](../progress/updates/2026-10-24-template-preview-refresh.md)。PR #48 head b1a610d的12项CI检查全通过，squash合并df30dd5，树与测试head相同；部署仍为旧b2c8394。平台/Worker无新增Office依赖；短时RSS/空间回收与外部存储稳定性、K3S仅静态渲染等边界见[最终记录](../progress/updates/2026-10-24-ppt-templates-clean-acceptance.md)。原后端sandbox缩略图实现详见[设计与专项](../progress/updates/2026-10-24-ppt-template-thumbnail-rendering.md)，旧Worker245MiB方案仍已撤销。

2026-10-24 工作区B：通知/排队/steer后端独立分支已实现，9992308本地SQLite1057/PG+Redis1138通过，尚未集成/部署；见[B记录](../progress/updates/2026-10-24-inbox-queue-backend.md)。

2026-10-24 Gate 3当前状态：PR #42 head `16a8822` 八项CI成功，已squash合入main为 `b07548995ffbce3fc618f94955c5d66b54b380c7`；同一固定提交已部署并通过有限真实模型浏览器smoke。已验完整备份恢复、既有数据0021→0024/ID数量/线性会话树/文件哈希、五服务健康与预算内短时RSS/CPU、标题指标抓取和30秒调度器真实到期执行。详情及残余边界见[Gate 3记录](../progress/updates/2026-10-24-release-ui-features.md#gate-3部署合并后的-mainround-3)。本次发布无待实现项，新增功能想法待用户决定。以下Round1/Round2及各子分支状态均为历史阶段，不代表当前仍未合并/部署。

2026-10-24 Round2：`d865356`修复标题gate预算/quiet非思考/失败兜底，真实Gate1逐项通过；SQLite1033、PG+Redis1112、E2E78通过。PG C locale Unicode搜索已修复。16K临时窗口压缩、非真机手机等边界见[发布记录](../progress/updates/2026-10-24-release-ui-features.md#round-2-最终本地结果代码提交-d865356)。[PR #42](https://github.com/shijunan126-creator/ailab-lite/pull/42)已创建/登记，当时Gate2待远端CI与父任务接续；现已merged/deployed，见页首。下方Round1失败保留为历史证据。

2026-10-24 发布门禁：`ec59932` 的真实模型 Chat 已观察思考大纲、框外正文流式与网页来源，但首轮早标题10秒超时、复测成功，Gate 1 未通过；其余真实模型场景未执行，未PR/merge/deploy。历史 mock 集成证据不等于外部验收，详见[发布门禁记录](../progress/updates/2026-10-24-release-ui-features.md)。

更新于 2026-10-23，分支 `feat/remaining-work`。任务编号对应 [差距审查](../reviews/2026-10-23-single-node-architecture-gap.md) 第 6 节，范围与默认决策见 [ADR 0001](adr/0001-single-node-scope.md)。

项目接续、计划和验收记录入口见 [项目进度](../progress/README.md)，最新授权范围见 [当前工作](../progress/current.md)。本页只维护能力状态；“合入”指当前集成分支，不代表已合入远端 main。

剩余工作的原设计见[设计稿](design/2026-10-23-remaining-work.md)。本轮由主Agent独立完成集成；设计稿的阶段顺序不代表当前状态，实际完成与未验边界以下表和[最终报告](../reviews/2026-10-23-t07-runtime.md)为准。

“完成”表示代码、迁移与回归测试都已合入，并在本机 SQLite 和 PostgreSQL 16 + Redis 两套测试中通过；“未验收”列出仍需真实外部组件或目标机器才能确认的部分。

## 总览

| 任务 | 状态 | 说明 |
|---|---|---|
| T01 可靠调用账本 | 完成 | 见下文“计量与额度” |
| T02 配额完整语义 | 完成 | 同上 |
| T03 安全 profile 与配置校验 | 完成 | 见“安全” |
| T04 真实扫描与隔离解析 | 完成（原生 ARM64 ClamAV 已验收） | 见“文件” |
| T05 服务认证与授权 | 完成（静态凭据适配器） | 见“安全” |
| T06 Broker 持久租约与对账 | 完成（Agent续租已接入） | 见“沙箱” |
| T07 Runtime / checkpoint / ask_user | 已实现，本地验收 | checkpoint/CAS、ask_user、续跑、attempt栅栏与稳定交付；长任务短claim续租、活跃预算/无进展暂停、后台exec和本轮上下文压缩已本地双库验收，瞬态续租容错、暂停来源与失败结果保留已集成，统一验收见[来源/长任务集成](../progress/updates/2026-10-25-long-runs-sources.md)；真实Linux Docker/runc+runsc与OpenSandbox/runc后台进程已验，真实长时模型/整栈容量/手机待验，见[最终记录](../progress/updates/2026-10-25-long-runs-sources.md#final-real-sandbox-validation)；原后端证据见[长任务](../progress/updates/2026-10-25-long-runs.md)。既有真实OSB/Docker恢复与SIGKILL历史证据见[验收](../reviews/2026-10-23-library-runtime-acceptance.md) |
| T08 单机调度隔离 | 已实现，本地验收 | Chat/Work固定有界分槽，暂停释放槽，回答/恢复重查准入，同会话串行；真实模型容量未验 |
| T09 事务副作用与重试 | 已实现，本地验收 | Run/文件/预览/缩略图/OCR/续跑经持久任务信封与claim/ACK；全业务attempt栅栏在T07接通，SIGTERM/Redis丢失与双库回归通过 |
| T10 单实例契约测试 | 完成（真实 MinIO 已验收） | 见“运维” |
| T11 部署解耦 | 完成（隔离 k3s 已验收） | 见“运维” |
| T12 备份恢复与存储生命周期 | 完成 | 见“运维” |
| T13 身份 / RBAC / 部门 / 审计 | 完成 | 见“身份” |
| T14 模型策略与上下文 | 已实现，本地集成 | `96654e6`能力约束/输出前降级、每次独立账本、分层deadline、预算历史与摘要；独立计费标题已补bootstrap接线；0014已集成，0016依赖缺口已解除；真实付费模型未验，见[T14报告](../reviews/2026-10-23-t14-model-context.md) |
| T15 Portal 恢复与长会话性能 | 已实现，本地验证 | keyset分页/上翻锚定/旧响应取消；预览由用户显式打开，生成更新不抢全屏/不换选中项；500轮SQLite/PG p95 19.64/47.13ms，主分支浏览器7项通过，见[UI报告](../reviews/2026-10-23-preview-session-pagination-titles.md) |
| T16 可观测性与负载基线 | 短时真实组件组合已验 | 指标/有界OTLP/传播/队列hook；真实Office/PG/Redis/S3/ClamAV/搜索组合负载、抓取与告警恢复已验，见[最终验收](../reviews/2026-10-23-library-runtime-acceptance.md)；不推导24小时或任意生产规模 |
| T17 OIDC | 完成（真实 Keycloak 已验收） | 见“身份” |
| T18 文件版本与回收站 | 完成 | 见“文件” |
| T19 OCR / 多模态 / Office 预览 | 已实现，本地验收 | OCR持久任务/文件归属计费、归一化vision输入、文本回退与图片预算/引用checkpoint已接通；受控TCP上游与双库回归通过，付费vision质量未验 |
| T20 办公 Skills 治理 | 治理实现完成，业务评测待外部输入 | manifest/哈希、管理员/部门策略、个人审核、管理UI；当前真实Docker证明/非root不可改/快照恢复通过；digest身份、公司模板/业务golden未验，见[最终验收](../reviews/2026-10-23-t07-runtime.md) |
| T21 文档与扩展交付 | 本轮接续记录完成 | current/矩阵/HANDOFF/ADR与最终证据同步，记录统一代码基线与外部未验边界；未推送/未部署 |
| 用户记忆与技能在线编辑（2026-10-24） | 已部署并通过验收；PR #39已合入 main | 用户记忆CAS/开关/按轮快照与Chat/Work工具；技能页Markdown编辑/ZIP整包替换、Agent更新CAS；结果与未验边界见[审查](../reviews/2026-10-24-memory-skills.md) |
| 对话能力与模式文案（2026-10-24） | 已部署并通过验收；PR #39已合入 main | 正常助手能力，仅无沙箱工具；不主动播报模式、无逐条模式标签，见[追加验收](../progress/updates/2026-10-24-chat-capabilities.md) |

## 计量与额度

| 能力 | 实现 | 未验收 |
|---|---|---|
| 调用级账本（T01） | 每次模型调用先预留、后结算：`call_id` 唯一、结算幂等、定价快照（模型倍率、缓存折扣、policy_version）冻结、金额为整数 milli；断流记 `estimated`，API 不可达时预留过期转 `unreconciled`，晚到结算原行修正。账本不引用 runs，对话被清除后用量仍在。对账 CLI：`ops.reconcile_usage` | 上游是否严格遵守 `max_tokens`、模型专用 tokenizer、网关侧对账 |
| 硬额度（T02） | 用户日/月额度在预留时原子检查（已结算 + 未结算预留），并发 Run 不会同时越界；周期按 `quota_timezone`（本轮保持UTC）。中途reserve额度拒绝保存并人工可续跑暂停，恢复准入保留结构化429，暂停元数据及月度快照保留见[额度暂停](../progress/updates/2026-10-25-quota-pause.md)。Run累计token cap使用FAILED终态并要求新任务，释放会话；日/月额度仍可暂停继续（ADR 0008第三轮修正） | — |
| 额度策略 | 用户策略 → 部门策略 → 默认策略 → settings，统一经 `quota.user_limits` 用于 Token、资料库空间、并发 Run、用量摘要 | — |
| 用量提醒 | `/v1/usage` 返回各窗口 used/reserved/percent/resets_at 与 80%/100% 告警；Portal 输入框上方显示提醒 | — |

## 安全

| 能力 | 实现 | 未验收 |
|---|---|---|
| 配置与 profile（T03） | `AILAB_PROFILE=dev/test/secure`；后端选择用枚举，未知值拒绝启动；数值范围校验；secure 拒绝 mock 沙箱、stub 扫描、默认/共享凭据、开放注册无邀请码、未授权外部模型；日志脱敏；`trusted_proxies` 之外的 XFF 不被信任 | — |
| 服务身份（T05） | 每个服务独立凭据 + 端点 scope 矩阵；路由巡检测试拦截漏声明 scope 的 `/internal` 端点。文件访问绑定：Agent/Broker 绑定 `run_id`，Worker 绑定处理任务，资料库渲染绑定当前 pending 预览任务；回收站文件 404、已清理版本 410 | SA TokenReview / TLS 轮换（保留 port，见 ADR 0002） |
| K8s Secret 分发 | 每个 Deployment 只挂自己的 identity Secret 与 verifier | 隔离 k3s 已检查各 Deployment 的实际 Secret 引用；账号仅 API、模型仅 API/Agent、OSB key 仅 Broker；未验轮换 |

## 沙箱

| 能力 | 实现 | 未验收 |
|---|---|---|
| 租约所有权与对账（T06） | 资源带 owner/lease_id/run_id 标签；LeaseStore CAS；启动与周期对账；Redis 清空后从标签重建；删除失败退避重试；续租与最长寿命；检测到第二个 Broker 拒绝启动 | Docker/OSB 生命周期各 1 passed、真实重启/Redis 记录丢失/外部删除共 2 passed；其他故障组合未验；Agent每60秒续租已在4f9f3cb接入，410中断并自动续跑一次；不再强制TTL大于Run超时，真实OSB恢复组合未验 |
| read_url 两级读取（ADR 0021） | httpx+Trafilatura 出 Markdown；正文过短/应用壳/403·429·503 时才交隔离 crawler（Crawl4AI+Chromium，无平台凭据，经 egress 代理只达公网80/443）；渲染失败有静态文本则带 note 返回，否则工具错误；secure 要求 crawler 令牌≥24位；移除 `/v1/reader` JSON 与旧扁平提取 | 已实现未验证（用户要求不跑测试）；真实 crawler/代理契约、渲染比例、目标机延迟与内存、lite/个人部署均未验，见[记录](../progress/updates/2026-10-26-crawl4ai-reader.md) |
| k3s/OSB 受控联网 | 节点runsc根overlay 4GiB、namespace ephemeral-storage 5GiB/容器；显式Kubernetes断网模式，secure需隔离验收声明；节点补ICMP规则；非root上传前建父目录 | 独立3CPU/5GiB VM契约已验；完整平台/模型、IPv6/多节点/其他CNI、长期压力未验，见[专项](../reviews/2026-10-24-controlled-network.md) |
| 准入（T08 Broker 部分） | 静态总量与内存预算、每用户上限、FIFO、带截止时间的有界队列、预览渲染保留槽 | 单 Broker；无分布式调度 |
| 大文件传输（F8） | 已实现 API → 磁盘临时文件 → provider 流上传；交付/快照/恢复/预览输出按块，实际传输计限额；失败与取消清理、重启回收 owner 临时目录 | 真实 Docker/OSB 流式契约通过；2CPU/8GiB、Broker384MiB、双路50MiB注入/交付/快照和双路100MiB扩限压力通过，RSS约68MiB；SQLite737+取消分项 / PostgreSQL766通过，详见[F8报告](../reviews/2026-10-23-broker-streaming.md)；非五服务联合负载 |

## 文件

| 能力 | 实现 | 未验收 |
|---|---|---|
| 扫描（T04） | Scanner 端口；ClamAV TCP/Unix INSTREAM；扫描器不可用时文件保持 pending、退避重试、最终标记，绝不当作干净 | 2026-10-23 原生 ARM64 ClamAV 1.5.4：契约 1 passed、EICAR rejected/clean ready、故障 30.03 s fail closed / 36.86 s 恢复；容器空闲 RSS 1045.9 MiB；冷启动签名重载峰值未验 |
| 隔离解析（T04） | 受限子进程：内存 192 MiB、CPU 45 s、墙钟 60 s、输出 16 MiB；ZIP 膨胀检查；缩略图解码同一执行器 | Linux RLIMIT_AS/OOM/timeout 已验；20 MiB PPTX 扫描+解析 27.91 s，Worker peak RSS 173.2 MiB / limit384；并发饱和未验 |
| Work 本地 Office 读取 | 镜像预装固定 Node/ZCode reader，office-read 按需解析工作区四格式；编辑生成沿用现有库，平台索引独立 | 实现与本机解析检查见[本轮记录](../progress/updates/2026-10-29-work-office-runtime.md)；真实ARM64/runsc镜像及DeepSeek Flash编辑重读通过；资源峰值/完整五服务交付未验，未部署 |
| 资料库预览 | 首次打开时生成并永久缓存：PDF/Word/PPT 页面、Excel/CSV 表格、图片（≤2048 px 显示图）、文本/Markdown/代码。Office/PDF 在 Broker 的 render 保留槽沙箱中渲染，xlsx/csv 在 Worker 受限进程中处理；ETag/304/CSP；删除时清理 | 2026-10-23 k3s→OSB render 保留槽 DOCX 8.12 s ready，中文 DOCX/PPTX JPEG 已看图；真实 OSB+S3 联合部署未验 |
| 渲染沙箱（按用户复用） | 用户绑定的 render 沙箱常驻 LibreOffice listener，同用户预览与解析复用；首页先发布，其余页批量光栅化后逐页发布；1 槽 / 768 MiB（ADR 0030） | 2026-11-01 runsc：15 页 PPTX 全页 2.5 s、7 页 DOCX 3.2 s、首页约 1.0 s，交替用户冷启动首页 4.2–4.6 s；见 [C4](../progress/updates/2026-10-31-prefix-cache-render.md#c4-收尾2026-11-01claude) |
| 对话发送附件预览 | 复用资料库渲染内容，以file_id建立侧栏标签；同名文件独立、切换/关闭/下载；手机保留带标签整屏侧栏 | 2026-10-29 本机Portal独立更新，真实DOCX/表格/图片及1440/390浏览器通过；[记录](../progress/updates/2026-10-29-chat-attachment-tabs.md)，非全站E2E重验 |
| 版本与回收站（T18） | 版本为不可变行（lineage + version 唯一）；旧版恢复 = 新建 head；每链最多 10 版，超出的旧版留 tombstone（内容 410）；删除整链进回收站 30 天并计入空间额度；项目删除把文件移入回收站；对象删除在同一事务写持久删除意图，失败由 lifecycle 重试 | 真实 S3 全套行为已验；生产规模历史版本 copy/RSS/延迟仍未验 |

## 身份

| 能力 | 实现 | 未验收 |
|---|---|---|
| 身份与权限（T13） | 稳定 user_id；(issuer, subject) 唯一；部门；角色：成员、部门管理员、平台管理员、审计员；额度策略；审计与变更同事务写入。没有任何跨用户读取对话/文件/Run 内容的端点 | — |
| 管理台 | `/admin`：用户、部门、额度、用量（按部门/用户/模型，读账本冻结金额与调用时部门快照）、审计 | — |
| OIDC（T17） | 授权码 + PKCE/state/nonce、JWKS 缓存与未知 kid 刷新、组 → 角色/部门映射、停用拒绝；浏览器不接触上游 Token；登录页按配置显示统一身份登录 | 2026-10-23 Keycloak 26.6.4 gate 1 passed / 2.74 s，Portal 浏览器登录/组映射/停用/注销通过；真实企业 IdP 未验 |

## 运维

| 能力 | 实现 | 未验收 |
|---|---|---|
| 契约测试（T10） | `platform/tests/contracts/`：Storage（Local/S3）、JobQueue（memory/Redis）、DB（SQLite/PG）共用一套用例；CI JUnit 防静默跳过、可测门禁与每周 Docker/Office/ClamAV/Keycloak 入口见 [2026-10-24 更新](../progress/updates/2026-10-24-ci-test-completeness.md)，本轮尚未远端验收；本地未配置外部后端仍跳过 | 2026-10-23 真实 MinIO 契约15 passed/1 local-only skip；Linux PG16/Redis/全局S3/原生clamd最终753 passed/9分项skip；CI 已改固定源码 SHA256 构建/缓存 MinIO，首轮新工作流验收见 [CI 更新](../progress/updates/2026-10-23-ci-refresh.md) |
| 部署（T11） | `platform/deploy/base` + `overlays/dev|lite`；Broker 独立 Service；公共入口路径白名单；NetworkPolicy（含 Worker→Broker、Worker→ClamAV）；PVC 参数化与容量告警。渲染需 kustomize ≥ 5.7 | 隔离 2CPU/8GiB k3s 7/7 Ready、网络/Secret/离线smoke通过；Redis故障探测约1s；单轮无OOM但有CPU节流；lite hostNetwork公网防火墙未验 |
| 备份恢复（T12） | `ops.backup create/verify/restore`：SQLite 在线备份或 pg_dump、对象清单与哈希、脱敏配置；恢复到空目录并冒烟 | 隔离k3s 57对象/241MiB create+verify+restore+verify=4.14s、RSS60.1MiB，新API下载/预览/Run通过；生产数据量RPO/RTO与异机介质未验；需停写/GC |
| 存储生命周期（T12） | 过期上传暂存、孤立派生对象、终态 Run 的旧事件、软删 30 天的对话、过期回收站、三天工作区；持久删除意图 + 重试；每类有设置与低基数指标 | — |

## 迁移链

0008 → 0009 ledger → 0010 lifecycle → 0011 preview outbox → 0012 identity → 0013 file versions → 0014 transactional jobs → 0016 conversation summary → 0017 skill policies → 0018 file usage → 0019 runtime → 0020 library namespaces。已在4f9f3cb统一基线双库验证升/降/再升与模型漂移；0015未占用，不补空stub。

操作见 [部署说明](../../platform/deploy/README.md)、[备份恢复 runbook](runbooks/backup-restore.md)、[契约测试分组](../../platform/tests/contracts/README.md)、[账本契约](usage-ledger.md)。

2026-10-23 真实组件的命令、资源表、修复与验收边界见 [真实组件验收](../reviews/2026-10-23-real-components.md)。单轮小机通过不等于生产规模、并发容量或公网安全承诺。

## main 集成补充（2026-10-23）

main `951ec16` 的 SearXNG 联网检索已集成；检索部署迁入 `base/search`，显式配置 Agent 到 Service 的地址，NetworkPolicy 仅放行 Agent。新回归与 dev/lite 渲染已通过，完整集成验收见 [本轮记录](../progress/updates/2026-10-23-main-integration.md)。尚未重跑真实 SearXNG 外部引擎或包含它的 2 vCPU / 8 GiB 容量验收；此前真实组件数据不覆盖该新增服务。

## 2026-10-23 资料库与运行修复补充

本轮结果优先于上方历史“未验收”措辞，完整证据：[资料库与生命周期验收](../reviews/2026-10-23-library-runtime-acceptance.md)。

- 资料库支持连续表格、分类内项目文件夹、游标分页、独立技能用途和同目录重名分配；生成TXT的受限预览已补齐。迁移0020双库升级/降级与历史数据整理通过。
- 取消贯通Agent等待、Broker排队和创建，终态业务定期核对；Docker有可信PID1独立过期移除。实际负载发现的provider旧启动快照误回收已红绿修复，真实Docker/gVisor和OSB/runc分别通过。
- 真实OSB Office+S3+PG+Redis+ClamAV联合负载、SearXNG外部检索、Prometheus抓取与告警恢复、实际Agent/Broker SIGKILL接管均已验；硬退出测试加速了该任务的持久租约与传输可见性期限。
- 付费OCR/vision/摘要已用可核对中文样本验证并核对账本；模型目录声明与真实能力一致。公司模板/业务golden由用户明确跳过，未使用合成样本冒充业务验收。
- 2CPU/8GiB独立混合验收首字p95=2.91s；额外冷启动或超载会显著恶化时延，保留实测，不承诺任意并发。个人部署明细只保留在本机独立HANDOFF。
- OpenSandbox网络侧车与gVisor仍不兼容。单节点IPv4 k3s已验显式Kubernetes隔离模式（default-deny + 节点ICMP规则），不是侧车组合通过；IPv6/多节点/其他CNI、企业IdP、生产规模RPO/RTO与24小时稳定性仍未验。

## 2026-10-26 完整思考与长会话

最新用户决定：移除普通会话详情入口；一次展开显示完整思考，历史过程连续分页。`4899eb2` 本地加入聊天正文分块/消息与步骤延迟挂载、流式尾部对象复用和有界会话缓存；打印保持完整渲染。40万字四组合及500轮负载通过，浏览器104场景覆盖通过（全量101＋修正补验3）、大内容/滚轮/打印6项最终复验通过；未合并/部署，见[本轮记录](../progress/updates/2026-10-26-full-thinking-rendering.md)。下方详情tab为已被取代的历史决定。

## 2026-10-26 任务详情侧栏

会话内“详情”接入桌面右侧/手机统一 tabs；关闭保留原对话及打开前位置，初始从任务开头展示，事件回放不自动滚底。本地代码`fa1435e`已通过41项浏览器回归及Portal单测/build，未合并/部署，具体基线与边界见[本轮记录](../progress/updates/2026-10-26-run-details-tabs.md)。

## 2026-10-24 预览与工作过程

全屏/聊天复用原生连续页面滚动；单一工作过程区域按事件顺序显示公开叙述、计划变化和工具活动，保留提问与恢复。0021为Run新增持久running区间计时，浏览器不自行累计。行为、旧数据处理、成本和验收见[专项记录](../reviews/2026-10-24-preview-work-process.md)。

## 2026-10-24 受控联网

沙箱保持 `--network none` + gVisor；Agent 以只连已检查公网地址的客户端读取网页/GitHub issue、下载文件、获取 GitHub 公开仓库和 PyPI 依赖，Broker 流式注入沙箱。URL 来源检查、每运行请求/字节预算、归档展开上限、runsc 根 overlay 4 GiB 上限；个人技能可经 Chat/Work 新建或按版本更新，走扫描与审核。已在当前 Docker Compose + runsc 运行环境真实验收并部署，独立单节点IPv4 k3s/OpenSandbox/gVisor 已完成契约级下载、仓库、离线依赖和隔离验收（含4GiB写满、非root目录、ICMP红绿），完整五服务/真实模型与IPv6未验；Compose并发见补充证据，npm/私有仓库与低带宽泄露信号仍未支持或消除，见 [ADR 0007](adr/0007-controlled-public-fetch.md) 与 [验收记录](../reviews/2026-10-24-controlled-network.md)。

## 2026-10-24 Chat 后端分叉 / 重试

后端分支 `feat/chat-backend-fork-retry` 基于 `94d3e22`：0022迁移、首问并发标题与思考流兼容、网页来源事件、事务分叉/重试、Run归属资料库工具、平台管理员上下文阈值已实现。本地后端双库验证通过（SQLite全套985/108跳过、PG+Redis1061/33跳过，最终回归各11通过），存储成本与测试基线见[后端交付记录](../progress/updates/2026-10-24-chat-fork-retry-backend.md)；Portal由父Agent独立实现，本轮不代表前后端集成、部署或main合入。

前端集成：`feat/ui-polish-fork-retry` 已合入上述后端并实现 Portal 侧思考大纲、正文流式分离、来源/引用、复制/重试/分叉、首问标题实时更新、管理员上下文阈值；本地 Portal/SQLite/五服务 E2E 结果见[前端/集成记录](../progress/updates/2026-10-24-ui-polish-fork-retry.md)。未部署、未合入 main，真机键盘与真实模型未验。

2026-10-24 Round 2 后端标题修复：`fix/chat-title-timing@0be9fac`基于父Agent合并基线`814c541`，正文终态后保留原预算内标题任务、终态SSE有界收尾与共享Markdown规范化；SQLite全套1000通过/108跳过、Ruff通过。PG/Portal/浏览器/五服务统一验收未运行，父Agent负责集成；见同一[后端交付记录](../progress/updates/2026-10-24-chat-fork-retry-backend.md)。


## 2026-10-24 会话树 / 置顶 / 全局搜索后端

后端分支 `feat/chat-tree-pin-search`，实际base `09a011b`，运行时 `e4c70cb`：0023树形Run/活动叶节点、编辑与版本选择、祖先限定history/workspace/summary/预览/fork/隐含文件授权、置顶、有界全局搜索、主调用上下文用量已实现。准确验证结果、最终契约及成本见[专项记录](../progress/updates/2026-10-24-chat-tree-pin-search.md)。尚待父Agent合入平行任务和Portal/E2E集成；未push/PR/deploy，不能视为main或生产能力。

## 2026-10-24 定时任务与页码引用后端增量

`feat/schedules-citations` 本地后端已实现：每用户五条任务/用户锁、next_run_at CAS和Run/outbox原子提交、DST与失败收敛、项目删除停用、Worker缓存页标记、工具页码及Chat/Work项目索引/引用规则。双库全套与最终增量通过；未集成Portal/会话树、未部署、未合入main。0024当前接0022，合入sibling0023后必须重接；详细数字、成本、未验边界见[交付记录](../progress/updates/2026-10-24-schedules-citations.md)。

2026-10-24 合并功能统一验收：`test/integration-ui-features` 基于 `2e1a741`，测试代码 `6d01882`；0024已重接0023，双库完整套件/迁移往返通过，新增13项浏览器验收，完整五服务E2E77通过/1失败/1手动辅助跳过。会话级Work分叉限制与当前祖先前缀语义存在待父Agent处理差异，保留失败测试；完整结果/首轮证据见[集成测试记录](../progress/updates/2026-10-24-integration-tests.md)。未发布、未合入main。

2026-10-24 前缀分支契约决定后复跑：基线 `a09d70b`，交付分支 `test/e2e-rerun-fork-contract`；fork 单跑1通过、完整五服务E2E78通过，均0失败/0跳过，Ruff及fixture内Portal31项/隔离build通过。未修改产品代码；上述会话级与前缀语义差异已按父Agent决定解决，历史失败不代表本次结果。未部署、未合入main，真实模型/真机等边界仍保留，见[复跑记录](../progress/updates/2026-10-24-integration-tests.md#复跑合入后-a09d70b)。

### 工作区后端 A（2026-10-24，独立分支）

`feat/projects-brand` 已实现项目说明/定时项目上下文、扫描品牌套件与冻结注入、会话产物 lineage 同事务移动；尚未集成 Portal 或重建/部署 sbx-office。验证、资源成本和旧资产回收缺口见[后端 A 记录](../progress/updates/2026-10-24-projects-brand-backend.md)。不改变上表其他能力的验收状态。


## 2026-10-24 C 后端分支补充（待集成）

`feat/shares-library` 实现内部只读快照分享、资料库持久摘要与OCR选页，双库本地验证通过；通知helper仍为no-op，0027暂接0024，尚未Portal/父分支集成或合入main。接口、证据与边界见[后端交付](../progress/updates/2026-10-24-shares-library-backend.md)。


## 2026-10-24 工作区七项功能集成验收

上述A/B/C独立分支状态属于历史记录，当前已集成0025–0027与Portal，通知接缝已实接。`e34e67c`修复确认插话详情、互相分享锁顺序与有效分享复用；父任务`57ddabf`/`e452ed6`修复两项真实Portal回归，`9f62667`补视觉整理。SQLite1094、PG+Redis1178、完整五服务E2E87均通过（跳过数/基线/迁移往返/新用例/有限真实模型/成本见[集成记录](../progress/updates/2026-10-24-workspace-features-integration.md)）。

状态仅为**已实现并本地验证**，未合入main/未部署本轮功能，远端CI与父任务人工视觉验收待完成；生产sbx-office需重建，旧品牌资产GC仍缺、SVG拒绝。不得据此升级其他目标能力或长期容量的状态。

2026-10-24 工作区功能（项目说明、品牌套件、通知收件箱、排队/插话、内部只读分享、资料库摘要/OCR选页/就文件提问、移入项目带文件）经 [PR #44](https://github.com/shijunan126-creator/ailab-lite/pull/44) 合入 main `2e64da5`：本地双库/五服务E2E/有限真实模型验收见[集成记录](../progress/updates/2026-10-24-workspace-features-integration.md)。**未部署**；品牌 PPT 需重建发布 `sbx-office`。

2026-10-26 PR #93 项目菜单：桌面/手机新增重命名、说明编辑和删除入口；本地回归修复旧详情覆盖确认写入及删除项目后当前会话判断。实现/验证/精确head CI与合并边界见[记录](../progress/updates/2026-10-26-sidebar-project-menu.md)，不代表部署完成。

## Portal / GenUI（2026-10-29，未提交）

结构化后端/Portal、聊天流式与非阻塞发送、预览前移已集成，本地浏览器134项、Portal68项/build、PG1610项通过；SQLite初次全套单个恢复时序失败后单项通过。范围、最后补丁验证及成本见[集成记录](../progress/updates/2026-10-29-frontend-genui-integration.md)。尚未完成完整HLD：GenUI chunk超60KB目标，严格性能闸门、全站组件/数据迁移/CSP、HtmlArtifact与真实模型评测未完成。用户随后要求全局和所有现有模型默认启用结构化GenUI，已本机部署、未提交/push；未做真实模型质量评测，不标记全站重构完成。

## GenUI 交互与前端重设计（2026-10-30，未提交）

GenUI 支持页内控件与表达式重算、图表读数、表格排序；全站 token/字号/强调色与 Composer 重做，Portal 已执行固定模式。状态不跨刷新，chunk 超预算，性能闸门/全站迁移/真实模型评测未完成。[记录](../progress/updates/2026-10-30-frontend-redesign-genui-interactive.md)。

更新（2026-10-30 P5 收尾）：
- 控件状态跨刷新保留，分享页和打印页可见（迁移 0039）。
- HtmlArtifact 已实现：同源 frame 路由运行，无网络。
- legacy.css 已并入各区域样式文件，启用 preflight，加 nonce CSP。
- 性能闸门除 GenUI chunk 85.7 KB（用户接受）外全部通过。
- PostgreSQL 全套通过。
- 仍未提交、未合并。[记录](../progress/updates/2026-10-30-p5-ui-state-html-artifact.md)。

## 对话固定模式（2026-10-30）

API新消息与排队统一执行开始后不可切换的合同；省略继承，模板隐含Work（2026-10-31起技能不再隐含），编辑/重试保留原轮次模式。fork新会话选择与每次新建会话的定时任务语义不变，无迁移/历史重写。当前为未提交本地实现；专项与完整验证、Portal/E2E边界见[记录](../progress/updates/2026-10-30-conversation-mode-fixed.md)。

## GenUI 目录 v2 与按需披露（2026-10-30，本地实施，未提交）

- 目录由 21 个组件扩到 57 个。派生值（最值、变化率、排名、最优、累计、转化、占比、差值）改由组件计算。
- GenUI 说明改为按需披露：系统提示只放索引，指南和组件文档通过 `ui_components` 按需读取。Chat 系统提示由 5.6k 降到 1.4k token，Work 由 6.3k 降到 2.2k。
- 本地检查：浏览器在 1280/375 宽、浅色/深色下逐个检查了全部组件，Portal 测试、Python 全套和 Ruff 均已跑过。
- P6 第五轮结果见[评测报告](../progress/updates/2026-10-30-genui-real-model-eval.md)。
- 决定见 [ADR 0029](adr/0029-genui-registry-v2.md)。

## GenUI P6 真实模型质量（2026-10-30，未达标）

- 门槛已由用户改为“整份回答没有错误陈述”；在块外复述数字只作诊断。
- 两轮提示词迭代：
  - v6 在两种模式的基础提示里加了一条通用的数据陈述规则，严格项 20/23（v5 为 15/23），错误陈述从 33 条降到 12 条。
  - v7 再加一条“只描述自己确实实现的功能”，严格项 16/23，错误陈述 18 条。新增的失败与这条规则无关，单次运行之间的波动约为 ±4/23。
- 采用 v6 的提示词，v7 那条已撤回，停止迭代。
- 遗留问题：
  - 缓存解释在 v6 和 v7 中都无条件给出延迟数量级。
  - 偶发的计数和排名笔误。
- 详见[评测报告](../progress/updates/2026-10-30-genui-real-model-eval.md#迭代-v7)。

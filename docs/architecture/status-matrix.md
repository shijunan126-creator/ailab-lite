# 单机能力状态

2026-10-24 PPT模板缩略图跟进：`feat/ppt-templates-backend` 起点 `833f64b`，共享LO/Poppler真实首屏JPEG、独立受限渲染及草图fallback已实现，SQLite全量待验。父任务报告初版已集成到Portal分支 `1c53b46` 并验证三种来源/冻结/chip；本分支不改Portal，未合入main/部署。本轮成本、默认192MiB下macOS回退和Linux镜像未验边界见[跟进记录](../progress/updates/2026-10-24-ppt-template-thumbnail-rendering.md)；初版验收见[原记录](../progress/updates/2026-10-24-ppt-templates-backend.md)。

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
| T07 Runtime / checkpoint / ask_user | 已实现，本地验收 | checkpoint/CAS、ask_user、续跑、心跳、attempt栅栏与稳定交付；后续真实OSB/Docker恢复与进程SIGKILL已验，见[最终修复验收](../reviews/2026-10-23-library-runtime-acceptance.md) |
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
| 硬额度（T02） | 用户日/月额度在预留时原子检查（已结算 + 未结算预留），并发 Run 不会同时越界；周期按 `quota_timezone`。Run 内 Token 预算保持软上限（ADR 0001） | — |
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
| k3s/OSB 受控联网 | 节点runsc根overlay 4GiB、namespace ephemeral-storage 5GiB/容器；显式Kubernetes断网模式，secure需隔离验收声明；节点补ICMP规则；非root上传前建父目录 | 独立3CPU/5GiB VM契约已验；完整平台/模型、IPv6/多节点/其他CNI、长期压力未验，见[专项](../reviews/2026-10-24-controlled-network.md) |
| 准入（T08 Broker 部分） | 静态总量与内存预算、每用户上限、FIFO、带截止时间的有界队列、预览渲染保留槽 | 单 Broker；无分布式调度 |
| 大文件传输（F8） | 已实现 API → 磁盘临时文件 → provider 流上传；交付/快照/恢复/预览输出按块，实际传输计限额；失败与取消清理、重启回收 owner 临时目录 | 真实 Docker/OSB 流式契约通过；2CPU/8GiB、Broker384MiB、双路50MiB注入/交付/快照和双路100MiB扩限压力通过，RSS约68MiB；SQLite737+取消分项 / PostgreSQL766通过，详见[F8报告](../reviews/2026-10-23-broker-streaming.md)；非五服务联合负载 |

## 文件

| 能力 | 实现 | 未验收 |
|---|---|---|
| 扫描（T04） | Scanner 端口；ClamAV TCP/Unix INSTREAM；扫描器不可用时文件保持 pending、退避重试、最终标记，绝不当作干净 | 2026-10-23 原生 ARM64 ClamAV 1.5.4：契约 1 passed、EICAR rejected/clean ready、故障 30.03 s fail closed / 36.86 s 恢复；容器空闲 RSS 1045.9 MiB；冷启动签名重载峰值未验 |
| 隔离解析（T04） | 受限子进程：内存 192 MiB、CPU 45 s、墙钟 60 s、输出 16 MiB；ZIP 膨胀检查；缩略图解码同一执行器 | Linux RLIMIT_AS/OOM/timeout 已验；20 MiB PPTX 扫描+解析 27.91 s，Worker peak RSS 173.2 MiB / limit384；并发饱和未验 |
| 资料库预览 | 首次打开时生成并永久缓存：PDF/Word/PPT 页面、Excel/CSV 表格、图片（≤2048 px 显示图）、文本/Markdown/代码。Office/PDF 在 Broker 的 render 保留槽沙箱中渲染，xlsx/csv 在 Worker 受限进程中处理；ETag/304/CSP；删除时清理 | 2026-10-23 k3s→OSB render 保留槽 DOCX 8.12 s ready，中文 DOCX/PPTX JPEG 已看图；真实 OSB+S3 联合部署未验 |
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

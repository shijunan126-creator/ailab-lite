# 单机能力状态

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
| 契约测试（T10） | `platform/tests/contracts/`：Storage（Local/S3）、JobQueue（memory/Redis）、DB（SQLite/PG）共用一套用例；真实后端未配置时跳过 | 2026-10-23 真实 MinIO 契约15 passed/1 local-only skip；Linux PG16/Redis/全局S3/原生clamd最终753 passed/9分项skip；CI 已改固定源码 SHA256 构建/缓存 MinIO，首轮新工作流验收见 [CI 更新](../progress/updates/2026-10-23-ci-refresh.md) |
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
- OpenSandbox网络隔离侧车与gVisor组合不支持，保留隔离策略分别验收，不宣称该组合已通过；企业IdP、生产规模RPO/RTO和24小时稳定性不由本轮短时验收推导。

## 2026-10-24 预览与工作过程

全屏/聊天复用原生连续页面滚动；单一工作过程区域按事件顺序显示公开叙述、计划变化和工具活动，保留提问与恢复。0021为Run新增持久running区间计时，浏览器不自行累计。行为、旧数据处理、成本和验收见[专项记录](../reviews/2026-10-24-preview-work-process.md)。

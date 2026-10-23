# 单机能力状态

更新于 2026-10-23，分支 `feat/arch-hardening`。任务编号对应 [差距审查](../reviews/2026-10-23-single-node-architecture-gap.md) 第 6 节，范围与默认决策见 [ADR 0001](adr/0001-single-node-scope.md)。

未开始与部分完成的任务（T07/T08/T09/T14/T15/T16/T19/T20 及 Broker 流式注入）的方案见 [剩余工作设计](design/2026-10-23-remaining-work.md)，暂不实施。

“完成”表示代码、迁移与回归测试都已合入，并在本机 SQLite 和 PostgreSQL 16 + Redis 两套测试中通过；“未验收”列出仍需真实外部组件或目标机器才能确认的部分。

## 总览

| 任务 | 状态 | 说明 |
|---|---|---|
| T01 可靠调用账本 | 完成 | 见下文“计量与额度” |
| T02 配额完整语义 | 完成 | 同上 |
| T03 安全 profile 与配置校验 | 完成 | 见“安全” |
| T04 真实扫描与隔离解析 | 完成（ClamAV 真机未验收） | 见“文件” |
| T05 服务认证与授权 | 完成（静态凭据适配器） | 见“安全” |
| T06 Broker 持久租约与对账 | 完成（Agent 续租未接入） | 见“沙箱” |
| T07 Runtime / checkpoint / ask_user | **未开始（仅 Portal 侧）** | Portal 已能展示计划、回答提问、继续中断的 Run；后端事件与接口尚未实现 |
| T08 单机调度隔离 | 部分 | Broker 准入完成；Agent 有界并发、Chat 保留槽、同会话串行未做 |
| T09 事务副作用与重试 | 部分 | `outbox` 表与派发器已有，承载预览入队；Run/上传入队、事件尚未迁入 |
| T10 单实例契约测试 | 完成（真实 S3 未跑） | 见“运维” |
| T11 部署解耦 | 完成（未上线验证） | 见“运维” |
| T12 备份恢复与存储生命周期 | 完成 | 见“运维” |
| T13 身份 / RBAC / 部门 / 审计 | 完成 | 见“身份” |
| T14 模型策略与上下文 | **未开始** | 跨模型降级、TTFT/总时限、按 token 的上下文压缩 |
| T15 Portal 恢复与长会话性能 | 部分 | 流断线按游标续读、退避、去重已完成；会话详情分页未做 |
| T16 可观测性与负载基线 | **未开始** | OTel、TTFT/排队等待指标、看板与告警、负载测试 |
| T17 OIDC | 完成（真实 Keycloak 未验收） | 见“身份” |
| T18 文件版本与回收站 | 完成 | 见“文件” |
| T19 OCR / 多模态 / Office 预览 | 部分 | 资料库预览完成；OCR、多模态未做 |
| T20 办公 Skills 治理 | **未开始** | manifest 版本/哈希、管理员禁用、golden 评测 |
| T21 文档与扩展交付 | 进行中 | 本页、AGENTS.md、ADR 0001–0003、runbook；目标文档已冻结入库 |

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
| K8s Secret 分发 | 每个 Deployment 只挂自己的 identity Secret 与 verifier | 未在真实集群操作 |

## 沙箱

| 能力 | 实现 | 未验收 |
|---|---|---|
| 租约所有权与对账（T06） | 资源带 owner/lease_id/run_id 标签；LeaseStore CAS；启动与周期对账；Redis 清空后从标签重建；删除失败退避重试；续租与最长寿命；检测到第二个 Broker 拒绝启动 | 真实 Docker/OpenSandbox 契约；Agent 每 60 秒续租尚未接入（因此配置强制 `sandbox_ttl_seconds > run_timeout_seconds`） |
| 准入（T08 Broker 部分） | 静态总量与内存预算、每用户上限、FIFO、带截止时间的有界队列、预览渲染保留槽 | 单 Broker；无分布式调度 |

## 文件

| 能力 | 实现 | 未验收 |
|---|---|---|
| 扫描（T04） | Scanner 端口；ClamAV TCP/Unix INSTREAM；扫描器不可用时文件保持 pending、退避重试、最终标记，绝不当作干净 | 真实 clamd（需 `AILAB_TEST_CLAMD`）；clamd 约 1.5 GiB 常驻 |
| 隔离解析（T04） | 受限子进程：内存 192 MiB、CPU 45 s、墙钟 60 s、输出 16 MiB；ZIP 膨胀检查；缩略图解码同一执行器 | Linux RLIMIT_AS 与容器内压测 |
| 资料库预览 | 首次打开时生成并永久缓存：PDF/Word/PPT 页面、Excel/CSV 表格、图片（≤2048 px 显示图）、文本/Markdown/代码。Office/PDF 在 Broker 的 render 保留槽沙箱中渲染，xlsx/csv 在 Worker 受限进程中处理；ETag/304/CSP；删除时清理 | 真实 OpenSandbox 渲染 |
| 版本与回收站（T18） | 版本为不可变行（lineage + version 唯一）；旧版恢复 = 新建 head；每链最多 10 版，超出的旧版留 tombstone（内容 410）；删除整链进回收站 30 天并计入空间额度；项目删除把文件移入回收站；对象删除在同一事务写持久删除意图，失败由 lifecycle 重试 | 真实 S3 下的性能 |

## 身份

| 能力 | 实现 | 未验收 |
|---|---|---|
| 身份与权限（T13） | 稳定 user_id；(issuer, subject) 唯一；部门；角色：成员、部门管理员、平台管理员、审计员；额度策略；审计与变更同事务写入。没有任何跨用户读取对话/文件/Run 内容的端点 | — |
| 管理台 | `/admin`：用户、部门、额度、用量（按部门/用户/模型，读账本冻结金额与调用时部门快照）、审计 | — |
| OIDC（T17） | 授权码 + PKCE/state/nonce、JWKS 缓存与未知 kid 刷新、组 → 角色/部门映射、停用拒绝；浏览器不接触上游 Token；登录页按配置显示统一身份登录 | 真实 Keycloak（需 `AILAB_TEST_OIDC_ISSUER`） |

## 运维

| 能力 | 实现 | 未验收 |
|---|---|---|
| 契约测试（T10） | `platform/tests/contracts/`：Storage（Local/S3）、JobQueue（memory/Redis）、DB（SQLite/PG）共用一套用例；真实后端未配置时跳过 | 真实 S3/MinIO 未在本机跑；CI 的 MinIO job 待远端执行 |
| 部署（T11） | `platform/deploy/base` + `overlays/dev|lite`；Broker 独立 Service；公共入口路径白名单；NetworkPolicy（含 Worker→Broker、Worker→ClamAV）；PVC 参数化与容量告警。渲染需 kustomize ≥ 5.7 | 未在 k3s 上实际部署；lite 的 hostNetwork 需宿主防火墙 |
| 备份恢复（T12） | `ops.backup create/verify/restore`：SQLite 在线备份或 pg_dump、对象清单与哈希、脱敏配置；恢复到空目录并冒烟 | 生产数据量下的 RPO/RTO；一致性快照需暂停写入与 GC |
| 存储生命周期（T12） | 过期上传暂存、孤立派生对象、终态 Run 的旧事件、软删 30 天的对话、过期回收站、三天工作区；持久删除意图 + 重试；每类有设置与低基数指标 | — |

## 迁移链

0008 → 0009 ledger → 0010 lifecycle → 0011 preview outbox → 0012 identity → 0013 file versions。

操作见 [部署说明](../../platform/deploy/README.md)、[备份恢复 runbook](runbooks/backup-restore.md)、[契约测试分组](../../platform/tests/contracts/README.md)、[账本契约](usage-ledger.md)。

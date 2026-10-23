# 单机能力状态

2026-10-23，基线 `feat/arch-hardening@6666573`，每个工作流合并时更新本页。

| 能力 | 本地实现与证据 | 剩余验收 |
|---|---|---|
| Storage / JobQueue / DB 契约（T10） | ports/storage.py；Local/S3 共享契约；memory/Redis 含 XAUTOCLAIM 游标；SQLite/PG 迁移、外键、GC EXPLAIN | 本机 Docker 不可用、MinIO 下载返回 410，真实 S3 未跑；CI 独立 MinIO job 待远端执行；不含 OpenSandbox 真机契约 |
| 部署解耦（T11） | platform/deploy/base + dev/lite；Broker Service/DNS；公共 allow-list 网关；NetworkPolicy；PVC 参数与 API 容量警告 | overlays 渲染通过，未上线；lite hostNetwork 仍需宿主防火墙，SQLite 不具备独立 DB 升级生命周期；现存 PVC 扩容依赖 storage class |
| 备份恢复（T12） | SQLite online backup、PG snapshot/pg_dump、对象哈希/引用 JSONL、脱敏配置；恢复空目录；SQLite 新进程 API 下载/对话 smoke；PG 导入新空库 | 生产数据规模 RPO/RTO 与异介质复制未验收；PG CLI restore 先恢复 SQL 文件，导入新库是显式运维步骤；确定性对象快照需暂停写入/GC |
| 存储生命周期（F8/T12） | 分页、分批、持久删除意图/重试；presign、孤立 derived/previews、终态旧 events、30 天软删 conversation、3 天 workspace | 文件回收站/十版本由后续负责人实现，当前只提供删除意图 machinery 与 retention setting；新 outbox/checkpoint/usage 表的 purge 关系需合并时整合 |
| 文件内存预算 | LocalStorage 50/100 MiB 流写/复制/读取探针：Python 峰值约 149 KB，RSS 增量 <1 MiB | Broker 50 MiB 注入/100 MiB 交付/60 MiB workspace、Worker 50 MiB 下载/20 MiB parse 整文件路径仍由其他负责人处理；不代表目标 2 vCPU/8 GB 的整机验收 |
| 用户日/月 Token 额度（T02） | Call-level atomic reservations, including other Runs and expired open intents; configurable IANA period timezone | SQLite and PostgreSQL + Redis; PostgreSQL row lock tested without a process lock. No department pool or automatic fallback policy. |
| 用量账本（T01） | Unique call IDs, immutable pricing snapshot and integer milli-unit settlement; estimated/released/unreconciled states and late correction | Duplicate delivery, API outage, stream loss, cancellation, legacy migration and both database query plans tested. Gateway-side reconciliation is outside this change. |
| Run Token 预算（F7） | Soft raw input + output budget read from ledger; check before the next call and before tools; a completed answer may cross it | Existing final-answer, recovery and tool-order regressions retained. |
| 用量读取与对账 | Window reservations, percentage, reset time and alerts; bounded keyset reconciliation CLI | Contracts and limits: [usage-ledger.md](usage-ledger.md). Portal banner rendering belongs to the Portal implementation. |
| T03 严格配置与 secure profile | 已实现 | backend/range、默认秘密、mock/stub、注册、外部模型、可信代理、日志脱敏有回归；scanner 实现由 T04 交付 |
| T05 服务身份和 scope | 已实现（静态凭据适配器） | 所有现有 `/internal` 路由巡检，含 FastAPI 延迟 include；新业务端点仍必须声明 scope |
| RunContext / Worker 文件状态授权 | 已实现 | 跨服务、跨用户、未附加文件、READY 扫描回写拒绝；会话附件与产物双库覆盖 |
| K8s Secret 最小分发 | 已实现配置 | 独立令牌 + verifier、幂等升级经 fake kubectl 和 manifest 测试；未操作真实集群 |
| SA TokenReview / TLS 自动轮换 | 未实现 | 保留 ServiceAuthenticator port，见 [ADR 0002](adr/0002-service-identity.md) |
| Broker 租约所有权与重启对账（F4/T06） | 资源标签 + LeaseStore CAS、启动/周期对账、Redis 清空后从标签重建、删除失败退避重试、续租与最长寿命 | `tests/faults/test_broker_recovery.py`（mock + Redis）；真实 Docker/OpenSandbox 契约未跑 |
| Broker 准入（T08 的 Broker 部分） | 静态总量/内存预算、每用户上限、FIFO、有截止时间的队列、预览渲染保留槽 | 单 Broker；无分布式调度与持久等待队列 |
| Agent 续租与租约丢失恢复 | 契约已提供（renew/status，410 `lease_lost`/`lease_expired`） | Agent runtime 接入待 T07 |
| 真实扫描（T04） | Scanner 端口、ClamAV TCP/Unix INSTREAM、dev 用 EICAR stub；不可用保持 pending，退避后标 scan_exhausted；base 部署单实例 clamd（约 1.5 GiB） | 真实 clamd 契约需 `AILAB_TEST_CLAMD`；未在 k3s 验收 |
| 隔离解析（T04/F3） | 受限子进程：内存 192 MiB、CPU 45 s、墙钟 60 s、输出 16 MiB、ZIP 膨胀检查；缩略图解码同一执行器 | macOS 故障测试通过；Linux RLIMIT_AS 与容器压测待验收 |
| 资料库预览（用户需求 / T19 预览部分） | 按需生成并缓存：pages/sheets/image/text；Office/PDF 在 Broker 的 render 保留槽沙箱里渲染，读文件绑定到当前预览任务；ETag/304/CSP；删除清理 | mock + 本机真实 Office/PDF 工具验证；真实 OpenSandbox 未验收 |
| Outbox（T09 起步） | `outbox` 表与派发器，目前承载预览入队 | Run/上传入队尚未迁入 outbox |
| 身份 / RBAC / 部门 / 审计（T13） | 稳定 user_id、(issuer, subject) 唯一、部门、角色（成员/部门管理员/平台管理员/审计员）、额度策略、与变更同事务的审计、管理 API；没有任何跨用户内容读取端点 | 管理台 UI 在 `/admin` |
| 额度策略接入 | 用户策略 → 部门策略 → 默认策略 → settings，统一经 `quota.user_limits` 用于 token 预留、资料库空间、并发 Run 与用量摘要 | — |
| 部门用量报表 | 按账本冻结金额与调用时的部门快照汇总，不经 runs（被清除的对话仍计入）；`(created_at, dept_id)` 索引 | 报表大范围扫描性能未压测 |
| OIDC（T17） | 授权码 + PKCE/state/nonce、JWKS 缓存与未知 kid 刷新、组映射、停用拒绝；浏览器不接触上游 Token | 真实 Keycloak 需 `AILAB_TEST_OIDC_ISSUER`，未验收 |
| 文件版本与回收站（T18） | 版本为不可变行（lineage_id + version 唯一、is_head）；旧版恢复 = 新建 head；每链最多 10 版，超出的旧版留 tombstone（内容 410）；删除整链进回收站 30 天，计入空间额度；项目删除把文件移入回收站；对象删除写入持久删除意图，由 lifecycle 重试 | 列表 SQLite covering index / PG Index Only Scan；真实 S3 性能未验收 |

操作见 [部署说明](../../platform/deploy/README.md)、[备份恢复 runbook](runbooks/backup-restore.md)、[契约测试分组](../../platform/tests/contracts/README.md)。

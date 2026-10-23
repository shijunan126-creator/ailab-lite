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

操作见 [部署说明](../../platform/deploy/README.md)、[备份恢复 runbook](runbooks/backup-restore.md)、[契约测试分组](../../platform/tests/contracts/README.md)。

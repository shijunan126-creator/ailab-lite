# 单机能力状态

2026-10-23，基线 `feat/arch-hardening@6666573`，本页先记录 ops 分支已实现与仍需验收的范围；其他工作流由各负责人补充。

| 能力 | 本地实现与证据 | 剩余验收 |
|---|---|---|
| Storage / JobQueue / DB 契约（T10） | ports/storage.py；Local/S3 共享契约；memory/Redis 含 XAUTOCLAIM 游标；SQLite/PG 迁移、外键、GC EXPLAIN | 本机 Docker 不可用、MinIO 下载返回 410，真实 S3 未跑；CI 独立 MinIO job 待远端执行；不含 OpenSandbox 真机契约 |
| 部署解耦（T11） | platform/deploy/base + dev/lite；Broker Service/DNS；公共 allow-list 网关；NetworkPolicy；PVC 参数与 API 容量警告 | overlays 渲染通过，未上线；lite hostNetwork 仍需宿主防火墙，SQLite 不具备独立 DB 升级生命周期；现存 PVC 扩容依赖 storage class |
| 备份恢复（T12） | SQLite online backup、PG snapshot/pg_dump、对象哈希/引用 JSONL、脱敏配置；恢复空目录；SQLite 新进程 API 下载/对话 smoke；PG 导入新空库 | 生产数据规模 RPO/RTO 与异介质复制未验收；PG CLI restore 先恢复 SQL 文件，导入新库是显式运维步骤；确定性对象快照需暂停写入/GC |
| 存储生命周期（F8/T12） | 分页、分批、持久删除意图/重试；presign、孤立 derived/previews、终态旧 events、30 天软删 conversation、3 天 workspace | 文件回收站/十版本由后续负责人实现，当前只提供删除意图 machinery 与 retention setting；新 outbox/checkpoint/usage 表的 purge 关系需合并时整合 |
| 文件内存预算 | LocalStorage 50/100 MiB 流写/复制/读取探针：Python 峰值约 149 KB，RSS 增量 <1 MiB | Broker 50 MiB 注入/100 MiB 交付/60 MiB workspace、Worker 50 MiB 下载/20 MiB parse 整文件路径仍由其他负责人处理；不代表目标 2 vCPU/8 GB 的整机验收 |

操作见 [部署说明](../../platform/deploy/README.md)、[备份恢复 runbook](runbooks/backup-restore.md)、[契约测试分组](../../platform/tests/contracts/README.md)。

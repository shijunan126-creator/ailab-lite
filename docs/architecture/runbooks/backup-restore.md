# 单机备份与恢复

在 platform 目录、受控运维环境运行。使用同一 DB/对象配置，`AILAB_ENV_FILE` 必须显式指定；不要把开发者数据库拿来演练。

```bash
python -m ailab_platform.ops.backup create --target /backup/2026-10-23
python -m ailab_platform.ops.backup verify /backup/2026-10-23
python -m ailab_platform.ops.backup restore /backup/2026-10-23 --into /restore/ailab
python -m ailab_platform.ops.backup verify /restore/ailab
```

目标目录必须为空，成功最后才写 backup.json。失败留下的目录不算备份，换一个空目录重试。SQLite 使用 online backup API，快照保留 WAL 已提交事务；PostgreSQL 使用 REPEATABLE READ 导出的 snapshot 和 `pg_dump --snapshot`，清单读取与 dump 属于同一个数据库快照。需要与服务端兼容的 pg_dump（宿主机安装；应用镜像不含此工具），密码通过子进程环境传递，不写命令行、日志或配置文件。

数据库快照、不可变主文件及其 size/sha256 引用、derived、preview、有效工作区、脱敏配置及 models.toml 一起保存。对象逐块复制，支持 Local/S3；S3 也保存 bucket/manifest 作为独立同步清单，恢复材料包含完整对象副本，不依赖原 bucket。暂未完成的 presign 不作为可恢复文件；已过 TTL 不存在的工作区允许省略。verify 校验清单、元数据文件、逐对象长度/哈希和引用，SQLite 额外检查完整性、外键与实际 files 表。校验不是签名，备份目录须仅由受信运维写入。

**跨数据库/对象存储没有分布式快照。** 确定性备份窗口应暂停文件删除、GC、Worker 派生写入和 Broker 预览/工作区写入，禁止新写请求并让在途写结束；SQLite API 读请求可继续。数据库 online snapshot 本身不要求停机。未暂停时，快照引用的对象若在复制前被删除或修改，备份会失败或哈希不符而拒绝完成；运行中的 derived/previews 不能承诺同一时刻一致性。不要把这种失败目录发布为成功备份。

恢复 SQLite：将 `AILAB_DATABASE_URL=sqlite:////restore/ailab/platform.db`、`AILAB_STORAGE_ROOT=/restore/ailab/storage`、`AILAB_DATA_DIR=/restore/ailab`、`AILAB_MODELS_FILE=/restore/ailab/models.toml` 指向新目录，注入新的服务凭据和模型密钥后启动 API，再启动其他服务。脱敏配置不是可直接使用的 `.env`；从受控 secret 管理处恢复凭据，不复制旧的开发默认值。自动演练会在新进程启动真实 API，下载文件并读取对话回复。

PostgreSQL restore 只将 SQL 和对象恢复到空目录；不会自动覆盖任何数据库。运维创建新的空数据库后执行 `psql -v ON_ERROR_STOP=1 -d NEW_EMPTY_DATABASE -f /restore/ailab/database.sql`，再把服务的数据库连接指向新库。S3 恢复可先以 LocalStorage 验收；迁回新 bucket 时按 objects.jsonl 的 key 上传并重新核对哈希。PostgreSQL 的离线 verify 校验 dump 哈希和快照引用清单，SQL 中的引用需在新库导入后再次演练确认。禁止在旧库上直接导入。

小机成本：LocalStore 以 64 KiB 块复制；不使用可被外部原地修改破坏的硬链接；备份和恢复各需一份完整对象磁盘容量。S3 写入使用临时磁盘 spool（单对象大小），multipart 关闭多线程；备份索引在临时 SQLite 中，清单用 JSONL，RAM 不随对象总量线性增长。校验/复制是顺序读写与 SHA256，耗时约为总字节数 ÷ 实测磁盘/网络吞吐，需监测 CPU、RSS、IO 和磁盘余量；不要在高峰与 Worker 同时跑大备份。

RPO 由最近一次**已校验且已异机/异介质复制**的备份决定。可信试点建议每晚一次、保留至少 30 天，明确接受最多 24 小时丢失；需 5 分钟 RPO 时应另做 PG WAL/PITR 与对象备份，当前 CLI 不提供该承诺。RTO 预算包括准备机器、下载备份、完整校验、复制/导入、服务启动及下载/对话验收；在实际数据规模测量后设目标，当前不承诺目标架构的 1 小时。每季度及升级前做恢复演练；备份同盘不能防主机/磁盘丢失。

生命周期默认值（环境变量均加 `AILAB_` 前缀）：`GC_BATCH_SIZE=100`（1–1000）、`GC_INTERVAL_SECONDS=3600`、`PRESIGN_STAGING_TTL_SECONDS=86400`、`ORPHAN_OBJECTS_GRACE_SECONDS=86400`、`RUN_EVENTS_RETENTION_DAYS=30`、`CONVERSATION_PURGE_DAYS=30`、`FILE_TRASH_DAYS=30`（仅预留，文件回收站尚未实现）。保留值为 0 时关闭对应 collector；presign 即使配置更短也至少等 900 秒签名期加 reaper 宽限期。原 `WORKSPACE_TTL_DAYS=3` 与 `WORKSPACE_CLEANUP_INTERVAL_SECONDS=3600` 保持。`BACKUP_TIMEOUT_SECONDS=3600` 约束快照/复制；`PROVISIONED_STORAGE_BYTES=0` 表示容量未知，不警告。

`lifecycle_deletions` 保存事务内对象删除意图，物理删除失败保留任务重试。`ailab_gc_objects_total{kind}` 和 `ailab_gc_errors_total{kind}` 只使用固定 kind；事件清理只针对终态且 finished_at 超保留期的 Run，summary/result 留存；软删会话分批清理 runs/events/usage/artifact 关系，资料库 FileObject 继续保留。新增 outbox/checkpoint/usage 保留策略需由合并负责人整合外键与长期账本要求，不能机械删除新增表。

LocalStorage 的 prefix 分页用 scandir + 有界 heap，内存 O(limit + 路径深度)，每页仍需遍历对应前缀子树；它不是数据库索引，百万对象应实测 CPU/时延并考虑 S3。游标保存在进程中，重启重新扫描；同一 tick 物理删除总对象数受 batch 限制，收集多个 kind 各自有界。跨副本通过队列锁减少重复任务，删除本身幂等；锁过期后重复扫描仍可能产生唯一约束冲突，该事务回滚后下轮重试，不会丢失已提交意图。LocalStorage 在进程被 kill 时可能留 `.metadata/*.tmp` 原子写临时文件，当前需在停写维护窗口清理；不要将隐藏目录当作用户对象直接导出。

# 单机备份与恢复

`ailab_platform.ops.backup` 生成可离线校验的数据库与对象快照。它不是高可用或跨机自动复制；跨数据库和对象存储没有分布式快照。部署和生命周期设置见 [部署模块](../../modules/deploy.md)。

在 `platform/` 下，用与目标环境相同的数据库、对象存储和 `AILAB_ENV_FILE` 运行：

```bash
python -m ailab_platform.ops.backup create --target /backup/snapshot
python -m ailab_platform.ops.backup verify /backup/snapshot
python -m ailab_platform.ops.backup restore /backup/snapshot --into /restore/ailab
python -m ailab_platform.ops.backup verify /restore/ailab
```

目标目录必须为空；`backup.json` 只在全部校验通过后写入，失败留下的目录不是有效备份。SQLite 用 online backup API，保留 WAL 中已提交的事务；PostgreSQL 在 REPEATABLE READ 下导出 snapshot，并用同一 snapshot 执行 `pg_dump`。`pg_dump` 必须由宿主机提供并与服务端版本兼容，应用镜像不含该工具；密码只通过子进程环境传递。

备份包含数据库快照、不可变主文件及其 size/sha256 引用、`derived/`、`previews/`、有效工作区、脱敏配置和 `models.toml`。对象按块复制，Local 和 S3 都生成独立清单；恢复材料包含对象副本，不依赖原 bucket。未完成的 presign 不算可恢复文件，已过 TTL 且不存在的工作区允许省略。`verify` 校验清单、元数据、逐对象长度/哈希和引用，SQLite 额外检查完整性、外键和 `files` 表。校验不是签名，备份目录只能由受信运维写入。

## 一致性窗口

备份期间暂停文件删除、GC、Worker 派生写入和 Broker 预览/工作区写入，停止新的写请求并让在途写结束；SQLite 的读请求可以继续，数据库 online snapshot 本身不需要停机。未停写时，如果快照引用的对象在复制前被删除或修改，备份会失败或哈希不符，不能把该目录发布为成功备份。运行中的派生资源和预览不承诺与数据库处于同一时刻。

## 恢复

SQLite：把 `AILAB_DATABASE_URL` 指向 `/restore/ailab/platform.db`，把 `AILAB_STORAGE_ROOT` 指向 `/restore/ailab/storage`，把 `AILAB_DATA_DIR` 指向 `/restore/ailab`，把 `AILAB_MODELS_FILE` 指向恢复目录的 `models.toml`；注入新的服务凭据和模型 Key 后启动 API，再启动其他服务。脱敏配置不是可直接使用的 `.env`，不能沿用旧的开发默认值。

PostgreSQL：`restore` 只把 SQL 和对象暂存到空目录，不覆盖现有数据库。运维创建新的空库后执行：

```bash
psql -v ON_ERROR_STOP=1 -d NEW_EMPTY_DATABASE -f /restore/ailab/database.sql
```

然后把服务连接指向新库。不要向旧库直接导入。S3 可先以 LocalStorage 验收，再按 `objects.jsonl` 的 key 上传到新 bucket 并重新核对哈希。离线 verify 只能证明 dump 哈希和快照引用；SQL 中的引用要在新库导入后再次演练确认。

## 成本与目标

LocalStorage 以 64 KiB 块顺序复制，不使用可被原文件修改破坏的硬链接；S3 写入用单对象大小的临时磁盘 spool，关闭多线程 multipart。备份和恢复各需要一份完整对象容量的磁盘。索引在临时 SQLite 中，清单为 JSONL，RAM 不随对象总量线性增长；耗时约为总字节数除以实测磁盘/网络吞吐，需同时观察 CPU、RSS、IO 和磁盘余量。

RPO 由最近一次已校验且已异机/异介质复制的备份决定；只放在同一块盘上不能防主机或磁盘丢失。可信试点可每晚备份并保留至少 30 天，明确接受最多 24 小时丢失；需要 5 分钟 RPO 时应另做 PostgreSQL WAL/PITR 和对象备份，当前 CLI 不提供该承诺。RTO 包括准备机器、下载、校验、复制/导入、启动和下载/对话验收，应在实际数据规模测量后设定。每季度和升级前做恢复演练。

## 生命周期默认值

| 变量 | 默认 | 作用 |
|---|---:|---|
| `AILAB_GC_BATCH_SIZE` | 100 | 每轮最多处理的删除/清理对象数（1–1000） |
| `AILAB_GC_INTERVAL_SECONDS` | 3600 | 完整清理周期 |
| `AILAB_PRESIGN_STAGING_TTL_SECONDS` | 86400 | 未完成 presign 的保留时间；0 关闭 |
| `AILAB_ORPHAN_OBJECTS_GRACE_SECONDS` | 86400 | 无主派生/预览对象的宽限期；0 关闭 |
| `AILAB_RUN_EVENTS_RETENTION_DAYS` | 30 | 终态 Run 事件保留期；0 关闭 |
| `AILAB_CONVERSATION_PURGE_DAYS` | 30 | 软删会话的清理延迟；0 关闭 |
| `AILAB_TRASH_DAYS` | 30 | 资料库回收站保留期（1–3650，不能设为 0） |
| `AILAB_WORKSPACE_TTL_DAYS` | 3 | 工作区快照保留期；0 表示全部保留 |
| `AILAB_WORKSPACE_CLEANUP_INTERVAL_SECONDS` | 3600 | 工作区快照扫描周期 |
| `AILAB_BACKUP_TIMEOUT_SECONDS` | 3600 | 快照和对象复制总时限 |
| `AILAB_PROVISIONED_STORAGE_BYTES` | 0 | 实际对象容量；0 表示未知，不告警 |

presign 清理即使配置更短，也至少等待 900 秒签名期加 reaper 宽限期。删除意图写在 `lifecycle_deletions`，物理删除失败下轮重试；`ailab_gc_objects_total{kind}` 和 `ailab_gc_errors_total{kind}` 只使用固定 kind。新增表若要接入清理，必须先处理外键、outbox、checkpoint 和长期账本引用，不能机械删除。

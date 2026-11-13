# 真实后端契约

这里的测试用真实 Redis、PostgreSQL、S3、Docker/OpenSandbox、ClamAV 或 OIDC 验证适配器，不替代普通单元测试。未配置对应后端时测试干净跳过；CI 中被选中的组不允许用跳过冒充通过。

从 `platform` 目录运行，先看单组结果：

```bash
.venv/bin/pytest tests/contracts/test_storage.py -ra
.venv/bin/pytest tests/contracts/test_queue.py -ra
.venv/bin/pytest tests/contracts/test_database.py -ra
.venv/bin/pytest tests/contracts/test_backup.py tests/contracts/test_lifecycle.py -ra
```

| 组 | 主要文件 | 后端与变量 |
|---|---|---|
| 对象存储 | `test_storage.py` | S3；`AILAB_TEST_S3_URL`、`AILAB_TEST_S3_ACCESS_KEY`、`AILAB_TEST_S3_SECRET_KEY` |
| 队列 | `test_queue.py`、`test_queue_health.py` | Redis；`AILAB_TEST_REDIS_URL` |
| 数据库与迁移 | `test_database.py`、`test_lifecycle.py` | SQLite；PostgreSQL 另用 `AILAB_TEST_POSTGRES_URL` 的私有 schema |
| 账本与备份 | `test_ledger_database.py`、`test_backup.py` | 一次性 PG；`AILAB_TEST_DATABASE_URL` / `AILAB_TEST_POSTGRES_URL` |
| 沙箱 | `test_sandbox_lifecycle.py`、`test_sandbox_transfer.py`、`test_controlled_fetch.py` | Docker 或 OpenSandbox；见下方 |
| 文档运行时 | `test_document_reader.py`、`test_office_production.py` | 指定镜像；`AILAB_TEST_DOCUMENT_IMAGE` / `AILAB_TEST_OFFICE_IMAGE` |
| 扫描与身份 | `test_clamd.py`、`test_keycloak_identity.py` | `AILAB_TEST_CLAMD`；`AILAB_TEST_OIDC_*` |

`platform/tests/contracts/conftest.py::engine` 为 PostgreSQL 建唯一临时 schema，结束后 `DROP ... CASCADE`；S3 用例使用唯一临时 bucket。只能连接一次性服务，绝不能指向开发库或生产对象存储。备份演练还需要在该一次性 PG 上创建和删除数据库的权限。

## 沙箱与联网

Docker、OpenSandbox 和真实公网下载都要求显式选择后端和一次性镜像：

```bash
AILAB_TEST_DOCKER=1 \
AILAB_TEST_OSB_URL=http://127.0.0.1:<osb-port> \
AILAB_OSB_NETWORK_MODE=kubernetes \
AILAB_OSB_USE_SERVER_PROXY=true \
AILAB_SANDBOX_IMAGE=<non-root-image> \
AILAB_TEST_PUBLIC_FETCH=1 \
.venv/bin/pytest tests/contracts/test_sandbox_transfer.py tests/contracts/test_sandbox_lifecycle.py \
  tests/contracts/test_controlled_fetch.py -q
```

上面的示例使用集群侧 `kubernetes` 模式，要求策略已经验收。本机 Docker/runc 的 OpenSandbox 服务改用 `sidecar`；不要在无策略的情况下自动重试。`AILAB_TEST_PUBLIC_FETCH=1` 才会实际下载公开站点、GitHub 归档和离线 PyPI 包。失败时先确认网络策略和镜像，不重试成“通过”。

`test_sandbox_network.py` 还要求实际可达的 `AILAB_TEST_SANDBOX_TCP_TARGETS`（逗号分隔 `IPv4:port`）与 `AILAB_TEST_SANDBOX_UDP_TARGET`。先用干净客户端验证目标存活，再从沙箱验证拒绝；已失效的地址不能证明隔离。该测试只覆盖 IPv4。

Docker 下受控联网需要 `[egress] mode = "dns+nft"`；只有 DNS 过滤会放过直接连接字面量 IP。不要复用同一个 `broker_owner` 给两个共享 daemon 的 Broker reconciler。

## 验证边界

`test_gc_queries_have_indexed_plans` 和 `test_ledger_queries_use_bounded_index_ranges` 证明查询能使用索引，不是生产规模延迟基准。后端契约证明协议适配与清理，不自动覆盖五服务浏览器链路、真实模型质量、目标机容量或灾备 RPO/RTO。

容量耗尽场景用一次性容器 tmpfs，不填宿主磁盘：

```bash
DOCKER_CONTEXT=<owned-context> AILAB_TEST_STORAGE_DOCKER_IMAGE=<pinned-image> \
  .venv/bin/pytest tests/contracts/test_storage_disk_full.py -q
```

CI 对必验组执行 `.github/scripts/verify_junit.py`；哪些组必须实际运行、S3 唯一允许的 local inode 跳过，以及重型组件分片见 [CI](../../../.github/CI.md)。新增契约组的方法见[测试](../../../docs/modules/testing.md#加一个真后端契约测试)。

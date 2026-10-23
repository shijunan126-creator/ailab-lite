# 2026-10-23 · 阶段成果集成与进度接续

## 目标与版本

- 用户要求：拉取最新 main，与现有成果合并，整理持续进度与 Agent 接续规则后提 PR；未要求远端合并或恢复剩余功能。
- 工作树 `/Users/michael/Developer/ailab-lite`，分支 `feat/arch-hardening`。起点 `9b49bd2`，拉取 main `951ec16a99b2efbb3eb5f72ff6d9ce44cd97df19`，merge commit `6f7e377476e98b2161617c655b513599ce11e71b`。
- 先前各实现分支已在起点内集成，不重复合并。现有未跟踪 `.DS_Store` 保留；已有 Portal 生成的 `AGENTS.md` 纳入版本控制，便于新 clone 遵循 Next.js 本地指南。

## 本轮结果

- 保留 main 的联网检索代码；把旧单文件部署里的 SearXNG 迁入 `platform/deploy/base/search/`，显式配置 Service 地址，default-deny 下仅 Agent 能访问。保留新 gateway/凭据拆分，不复活旧清单；发布等待覆盖 8 个 Deployment，代码升级不额外重启 SearXNG。
- 回归 `tests/test_web_deployment.py` 在缺少搜索配置的集成前树上失败（KeyError），补齐资源/地址/策略后与 6 项 web 用例一起通过。
- 新增 progress 索引、当前状态、路线图和本记录；复用 status-matrix、design、reviews、ADR 与 HANDOFF，明确各自职责。AGENTS 要求开始先读、交接前更新、核对真实分支与证据，并持续遵守用户暂停指令。
- 修正“两个并发必然 OOMKill”的过度表述：现有证据为单次测量与并发风险推算，尚未复现并发 OOM。

## 本轮验证

代码基线为 `6f7e377`；其后的本轮改动为文档与生成的 Portal AGENTS。测试使用独立临时目录、PostgreSQL 16 端口 15423（`ailab_test` / `ailab_migrations`）、Redis 16423（契约库 15 / E2E 库 13），未使用开发数据库。macOS PATH 前置 GNU coreutils/findutils。

| 检查 | 结果 |
|---|---|
| `cd platform; .venv/bin/ruff check .` / `ruff format --check .` | 通过，196 个文件格式检查 |
| `.venv/bin/pytest tests -q`（SQLite） | 693 passed / 77 skipped；真实外部 gate 未配置，不算通过 |
| 临时 PostgreSQL 16 + Redis 全量 Python | 755 passed / 15 skipped；未配置的 S3/ClamAV/Keycloak/真实沙箱等外部 gate 仍跳过 |
| `AILAB_E2E=1 AILAB_TEST_REDIS_URL=redis://127.0.0.1:16423/13 .venv/bin/pytest e2e -q` | 41 passed；fixture 在隔离副本执行 Portal `npm test` 和 `npm run build`，使用真实 Chromium、mock 模型/沙箱 |
| `bash scripts/lock_deps.sh --check` | 通过 |
| `shellcheck -S warning deploy.sh tests/*.sh platform/scripts/*.sh` / `bash -n deploy.sh` | 通过 |
| `kubectl kustomize platform/deploy/overlays/{dev,lite}`（分别执行） | 通过，dev 34 / lite 32 个资源；另核对检索地址、Service、NetworkPolicy、gateway 与 scanner |
| 本轮文档相对链接 / `git diff --check` | 通过 |

PostgreSQL 命令在上述 Python 命令前指定 `AILAB_TEST_REDIS_URL=redis://127.0.0.1:16423/15`、`AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15423/ailab_test`、`AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15423/ailab_migrations`。本轮完整终端日志临时保存在 `/tmp/ailab-main-integration-20261023.qKGla3`；持久结论以本文为准。临时 PostgreSQL/Redis 已关闭，15423/16423 无监听。既有 Starlette/httpx 弃用警告仍在，本轮无新增运行时依赖。

## 既有真实组件证据与边界

此前专用 ARM64 k3s 2 vCPU / 8 GiB 的 ClamAV、MinIO、PG16/Redis、Keycloak、Docker/OSB、Office、网络/Secret 和备份验收见 [真实组件报告](../../reviews/2026-10-23-real-components.md)。这是历史版本证据，本轮未重启 VM，也不代表新增 SearXNG 或合并后全栈的真实负载验收。

保留问题：Broker 整文件缓冲、CI MinIO 镜像源、SearXNG 镜像/密钥与目标机验收、lite 公网宿主防火墙、长期并发与企业集成。具体下一步见 [路线图](../roadmap.md)。旧报告中的 Redis health pool “最多1连接”描述的是后续修复前版本；当前 `826899c` 已去掉池上限，保留短超时与无重试，并发探针有回归。

## 发布与接续

PR 发布后补链接与远端状态核对时间。剩余功能继续暂停；下一 session 先看 [current.md](../current.md)，重查 PR / CI，不能从旧设计自动开工。

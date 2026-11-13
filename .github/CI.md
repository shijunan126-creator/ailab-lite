# CI

主工作流是 `.github/workflows/ci.yml`。PR 按 `base...head` 的完整 diff 选择套件，`main` push 与手动运行总是全套。稳定检查名是 `CI`，供分支保护使用；仓库的实际保护规则由维护者配置。

本地跑什么、各套件覆盖什么以及验收口径见[测试](../docs/modules/testing.md)。

## 范围选择

`.github/scripts/ci_scope.py` 输出 `code`、`backend`、`e2e` 三个标志：

| PR 改动 | 运行 |
|---|---|
| 白名单 Markdown（`docs/**/*.md` 与指定 README/规则文件） | 只跑选择器测试与 `CI` 汇总 |
| 仅 `platform/portal/**` | `quality`、`shell`、`crawler`、`e2e` |
| 后端、共享契约、依赖、部署、工作流、未知路径 | 全部 |

选择器使用 merge base，并关闭 rename detection：移入或移出 `docs/` 的两端都会参与判断。`docs/` 下的脚本、配置、JSON 与非 Markdown 文件仍按代码处理；未知路径一律保守执行全套。白名单细节以 `.github/scripts/ci_scope.py::DOC_FILES` 与路径判断为准。

`changes` job 永远先执行选择器单测和 `ci_scope.py`。选择器失败时不会降级成“跳过”，`CI` 汇总也会失败。

## 套件与门禁

各 job 的命令在 `.github/workflows/ci.yml`，测试分层与时限表见[测试](../docs/modules/testing.md#ci-验收)。CI 特有的门禁如下：

- `quality` 从生产 extras 建一个干净 venv 并导入服务，避免开发依赖掩盖运行时缺包。
- `python` 的 SQLite 分片跑覆盖率，`platform/pyproject.toml::tool.coverage.report` 要求 `ailab_platform` 行覆盖率至少 80%；PostgreSQL 分片跑全套。
- `python` 与 `storage-contracts` 产出 JUnit，`.github/scripts/verify_junit.py` 要求真实队列、数据库/账本/备份/迁移和 S3 组实际执行且成功。缺报告、没有匹配用例、`skip` 或 `xfail` 都失败；只有明确列出的 local inode 用例允许跳过。
- `ci` job 把 `toJSON(needs)` 交给 `.github/scripts/ci_gate.py`。它检查 job 集合完整、范围合法、选中 job 成功、未选中 job 恰好跳过；失败或取消不能被吞掉。

`fail-fast` 只影响同一矩阵的其余分片，不影响汇总门禁。PostgreSQL 服务对一次性测试库关闭 `fsync` 等持久化选项，查询计划与锁语义不变。

## E2E 与真实资源

`e2e` 用 `AILAB_E2E_SHARD=k/3` 按用例 id 的稳定哈希分三片，每片启动自己的栈并构建一次 Portal 生产包；Portal 单测只在第 1 片跑一次。失败保留 `platform/e2e-logs/` 中的 trace 与截图，成功丢弃 trace，产物保留 7 天。

浏览器依赖下载按 runner 镜像版本缓存。只调整安装/缓存，不改变用例断言；每片仍受 20 分钟上限约束。

存储契约使用 `platform/scripts/ci_minio.sh` 从固定源码构建 MinIO，并校验归档 SHA256。缓存 key 含 OS、架构和脚本哈希；更新版本或校验和时必须同时验证冷缓存与真实 S3 契约。

## 重型任务与审计

`.github/workflows/real-components.yml` 在自身、fixture、等待脚本、Office 镜像或办公运行时变更时运行，也支持每周定时和手动运行。四个独立分片覆盖：

| 分片 | 真实边界 |
|---|---|
| `docker` | Docker/Redis 生命周期、续租、恢复与流式传输 |
| `office` | 实际 Office 镜像、断网 smoke、渲染与技能快照 |
| `clamd` | 固定 ClamAV 镜像的协议、干净文件与 EICAR |
| `keycloak` | 浏览器授权码 + PKCE、登出与禁用账号 |

分片使用一次性 fixture 和独立 runner，Office 上限 45 分钟，其余 25 分钟；选中用例必须执行成功且零 skip。失败仍收集服务日志、资源快照并清理容器。普通业务 PR 不启动这些重型依赖。

`.github/workflows/audit.yml` 每周扫描 Python 固定依赖和 Portal 全部依赖；它不与 PR 主链路共享状态，每个 job 上限 10 分钟。

OpenSandbox/gVisor、多架构 Office、真实 2 vCPU/8 GiB 联合负载、付费 vision、企业 IdP 与备份 RPO/RTO 仍需专门环境验收。单组件契约或普通 CI 的 mock 结果不能替代这些结论。

## 修改 CI

改选择器、门禁或工作流后，至少运行对应的本地检查：

```bash
python3 -m unittest discover -s .github/tests
cd platform
.venv/bin/ruff check --config pyproject.toml . ../.github
.venv/bin/ruff format --config pyproject.toml --check . ../.github
.venv/bin/python scripts/check_imports.py
```

Shell 或 workflow 变更还要跑 `actionlint`、ShellCheck 和 Kustomize 渲染，命令以 `.github/workflows/ci.yml` 的 `shell` job 为准。修改测试范围或门禁后，实际查看目标 head 的 Actions 结果；本地单测通过不能证明 GitHub runner 上已通过。

不要把 PR 代码放到 `pull_request_target` 下执行，也不要为测试增加生产凭据。新增外部依赖或重型 fixture 时，在 PR 的成本与部署一节说明 CPU、内存、磁盘、下载量和新增 CI 时间。

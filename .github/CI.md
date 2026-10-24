# CI

`CI` 工作流跟随当前单机架构，入口在 `workflows/ci.yml`。任务选择依据整个 PR（base...head），不是最后一次提交；main push 和手动运行总是全套。

| 改动 | 验证 |
|---|---|
| 纯文档白名单 | 选择器测试 + 最终 CI |
| 仅 Portal | quality、shell/部署静态检查、E2E（含 Node tests 与隔离生产 build） |
| 后端/契约/依赖/部署/工作流/未知路径 | quality、SQLite + Redis coverage、PG16 + Redis 全套/迁移、真实 MinIO S3 契约、shell、E2E |

只取消同 PR 被新提交替代的运行；main 不取消运行中的验收。双库 fail-fast=false，存储契约独立，便于一次拿到完整诊断。每个 job 有截止时间，JUnit/coverage 与服务日志保留 7 天。

稳定汇总检查名为 **CI**，应作为分支保护的 required check；分组 job 因范围跳过是正常结果。最终检查仍会拒绝失败、取消、选择器错误和选中任务意外跳过。仓库保护设置由维护者配置，本轮没有修改。

MinIO fixture 来自固定官方源码，构建脚本 `platform/scripts/ci_minio.sh` 验证归档 SHA256，用 Go 1.24.2；缓存按 OS/架构/脚本哈希隔离。更新版本时同步版本与校验和，先验冷缓存，再验真实 S3 契约，不用跳过测试规避源失效。首次编译较慢，热缓存直接启动二进制。fixture 账号仅用于一次性 loopback 测试，不连接任何生产服务。

每周真实组件与依赖审计独立于 PR 主链路：Docker、Office、ClamAV、Keycloak 使用一次性 fixture，必须实际执行；未配置的外部 OpenSandbox 等本地 gate 仍会显示 skip，不能视为已通过。参见 `platform/tests/contracts/README.md` 与 `docs/reviews/2026-10-23-real-components.md`。

修改 CI 先跑：

```bash
python3 -m unittest discover -s .github/tests
actionlint
shellcheck -S warning deploy.sh tests/*.sh platform/scripts/*.sh
cd platform
.venv/bin/ruff check --config pyproject.toml . ../.github
.venv/bin/ruff format --config pyproject.toml --check . ../.github
.venv/bin/python scripts/check_imports.py
```

修改 job 或范围后，还要实际查看 Actions 对应 head 的结果。不要切换到 pull_request_target 执行 PR 代码，或给测试添加生产凭据。

## 门禁与覆盖完整性

- 文档优化只信任明确的 Markdown 白名单；`docs/` 中 Python/Shell/配置等仍跑全套。整个 PR 的增加、删除、重命名两端均参与判断；未知路径保守执行。
- `ci_gate.py` 独立验证范围输出、完整 job 集合与终态；选中的 job 必须 success。门禁的 unittest 穷举三种有效范围下的 3,072 组任务终态组合。
- `verify_junit.py` 要求真实 Redis、PostgreSQL 数据库/账本/迁移/备份和 S3 的目标组实际执行并成功。JUnit 缺失、没有匹配用例、skip/xfail 都失败；S3 仅允许明确的 local inode 用例跳过，不能让整组跳过。
- SQLite 统计全部 `ailab_platform` 行覆盖，最低 80%；当前修改基线约 85%，不是 100% 行/分支覆盖。pytest 对未知配置和 marker 严格报错。
- quality 在全新 venv 安装生产镜像使用的 extras，导入四个服务；开发依赖不能替运行时补包。约增加一次缓存 pip 安装与导入，不增加线上后台任务。

## 浏览器失败诊断

E2E 仍只构建一次 Portal。`portal-tests.log`、`portal-build.log` 和服务日志流式落盘，构建失败或超时也可下载。每个浏览器 context 记录 trace，成功立即丢弃，失败保存 `failure-<node-id-hash>.zip` 和视口截图；用 `playwright show-trace` 查看。仅使用隔离账号、离线模型与一次性数据，产物保留 7 天，不能指向真实用户环境。trace 是测试侧开销，不改变线上包；本机 61 项 E2E（含生产构建）约 158 秒，不代表 GitHub runner 或目标机性能。

## 重型与外部边界

`Real component contracts` 每周二 04:41 UTC 或手动运行，四个隔离 runner 分别验证：

| 组件 | 自动契约 | 资源与边界 |
|---|---|---|
| Docker + Redis | 生命周期、续租/过期、取消、流式限额/符号链接、Broker 重启/Redis 丢失/外部删除 | 最小 Python fixture；每个沙箱执行现有 CPU/内存限制，不含 Office/skills/runsc |
| Office 镜像 | 构建实际镜像、PPT/XLSX 生成/中文字体/LibreOffice/PDF 渲染、断网、skills attestation 与快照恢复 | smoke 限制 1 CPU / 2 GiB / 600 秒，记录峰值与渲染耗时；runc 单架构，非 gVisor/多架构证明 |
| ClamAV | 协议就绪、干净内容、EICAR | 与部署相同镜像 digest；容器内存上限 2 GiB，签名下载可能使任务失败 |
| Keycloak | 浏览器授权码 + PKCE、登出、平台禁用账号 | 固定版本 26.6.4；一次性 realm/public client；容器内存上限 1 GiB |

Office job 总截止 45 分钟，其余每 job 25 分钟、组件就绪最多 600 秒；JUnit 必须有成功用例且零 skip。失败仍收集服务日志/资源快照并清理容器。此工作流不在 PR 必需路径重复拉取 ClamAV 签名和启动 JVM。每周依赖审计独立，Python constraints 与 Portal 全部依赖（含 dev）均检查，每 job 截止 10 分钟。

OpenSandbox/gVisor、Office/skills 多架构组合、真实联合负载/2 vCPU 8 GiB 容量、付费模型/vision、企业 IdP、备份 RPO/RTO 仍按 `platform/tests/contracts/README.md` 和 `platform/tests/load/` 在专门环境验收；普通 CI 的 mock 或单组件绿不能代替这些结论。新增重型工作流必须在 Actions 对实际 head 运行后才称为远端通过。

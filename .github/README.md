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

人工真实组件验收与每周依赖审计独立：未配置的 ClamAV、Keycloak、Docker/OSB 等 gate 会显示 skip，不能视为已通过。参见 `platform/tests/contracts/README.md` 与 `docs/reviews/2026-10-23-real-components.md`。

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

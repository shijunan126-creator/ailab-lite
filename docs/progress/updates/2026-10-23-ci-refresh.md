# 2026-10-23 · CI 跟随项目结构更新

## 目标与范围

用户要求优化 CI 本身，核对它是否跟上当前项目；不是单独修复 PR 的业务功能。基线 `feat/arch-hardening@e9cdf14`，继续更新 [PR #30](https://github.com/shijunan126-creator/ailab-lite/pull/30)。剩余业务功能仍暂停。

## 问题与调整

| 旧配置的问题 | 调整 |
|---|---|
| 文档也启动全套服务，连续推送重复跑 | 按整个 PR 的三点 diff 选择任务；同 PR 新提交取消旧运行。main push / 手动运行始终全套 |
| SQLite、PG 同一 job 串行，SQLite 失败使 PG 和下游 S3 都跳过 | 双库 matrix，fail-fast=false；S3 独立，不再依赖 Python 测试结果 |
| Portal 单独 build，E2E fixture 又 test/build 一遍 | 保留 E2E fixture 作为 Node 测试、隔离生产构建及浏览器验证的统一入口，移除重复 build job |
| 新的服务边界规则没有进入 CI | quality 检查加入 `check_imports.py`，并检查工作流辅助脚本 |
| S3 契约固定了失效的 Docker Hub MinIO tag | 固定官方源码版本和 SHA256，Go 1.24.2 构建真实 MinIO；按 OS/架构/构建脚本哈希缓存二进制。冷缓存有编译开销，热缓存跳过 Go 安装和编译 |
| 各 job 缺少截止时间、JUnit 与持久结果 | 所有 job 有 timeout；Python/S3/E2E 保留 JUnit，SQLite 保留 coverage XML，失败服务日志与结果保存 7 天；输出慢测试和 skip 原因 |
| 分组后 required checks 容易漏等或跳过 | 新增稳定汇总检查 `CI`：要求所有选中的 job 成功；选择器失败、空输出、失败/取消/意外跳过均不能绿。建议维护者将此检查设为 branch protection 的 required check |
| Linux coverage 下“1ms 超时”测试受父进程调度影响 | 用真实阻塞子进程验证 watchdog 超时及 SIGKILL/reap；不放宽生产时限、不跳过故障覆盖 |

原始运行 [39923111079](https://github.com/shijunan126-creator/ailab-lite/actions/runs/39923111079)：Python 约 151 秒（SQLite 失败，PG 未跑），E2E 约 129 秒，重复 Portal job 约 24 秒；S3 因依赖被跳过。新结构减少重复与依赖阻塞；双库并行会增加同时使用的 runner 数，不声称所有 PR 的总 runner-minutes 都下降。

## 选择与保留的覆盖

- 文档白名单：`docs/` 与明确列出的开发/部署 README、AGENTS、HANDOFF；只执行变化选择器自检与最终 `CI`。不是所有 `.md` 都跳过，沙箱 Skill Markdown 仍触发全套。
- 仅 `platform/portal/`：quality、shell/部署静态检查、完整 E2E（包含 Node tests / build）；不启动双库和 MinIO。
- 后端、契约、依赖、部署、工作流、未知路径：全套。重命名按删除+新增处理，不漏源路径；Git diff 失败直接失败。
- main push / workflow_dispatch：无条件全套；没有 workflow 级 paths-ignore，因此纯文档也有明确结束的汇总检查。
- 真实 ClamAV、Keycloak、Docker/OSB、Office 和目标机负载仍按现有契约 gate 与人工验收流程执行；本轮不把它们冒充成每次 PR 已验证。依赖安全审计仍沿用每周/手动 workflow。

## 验证与接续

本地 actionlint（含表达式/ShellCheck）、Ruff、import 边界、scope 的 5 项测试（含真实 Git 仓库的“代码提交后再补文档”场景）、故障测试均通过。官方 MinIO 源码归档 SHA256 已实际下载核对；冷/热缓存及 Linux 服务运行须以远端结果为准。

本轮 SQLite + 临时 Redis + coverage 全套：727 passed / 43 skipped，98.60 秒；真实外部 gate 按配置跳过，未接开发库。临时 Redis 16424 已关闭。严格 SIGKILL 断言补充后另复验故障测试 2 passed。Portal/浏览器没有源码变更，前轮 41 项与生产构建证据复用，首轮新工作流仍会重新验证。远端首轮结果待完成后补充。下一 session 从 [current.md](../current.md) 接续，重查最新 head 的 CI；不得用本地 YAML 检查代替 Actions 验收。

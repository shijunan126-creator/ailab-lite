# 2026-10-24 CI 与测试完整性

## 授权、基线与隔离

用户授权优化 CI/测试并调用一个 GPT‑6.1 Sol medium 子代理。独立工作树 `/Users/michael/Developer/ailab-lite-ci-test-completeness`，分支 `fix/ci-test-completeness`，base/HEAD/origin/main 均为 `1a384558b4ac27deea660e5314d82eddf462b386`；验证对象为此基线加本轮未提交 diff。原工作区 `fix/k3s-osb-network-path` 的已有修改未移动、stash 或覆盖。旧 current.md 仍停在 PR #33 后，实际 HEAD 已含 #34；不以旧记录推断当前部署或迁移状态。本轮不部署、提交、推送或创建 PR。测试初始工作树在 `/tmp`，验证后用 `git worktree move` 移至上述持久目录，代码未变。

子代理通过 T3 使用 `codex / gpt-6.1-sol / reasoningEffort=medium / serviceTier=default`，负责范围选择与门禁及其回归；父代理负责接线、真实契约防跳过、E2E 诊断、组件工作流、隔离验证与交接。

## 结果与成功标准

- 完整 PR diff 的增加/删除/重命名都参与选套；只有受信 Markdown 可省略重型测试。旧选择器运行新回归出现 10 个失败；修复后通过。
- 稳定 CI 汇总门禁提取为可测脚本，拒绝缺失/无效 job、无效范围、失败、取消和选中却跳过；穷举 3,072 组有效范围/终态组合。
- 真实 Redis、PG 数据库/账本/迁移/备份、S3 契约必须在 JUnit 中实际成功；缺失报告、无匹配、skip/xfail 不能冒充通过。只保留 S3 不适用的 local inode 单项白名单。
- pytest 严格配置/marker，行覆盖率底线从 70% 提高到 80%，最终实测 85.11%。没有声称 100% 行/分支覆盖。
- quality 在全新 venv 安装镜像使用的生产 extras，导入四个服务，不让 dev 依赖掩盖启动缺包。
- E2E 构建/Node 测试输出流式落盘；失败留 trace/截图，成功丢弃 trace。修复 readiness 把任意 404 当就绪，以及 kill 后不 wait 的清理问题。两个 harness 回归修复前失败、修复后通过；故意失败的真实 Chromium 探针验证 trace 与截图确实保存，临时探针文件已删除。
- 修复既有配置脱敏测试的 macOS 路径误报：`private` 同时是测试密码和正常 `/private/` 目录片段。现在精确验证敏感字段和 URL 的脱敏值，保留正常路径，不放宽产品脱敏。
- ShellCheck/语法扫描覆盖全部 tracked `.sh`（含镜像 smoke 与历史脚本），额外按 POSIX sh 检查镜像 smoke；Kustomize 三套保持必验。
- 新增每周二 04:41 UTC/手动真实组件工作流：最小 Docker/Redis 故障恢复、完整 Office 镜像/断网渲染/skills 快照、ClamAV、浏览器 Keycloak。独立 runner，选中用例零 skip，失败保留日志/资源快照并清理容器。
- 每周依赖审计有总超时；Portal 审计包括 dev 依赖。生成的 build/test-results 不入 Git。

## 本轮验证

最终检查已通过：CI unittest 28 项；Ruff check/format；actionlint 1.7.12；ShellCheck；三套 Kustomize；bash syntax；依赖锁一致性；skills manifest/hash；结构 golden；干净生产依赖导入；Portal 19 项 Node tests 和 build；生产构建/真实服务 Chromium E2E 61 passed，158.41 秒；故意失败探针产生一份 trace 和截图。

初轮 SQLite：961 passed / 59 skipped / 1 failed，85.13%；PG16：995 passed / 25 skipped / 1 failed。两次失败都是上面的既有 `/private/` 脱敏测试误报，不是通过证据；修复后定向三项通过，并在同一最终代码 diff 上顺序重跑双库，结果为 SQLite **962 passed / 59 skipped / 0 failed，187.50 秒，85.11% 行覆盖**；PostgreSQL 16 **996 passed / 25 skipped / 0 failed，161.86 秒**。Redis/PG 的 JUnit 必验组分别确认：队列 4、PG 数据库 3、账本 3、备份 1、迁移 40 项成功。Portal 全部依赖 npm audit：0 漏洞。临时 Redis/PG 已关闭。Redis/PG 均为本轮独立 loopback 实例，数据库只用于测试，未读取开发者 `.env`。

## 成本与边界

线上没有新增依赖、后台任务或轮询。PR quality 增加一次缓存 pip 安装与服务导入，ShellCheck 增加两份既有脚本，JUnit 检查是报告大小的线性扫描。浏览器每测试记录 trace，仅失败保留；本机全套含 build 约 158 秒。重型镜像/签名/JVM 留在每周/手动 runner：Office smoke 1 CPU/2 GiB/600 秒，Office job 45 分钟，其余 job 25 分钟；ClamAV 2 GiB、Keycloak 1 GiB，上述是资源限制，不是目标机实测。

本轮 Docker daemon 不可用：新增四种真实组件 job、MinIO S3 未在本机真实重跑，远端 Actions 尚未运行；不得称为远端 CI 全绿。OpenSandbox/gVisor、多架构、付费模型/vision、企业 IdP、目标机联合负载及长期容量仍需专用环境。历史真实组件证据不充作本轮结果。操作/覆盖边界见 [.github/CI.md](../../../.github/CI.md)。

下一步：获提交/推送授权后，在实际 head 运行 PR CI 与手动 `Real component contracts`（包含冷缓存镜像），核对全部分项和稳定 CI；不得用历史绿色代替。没有自动继续路线图或部署。

## 后续授权与集成

用户后续明确授权重建已提交的新版 Portal，并提交/合并 CI PR；此前暂缓合并已解除。不删除工作树、旧镜像或业务数据。CI 分支已集成最新 `origin/main@2093dfc`，保留 #36/#37 的代码和交接内容；首轮数字仅代表此前 `1a384558 + CI diff`，集成后的远端 head 验证另记。Portal 运行配置和验收只进入本机独立 HANDOFF。

集成后的本地检查：Ruff check/format 282 文件、actionlint 1.7.12、28 项 CI unittest、全部 tracked ShellCheck 通过；PR [#38](https://github.com/shijunan126-creator/ailab-lite/pull/38) 已提交并关联当前线程，主流水线全套运行中。最新 Portal（main 2093dfc）已经独立重建并验证源文件哈希、浏览器、登录/API、文件完整性；后端运行版本不变，操作明细只记录私有 HANDOFF。下一步等待此 PR 最新 head 绿后合并；不删除分支/工作树/镜像。

首轮手动触发新增 workflow 返回 GitHub 404（尚未出现在默认分支），因此为重型工作流增加 PR 路径触发：仅工作流自身、fixtures、组件等待脚本或 Office 镜像变更才运行。本 PR 会在合并前实际验证这四项；普通业务 PR 不额外启动重型组件。

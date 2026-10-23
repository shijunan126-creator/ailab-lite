# 当前工作

更新：2026-10-24。用户授权：实现安全、完整的受控联网并真实验收、部署；更新 README；提交 PR，CI 通过后合并 main；最后委派 Codex（gpt-6.1-sol, medium）把本项目迁移到移动硬盘（APFS 分区、挂载、应对拔插、验证）。

## 已完成

- 受控联网：沙箱保持 `--network none` + gVisor，Agent 获取公开资源后经 Broker 流式注入；网页/GitHub issue、下载、仓库、PyPI 依赖、个人技能保存。决策 [ADR 0007](../architecture/adr/0007-controlled-public-fetch.md)，证据 [专项验收](../reviews/2026-10-24-controlled-network.md)，本轮记录 [更新](updates/2026-10-24-controlled-network.md)。
- 已部署到现有本机运行环境（Colima VM 内 Docker Compose），同时上线了此前未部署的 PR #32 内容和迁移 0021；runsc 根 overlay 4 GiB 上限写入 Colima profile。
- README 已更新。前序工作（PR #32）已在 main。

## 代码与证据

- 已合并到 main `ab85a9b`（原分支 `feat/safe-network-access`，base `5fc9103`）；三个未跟踪 `.DS_Store` 保留。HANDOFF 中原有一条未提交记录（PG15511 随旧 VM 删除）随本分支提交。
- 部署明细、备份位置、验收脚本只在本机独立 HANDOFF。

## 下一步

1. 已完成：[PR #33](https://github.com/shijunan126-creator/ailab-lite/pull/33) 最新 head `944d977` 的 CI 全部通过（含 e2e、双库），已合并为 main `ab85a9b`，本地 main 已快进并核对。
2. 进行中：Codex 子任务把本项目迁移到移动硬盘；结果、挂载方式与拔插行为只记录在本机独立 HANDOFF。迁移后工作树和运行环境路径可能变化，接续前先核对本机 HANDOFF 与实际挂载。

不自动开展路线图其他事项。残余风险（低带宽泄露信号、npm/私有仓库、k3s 路径未验）见专项验收。

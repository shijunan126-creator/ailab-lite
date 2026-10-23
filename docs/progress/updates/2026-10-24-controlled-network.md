# 受控联网、个人技能保存与 README 更新

2026-10-24，工作树 `/Users/michael/Developer/ailab-lite`，分支 `feat/safe-network-access`，base `origin/main@5fc9103`（PR #32 已合并）。用户授权：实现安全完整的联网能力并验证、部署；更新 README；提交 PR，CI 通过后合并；最后委派 Codex 迁移到移动硬盘。

## 本轮交付

- 沙箱外受控获取后注入（[ADR 0007](../../architecture/adr/0007-controlled-public-fetch.md)）：网页与 GitHub issue（含评论）、文件下载、公开仓库、PyPI 依赖；公网地址钉住、来源检查、运行预算、归档限额。
- 个人技能经 Chat/Work 新建（create）或按版本更新（update），外部技能只临时使用。
- 本轮未提供的工具只回错误、不执行；图片下载改用同一安全客户端；runsc 根 overlay 4 GiB 上限（运行环境配置）。
- README 重写为当前能力、架构、安全边界、部署与测试入口；platform/README 修正 Chat 模式描述。

## 本轮发现并修复

部署与真实验收中发现三处测试未覆盖的问题，均已在根因处修复并补回归：`packaging` 未声明为运行时依赖（Agent 镜像无法启动）；技能名冲突用了 fencing 专用的 409，导致 Run 崩溃；归档被拒绝时遗留部分目录。另修正离线演示模型在 Chat 提供 save_skill 后误走工作脚本。

## 验证

代码与证据见[专项验收](../../reviews/2026-10-24-controlled-network.md)。双库完整套件、Portal 测试与构建、Playwright E2E、真实沙箱、Agent 容器内边界探针、7 个真实 DeepSeek 端到端场景、内置浏览器检查均在本轮执行；最终提交的完整结果写在专项记录“最终运行”。部署前做 SQLite 备份；切换前后全部原有 ID 保留、文件哈希全部一致，迁移 0020→0021。

## 未验证与残余

低带宽泄露信号、npm/私有仓库、k3s/OpenSandbox 路径、并发联网负载、IPv6 实网未验或未消除，详见专项记录。个人部署配置、凭据、脚本与运行明细只在本机独立 HANDOFF。

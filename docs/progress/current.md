# 当前工作

更新：2026-10-23。接续时先核对实际 Git / PR 状态，本文不是实时远端状态。

## 当前目标与范围

用户最新授权：优化 CI 本身，使其跟随当前项目结构、减少重复与无关执行，并补齐验证/诊断。继续更新 PR #30，不恢复剩余业务功能。前轮 main 集成和进度体系已交付。

**剩余功能仍暂停实施**：T07/T08/T09/T14/T15/T16/T19/T20 和 F8 流式改造仅保留设计。用户本轮新增授权是 CI 更新；不要自动进入后续开发。

## 分支与交付

- 工作树：仓库主 checkout `/Users/michael/Developer/ailab-lite`，分支 `feat/arch-hardening`；base 为 `origin/main`。
- 已整合先前各功能分支及真实组件验收。最新 main `951ec16`（SearXNG 联网检索）已通过 merge commit `6f7e377` 合入本分支；采用 merge 保留祖先关系。
- 本轮补齐检索在新部署结构中的资源、Service 地址和 Agent 专用 ingress，未恢复已废弃的单文件清单。进度入口、路线图与 AGENTS 接续要求已建立。
- 已推送 `origin/feat/arch-hardening`，创建 [PR #30](https://github.com/shijunan126-creator/ailab-lite/pull/30) → `main`。2026-10-23 16:12 +08 核对：OPEN、MERGEABLE，CI 正在运行；未合入 main。后续发布跟进文档提交不改变已测代码。
- 能力逐项状态以 [状态矩阵](../architecture/status-matrix.md) 为准；本轮验证与 PR 发布记录见 [交付记录](updates/2026-10-23-main-integration.md)。已有 `.DS_Store` 保留，不纳入本轮新增提交。

## 保留的问题

- Broker 大文件仍整份缓冲：单次峰值 265–284 MiB，limit 384 MiB，并发 OOM 风险待测与改造；不是已复现的双请求 OOMKill。
- CI 已改为固定源码与 SHA256 构建/缓存真实 MinIO，首轮远端验收待完成，见 [CI 更新记录](updates/2026-10-23-ci-refresh.md)。SearXNG 新服务尚未纳入真实小机容量验收，沿用 main 的 `latest` 镜像和静态搜索服务 key，发布加固待办见路线图。
- 真实组件数据为前次专用 VM 的历史结果，本轮不重启该环境；公网防火墙、gVisor、长期并发、企业 IdP 与付费模型边界见验收报告。

## 下一步

核对 [CI 更新记录](updates/2026-10-23-ci-refresh.md) 与 PR #30 最新 head 的远端检查。处理实际集成/CI 阻塞后，由用户决定合并与何时恢复后续功能；获准开工时优先按 [路线图](roadmap.md) 阶段 1 的故障契约和性能标准执行。

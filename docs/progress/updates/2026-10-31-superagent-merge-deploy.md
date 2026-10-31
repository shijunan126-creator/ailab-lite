# Super-agent 借鉴：合并与本机 k3s 部署（2026-10-31）

用户要求：参考 DeepSeek《Super-agent 设计探索》的可用部分全部实现、分别提 PR、合并并部署；途中追加优先排查前缀缓存命中（参考 DeepSeek Harness）。

## 合并

| PR | 内容 | 合并提交 | 记录 |
|---|---|---|---|
| #114 | 用户插话打断 `exec_wait` | `db97ca9` | [记录](2026-10-31-steer-interrupts-wait.md) |
| #115 | `read_file` 分段读取 | `c24649c` | [记录](2026-10-31-read-file-windows.md) |
| #116 | 历史召回附带交付文件与相邻轮次 | `d989a69` | [记录](2026-10-31-recall-linked-ids.md) |
| #117 | 模型请求前缀稳定（缓存命中） | `05997b4` | [ADR 0030](../../architecture/adr/0030-prefix-stable-model-requests.md)、[记录](2026-10-31-prefix-cache.md) |
| #118 | Work 进展监督（默认 observe） | `23de08b` | [记录](2026-10-31-progress-watchdog.md) |
| #119 | 并行子任务 `run_subtasks` | `cd74472` | [ADR 0031](../../architecture/adr/0031-parallel-subtasks.md)、[记录](2026-10-31-parallel-subtasks.md) |

每个 PR 合并前最终 head 的 CI 九项全绿（#116 的 PG 任务首跑因 `test_document_indexes_are_bounded_at_100000_blocks` 查询计划偶发失败，重跑通过）。#117 首次 e2e 失败：e2e 脚本模型按“最后一条 user 原文”匹配，加 `<ailab_context>` 后匹配不上；已改为先 `strip_turn_context()`。#118/#119 按 #117 之后依次变基合并。

本机全量（SQLite，未设 PG/Redis）：main@f316b85 基线 19 项环境性失败（OCR/PDF 工具链）；#117/#119 及三层叠加分支均无新增失败。

## 部署

本机（Linux，k3s）按用户授权部署 `main@cd74472`：主检出 `git pull --ff-only`，保留本机 `overlays/lite/kustomization.yaml` 的 PVC 2Gi 改动，丢弃 Next 生成的 `next-env.d.ts`；`./deploy.sh --dry-run` 后执行 `sudo ./deploy.sh`（Key 沿用 `platform/.env`），exit 0。重建 sbx-office（技能/Dockerfile 有变）、平台与 crawler 镜像并导入 containerd；api/agent/broker/worker/crawler 新 Pod ready、0 重启，clamav/egress/public/redis/searxng 复用。deploy.sh 自带冒烟：匿名代理拒绝、登录 httpOnly、模型列表（deepseek-v4.1-flash、ailab-demo）、离线模型任务 12 s 成功。未做备份，未额外跑浏览器或真实模型验收（按用户测试环境约定）。上次本机部署为 5 天前的旧版本，本次一并带上 main 上此前未部署的提交与迁移。

## 未验 / 下一步

- 真实 DeepSeek 缓存命中率：按 run 比较 `model_usage.cached_tokens / prompt_tokens`（或 `ailab_model_tokens_total{kind}`），同一组多轮 Work 对话对比修复前后。用户 Mac 部署（`~/Documents/AILab-local`）未更新。
- watchdog 真实判定分布与误报率（默认 observe，观察 `agent.watchdog` 后决定是否切 nudge）。
- `run_subtasks` 真实模型下的调用时机、报告质量与并发成本；Portal 只显示其工具步骤。

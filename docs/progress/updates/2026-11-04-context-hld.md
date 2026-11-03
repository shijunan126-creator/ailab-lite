# 上下文压缩 HLD 与运行过程快照（2026-11-03 → 11-04）

## 范围与授权

用户提供 HLD v4（[设计副本](../../architecture/design/2026-11-03-context-compaction-hld.md)），要求按其实施并更新文档：Claude 只做编排与文档，实施、测试、定位、修复全部交给 Codex（gpt-6.1-sol medium）；要求真实模型测试。过程中用户追加三项决定：压缩后改为 10K–48K / 硬上限 64K；以每次调用返回的实际 token 为计数依据；最终版本不再重复真实模型测试。最后授权提 PR、CI 通过后合并，并由 Codex 部署。决定与 HLD 的差异见 [ADR 0033](../../architecture/adr/0033-context-epoch-band-process-snapshot.md)。

基线 `origin/main@ab31537`。分支链：`feat/context-hld`（阶段 1–3）+ `feat/run-process-snapshot` → `feat/context-hld-int`（合并、ContextManager 抽取、多租户矩阵）→ `-fix`（评审与真实模型缺陷）→ `-band`（绝对区间）→ `-final`（批次上限）→ `-rc`（第二轮评审）→ `feat/context-hld-usage@f23e470`（实际值计数，最终产品代码）。

## 结果

- 上下文纪元/ABI、原子边界激活、显式安全压缩点、三路径统一结构化摘要、确定性运行状态/模块块、来源只取类型化元数据；`agent/context.py` 中按运行作用域的 ContextManager。
- 90% 触发；压缩及任何重建后的整份请求 10K–48K、上限 64K（小窗口缩放），三路径共用 `agent/context_band.py` 规划/校验；工具结果按整批准入；计划/交付/模块/记忆块有预算并优先保留用户偏好；当前输入例外与 ContextPause 失败合同。
- 计数锚定供应商 `prompt_tokens`（checkpoint 中最多 32 个锚点，边界 CAS 时清空），删除进程内只增系数。
- RunProcessSnapshot（迁移 0050，ProcessView V2）、`/v1/runs/{id}/process`、事件 GC 覆盖保护、按页补快照、Portal 历史过程改读快照；迁移 0051 把恰为 0.85 的持久阈值改为 0.90。
- 两轮独立评审共 8 项确认缺陷与 2 项真实模型缺陷均已在根因处修复：恢复后半截回答丢失、伪造来源、边界事件失败不推进纪元、补快照全表扫描、摘要截断杀后台任务、已取消仍显示运行中、重建绕过上限、保留租约被续约失败释放、记忆按审计字段计价并删偏好、2,048 来源上限报错；另将重建 O(n²) 改为线性（200/400/800 轮访问量 1.64×/1.78×）。

## 验证

最终产品 SHA `f23e470`（冻结后未改）：Ruff 通过；SQLite 2314 passed / 201 skipped；PostgreSQL+Redis 2446 passed / 69 skipped；Portal 237 passed、build 通过；E2E 184 passed / 6 skipped（均为 opt-in 性能基准）。跳过项为未配置的外部后端（OpenSandbox、S3、ClamAV、爬虫、OIDC、部分 Office/viewer 钩子）。

真实模型（DeepSeek `deepseek-flash`，隔离栈）为历史证据，不在最终 SHA 上：

| 基线 | 场景 | 结果 |
|---|---|---|
| `33708ca` | Chat 10 轮 / Work 22 步 | 续轮严格追加；Chat 累计 95.64%（3 次 94.3–94.7%），Work 97.42% |
| `33708ca` | 32k 强制压缩、后台任务、子任务、两用户 | 通过；22k 摘要截断导致失败并杀任务（已修） |
| `5bc600e` | 22k/32k 复测、取消/恢复过程快照 | 通过；压缩后小步续轮 98.7–99.2% |
| `92cffa8` | 约 300K 压缩（W=366,667） | Work 293K→23.8K、Chat 299K→21.7K（供应商计数）；摘要 9.9s/6.5s，finish=stop；6 条事实各问 6 次全对；后续小步续轮 98.9–99.9% |

当时估算器比供应商计数偏高（压缩前约 13%，压缩后 30–50%），这正是改为锚定实际值的原因。300K 测试共约 4105 万 tokens，按官方价约 0.43 美元。

## 未测与风险

- 最终 SHA 未做真实模型回归（用户决定）；锚定计数的真实偏差、首个边界后请求仍为整份估算。
- 未测：1M 实际填满、真实 >64K 当前附件例外、其他供应商、长期缓存 TTL、摘要中途崩溃、生产 CPU/RSS/负载。
- 迁移前已被 GC 的过程事件无法恢复；超过 8 MiB 的快照拒绝物化并保留事件。
- 因上下文暂停而保留的沙箱受原有 TTL/最长寿命回收约束。

## 下一步

提 PR，CI 全绿后合并，Codex 增量部署（迁移 0049→0051）；结果追加到本文与 [current](../current.md)。

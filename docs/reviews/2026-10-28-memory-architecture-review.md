# 记忆架构与实现审查

审查基线：`origin/main@0659101142d972e7b28b6b282ec4a2d21aee37fc`（2026-10-27，PR #97）。2026-10-28 已 fetch，在独立工作树 `/private/tmp/ailab-memory-review-20261028`、分支 `review/memory-architecture-20261028` 审查。原主目录 `fix/full-thinking-rendering@ab0e8d9` 的未提交文档及 `.DS_Store` 保留。

对照材料：用户附件 `chatgpt_like_memory_system_architecture.md`，v1.0 / 2026-10-26，重点 §4、§7–15、§20、§22–24、§32。附件自己说明这是推荐架构，不代表 OpenAI 私有实现。本轮判断的是**当前代码是否满足附件的行为契约**，不以组件名或表数量判断，也不审查个人部署当前版本。

结论：**已经有 ChatGPT-like 记忆的主要组成部分，但不是附件定义的完整、生产就绪实现；实现逻辑也存在可复现缺陷。** 当前是有界账号事实文档 + 按需历史检索 + 夜间抽取/裁决。不能表述为“全部实现，逻辑没有问题”。

## 1. 先看实现缺陷

严重度：P1 为应优先修复的隐私控制、错误状态或可靠性问题；P2 为来源可信度、特定配置下的功能退化。以下 9 类发现对应 12 个失败的合同探针；另有 1 个正常路径对照通过。探针断言期望的正确行为，当前失败是本次审查证据，未修改产品实现。

### R1 · P1 · 暂停/失效条目仍可通过其他路径进入模型

位置：[routes_memory.py](../../platform/ailab_platform/api/routes_memory.py) 第 371–373、405–408 行；[memory_dream/service.py](../../platform/ailab_platform/memory_dream/service.py) 第 87–88、321、573 行。

普通 bootstrap 用 `memory_for(..., for_model=True)` 正确过滤 suppressed/invalid/superseded。但 `memory list` 直接返回管理页的完整 `memory_for(user)`；写操作结果及幂等重放也返回完整文档。Agent 会将工具结果传给模型，暂停条目因此重新暴露。夜间 `_active()` 也不排除 suppressed，随后把内容发给抽取和裁决模型。

复现：通过公开 API 保存 suppressed 条目，再让运行中的 Agent 调用 list，200 响应仍含原文；纯函数探针确认 `_active([suppressed])` 仍返回该项。对照证明 bootstrap 的过滤确实正常。影响是页面承诺“AI 不会参考”的控制不能成立；失效事实还可能从 list 重回模型上下文。

修复方向：统一模型可见视图，覆盖 bootstrap、所有工具返回/重放、dream 输入；管理页视图单独保留。不要只在 prompt 提醒“不要使用”。

### R2 · P1 · 关闭“自动记忆”后，nightly 仍自动新增事实

位置：[memory_dream/service.py](../../platform/ailab_platform/memory_dream/service.py) 第 568–575、473–477 行。

前台 Agent 写入检查 `auto_save`，nightly 只检查 `enabled`。用户保留读取、关闭自动记忆、夜间整理保持默认开启时，新对话仍被抽取并落库。

复现：公开 API 设置 `enabled=true, auto_save=false`，调用实际 `process_dream`，仅替换模型返回，最终仍出现 `source=nightly` 的新事实。不是模型口头声称记住，而是数据库已写入。

修复方向：统一自动写入资格，在调度、模型调用前和提交时检查。若夜间开关只负责整理已有事实，可以保留整理，但不得绕过自动记忆关闭去抽取新事实。

### R3 · P1 · 处理游标会永久跳过未处理的历史

位置：[memory_dream/service.py](../../platform/ailab_platform/memory_dream/service.py) 第 125–142、280–286、322、615–616 行。

`_extract` 为输入预算保留最新 episodes、丢掉前面的 episodes，但成功后游标直接推进到原始 `runs[-1]`，没有保存被裁剪的待处理工作。另一个同源问题是游标只比较 `created_at > since`：先创建、晚完成的 Run，以及相同时间戳边界，都可能落在游标后方而永不处理。每轮 5 条变更上限之外的候选也没有持久待办。

复现一：3 轮各约 6000 字、默认 8000 字输入预算，模型实际只收到最后 1 轮；提交后另外 2 轮不在下一次 `_new_runs` 中。复现二：旧 Run 后完成，但其创建时间早于上次游标，下一次候选为空。两者均为确定性的任务遗漏。

修复方向：以实际处理单元记录待办/完成，预算剩余进入下一批；以稳定复合游标或处理记录覆盖晚完成、同时间戳和重试，不能用“最后取到的创建时间”替代完成进度。

### R4 · P1 · 删除来源没有清理 dream 快照，原话可再次读出

位置：[memory_recall/lineage.py](../../platform/ailab_platform/memory_recall/lineage.py) 第 34–67 行；[routes_memory.py](../../platform/ailab_platform/api/routes_memory.py) 第 277–289、292–350 行。

删除链清理 episodes、embeddings 和当前事实中的 quote/证据；没有处理 `MemoryDreamRun.before_memory/after_memory/plan`。这些字段保留同一份原话/证据，详情 API 原样返回，revert 还可重新带回当前文档。

复现：创建带 source_quote 的事实和整理快照，调用真实删除对话 API；当前事实的 quote 已清空，但 dream 详情仍返回测试原话标记。这里指出的是**原话副本未清理**，与 ADR 0019 明确选择“删除对话后保存事实正文”是两件事；后者是产品取舍，本轮未误报为新 bug。

修复方向：所有保存记忆内容的快照/计划/结果纳入统一删除谱系；撤销时重检可见性和墓碑，避免恢复已删除证据。

### R5 · P1 · 模型等待期间的用户删除不能约束后续 apply

位置：[memory_dream/service.py](../../platform/ailab_platform/memory_dream/service.py) 第 575–576、470–537、609–610 行。

候选使用旧 tombstones 筛选，随后等待 judge；`_apply` 刷新最新文档，却不重新校验最新墓碑、来源可见性、dream 开关或被裁决条目的版本。它用**刷新后的 revision** 写入，因此 CAS 无法识别模型基于旧版本作出的决策。新删除事实可能被重新添加，新编辑事实也可能被旧裁决失效。

复现边界：在 screen 与 apply 之间完成删除，再调用真实 `_apply`，已删事实被新增回来。此为提交边界的确定性状态交错探针；没有声称完成真实 PostgreSQL 多客户端并发复现。SQLite 首次插入 episode 时另有 R9 的写锁；该写锁不是可靠的业务并发控制，重用已有 episode 或 PostgreSQL 路径仍需提交校验。

修复方向：plan 绑定读取版本，提交锁内重检最新 policy、墓碑、每个来源及变更目标；版本改变后重算或安全放弃，保留待办。

### R6 · P2 · 证据 ID 合法，不代表证据与事实匹配

位置：[memory_dream/service.py](../../platform/ailab_platform/memory_dream/service.py) 第 339–358 行；[routes_memory.py](../../platform/ailab_platform/api/routes_memory.py) 第 396、137–165 行。

抽取只验证 quote 出现在本批任一 episode；非空 evidence IDs 只要属于本批就接受，不要求 quote 出现在所声称的来源里。模型把 A 的引文指向 B，会生成错误溯源和 valid_from，删除 A 时也无法正确定位。Agent `replace` 则没有将证据改绑到当前纠正 Run，旧 `evidence_run_ids` 被保留，新旧事实共用同一个 id。

两个探针分别确认：蓝色偏好的引文来自 A、证据声明为“住在上海”的 B，抽取仍接受；Agent 把上海改为北京后，证据仍是旧上海 Run。助手内容不能充当证据的现有检查是有价值的，但不足以证明完整来源链正确。

修复方向：服务端按实际含引文的源构建并交叉校验 run/episode 关联；纠正形成新事实及 supersedes 关系，或至少重新绑定纠正来源并清掉不再成立的证据。

### R7 · P2 · 关闭记忆的账号能阻塞其他账号的向量回填

位置：[memory_recall/embeddings.py](../../platform/ailab_platform/memory_recall/embeddings.py) 第 134–164 行。

先取全局最新 500 条无向量 Run，再在 Python 中排除关闭记忆的账号；没有续扫游标。这 500 条永远不会被索引，后面的有效账号历史也永远进不来。后台收到 0 就结束当次 backlog 循环，下次仍扫描同一页。

复现：关闭记忆账号创建 500 条较新记录，另一个开启账号有较旧待索引记录；按开启账号单独索引成功，删掉该控制向量后全局回填始终返回 0。使用内存假 embedding，无真实外部调用。影响仅在启用可选 embedding 通道时发生，纯词法路径仍可工作。

修复方向：把资格过滤放到 LIMIT 之前，或用有界 keyset 续扫跨过不合格行；保留每用户公平性和扫描上限。

### R8 · P1 · 变更数量截断把“新事实 + 旧事实失效”拆开

位置：[memory_dream/service.py](../../platform/ailab_platform/memory_dream/service.py) 第 591–608 行。

新事实先 append、矛盾旧事实再 append invalidate，最后按 5 条操作切片。第 5 条是 add 时，第 6 条 invalidate 被裁掉；两条互斥事实会同时保持 active，并一起进入下一轮 prompt。

复现：3 对明确的新旧状态替换，经真实 `process_dream` 处理后，前两对正常，第三对新旧同时有效。模型的抽取和裁决均正确，错误来自服务端对操作列表的截断。

修复方向：把每条候选事实及其全部 invalidations 作为不可拆的变更组，容量不足则整组留待下批；验收必须断言最终当前状态，不能只看变更数。

### R9 · P1 · 夜间模型调用持有 SQLite 全库写锁

位置：[memory_dream/service.py](../../platform/ailab_platform/memory_dream/service.py) 第 233–259、572–576、610–622 行；[db.py](../../platform/ailab_platform/db.py) 第 31–45 行。

`_episodes` 插入后 `db.flush()`，事务保持打开，随后等待抽取、裁决网络调用，直到 `_finish` 才 commit。SQLite 同一时刻只能有一个 writer；因此夜间整理一个用户会阻塞**其他用户**的登录审计、发消息和写入。生产 busy_timeout 为 30 秒，慢模型可能让正常请求变成 `database is locked`。

复现：实际运行 `process_dream`，在抽取调用接缝开启第二个真实 SQLite session 写另一个账号；把该探针 busy_timeout 缩短到 50ms，稳定得到 `database is locked`。未做 30 秒人工等待或付费模型调用；这是写锁持有的实际复现，不是 RSS/容量估算。

修复方向：短事务先持久化 episode/job 输入，结束事务后调用模型，再用短事务和 R5 的版本重检提交结果。不要为整个模型等待持有用户锁或数据库写事务。

## 2. 附件架构覆盖度

| 附件契约 | 当前实现与证据 | 判断 |
|---|---|---|
| Working memory / 上下文预算 | `routes_model_context.py` 分页、`AgentLoop._assemble_context` token 预算/摘要、checkpoint 续跑 | 已有主要机制；本轮未重验完整 runtime |
| Episodic history | 直接搜索可见 `runs`，BM25；可选 embedding 余弦通道 + RRF；无关问题允许弃权 | 部分满足；最近 400 个成功轮次、文本窗口截断，无多级详情检索 |
| Semantic long-term memory | `users.settings.memory`，100 条 / 12000 正文字符；可编辑、Agent 工具和 dream 写入 | 有基础；每轮注入所有有效事实，未做事实检索/相关性门控/独立 token 配额 |
| Evidence separate from facts | `MemoryEpisode` 独立表，事实含 run/episode IDs、source_quote | 部分满足；R4/R6；Agent replace 仍覆盖原事实 |
| Memory governance / dreaming | ADD-only 抽取、独立去重/冲突 judge、invalid_at、suppressed、hash 墓碑、整理快照/撤销 | 已实现框架，但 R1–R5/R8/R9 使关键契约不能成立 |
| 当前状态与历史状态 | invalid_at 可保留旧事实，原始历史仍可搜索 | 无明确 as-of 查询/current-state projection；superseded_by 字段未贯通 nightly 关系链；R8 可注入矛盾状态 |
| User/project/session scope | SQL 按 user_id 过滤，项目说明另走 workspace input | 账号隔离有测试；记忆和 recall 没有 project-only scope，项目 A 可检索同账号项目 B |
| 临时 / 不记忆会话 | ADR 0019 明确记录已移除临时会话 | 附件要求未满足，但这是已记录的产品决定，本轮不恢复功能 |
| 独立控制读历史/读事实/自动写入 | 一个 enabled 同时控制长期事实和 recall；另有 auto_save、dream enabled | 没有附件 §31 的独立控制；R2 还造成现有开关不一致 |
| 删除传播 | 对话删除清 episode/vector/当前 quote，保留事实并标记 evidence_deleted | ADR 选择偏离附件递归删除；即使按该取舍，R4/R5 仍不合格 |
| 注入防御与敏感数据 | 来源分段、用户引文校验、凭据/指令正则、prompt 数据声明 | 有基础控制；非完整语义验证/敏感类别策略/争议审核，不承诺靠正则防住所有投毒 |
| 可解释性 | 后端提供证据字段与 dream 详情 | 记忆页未展示 run/episode/quote 来源跳转，且 R6 来源可能错误 |
| embedding 升级/故障 | 以 run_id+model 存向量；换 model 重建，调用失败退词法 | 已有降级；同名模型换权重/维度没有独立版本指纹；R7 回填可能饿死 |
| 异步与幂等 | API 进程内 dream 调度、用户日期唯一、Agent RunDelivery/CAS | 前台与后台分离，但没有原子 job claim/fencing；同一 running dream 可再次进入处理；不能据此保证并发幂等 |
| Golden / memory-hurt 评测 | 有单元/集成测试与 `evaluate_memory_dream.py` 抽取比较脚本 | 没有附件 §22 的完整 golden、无记忆基线和 memory-hurt 度量；65 项通过不能替代它们 |

不需要为了“像 ChatGPT”立即引入 Qdrant、独立微服务或把 100 条有界事实强行迁表。单机可以保留现有五服务/SQL 后端；先保证可见性、工作进度、来源和并发契约，再决定是否扩展存储。

## 3. 额外边界与性能成本

以下是源码确认的设计债务，本轮没有作为新增动态复现缺陷计数：

- dream `_chat` 直接调用 `llm_for(...).chat`，没有经过 Agent 的每调用 reserve/settle 账本，token 只存在 dream 的汇总 JSON；失败重试的费用、日/月额度和冻结价格输入没有统一合同。当前 transport 也明确标注为 unmetered caller。需明确系统后台预算并纳入统一记账，不能仅依赖 x-user-id 上游限流。
- `_candidate_users` 每次加载全部未停用用户，`_episodes` 每次加载该用户全部历史 episode，dream metrics 也全量加载历史；缺乏随数据增长的界限。未测 CPU/RSS/双库 EXPLAIN 或目标 2vCPU/8GiB 整栈并发，不提供虚构容量数字。
- 稠密查询同步外调有默认 3 秒 timeout；全量事实常驻 prompt 增加每次推理输入成本。没有跑附件建议的 300ms 读取 p95 或真实模型质量评测。
- SQLite writer 问题必须先解决，不能用“夜间低峰”替代并发合同；失败夜间任务还会被重试，重复付费调用的成本需一起收敛。
- `revert` 恢复完整旧快照并采用当前 revision，不检查是否已经有更晚的用户编辑/夜间整理；页面有“恢复整理前的内容”确认，故本轮不将整份回滚本身误报为无提示丢更新。但它与删除谱系、过期快照和父页面刷新之间仍需统一设计。

## 4. 本轮验证与复现

环境：隔离 Python 3.12.11 venv，按 `platform/constraints.txt` 安装项目 dev/postgres 依赖；测试使用 `tests/conftest.py` 的临时 SQLite/storage、memory queue、mock sandbox。没有读取个人 `.env`，没有调用付费模型、外部 embedding、个人部署或业务数据库。

- 既有 `test_user_memory.py / test_memory_dream.py / test_memory_recall.py / test_memory_embeddings.py`：**65 项通过**。包含原有权限、CAS、幂等、检索、来源与 bootstrap 覆盖。
- [审查合同探针](memory-architecture-20261028/test_review_contracts.py)：**12 失败 / 1 通过**，归并上述 9 类问题。最终输出：[probes.txt](memory-architecture-20261028/probes.txt)。此前的 10、11 失败阶段是逐步补齐探针的中间结果，以最终输出为准。
- 探针和产品源码 Ruff check/format、diff 检查见本轮交接。失败探针放在 review evidence 下，不加入默认产品 tests，也未改成 xfail 掩盖问题。
- **未执行**全量 Python、PostgreSQL/Redis 合同、Portal build/E2E、真实模型/embedding/浏览器、故障重启与目标机容量测试。本轮仅审查，无产品修复，不把局部通过表述为全产品逻辑正确或已修复。

复现命令（先按项目锁约束安装环境；从 `platform` 运行）：

```sh
PYTHONPATH=. /private/tmp/ailab-memory-review-venv/bin/python -m pytest \
  ../docs/reviews/memory-architecture-20261028/test_review_contracts.py \
  --tb=short --show-capture=no -q
```

## 5. 下一步建议

先把问题按共同根因收敛成三组，不逐条追加补丁：

1. **统一读写资格与提交边界**：R1/R2/R5/R9；管理视图与模型视图分开，模型调用在事务外，提交做 policy/版本/墓碑/来源重检。
2. **持久工作进度与原子状态变更**：R3/R7/R8；记录真实处理单元，预算可续，替换及失效不可拆，后台 claim 可 fencing。
3. **完整来源与删除谱系**：R4/R6；覆盖当前文档、episode、embedding、快照/计划和重放结果，纠正不能继续指向旧证据。

每组完成后统一做红绿验收，再跑双库全量、Portal build 和必要浏览器验证。之后才评估项目作用域、事实 context compiler、temporal projection 和质量评测；是否恢复临时会话须尊重现有产品决定。本轮没有实现授权，不自动修复、提交、push、PR、merge 或 deploy。

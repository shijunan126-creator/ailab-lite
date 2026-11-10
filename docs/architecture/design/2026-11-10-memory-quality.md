# 记忆质量与整理节奏（计划，2026-11-10）

状态：已实施（2026-11-10，[ADR 0036](../adr/0036-memory-quality.md)，[记录](../../progress/updates/2026-11-10-memory-quality.md)），PR CI 验收中。基线 `main@36b17590`（含 PR #174 有界记忆文档，[ADR 0035](../adr/0035-memory-v2.md)）。

## 目标与成功标准

记忆只保存"下次新对话知道了会让回答更好"的内容，并保留变化过程。用户在记忆页看到的应是关于自己的稳定画像，不是提问流水账或交付清单。

验收：
- 用真实对话副本重跑：「在做的事」没有单次已交付的任务；「关注的话题」没有逐次提问的日志；测试/复现对话产出 0 条。
- 合成用例：身份或项目发生转变后，旧状态以带时间的过去式保留，并新增现状。
- 每个用户每晚最多一条整理链，02:00–05:00 之间开始，单用户最长 30 分钟；白天不会因为任何路径自动整理。
- 删除对话后记忆保留，页面不再链接到已删除的对话。
- 共享项目：历史检索和文件检索优先本项目。独立项目：与外部完全隔离，包括文件读取。切换模式时按来源项目移动记忆，不复制。

## 现状问题（代码证据）

1. **白天也在整理，原因有两个。**
   - `claim_due` 只检查本地时间是否已过 `window_start`，没有检查 `window_end`（`memory_dream/service.py:201`）。
   - 一条链因为超出窗口或达到批数上限停下时，仍会新建一个 pending 续批，`due_at` 沿用上一批、已经过期（`service.py:804-808`）。`recover_pending` 每 5 分钟就会把它捡起来执行（`service.py:891-916`），`prepare` 也不检查窗口。结果是只要有新对话，白天每次检查都会跑一批，原有的 6 批上限也拦不住。
2. **提示词没有价值标准。** `memory_dream/prompts.py` 只有一句 "Keep stable personal context…"，没有分类定义，也没有反例。旧设计（2026-11-09）还明确要求把关注写成"关注/问过 X，带日期"。于是每次提问记一条，每次交付记一条，测试对话「复测-chat」也被当成了证据。
3. **删除来源会删掉记忆。** 每条记忆只登记首次出现的 run：add 只写当前 episode，update 会把来源替换掉。以下几处都会因此删除或隐藏记忆：
   - 删除对话：`FORGET_DERIVED_FACTS=True`（`memory_recall/lineage.py:24`），唯一来源被删时，整行删除。
   - 文件来源失效，共 3 处：`source_lifecycle.py:178` 删除行；`_drain_memory_copies`（`:258-273`）删除撤销和交付副本里的行；`visible_memory_entries`（`:319`）在 `memory_for` 中过滤掉这些行，下一次 `replace_entries` 写入时会把它们物理删除（`store.py:71-73`）。同一规则还用在变更视图和撤销恢复上（`routes_memory.py:507,535`）。
4. **变化被当作删除。** 删除理由里有 `outdated`，旧状态直接消失，没有"转变"的语义。
5. **项目模式不完整。**
   - 共享项目的 `recall_history` 先按时间取最近 400 条作为候选（`search.py:92`），再做 BM25 和向量融合排序（`:190-208`）。本项目较早的对话可能根本进不了候选。
   - 独立项目的文件没有隔离。`search_library` 会搜出用户的全部文件（`routes_library_tools.py:105`）；`authorized_source`（`:46-60`）不检查项目，所以拿到外部 file_id 的 `read_library_file` 和 `documents/*` 都能读取。
   - 共享转独立时按"证据 run 全部属于本项目"移动记忆（`routes_projects.py:105`），但证据只记了首次来源，并不可靠。
6. **页面来源标注不准。** 没有来源的 explicit 行会显示「你添加的」。`_item_out` 把 manual 输出为 edited（`routes_memory.py:957`），页面一句话新增的行写入的就是 edited（`maintenance.py:120`）。

## 决定

### A. 整理节奏
- 每个用户每个本地日期最多一条 cron 链。`claim_due` 的条件改为两条：当地时间在 `[window_start + 错峰, window_end)` 之内，并且当天还没有 cron 行。
- **去掉续批行**。链因为超时、达到批数上限或没有更多对话而停止时，只把这条链标记为结束，不再新建行。没处理完的对话依靠 `MemoryWorkItem` 的状态，自然留到下一晚。`recover_pending` 只负责续跑租约过期、确实没跑完的批次，不新建批次。
- 链上持久化三个时间，重启后据此判断：
  - `cutoff_at`：等于链的创建时间。只处理 `coalesce(finished_at, created_at) <= cutoff_at` 的对话（用 `ix_runs_finished`）。
  - `chain_started_at`：第一批真正开始执行的时间。
  - `deadline`：`chain_started_at + AILAB_MEMORY_DREAM_CHAIN_TIMEOUT_SECONDS`（默认 1800，取值 60..7200）。
- 每一批开始前检查 `deadline`。到期后不再开始下一批，并把链标为 `failed(reason=timeout)`。正在进行的模型调用仍受现有 240 秒超时约束。05:00 时已经在跑的链不中断。
- 05:00 前建好、但 05:00 以后才被接纳执行的链，直接作废（不调用模型）。它仍算作当晚的那一次，没处理的对话留到下一晚。
- `memory_dream_auto_batches` 的含义改为"每条链最多几批"。
- 「立即整理」（manual）保持现状，不占当晚名额，也不受窗口限制。
- 某晚机器没开，下一晚的那一条链会处理积压，不补跑多晚。

### B. 删除来源不删除记忆
- 对话删除、文件来源失效、项目删除，采用**同一条规则**：从记忆行中移除失效的 run、episode 和引文，记忆行本身保留。具体改动：
  - `FORGET_DERIVED_FACTS=False`；
  - `source_lifecycle.py:178`、`_drain_memory_copies`、`visible_memory_entries` 三处同步修改，`routes_memory.py:507,535` 的调用随之生效。
- 历史版本照旧处理：`redact_closed_sources` 继续删除或清空只依赖被删来源的已关闭版本。这部分只影响历史版本，不影响当前行。
- 隐私清理保持不变：原文、episode、episode context、撤销副本里的引文都会清除。
- 想忘掉一条记忆，只能在记忆页删除，或者在对话里明确要求忘记。这两种方式会留下墓碑，防止以后被重新学到。
- Portal 只显示仍然存在的来源（`_sources_for` 已经过滤）。没有来源时只显示日期。「你添加的」只在 `origin=edited` 且没有来源时显示。

### C. 提示词与转变
整理提示词 `DOCUMENT_SYSTEM`、页面编辑提示词 `EDIT_SYSTEM`、对话中的 `MEMORY_RULES`，以及 memory 工具的 kind 说明，统一改为同一套中文标准：

- **判断标准**：下次开新对话时，知道这条会不会让回答更好。
- **分类定义**：

  | kind | 记 | 不记 |
  |---|---|---|
  | fact 关于你 | 稳定身份、角色、组织、专业背景、常用环境、经历（含已结束的项目） | 一次性的情况 |
  | preference 偏好 | 明确说过的，或反复体现出来的做法 | 只针对单次任务的要求 |
  | project 在做的事 | 跨多次对话且尚未结束的目标，写成"做什么 + 进展" | 单次交付（如果属于某个进行中的项目，就并入那个项目的行） |
  | interest 关注的话题 | 领域级的兴趣，可以带有用的细节；第一次出现就可以记，之后同领域的问题并入同一条 | 纯事实查询、逐次提问的日志、询问日期 |
  | reference 资料 | 用户提供、以后会再用到的信息 | 助手生成的内容、外部常识 |

- **判断顺序**：先看是不是测试或复现，再看是不是一次性的，然后看是不是转变、能不能并入已有的行，都不是才新增。
- **转变**：新增操作 `transition {id, past, now, quote, run, r}`。
  - 旧行的 `superseded_reason=correction`，由过去式的行接替，现有的纠正通知原样生效（`invalidations.py:29`，`context.py:954`）。
  - 过去式的行可以换分类：项目结束后，过去式归入 fact 作为"经历"，不再留在「在做的事」里。
  - 现状行按新增处理，不推送通知。
  - 两行都继承原来的来源，`touched` 同时登记两行。
  - 现状行撞上活动唯一键或墓碑时，只改写过去式那一行。
  - 受保护的行发生转变时，需要更新对话中的原话引文。
  - `created_by` 的取值增加 `transition`，同步修改 contracts、`write_memory` 和 Portal 的 `lib/api.ts`。
- **对话中的工具不加新操作。** `MEMORY_RULES` 写明：用户说状态变了，就用 replace 把旧行改写成过去式，再 add 一条现状。
- **删除**：只保留 `r=H`（重复）和 `r=I`（错误），去掉 delete 的 `reason` 字段和 `outdated`。`I` 必须附原话引文和 run。
- **压缩顺序**：先把过去式归纳成「经历：A → B → C」，再压缩长期没有被提到的兴趣，最后才淘汰其他推断行。
- **正反例**：直接取自用户截图（PPT 交付、乐信的一次提问、复测-chat）。
- **理由代号 `r`**：每个 op 和每条 `ignored_runs` 都附一个字母。程序不据此拦截操作，未知代号记为 `?`，计数写进已有的 `MemoryDreamRun.report`。
  - 新增：A 明确要求 / B 稳定事实 / C 持续目标 / D 领域兴趣
  - 转变：E
  - 补充：F
  - 合并：G 归纳
  - 删除：H 重复 / I 错误
  - 忽略：T 测试复现 / O 一次性 / K 已完成的单次任务 / W 外部知识 / P 临时指令 / N 无信息
  - `ignored_runs` 的映射：T 对应原来的 test/repro（跳过，并清理 context），其余对应 no_signal。

### D. 项目模式
- **独立项目**
  - 记忆文档和历史检索已经隔离。
  - 文件：把项目判断放进 `authorized_source` 这一个入口，同时覆盖 search、read 和 documents。用户显式附加的文件（`MessageFileRef`）放行。
  - `library_query` 在非独立范围内排除独立项目的文件，并做 EXPLAIN 检查。
  - Portal 资料库页面仍按现有方式展示所有文件，隔离只作用于模型检索。
- **共享项目**
  - 与账号共用一份文档。
  - `recall_history` 在候选阶段额外取一批本项目的近期对话，与全局候选合并去重后再做融合排序，本项目的结果加分。不改 SQL 的翻页语义（cursor/before/asc）。
  - `search_library` 已经是本项目优先。
- **来源项目标签**
  - 新增 `memory_items.origin_project_id`：可为空，不加外键，不级联删除。迁移 0067 用 `ALTER TABLE` 加列，支持升级和降级。
  - 字段写入整条往返路径：`legacy()`、`replace_entries`、`MemoryEntryInput`、`write_memory` 的字段合并表、`_stored_entry`、`normalized`、`maintenance.inputs`。需要一条往返测试。
  - 共享项目的 Dreaming 每批只处理一个项目，标签直接取该批的 `pid`。工具 add 取当前会话所在项目。transition 和 update 继承原标签；merge 只在所有标签相同时保留，否则置空。
  - 回填只处理 `project_id IS NULL` 的行：沿 `memory_item_sources`→`runs`→`conversations` 判断，项目唯一且非空时才写入。
  - 删除项目时，在同一事务里把指向它的标签置空，账号行保留。
- **共享转独立**：把带本项目标签的 active 行和 review 行移到项目范围，不复制。
- **独立转共享**：沿用 `merge_project` 程序融合，并补打标签。
- **生效时机：不新增上下文边界。** 现有的失效通知（`for_handles` 按当前模式判断可见性）会在用户下一条消息的开头追加通知，前缀保持不变。通知措辞对"移出范围"的行改为「已不在当前范围」，不再说「已删除」。已校验并缓存的历史摘录（`validated_recall_run_ids`）不会撤回，这一点写进 ADR。

### E. 现有数据清理
每次成功的整理都把提示词版本写进 `MemoryDreamRun.report.prompt_version`。`claim_due` 只读查询最近一次成功的整理：如果版本过期，即使没有新对话、也不需要维护，当晚仍然建链。链里先对整份文档按新标准做一次整理（受保护的行不动），再处理新对话。不写 `user.settings`。

### F. 页面
已确认方案 A：
- 每个分类是一张卡片：圆角 `--radius-lg`，`--border` 描边。标题 18px、字重 600，前面是青绿线性图标（不带底色），后面是淡色计数。
- 空分类卡片改用 `--subtle` 底色、不描边，图标和标题变淡，正文只有「暂无」。
- 5 个分类全部显示，顺序和 API 一致。条目行、来源和菜单保持现状。来源标注按 B 修正。
- 图标从 `components/icons.tsx` 取：关于你 user、偏好 sliders、在做的事 target、关注的话题 compass、资料 bookmark。缺少的图标补进这个门面。
- 替换后删除 `styles/app.css` 中旧的 `.memory-section h3` 规则。

## 测试与评估
- **整理节奏**
  - 窗口结束后不开始新链；每晚只有一条链；白天 `recover_pending` 不执行任何 cron 行。
  - `cutoff_at` 之后结束的对话留到下一晚；重启后能按持久化的截止时间续跑。
  - 达到 30 分钟后标为超时，未处理的对话保留；05:00 以后才被接纳的链作废；手动整理不占名额。
- **删除来源**：删除对话、文件失效、删除项目之后，当前行都保留并去掉来源；历史版本照旧清理。
- **转变**：应用、换分类、撞键或墓碑、保护规则、撤销、通知；`I` 删除要求引文；代号缺失时记为 `?`。
- **项目**：`origin_project_id` 往返与回填；模式切换按标签移动；`authorized_source` 隔离；共享项目历史检索中本项目较早的对话能被召回；迁移漂移检查。
- **需要修改的旧测试**：`test_memory_document.py:61` 中的 outdated，`test_memory_review.py` 和 `test_memory_system_review.py` 里关于删除对话的断言。
- **前缀**：`MEMORY_RULES` 和工具描述会变，需要把上下文 ABI 升到 6，并更新 `tests/test_prefix_cache.py` 的录制断言。已经打开的会话会遇到一次 `context_abi_changed` 边界。
- **真实模型评估**：在本机库的只读副本上，用相同的 episodes 分别跑新旧提示词，统计每类条数、代号分布和上述三类误记数量。另跑三组合成用例：转变、项目结束、偏好变化。结果写进 dated update，副本用完即删。

## 成本
- 每个用户每晚最多一条链，原来白天可以不受限地跑，调用量只会减少。
- 静态提示词约增加 1.5k token，所有用户共用，可以命中缓存。清理整理每个用户只跑一次。
- 共享项目的历史检索多一次有上限的候选查询。
- 新增一列，不加索引；没有新依赖。
- 部署：一个迁移，API、Agent、Worker 都要更新；已打开的会话会遇到一次 ABI 边界。

## 文档
新增 ADR 0036，内容包括：删除语义、转变、夜间单链、来源项目标签、独立项目文件隔离，并取代 ADR 0035 中对应的段落。实现完成后更新 status-matrix 和 dated update，并在 HANDOFF 中补充续批和往返字段这两个坑。

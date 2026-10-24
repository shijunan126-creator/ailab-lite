# 2026-10-24 会话树 / 置顶 / 全局搜索 / 上下文用量

## 范围与固定基线

后端子任务，工作树 `t3-14e9630c`，分支 `feat/chat-tree-pin-search`，实际起点 `09a011baa274a41a823c8769fb5f7f7522c5dff1`（开始时 T3 分支名为 `t3/chat-tree-pin-search`；已按任务新建目标分支）。起点属于父集成分支 `feat/ui-polish-fork-retry`；父分支之后已继续前进，本子任务没有自动合入平行标题或计划任务改动。只本地提交，不 push / PR / deploy，不修改 `platform/portal/`。

最终运行时代码及测试固定为 `e4c70cb9c1cbf5056ad76e4b94c351e549e2d863`。提交顺序：

- `a952246`：树/置顶迁移、DTO、quiet 调用用途的预留与结算。
- `bfc2c8e`：路径、编辑/重试/切换、上下文/工作区/文件授权/分叉。
- `c59e7c3`：有界全局搜索。
- `8cce3f7`：双库迁移/API/并发/搜索与旧测试数据适配。
- `72f5e69`：递归祖先查询固定为主键探测，避免会话分支数乘深度的计划。
- `e4c70cb`：未删会话部分索引，防止搜索为过滤旧软删行而超出候选访问预算。

后续交接提交仅文档，不改变这个测试基线。父 Agent 负责 Portal、平行分支合并、最终浏览器/E2E 与发布决策。

## 最终公共契约

下列 JSON 的 `string|null` 等为类型记法；日期一律 ISO UTC。保留原有 RunDetail、ConversationOut 字段与错误响应 `{detail: ...}`。

| 请求 | 响应 |
|---|---|
| `POST /v1/conversations/{id}/messages`，原 `{prompt, file_ids?:string[], model?:string|null, mode?:"chat"|"work", skill?:string|null}` | 201 RunDetail；parent 为发送时 active leaf；用户锁 + active_run_id CAS，与任务/outbox 同事务 |
| `POST /v1/runs/{id}/retry`，`{model?:string|null, mode?:"chat"|"work"|null}` | 201 RunDetail；仅当前路径末轮；新兄弟 `retry_of=旧id` |
| `POST /v1/runs/{id}/edit`，`{prompt:string, file_ids?:string[]|null, model?:string|null, mode?:"chat"|"work"|null}` | 201 RunDetail；任意当前路径终态轮次；新兄弟 `edited_from=旧id`；省略/null file_ids 复用附件，`[]` 清空 |
| `POST /v1/conversations/{id}/active`，`{run_id:string}` | 200 ConversationDetail，默认最后20轮第一页；沿选中节点每层 `(created_at,id)` 最新子节点走到叶子 |
| `PATCH /v1/conversations/{id}`，新增 `{pinned:boolean}`，可与原 title/project_id 字段合用 | 200 ConversationOut；新增 `pinned_at:datetime|null`、`active_run_id:string|null` |
| `GET /v1/conversations?limit=&project_id=` | 原列表形状；先 pinned_at 降序（null最后），再 updated_at/id 降序；默认仍只列非项目会话，有 project_id 时只列该项目 |
| `GET /v1/conversations/{id}?limit=20&before=<run_id>` | ConversationDetail；当前路径从根到叶，按路径向前分页，limit 1..100；before 必须在当前路径，否则404 |
| `POST /v1/conversations/{id}/fork`，原 `{run_id,mode}` | 原201 ConversationOut；现在复制指定节点的祖先路径，包括非活动分支；映射所有 parent 和 active leaf |

RunDetail 完整字段为：`id, user_id, conversation_id, title, prompt, status, model, input_file_ids, options, summary, error, result, resumable, created_at, started_at, finished_at, work_ms, artifacts, input_files, usage, retry_of, superseded_by_run_id, parent_run_id, edited_from, versions`。`retry_of/superseded_by_run_id/parent_run_id/edited_from` 均 `string|null`。`versions` 仅在 ConversationDetail.turns 中填充，其他 RunDetail 为 null：

```json
{"index":2,"count":3,"ids":["run_old","run_current","run_other"]}
```

同一 conversation + parent 槽位的版本按 `(created_at,id)` 排序，index 从1开始；上限50，超限422。保留 `superseded_by_run_id` 供旧客户端审计，但它不再控制显示、权限、模型历史或分叉资格。ConversationOut 完整字段：`id, project_id, title, model, created_at, updated_at, last_status, forked_from, pinned_at, active_run_id`。ConversationDetail 另有 `has_more:boolean, turns:RunDetail[], previews:dict[], context`，previews 为整条活动路径最新文件预览（不只当前页）。

```json
{"context":{"used_tokens":321,"model":"test-fast","compact_at_tokens":64000}}
```

三个 context 字段分别是 `int|null, string|null, int|null`。used_tokens 来自活动叶子最后一条 `quiet=false,status=settled` 的 ModelUsage.prompt_tokens，按 `(created_at,id)` 降序取1；quiet title/summary、estimated/released、旧的不明用途账本均不计入。没有已知主调用时 used_tokens=null，model 回退到叶子的模型（空会话则null）；compact_at_tokens 为管理员 ratio ×该模型 context_tokens，auto_compact=false 或模型未知时null。运行时仍有原有 ModelPolicy 硬输入预算，可能早于管理员软阈值触发压缩。

`GET /v1/models` 保留 `{default:string,models:[...]}`；每个公开 item 增加 `context_tokens:int, input_budget_tokens:int`，后者直接取 `ModelPolicy.input_budget(spec)`。

`GET /v1/search?q=<1..200字符>&limit=20&cursor=<opaque>`，limit 1..50，响应：

```json
{"items":[{"conversation_id":"cnv_x","title":"标题","project_id":null,"run_id":"run_x","match":"answer","snippet":"命中附近文本","highlight":[[0,2]],"updated_at":"2026-10-24T00:00:00Z"}],"next_cursor":null}
```

`project_id` 和 `run_id` 为 `string|null`；title 命中时 run_id=null；match 为 title/prompt/answer。返回本人未删会话（含项目）及所有分支，一会话只一条；title 优先，否则取最新匹配 Run，同一 Run prompt 优先 answer。snippet≤120个Unicode字符，highlight 为其内 Unicode code point 的 `[start,end)`（Portal 若用 JS String.slice，须处理非BMP字符）；超过120字的查询将高亮裁剪到可见部分。LIKE 的 `\\`、`%`、`_` 按字面转义；SQLite 注册专用 Unicode lower，PG 使用数据库 lower。

**明确边界/增量**：搜索每请求最多访问50个会话、检查1000个Run候选（每窗口100）；可能返回不足limit或空items但有next_cursor，客户端须续查至null。游标绑定用户和q，最长2048字符；并发更新导致续查会话顺序改变可返回422游标过期，重新搜索即可。没有全文索引或后台索引任务。没有偏离要求中的路径/编辑/置顶语义；额外公开 active_run_id 和 parent_run_id，方便前端确认当前选择；旧 superseded 字段保留。历史账本用途无法可靠重建，按要求未知值为null。

对话任意 Run 未终态时，编辑/重试/切换409 `先停止当前回答`；404用于不归属/找不到的切换节点；不在当前路径的编辑或非末轮重试409中文。编辑/重试附件不属本人、非READY、删除/purge、非library均422 `附件已不可用`；其他 quota/model/skill 检查复用 start_run，不重复实现。

## 数据、隔离和迁移

新增 `0023_conversation_tree.py`，revision `0023`，down_revision `0022`，最终链 `0021→0022→0023`，没有改0022；兄弟计划任务迁移0024由父Agent接到0023后。新增 runs.parent_run_id/edited_from、conversations.active_run_id/pinned_at，以及 ModelUsage/UsageReservation.quiet（可空；预留时冻结用途，结算沿用）。SQLite仅ADD/DROP COLUMN，无被引用表重建。

回填按时间构造链，以 retry_of / superseded 关系把重试放回同一parent槽位，末轮设active leaf。新增索引：`ix_runs_conversation_parent(conversation_id,parent_run_id,created_at,id)`、`ix_conversations_user_pinned(user_id,pinned_at,updated_at)`、`ix_conversations_user_search(user_id,updated_at,id) WHERE deleted_at IS NULL`、`ix_model_usage_context(run_id,quiet,status,created_at,id)`。迁移按100个会话翻页，每个会话加载自身历史元数据；超大既有会话的迁移内存仍随该会话Run数增长，未做百万行迁移压测。

共享摘要仍是一个可替换缓存，不是所有分支的永久摘要：每次读都检查 through_run_id 属于当前Run祖先；不属于则返回空摘要并从祖先重算，写入以当前缓存边界CAS，并验证新边界/旧边界沿该祖先路径前进。切换回来可能再付一次压缩调用，但不会将别的分支内容带入模型。编辑/重试主动清掉不再有效的缓存；无需增加按节点重复存储的摘要。

模型历史、workspace、内部旧历史接口、最新预览和fork共用祖先关系。workspace选择最近有快照的祖先（包括失败/取消的保存点），不读被编辑节点本身；过期仍沿既有expired契约，不静默恢复其他分支。隐含附件授权和同名产物版本父节点也限定本Run/祖先，其他分支产物仍在资料库，可由用户显式选择。

## EXPLAIN 与成本

`tests/test_conversation_tree_migration.py` 对生产查询构造器执行双库EXPLAIN；PG空小表使用 `SET LOCAL enable_seqscan=off` 验证索引可达，**这些cost是规划器估计，不是毫秒或生产统计**。实际API微探针使用默认规划器和ANALYZE。主要结果：

| 查询 | SQLite计划 | PostgreSQL计划 / 顶层cost |
|---|---|---|
| 祖先路径 | seed和每层两次`sqlite_autoindex_runs_1(id=?)`；仅有界CTE临时排序 | Recursive Union + WorkTable Scan，父归属按`runs_pkey`，无按会话分支Join Filter；Sort 908.53..908.56 |
| siblings / newest child | covering `ix_runs_conversation_parent`，约束conversation+parent | Index Only Scan（子节点倒序），Limit 0.14..8.16 |
| 搜索会话候选 | `ix_conversations_user_search(user_id=?)`，仅未删会话，无排序 | 反向user_search部分索引，Limit 0.12..8.14 |
| 搜索Run窗口与匹配 | `ix_runs_conversation_created_id`，再仅扫描100行search_window | 先index+LIMIT100，再LIKE过滤；Limit 0.14..8.25 |
| 置顶列表 | `ix_conversations_user_pinned(user_id=?)` | 小表选择user_search部分索引 + NULLS LAST Sort，Limit 8.15..8.16（pinned索引亦保留供实际统计选用） |
| 主调用用量 | `ix_model_usage_context(run_id,quiet,status)` | 同索引反向取1，Limit 0.14..8.16 |

初版SQLite recursive join会被优化成每层扫描会话索引；改用相关子查询后发现PG仍会把EXISTS改成会话半连接（一次500轮检查p95 165.18ms，失败）。最终使用两次按id的标量查找，PG专门断言无该Join Filter，500轮回归p95 49.40ms通过；SQLite同类回归p95 22.64ms。保留这一红→绿证据，不把索引存在当成成本有界的证明。

索引追加前的 `72f5e69` 本机Mac API微探针（`e4c70cb`仅增加上述部分索引，未重复该微探针）：1500个Run、其中500轮活动路径，每Run 8000字符prompt+16000字符summary（重复ASCII文本），热身后25次；与完整测试同机运行。CPU为API Python进程CPU，**未测PG server CPU**；RSS是热身后全进程峰值增量，不是每请求分配，也不是总RSS。

| 场景 | SQLite p95 / CPU每次 / 峰值RSS增量 | PG p95 / API CPU每次 / 峰值RSS增量 |
|---|---|---|
| 500轮详情首屏，响应492452字节 | 48.48ms / 29.99ms / 7241728B | 40.73ms / 20.96ms / 6979584B |
| 1000候选无命中，返回续查cursor | 48.63ms / 45.76ms / 835584B | 146.78ms / 15.42ms / 638976B |
| 最新answer命中（200字查询） | 4.15ms / 3.33ms / 0B | 11.59ms / 3.88ms / 0B |

无新依赖/后台进程/模型调用（重算既有压缩除外），无原文件复制；新增持久磁盘为每Run的parent/edit标识、每会话active/pin和上述四个索引，随记录数线性增长。每次编辑/重试保留历史分支与原有产物/事件/账本，增加的历史成本随版本数增长；fork沿用既有对象复制上限与TTL。搜索不是FTS，最坏每页1000条内容lower/LIKE，PG字符串处理CPU未归因；若高并发或大文本搜索成为热点，应单独评估索引适配器，而不是放宽当前扫描预算。重复文本、单用户热缓存不能代表真实中文、难压缩文本、S3、2vCPU/8GiB联合容量或长期吞吐。

## 验证与剩余事项

测试使用本工作树源码与已有Python3.12解释器，未修改依赖或开发.env；GNU coreutils/findutils加入PATH。固定最终基线 `e4c70cb`：

- Ruff check、format --check通过，291个Python文件；git diff --check通过。
- SQLite完整 `pytest tests -ra`：**1004 passed / 110 skipped**，133.86秒。
- PostgreSQL16 + Redis完整 `pytest tests -ra`：**1081 passed / 33 skipped**，171.62秒。
- 独立双库最终索引/EXPLAIN检查2项通过；测试中的迁移up/down/re-up和drift随完整套件执行。首次未修测试数据时SQLite997通过/110跳过/3失败，PG1072通过/33跳过/5失败：备份/服务权限数据未设置树指针、0018中间迁移使用head ORM、大树超过1000行使对账测试单页假设失效，均已修复并全套重验。另一次PG165.18ms分页失败的查询根因与修复见上文，不删除红态证据。
- `8cce3f7`和`72f5e69`两轮各SQLite1004/110跳过、PG1081/33跳过是更早代码验证，只作为迭代历史，不冒充最终基线。微探针固定72f5e69；最终索引追加后未重复容量数字。

SQLite110跳过包含未配置PG/Redis等真实依赖。PG套件的33跳过：真实Docker/OpenSandbox生命周期/传输/网络/技能/恢复，S3，ClamAV，Keycloak，公开外网抓取，以及Redis scan cursor通用契约和PG行锁不适用的并发claim注入。真实PG迁移/专用并发和Redis队列/契约已执行。SQLite对既有表达式索引uq_files_folder_name的反射警告仍在；新普通/部分索引与新增列的drift检查通过。

明确跳过Portal npm/build、浏览器、五服务E2E（本任务要求父Agent集成验收）；未运行真实外部付费模型、百万行迁移或目标机长期容量。临时PG/Redis用独立目录与非默认端口15643/16643，从未连接开发数据库；已关闭本任务PG/Redis实例，并用TCP探测确认15643/16643均不再监听；未触碰其他实例。

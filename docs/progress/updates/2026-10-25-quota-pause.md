# 用户日/月额度耗尽暂停（2026-10-25）

授权：后端实现子任务，仅本地提交，不改Portal，不push/PR/merge/deploy。工作树 `/Users/michael/.t3/worktrees/ailab-lite/t3-cfac648a`，分支 `feat/quota-pause`，base `80d3b4a3ce2a04061d8c19abbba3b596715a67e7`；该基线已包含Portal消费契约的`1d531fe`及长任务/来源集成。产品/回归基线 `b90ffa8b323f7cb0560130cdf82434ffd4cf9d37`（主实现`30a1fb3`，HTTP重试修复`b90ffa8`），文档另提交。本轮不是上线验收。

## 最终行为与契约

- 只在Agent模型预留接缝识别API业务429 `detail.code=quota_exceeded`，转为QuotaPause（BudgetPause子类）。不当作LLMError，不重试预留/模型、不降级、不增加LLM失败计数。标题推理仍独立best-effort，不因标题预留失败暂停成功主回答。真实上游429与其他ClientError沿用原契约。
- 保存checkpoint、工作区和已有previews/artifacts/plan/sources，停止追踪的后台命令，跳过普通预算暂停的leftovers自动交付；状态`interrupted`、`resumable=true`，不自动续跑。事件`agent.budget` payload 为 `{"kind":"paused","reason":"<下述同一句中文>","minutes":<活跃分钟，保留一位小数>}`。
- `run.error`日额度：`今日额度已用完，已暂停，额度恢复后可继续`；月额度：`本月额度已用完，已暂停，额度恢复后可继续`。
- `run.result.pause` 为 `{"kind":"quota","window":"daily_tokens"|"monthly_tokens","resets_at":"<ISO UTC，例如2026-11-01T00:00:00+00:00>"}`。恢复成功同事务移除pause；后续非额度暂停即使省略result也移除。其他部分结果继续合并保留。
- `POST /v1/runs/{id}/resume`仍超额时维持429 `{"detail":{"code":"quota_exceeded","window":"daily_tokens"|"monthly_tokens","resets_at":"<ISO UTC>"}}`，Run仍interrupted/resumable且结果不变。提高额度后准入成功并执行。新增消息准入原已符合相同detail。手动queue/resume此前吞成409，本轮修复为透传原准入错误；后台终态自动派发仍保存队列暂停，不回滚成功终态。
- 正剩余额度小于保守reservation也暂停。准入只检查已用/预留是否达上限，未达到时允许人工继续；下一次保守预留仍不够会再次暂停，每次人工动作至多尝试一次，不自动循环。单Run累计`run_max_tokens`不重置，维持独立409 `本轮累计用量已达上限，请在新任务中继续`。
- 月度恢复不能先被生命周期清掉：额度暂停的取消宽限期起点为max(now,resets_at)，重置后仍有既有`ask_user_timeout_hours`（默认72小时）人工继续窗口；不是永不回收。workspace GC保护非终态Run的精确checkpoint工作区对象，终态和被替代快照保持原TTL。

## 当前验证

除独立archive基线探针外，全部命令从本worktree `platform/` 执行，Python3.12复用主checkout虚拟环境但导入本worktree源码，macOS使用GNU coreutils/findutils PATH；Portal依赖硬链接本地副本，未修改Portal源码。日志/JUnit/EXPLAIN在本机 `/tmp/ailab-quota-cfac/`，不提交原始日志。

| 套件 | 本轮结果 |
|---|---|
| 变更前基线 | 隔离archive原基线：10项业务回归失败、1项瞬态429对照通过；最终定向11 passed / 0 skipped / 0 failed，1.776秒 |
| 定向：新契约/fault、workspace TTL、生命周期 | 通过，完整计数以全量JUnit为准 |
| Ruff check / format | 349文件通过 |
| SQLite `pytest tests -q` | 1191 passed / 121 skipped / 0 failed，188.829秒，最终基线`b90ffa8` |
| PG16+Redis `pytest tests -q` | 1277 passed / 35 skipped / 0 failed，241.224秒，最终基线`b90ffa8` |
| Portal `npm test` / `npm run build` | 38通过/0跳过，默认production build通过 |
| 五服务E2E | 98 passed / 0 skipped / 0 failed，291.347秒，最终基线`b90ffa8` |
| 10000行EXPLAIN | SQLite `sqlite_autoindex_runs_1`、PG `runs_pkey`；既有运行时查询索引断言一并通过 |

新增回归覆盖HTTP业务429仅请求1次/瞬态429最多4次，以及日/月额度、真实API reserve拒绝、保守预留余量、快照/预览/来源/计划/产物保留且禁止额外交付、暂停事件、恢复429且仍可继续、提高额度后执行并清pause、继续后预算暂停清标记、API省略result仍清标记、fallback/重试/失败计数、消息/手动队列准入、月度超时/快照GC，以及暂停后ACK丢失重投不执行、旧ACK不删除人工继续的新job。

失败历史：最初变更前探针因顶层导入新QuotaPause导致collection失败，已改为断言既有BudgetPause类型，重跑取得9项业务断言失败。定向初轮修正测试夹具的无job_id默认、快照key前缀、已结束attempt不可重复pause及指标名称，未放宽业务断言。首次全量忘记GNU PATH，两个原有snapshot用例因macOS stat不支持`-c`失败并主动中止；随后使用仓库指定PATH重跑全套。`30a1fb3`完整SQLite1189通过/121跳过、PG1275通过/35跳过；继续核查发现共享HTTP usage重试层仍重试429，补`b90ffa8`与HTTP次数回归后重跑最终全套，不能以先前绿代替最终代码验收。新增GC查DB后，独立生命周期测试必须把collector的SessionLocal绑定本测试factory，workspace TTL测试初始化隔离API，避免读另一数据库。

## 成本、清理与边界

无迁移、新配置、依赖或后台任务。正常模型成功路径不增加请求；quota拒绝在共享HTTP usage重试层也立即停止，复用已有保存/预览/快照工作，额外JSON字段为常数规模。Workspace GC每页最多`gc_batch_size`（默认100）对象，一次按既有Run主键索引批查，仅投影runtime_meta，不加载prompt/result；空页无查询。EXPLAIN在SQLite/PG各10000行验证。快照磁盘保留延长至额度重置后人工宽限期，最多当前被引用的一个对象/Run；旧对象按TTL删除。没有目标2vCPU/8GiB机器的CPU/RSS/IO实测，不将索引/有界内存推导当作容量验收。

专属PG16.15与Redis8.10.2绑定127.0.0.1:35682/36689；业务库`ailab_test`、迁移库`ailab_migrations`均本任务新建，启动前端口空闲，Redis pidfile与INFO process_id核对一致。五服务预留38480–38483/33400。E2E夹具已停止五服务；PG以本轮data-dir执行pg_ctl停止，Redis核对pidfile与INFO process_id=55576后关闭。35682/36689/38480–38483/33400七端口最终均无监听，未停止其他服务。

SQLite121跳过为未配置PG/Redis与外部组件；PG35跳过：Docker11、OpenSandbox10、S3 8，ClamAV/公共fetch/OIDC/网络targets/Redis scan cursor/PG dispatcher锁语义分支各1。不将skip计为通过。

真实provider跨日/月运行、真实沙箱长时间磁盘保留及目标机容量未验；重置后路径以提高额度模拟解除拒绝，未等待真实日历月份。已有未配置外部组件按全量skip如实保留。父任务下一步按顺序cherry-pick `30a1fb3`、`b90ffa8`及本轮文档提交并核对最终树；无待实现修复，不启动发布。

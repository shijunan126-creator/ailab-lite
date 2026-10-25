# 2026-10-25 长任务与来源集成

授权仅本地实现、测试和提交；不push/PR/merge/deploy，不改Portal行为。固定集成基线`abb4a9f`包括Portal来源面板`aa1b31e`与长任务提示`abb4a9f`、来源后端`41da18f`、长任务后端`8d7762d..a1d2137`。`loop.py`合并冲突已检查：步数耗尽走BudgetPause，正常最终结果保留sources，两者均在。

修复工作树`/Users/michael/.t3/worktrees/ailab-lite/t3-12d66bb9`，实际分支`feat/long-runs-sources-fixes`，base `abb4a9f`，修复提交`b23730e`，consumer契约测试`fe78365`，E2E定位器适配`c7a3290`。指定分支`feat/long-runs-sources`同时在主checkout使用；执行期间主checkout并发提交`1d531fe`，共享分支导致本地旧index呈现反向Portal差异。已隔离回指定基线并保留父任务提交，未提交反向差异、未改Portal产品文件。本轮证据不覆盖`1d531fe`；父任务接收本轮提交后须复核最终树。

## 修复与回归

1. Job续租：正常仍每min(60, lease/3)秒一次；瞬态异常在剩余安全窗口内指数退避，默认15/30/60秒；每次总超时最多10秒，且不越过剩余安全窗口。只有成功响应才更新本地所有权期限，按请求开始时间保守计时；初始期限从claim请求前开始，网络响应时间不授予额外租约。409立即取消工作；持续故障在lease减min(30秒, lease/5)处停止，默认300秒租约在270秒边界停，durable job保留恢复。Fault回归覆盖1/2次503后恢复且超过两轮原始租约仍继续、409、连续503、挂起HTTP调用和claim耗时。
2. 崩溃结果：API transition在用户锁/attempt fence内对FAILED的部分result与旧run.result合并，后续新值覆盖同名键；错误仍以FAILED、run.error及run.failed事件暴露。真实API+checkpoint+pause+resume+AgentLoop回归分别注入ClientError和RuntimeError，保留旧previews/plan/自定义键与sources，不把崩溃伪装为成功。
3. 暂停来源：waiting_user、BudgetPause及LeaseInterrupted都传result.sources；API pause在用户锁内合并部分结果。来源仍由原工具/checkpoint路径按URL去重、单工具10条/整轮50条，不增加历史事件回放。回归覆盖三类暂停；原run-sources的token预算“failed”断言更新为ADR0008的interrupted。
4. 输出契约：单Run与conversation.turns均已有result.sources/run.error，回归验证resumable=true时来源、旧预览和中断原因均可重新读取。无需改Portal或DTO。

## 当前验收

测试命令以本worktree `platform/`为cwd，复用主checkout Python3.12虚拟环境，实际导入本worktree代码；GNU coreutils/findutils PATH。依赖目录使用本地副本/硬链接，Portal由E2E夹具复制同基线源码后运行默认production build。完整日志与JUnit保留在本机`/tmp/ailab-integration-12d6/`，不提交原始业务/测试数据。

| 检查 | 本轮结果 |
|---|---|
| Ruff check / format --check | 均通过，347个Python文件 |
| 定向来源/恢复与心跳 | 来源/恢复18项通过；补充claim计时后心跳6项通过 |
| 完整SQLite `pytest tests -q` | 1180 passed / 121 skipped / 0 failed，178.417秒；产品基线`fe78365`（与`c7a3290`后端树一致） |
| 完整PG16+Redis `pytest tests -q` | 1266 passed / 35 skipped / 0 failed，228.406秒；产品基线`c7a3290` |
| Portal `npm test` / `npm run build` | 37通过/0跳过；E2E独立副本默认production build通过 |
| 五服务 `AILAB_E2E=1 pytest e2e -q` | 98 passed / 0 skipped / 0 failed，291.365秒；基线`c7a3290` |

SQLite跳过121项为未配置PG/Redis和外部服务契约；PG跳过35项：Docker11、OpenSandbox10、S3 8，ClamAV/公共fetch/OIDC/网络targets/Redis scan cursor/PG dispatcher锁语义分支各1。没有将跳过计为通过。

隔离PostgreSQL16.15/Redis8.10.2绑定127.0.0.1:35572/36579；PG业务测试库`ailab_test`、迁移库`ailab_migrations`均本任务新建。E2E五服务端口38280–38283/33200、Redis DB13；后端Redis契约DB15。启动前核对端口空闲，启动后核对postgres/redis进程归属。测试后PG以pg_ctl停止、Redis核对pidfile与INFO process_id=14209后关闭，E2E夹具停止五服务；最终35572/36579/38280–38283/33200七个端口均无监听。

失败历史：新增回归在修复前复现8项失败（瞬态续租、持续失败截止和暂停来源），409立即停止原已通过；在只修pause合并后，两类续跑崩溃仍因缺失previews失败，之后修FAILED合并通过。首个定向命令误从仓库根启动，SIGKILL子进程无法导入tests.faults；从platform重跑18项通过。首次全量SQLite因补齐claim耗时边界主动中止，不计最终验收；随后完整运行中的旧consumer测试仍要求一次ConnectionError立即停止，已将其改为真正的409所有权丢失，瞬态容错由新fault测试覆盖。首次完整SQLite为1179通过/1失败/121跳过，PG为1265通过/1失败/35跳过，均仅此旧consumer断言失败；随后按新契约重跑完整双库。首次E2E为94通过/4失败/0跳过，380.350秒，原因均为Portal来源侧栏改版后的旧定位器（关闭预览按钮、preview-tab类和文件预览区域名）；仅修改E2E以当前pane-tab/侧栏/文件关闭按钮及Esc定位，保留焦点、选择、关闭/返回和滚动验收，没有修改Portal产品代码。

## 成本与剩余边界

无新依赖、迁移、查询、轮询或后台任务。健康路径每运行Run每分钟一次已有索引单行续租UPDATE+commit；故障路径默认15/30/60秒退避，只在原lease安全期限内重试，避免API短暂重启重做长任务。API结果合并在原用户锁/事务中完成，CPU与内存随已有result大小线性，来源额外至多50条；不复制大文件或base64输入，也不新增常驻缓存。没有新CPU/RSS/磁盘容量实测，不外推2vCPU/8GiB负载表现。

未验真实模型连续1–4小时、真实Linux/OpenSandbox/Docker sandbox后台exec及目标机cgroup/RSS/长期容量。宿主子进程/SIGKILL与mock五服务证据不能替代这些边界；来源真实外网provider与中断部署演练亦未验。下一步由父任务cherry-pick修复/交接提交、复核最终Portal集成树；发布与真实长期验收另行授权，不自动开始下一路线项。

## 最终整树复核与额度暂停GC修复

此节为最终集成验收，覆盖上文各隔离子分支的结果；上文计数及“等待cherry-pick”均为历史阶段。用户授权本地修复/完整重验/提交，不改Portal，不push/PR/merge/deploy。

### 基线与范围

- 集成分支 `feat/long-runs-sources@0caa73b058bbde291a755fd38c53ce8b9befd101`，共同base `origin/main@09599a03e774983a2bb66202c2376e04ada58e94`；修复分支 `feat/quota-pause-gc`，工作树 `/Users/michael/.t3/worktrees/ailab-lite/t3-59566e53`。起始实际分支名为T3的`t3/59566e53`但HEAD与指定集成head一致，工作树干净，直接从该HEAD创建修复分支。
- 最终产品/测试提交 `65b47e0`，其父为 `0caa73b`。已包含Portal来源面板/引用入口、Run来源持久化与checkpoint恢复、长任务时间/步数/token预算、后台exec生命周期、短claim续租与故障退避，以及 `30a1fb3`/`b90ffa8` 的额度暂停/HTTP拒绝不重试契约。`0caa73b`为额度交接文档。无需再次接收已包含的历史子分支提交。
- 测试从本工作树 `platform/`启动，复用主checkout的Python3.12虚拟环境但导入本工作树源码；GNU coreutils/findutils PATH。Portal node_modules为本地硬链接副本，未改Portal源码。测试原始日志/JUnit/EXPLAIN存于本机 `/tmp/ailab-gc-5956/`，不提交运行数据。

### 根因、修复与EXPLAIN

旧GC从对象页提取Run ID再构造IN条件，并对对象页额外list化。核对实现后确认`_page()`本身只取`gc_batch_size`个对象的一页并持久cursor，并非一次遍历所有存储对象；但对象数量驱动SQL参数的耦合不合理，也不应额外物化迭代器。

新实现先按queued/running/waiting_user/interrupted四个非终态状态读取ID/runtime_meta，每页最多`gc_batch_size`，按ID keyset继续，只保留非空checkpoint.workspace.key。然后直接迭代当前对象页，保留精确引用key，旧快照/终态对象继续按原TTL删除。SQL每页最多4个绑定参数（SQLite含LIMIT/OFFSET），与快照数无关；不读prompt/result，不新增模型请求、外部调用或后台任务。

仅已有status索引时，初始10000行EXPLAIN显示SQLite可能为ORDER BY id沿主键遍历终态历史，PG小集合则用status索引后排序。因此新增Alembic `0030`（父0029）创建`ix_runs_status_id(status,id)`，模型同步；升级/降级/再升级保留原Run，全链测试从0021至0030并回退。无新增配置/依赖。

最终EXPLAIN探针为10000 Run（9600终态、四种非终态各100），10000 outbox、1000 checkpoint；四个状态均执行实际等价的`status=:status AND id>:after ORDER BY id LIMIT 100`：

- SQLite：`SEARCH runs USING INDEX ix_runs_status_id (status=? AND id>?)`，无需临时排序。
- PostgreSQL16.15：每状态`Index Scan using ix_runs_status`，`Index Cond: status=...`，`Filter: id>...`后对至多100个状态匹配行排序；无顺序全表扫描。优化器仍偏好既有更窄索引，未强制指定或禁用seqscan。复合索引提供更大/稀疏集合的有序访问路径，但本次不声称已验生产规模计划。
- 原waiting/claim/checkpoint-object/checkpoint-GC四项索引探针同时通过。原探针以9850个queued为主，只适用于旧主键IN查询；本轮明确更换为终态历史占多数的GC分布。最初“PG必须选新索引”的过严断言已按实际允许status索引修正，保留原始日志说明，不将其误报业务故障。

两项新增回归以23个暂停Run、页长7及2000个过期对象覆盖：lazy迭代器禁止删除前预读全部对象；eager页则用statement计数约束每条查询参数及LIMIT，二者都验证跨多页精确保护。隔离替换回原`0caa73b`的reaper时两项均失败，修复后通过。定向含额度/TTL/迁移18通过、2个未配置PG跳过；定向初次因迁移链预期漏加0029父节点失败，已修正并重跑，最终全套包含双库升降级。

### 最终完整验证

全部基于产品/测试提交`65b47e0`，此后仅改交接文档；双库均完整运行一次，未以历史计数代替本轮最终树。原始日志与JUnit在上述本机目录。

| 检查 | 最终结果 |
|---|---|
| Ruff check / format --check | 通过，350个Python文件 |
| SQLite `pytest tests -q` | 1194 passed / 122 skipped / 0 failed，182.58秒 |
| PG16+Redis `pytest tests -q` | 1281 passed / 35 skipped / 0 failed，230.07秒 |
| `cd portal && npm test && npm run build` | 38 passed / 0 skipped / 0 failed；默认production build通过 |
| PG16+Redis五服务 `AILAB_E2E=1 pytest e2e -q` | 98 passed / 0 skipped / 0 failed，296.89秒；模型/沙箱为mock |
| 10000行SQLite与PG16 EXPLAIN | 四种非终态状态查询与四项既有runtime查询索引断言通过 |

完整套件没有失败。SQLite122跳过包括未配置PG/Redis与外部组件；新增0030迁移往返在SQLite通过、PG参数分支在SQLite轮跳过，PG轮通过。PG35跳过：Docker11、OpenSandbox10、S3 8，ClamAV/公共fetch/OIDC/网络targets/Redis scan cursor/PG dispatcher锁语义分支各1。既有Starlette测试客户端弃用和SQLite表达式索引反射警告保留，未将skip计为通过。E2E独立Portal副本另执行同基线test/build，不代替上表直接命令。

### 成本、清理与剩余验收

每轮GC对象页仍最多`gc_batch_size`（默认100）；metadata临时内存受页长限制，持久到本轮结束的保护集仅含非终态Run引用的key，内存O(被保护key数)，不随历史快照对象总数增长。每状态至少一次有界SELECT，再随该状态Run数按页增长；空存储也会有四个最小查询。数据库索引额外磁盘O(全部Run数)，每次status变化维护一次，迁移建立索引需要扫描历史Run；没有在目标机测索引建立时间/锁影响。保留快照磁盘契约不变，不声称消除大量暂停任务自身的容量开销。

专属PostgreSQL16.15绑定127.0.0.1:35782，Redis8.10.2绑定127.0.0.1:36789；本任务新建data-dir与`ailab_test`/`ailab_migrations`/`ailab_e2e`三个数据库。后端契约Redis DB15，E2E DB13，五服务端口38580–38583/33500。均启动前确认端口空闲，启动后核对监听进程归属。最终E2E夹具停止五服务，Redis核对本轮pidfile与INFO process_id=87845一致后shutdown，PG使用本轮data-dir经pg_ctl fast停止；35782/36789/38580–38583/33500七端口最终均无监听。未停止其他用户进程。

仍未验真实provider连续1–4小时、真实跨日/月额度恢复、真实Linux/OpenSandbox/Docker后台exec与长期快照保留、目标2vCPU/8GiB机器RSS/CPU/磁盘、真实手机键盘/触控。五服务模型/沙箱替身与宿主子进程证据不能代替上述验收；外部服务skip保留。未合并main、未push/PR/merge/deploy。父任务下一步接收本修复分支的产品和文档提交，按独立授权安排真实长期/目标机/手机验收；不自动发布或开始下一路线项。

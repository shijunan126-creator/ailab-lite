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

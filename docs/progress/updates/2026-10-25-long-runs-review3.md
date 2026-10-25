# 长任务第三轮独立审查修复 · 2026-10-25

## 范围与基线

父任务授权 implementation sub-agent 修复独立审查的12项发现；不改 Portal、不 push/PR/merge/deploy。工作树 `/Users/michael/.t3/worktrees/ailab-lite/t3-a50ec0f9`，起始 T3 分支 `t3/a50ec0f9` 干净，HEAD 与指定集成基线 `feat/long-runs-sources@a0a3ae008e93f00653cb3d1a213ea08a2f02fcf7` 一致。新分支 `feat/long-runs-review3`。

产品/回归提交 `490de63107066177ac69336a2a3f1612b37bc13a`，Mock 文件传输修正 `a96cd842dd87c6fc5a9a4e7ce1c4e47991f17325`。最终整树验证基线为 `a96cd84`；后续只更新本文、current、HANDOFF、ADR0008和能力矩阵。旧交接中的 v2 待集成与旧分支 HEAD 是历史状态，本轮以实际起点和父任务最新授权为准。

## 逐项结论

12项均确认并修复；#9/#10没有被反驳。以下测试名均位于本分支，真实provider与宿主测试分开列证。

| # | 确认与修复 | 回归证据 |
|---|---|---|
| 1 | Docker expiry PID 1显式忽略SIGCHLD以回收退出子进程。helper使用每租约一个常驻父进程启动supervisor，Linux启用PR_SET_CHILD_SUBREAPER并忽略SIGCHLD，回收孤儿后代，不依赖OpenSandbox的PID 1。supervisor自身恢复SIGCHLD默认处理以保留命令真实exit code。Linux身份检查拒绝Z；无exit文件的已死进程持久化exit_code=-1及检查文件提示，可被淘汰。 | `test_linux_zombie_is_not_alive`、`test_supervisor_disappearing_without_exit_is_failed_and_prunable`；真实Docker/OpenSandbox压力契约见下。 |
| 2 | running/no-op wait不置脏、不触发周期tar；完成wait/kill首次确认时有意置脏并保存，重复完成句柄不重复保存。存在后台命令时，快照不能清掉工作区可能继续变动的标记。新checkpoint CAS提交后立即best-effort删除同Run被替代快照，同事务写LifecycleDeletion意图覆盖崩溃/删除失败；未改变0030精确保护集与TTL兜底。 | `test_running_polls_do_not_checkpoint_workspace`（6次running poll仅最终1次快照）、`test_superseded_workspace_deletion_waits_for_winning_checkpoint`（stale CAS不删除、胜出后旧对象消失且最新对象可读）。 |
| 3 | 原始用户消息索引在真实分页/历史摘要assembly完成后计算。 | `test_original_user_index_comes_from_real_paginated_assembly`覆盖12轮旧history、摘要与分页后消息索引；既有整组压缩回归继续验证原始输入和assistant/tool配对。 |
| 4 | kill-all为best-effort，错误记录日志，由lease release最终销毁沙箱；暂停的预览、补交与快照失败不改变interrupted结果，不宣称已保存最新工作区。409业务所有权围栏仍传播。 | `test_cleanup_failure_does_not_fail_success`、参数化`test_pause_cleanup_failure_still_pauses`；既有ask_user/legacy registry清理、租约故障回归。 |
| 5 | Run累计token cap改为FAILED终态，保留摘要/来源/已交付产物并best-effort补交/快照。预算不足无法完成的任务不声明成功；终态释放会话，用户可新建任务。沿用FAILED暂停收件箱队列的既有契约，用户显式继续队列可准入新Run。日/月额度仍是可继续的quota暂停。 | `test_token_cap_is_terminal_and_preserves_sources`、`test_run_token_cap_releases_conversation_and_permits_inbox_resume`及来源/恢复旧回归更新。 |
| 6 | 收尾阶段exec有固定120秒生命周期，仍受helper、工具yield与三步收尾上限约束；普通阶段仍限制到剩余窗口。 | `test_wrap_up_exec_has_bounded_useful_lifetime`，既有remaining-budget kill回归。 |
| 7 | helper以SHA256命名，首次通过put_file安装，调用时验hash；请求JSON经一次性文件传输，helper读取后删除。每租约复用，Agent重启可重复安装、新租约独立安装；模型命令按原20,000字符上限验证，不与helper/JSON转义竞争Broker命令长度。 | 三种19,997字符ASCII/CJK/引号命令回归；`test_helper_is_installed_once_verified_and_reinstalled_on_agent_resume`覆盖复用、重装与篡改拒绝。Mock另有文件内/workspace路径映射回归。 |
| 8 | 完成回答或finish的Run即使越过4h也成功；只有未完成时触发wrap/pause。 | `test_finished_answer_crossing_time_budget_succeeds`及更新的4h收尾回归。 |
| 9 | 真实可不收敛：保留的原始输入/系统提示/最近两组仍可能超过65%。记录post-compaction estimate/limit，不能降到阈值以下即清楚暂停；恢复也在再次付费压缩前拒绝重复整理。 | `test_context_nonconvergence_pauses_before_second_paid_compaction`；第二次调用不增加reservation。 |
| 10 | 确认旧小结果累计越界：1200个约3.9KB结果的日志为5,023,519B。所有已消费结果改为完成状态/错误/不确定标记与参数/结果指纹；未消费pending全文、started非重放围栏保留。每次checkpoint按API同一JSON/UTF-8规则验算；仍过大时写入有界、不可执行的冻结恢复记录，再清楚暂停，避免413后FAILED。冻结记录保留工作区、预算、unfinished started标记，按容量保留交付/产物/来源/计划/日志；人工继续也不能执行被省略的paid/pending记录。 | `test_consumed_log_multi_window_size`、`test_oversized_checkpoint_pauses_instead_of_413`、既有pending/非重放/SIGKILL恢复回归；四窗口完整状态测量见下。 |
| 11 | 步数耗尽总结和`out_of_steps_done`一起checkpoint；同窗口重投直接暂停，不再发付费总结或追加平台提示。明确开启新窗口时清标记。 | `test_out_of_steps_summary_not_repeated_after_redelivery`。仍不承诺provider成功返回到checkpoint提交前的外部调用exactly-once。 |
| 12 | `budget_window_id`独立于队列job_id。answer与lease自动继续只换job，不换窗口；显式用户继续budget/quota暂停才新建窗口并注入解除收尾提示。新Agent预算暂停写`result.pause.kind=budget`，日/月额度保持quota。 | 参数化`test_only_explicit_budget_resume_resets_window`覆盖answer/automatic/budget；原步骤窗口与pending配对回归。 |

红绿：在独立detached工作树的原始产品 `a0a3ae0` 上运行当前专项回归22项，21失败、1通过（原来显式人工budget继续已经可换窗口）；没有以当前实现失败代替旧基线复现。三种大命令、Z态、缺失exit、真实assembly、running poll、120s收尾、清理失败、token终态、不收敛、超大checkpoint、总结重投与非人工换窗口均为旧基线失败。删除测试在旧树证明旧快照仍存在。日志与JUnit仅保留本机 `/tmp/ailab-review3/`，不提交原始测试业务数据。

## 性能与容量

- 不增加依赖、服务或数据库迁移。每个使用exec的租约新增一个常驻reaper；最多8个活跃supervisor/保留句柄共享同一上限，既有日志8MiB上限不变。reaper每50ms扫描最多8个请求目录；Linux路径不启动ps。宿主macOS回归需要ps身份探针，不能当成目标Linux成本。
- 宿主30次`true`测量：helper只上传1次，30次控制exec；单命令中位0.343s、最大0.406s。reaper RSS 15,904KiB，ps采样CPU 1.0%、累计CPU 0.15s；包含Python启动和macOS身份探针，不是2vCPU/8GiB生产容量承诺。
- helper源11,947B，每租约首次1次put_file；每次控制多1次小JSON put_file（最长20,000字符命令约60KB UTF-8），随后1次Broker exec。校验hash只读约12KB。请求文件正常读取即删除，helper/log不进入workspace；崩溃遗留的一次性请求可能随workspace被保存，但不会自动执行。
- 1200条已消费观察最终日志351,919B，旧实现5,023,519B。含1200条约3.9KB工具消息的四窗口完整状态5,125,517B，安全冻结到352,091B，本机容量检查约13ms。一般未越界时仍有一次有界JSON大小验算；最大4MiB不会以413冒充产品失败。实际Token估算不替代字节验算。
- running poll不再有60秒整目录tar/上传成本。实际变更快照仍有原60MiB/次和60秒debounce预算；每次快照增加一个小删除意图与一次旧对象删除，失败由已有GC批量重试，无新后台任务。极端存储持续不可用仍可积累有界Run工作产生的orphan/删除意图；不能承诺故障期间磁盘零增长。

## 最终验收与环境

最终产品/测试树 `a96cd84`，macOS宿主、Python3.12，所有Python命令从本worktree的platform目录执行，并添加GNU coreutils/findutils PATH：

| 检查 | 当前最终结果 |
|---|---|
| Ruff check / format --check | 通过，355文件 |
| SQLite `pytest tests -q` | 1246 passed / 125 skipped / 0 failed，184.533秒 |
| PG16 + Redis `pytest tests -q` | 1333 passed / 38 skipped / 0 failed，226.012秒 |
| Portal `npm test` / `npm run build` | 38 passed / 0 failed；production build通过 |
| PG16 + Redis五服务E2E | 98 passed / 0 skipped / 0 failed，295.162秒；mock模型/沙箱 |
| helper宿主定向回归 | 28 passed / 0 failed；真实subreaper/prctl不在此计数中 |
| 原基线专项红绿 | 原始产品a0a3ae0：22项中21失败/1通过 |

PG38跳过为Docker12、OpenSandbox11、S3 8、宿主非真实Linux namespace压力项1，以及ClamAV/公共fetch/OIDC/网络targets/Redis scan cursor/PG dispatcher锁语义分支各1。SQLite125跳过包含未配置PG/Redis、Docker/OpenSandbox/S3等外部组件；新增真实exec压力契约的mock项有意跳过（宿主非Linux namespace），Docker/OSB项无后端而跳过。保留既有Starlette TestClient弃用、SQLite表达式索引反射警告。最终E2E独立Portal副本另执行同源码38项和build，不代替直接npm检查。

隔离PostgreSQL16监听127.0.0.1:36082，专属data-dir `/tmp/ailab-review3/pg`；业务、迁移和E2E各用本轮新建数据库。Redis监听37089，pidfile记录16261且INFO归属一致；后端DB15、E2E DB13。五服务38880–38883/33800。最终E2E夹具停止五服务；Redis INFO再次核对PID16261与本轮pidfile一致后shutdown，PG按本轮data-dir核对PID16246后pg_ctl fast停止。36082/37089/38880–38883/33800七端口最终lsof均无监听，临时红绿工作树已移除。未连接个人业务库、未终止其他用户服务、未更改既有运行环境。

失败与重跑记录：首轮SQLite有7项失败，包含旧日志/额度/来源/技能断言、错误地混用budget_window_id与job_id的状态查询接线，以及旧快照只排队删除的不足；均保留日志，后续修正并整套重验。定向测试初期清理夹具必须验证PID身份，不能信任故意伪造的meta；未把该夹具中断计为产品成功。首轮E2E94通过/4失败，根因是Mock原来只翻译内联cmd，文件payload的/workspace未翻译；新增回归先红后绿，在 `a96cd84` 修复。中间SQLite完整运行在冻结最终Mock基线时主动停止；最终必须从同一产品基线重跑。E2E重跑前重建本轮自己的临时库，避免文件名重名污染。Portal直接build改写的next-env.d.ts已还原，最终Portal源码与base完全相同。

真实Docker：`docker info`连接 `/var/run/docker.sock` 返回 `connect: no such file or directory`，`docker context ls`只有default；未擅自启动或重配个人Docker/VM。新增真实provider契约会在同一租约执行270次命令后检查/proc僵尸、tar和kill-all；本机Docker/OpenSandbox不可用时明确skip，宿主ps/单元证据不替代真实PID namespace、prctl/gVisor/execd验收。

## 剩余边界与下一步

父任务审阅并接收本轮两个产品提交与文档提交；不自动集成、发布或启动其他路线项。真实Docker/OpenSandbox 270次压力、真实模型1–4小时（含额度重置）、目标2vCPU/8GiB CPU/RSS/磁盘和真机手机仍待外部验收。进程清理失败会保留已完成结果并依赖租约销毁；未完成写入不保证完成。容量冻结会明确要求新任务，不宣称可继续执行已省略的上下文。旧版本已经写成不可继续token暂停的Run仍需用户取消；本轮修复保证新发生的累计上限为终态，不对历史业务数据做隐式迁移。

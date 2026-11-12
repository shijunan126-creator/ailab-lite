# 工作区工具对齐 Pi（2026-11-11）

- 用户要求：把 `read_file`/`write_file`/`list_files`/`exec` 换成 Pi 的 `read`/`write`/`edit`/`bash`（另补 `grep`/`find`，底层 `rg`/`fd`），逻辑与性能优化照 Pi 源码做；随后临时调整 bash 的等待与期限。分支 `feat/pi-aligned-workspace-tools`，base `origin/main@7d17db92`，工作树 `.worktrees/pi-aligned-workspace-tools`；未授权推送、合并、部署。
- 已完成：九个工具落地（行窗口、容错 `edit`、FIFO 文件锁、沙箱内 `ls`/`grep`/`find`）；bash 把「等待窗口 / 平台期限 / HTTP 单次等待」分成三件事，一次工具调用内部多段有界等待、不忙轮询、不按 idle 杀进程，期限由沙箱 supervisor 到点强制，句柄确定化使重启后接管原执行而不是重跑；提示词、技能生成物、Portal 步骤名同步。
- 真模型验收（DeepSeek V4.1 Flash / OpenCode Go，16 个任务 29 次调用）：改前模型完全不用 read/edit/write、一律 bash + python 改文件；补 4 行工具选择规则后 read 出现在 9/16 个任务里，edit/write/ls/find/grep 均按意图使用。据此修了三处：grep/find 无结果写明搜索范围并给计数、bash 非 0 退出补 `Command exited with code N`、交付只在要成品时。等待窗口语义实测通过（10 秒窗口连续三次 bash_wait 后拿到最终结果，未被当成超时）。
- 验证：[记录](updates/2026-11-11-pi-aligned-workspace-tools.md)。ruff 通过；单元套件 3014 项、16 失败（后查明是本地未构建 Office 读取器 `runner.mjs`，见第五轮更正）、189 跳过；Portal 286 项 + `tsc` 干净。提示词 system 2591 → 2765 tokens，含 28 个工具 schema 时 7283。
- 容器内验证：按同一 base 与 Debian 快照装了 `ripgrep`/`fd-find`，在 uid 10001 下把 ls/grep/find/read/edit/write/bash/bath_wait/期限杀进程/kill 全跑通（`fd-find` 实际是 `fdfind`，探针回退必要）；由此修掉短命令丢失 `error`/`killed_by` 的真 bug。
- 整栈补齐：完整 sbx-office 镜像在本机 arm64 构建成功（4.85 GB，SMOKE PASS，rg/fdfind 在、渲染 1 秒、断网），contracts 135 项 0 失败（52 项缺外部服务跳过），E2E 210 项 0 失败（15 跳过），Next 生产构建通过，真 AgentLoop 下 3 个子任务并发 edit 同一文件三节全落盘。
- 未覆盖：amd64/双架构镜像与 k3s/gVisor 冒烟、E2E 的 docker/opensandbox 沙箱档、依赖外部服务的 contracts。
- 独立代码审查（2026-11-12）提出 3 条必须修 + 4 条应该修 + 补充项，已全部修复并各配"不修就失败"的用例：`edit` 模糊匹配只看匹配段（不再把整行全角标点折成半角）；重叠出现计数；子任务调用编号带父 step（否则第二批同命令复用旧句柄）；恢复时只有真的丢失才算 `UNCERTAIN_EXEC`、事件名沿用原工具名；`grep` 的 glob `removeprefix` 与 `--no-require-git`；`bash_kill` 成功即 ok；bash 结果统一一个形状（去掉 `output_tail`，缺口有 elided 标记）；`write` 进文件队列；`timeout` 参数忽略并附 note；主参数改 `command`；提示词 2765 → 2657 tokens。
- 验证：ruff 干净；全量 3166 项 / 16 失败（与未改基线逐条相同）/ 276 跳过；改动模块定向 342 项 0 失败；Portal 286 项 + `tsc` 干净；整栈 E2E 210 项 0 失败；真模型重跑 9 个任务，`command` 参数与 10 秒窗口多次 `bash_wait` 均正常。
- 第三轮审查（2026-11-12）指出的 5 条必须修 + 4 条应该修已全部落地：`edit` 改成 "oldText 编译成正则、直接在原文上匹配"（5 MB 16 ms、峰值约 8 MiB，原实现 403 MiB）、尾空格 bug、`max_edit_bytes` 2 MiB + `to_thread`；Broker 的 404/413/400 与 `SandboxFileNotFound`；子任务编号带父调用；恢复不再多发 `tool_call`（`announce=False`）；旧目录含退役名时重建并记 `tool_catalog_changed`；`lost` 标记取代 `exit_code == -1`；缺口用 helper 的读取起点；运行中用 `output`；MockLLM 发 `command`；精简目录补 `read`/`write`/`edit`；提示词版本号升级。
- 验证：ruff 干净；全量 3181 项 / 16 失败（与未改基线一致）/ 276 跳过；Portal 288 项 + `tsc` 干净；整栈 E2E 210 项 0 失败；真模型两轮 Work，第二轮前缀逐字节保留、`prompt_tokens` 单调增。
- 第四轮审查（2026-11-12）的 4 条必须修已落地：`edit` 从正则改成线性折叠匹配（2 MB 最坏用例 14.3 s → 6 ms，峰值 8 MiB，GIL 不再被整段占住）、`_file_error` 只映射 400/404/413（410 重新触发租约中断）、`search_images`/`save_skill` 按 404 判断、提示词版本按模式拆开（Chat 不重建）。另外：旧目录重建时同一边界刷新 `messages[0]`、边界原因区分 `model_changed`/`tool_catalog_changed`/`platform_rules_changed`、`edit` 用 `max_bytes` 提前 413；两个后续项也做了——同一回复里连续 `read`/`ls`/`grep`/`find` 并发执行（结果按 ordinal 回填），`read` 走 Broker 的 `offset`/`length` 只取 2 MiB 窗口。
- 验证：ruff 干净；全量 3192 项 / 16 失败（与未改基线一致）/ 276 跳过；改动模块 471 项全过；整栈 E2E 210 项 0 失败。
- 第五轮审查（2026-11-12）的 4 条必须修已落地：并发 `_checkpoint` 按 run 加锁（只读工具不再写 started 记录、也不单独 checkpoint）、`agent.tool_call`/`tool_result` 带 `call_id` 且前后端按 id 配对、provider 层实现真正的字节范围读取（大文件不再整份 413，Broker 只传窗口）、`read` 到窗口末尾给续读提示、`edit` 起点/行中空白的两个边界修正。
- 验收：全量 **3202 项 / 0 失败 / 252 跳过**（先按 CI 方式构建 `vendor/office-suite/runtime/runner.mjs`；此前记的"16 项既有失败"是缺这个构建产物，已在 dated update 更正）；Portal 290 项 + `tsc` 干净；整栈 E2E 211 项 0 失败。真模型 ADR 0030：Work 13 次调用 `cached/prompt` **95.9%**、Chat 5 次 **96.6%**，每次调用都是上一请求的逐字节前缀；真模型崩溃恢复里后台命令只启动一次，恢复用原 handle 等到了结果。
- 第六轮审查（2026-11-12）：上一轮的范围读在真实沙箱里不可用（Docker/OpenSandbox 的下载只收 `/workspace`，切片却写在 `/tmp`；mock 重写了该方法，所有测试都没执行到）。已改为 provider 各自实现的按行窗口（`tail -n +N | head -c L`），`read` 任意 offset 都能读、到末尾才报总行数；另修 `edit` 行尾判断与 checkpoint 锁清理。真实 Docker 容器 52 MB 文件第 350 万行 63 ms。详见 dated update 第六轮。
- 真实 OpenSandbox + 真模型（2026-11-12）：按行窗口在真实 OpenSandbox 上可用；调用链路优化后 `write`/`read`/`edit` 从 1–3 秒降到 0.04–0.1 秒，续轮第一个工具等沙箱从 9–13 秒降到约 1 秒，快照 4.1 → 1.1 秒；真模型 Work 缓存 98.5%、Chat 99.1%；真实契约 27 passed。详见 dated update 最后一节。
- 下一步：PR 的 CI 全绿后由用户决定合并；镜像重建与部署需用户当次明确要求。

---

# 记忆页不再显示来源对话（2026-11-11）

- 用户要求：记忆行去掉「来源对话 / 等 N 段对话」，后端保留来源数据并保证删除链正确。分支 `feat/memory-hide-sources`，base `main@bc5c5b5c`；未授权部署。
- 已完成：Portal 只显示内容与日期；API 视图不再返回 `sources`，删除无调用方的来源接口；删除链核对并补测试。详见[记录](updates/2026-11-11-memory-hide-sources.md)。
- CI：首轮双库只挂 `test_memory_v2.py` 的来源批量查询断言（该查询已删），改为断言视图不查来源；`e70c5d0d` 11 项全绿。
- 已合并：用户要求合并，[PR #187](https://github.com/shijunan126-creator/ailab-lite/pull/187) squash 为 `087c60a6`，提交树与 `e70c5d0d` 一致；分支已删除。
- 下一步：无待实现项；部署需用户当次明确要求（API + Portal）。

---

# Outbox 提交即派发（2026-11-11）

- 用户授权：按讨论实现“提交后直接叫醒派发”，不改变 outbox 可靠性契约。分支 `perf/outbox-wake`，base `origin/main@c8f93131`；改动只在 `/tmp/ailab-outbox-wake` 工作树，主工作区既有改动未动。
- 已完成：`after_flush` 标记本事务新增的 `OutboxMessage`，`after_commit` 唤醒 `dispatch_forever`；savepoint 内提交跳过（行对派发器尚不可见，由外层提交唤醒）。派发循环改为事件等待 + 1 秒轮询兜底，`reconcile` 节拍不变。Run/文件/预览/OCR/文档及直接写 outbox 的路径都覆盖，接口不用逐个接线。
- 验证：[记录](updates/2026-11-11-outbox-wake.md)。新增 5 项测试含红证据，相关模块 232 项通过 / 7 跳过，ruff 通过。[PR #185](https://github.com/shijunan126-creator/ailab-lite/pull/185) 11 项 CI 全绿（双库、三片 E2E、quality 等，最终 head 见 PR）；首次 postgres 失败是 broker 队列 0.1 秒墙钟断言，与本改动无关，重跑通过。
- 已合并：用户要求合并，PR #185 squash 为 `27ed82cd`，提交树与验证过的 head `39d85b02` 一致；分支与临时工作树已删除。
- 下一步：无待实现项；部署需用户当次明确要求（只需增量更新 API 镜像）。

---

# 定时任务绑定会话（2026-11-11）

- 用户要求：检查定时任务逻辑与用例；每个任务绑定一个会话，到点在原会话后接着回复；Chat/Work 按创建来源标注，页面新建为 Work；真实模型测试；UI/UX 做到最好。分支 `feat/schedule-in-conversation`，base `origin/main@81d24699`（已合入 `70e04e57`）；未授权部署；[PR #184](https://github.com/shijunan126-creator/ailab-lite/pull/184)。
- 已完成：迁移 0071、对话内 `schedule` 工具、会话忙时顺延、一次性任务、到点轮的平台头与 Portal 时间标记/会话条/列表标签。决定见 [ADR 0040](../architecture/adr/0040-schedule-in-conversation.md)，验证与真实模型数字见[记录](updates/2026-11-11-schedule-conversation.md)。
- CI：`c27af02a` 11 项全绿（双库、三片 E2E 等）。此前失败与根因（测试未随改动更新、三个既有时序/顺序依赖用例）见记录。
- 已合并：用户要求合并，PR #184 squash 为 `4b4628c4`，提交树与 PR head `d67e32f7` 一致（其代码与全绿的 `c27af02a` 相同，`d67e32f7` 只改文档）。分支已删除。
- 下一步：无待实现项；部署需用户当次明确要求。

---

# 模型菜单不再随悬停跳动（2026-11-11）

- 用户反馈：模型菜单里指针移到「离线演示」这类没有档位的模型时，弹层高度变化把行顶走，选择在两个模型之间反复横跳。用户指定解法：悬停不更新右栏，点进去再更新；选完档位不自动收起，点别处关闭；手机端同样检查。分支 `fix/model-menu-height`，base `origin/main@81d24699`，工作树 `/Users/michael/Developer/ailab-lite-wt/fix-model-menu-height`。
- 已完成：右栏只跟已选中模型；点和档位都不收起；右栏按最高档位列表补等高占位，弹层尺寸恒定。Portal 单测 282 通过、`tsc` 干净，900/375 宽浏览器检查通过（临时页面，已删），本机 E2E 思考强度+模型列表 2 项与 genui 布局 5 项通过。E2E 用例已按新交互更新。细节与边界见[本轮记录](updates/2026-11-11-model-menu-stable.md)。
- 已合并：PR #183 squash 为 `73e919a5`，提交树与该提交一致。CI 首次 `python (postgres)` 因 `tests/faults/test_job_heartbeat.py::test_ownership_loss_or_renew_deadline_stops_work_before_expiry[outage]` 的实时钟断言（work 在 0.343s 才被取消，界限是 0.3s 租约）失败，与本改动无关；重跑后 11 项全绿（双库、三片 E2E、quality、crawler、shell、storage-contracts）。工作树与分支已删除，`/tmp` 截图、dev server、临时 Redis 已清理。
- 下一步：无待实现项；部署由用户决定（只换 Portal 产物）。该 heartbeat 用例的实时钟界限偏紧（已在 PR #184 改为虚拟时钟事件循环）。

---

# 搜索正确性复核与存储口径纠正（2026-11-11）

- 用户授权：保留 PR #181 的索引方案，修复 bug、更正错误记录；追加授权提 PR、最终 CI 通过后合并并增量部署最新版。[PR #182](https://github.com/shijunan126-creator/ailab-lite/pull/182) 已 squash 合并为 `b8fe90b2`，当前分支 `main`，功能基线 `origin/main@b8fe90b2`，唯一工作树为仓库根。
- 已修复：Unicode 归一化预筛选漏搜、隐藏 OpenUI 源码误报、拉丁词边界误报、新会话与 Run 同 flush 漏索引、并发词项覆盖，以及固定会话/Run 截断漏搜。分页保留，5 秒协作式期限超时返回明确 503。未改索引结构或 Portal。
- 错误记录：已撤回“9 倍”；基准按 UTF-8 原文字节、逻辑主库页、搜索专用页与 WAL/SHM 分列。SQLite 与 PostgreSQL 的增量不能混用，数字及原始口径纠正统一见[搜索记录](updates/2026-11-11-search-index.md)。
- 验证与边界：[本轮复核](updates/2026-11-11-search-correctness.md)。最终代码 `cf4567a` 的 11 项 CI 全绿（完整双库、quality、三片 E2E 等），无未处理评审；合并树与该提交一致。此前失败根因和本地全套结果均保留在复核记录，不延长期限或增加重试。
- 下一步：本轮代码已合并，无待实现项；按当次授权增量部署，本机部署结果只在对话汇报，不当作能力状态写入仓库。后续以实际使用反馈为准。

---

# 思考强度选择与基础技能收起（2026-11-11）

- 用户要求：模型菜单加思考强度（每个模型档位单独配置，DeepSeek 默认低，记住上次选择，同一菜单）；技能页收起四个始终开启的基础技能；提 PR、CI 过后合并并部署最新版。分支 `feat/reasoning-effort`，base `main@323184b1`。
- 已完成：`platform/reasoning.toml` + `models.toml` 引用、API 各发送路径写 `run.options.reasoning`、Agent 整次回答同档位、Portal 两栏菜单与回答标注、基础技能收成一行、深色菜单悬停 token。决定见 [ADR 0038](../architecture/adr/0038-reasoning-effort.md)，验证见[记录](updates/2026-11-11-reasoning-effort.md)。
- 下一步：推送、开 PR、CI 全绿后合并，增量部署本机（本机 `models.local.toml` 需加 `reasoning = "deepseek-v4"`）。


---

# 记忆只有一种行（2026-11-11）

- 用户要求：去掉受保护/推断/待确认/暂停，敏感内容照常记（证件号、银行卡、凭据除外），用真实模型测整理、记忆页对话框和对话工具并微调提示词，前缀缓存在同一次测试中观察。分支 `feat/memory-single-origin`，base `main@2a29fffe`；不授权合并、部署。
- 已完成：代码、迁移 0069、ABI 7、Portal 去标签与确认、提示词（一行一个分类、混合行拆分）；定向测试与真实模型检查通过。决定见 [ADR 0037](../architecture/adr/0037-memory-single-origin.md)，数字与边界见[记录](updates/2026-11-11-memory-single-origin.md)。
- 下一步：推送、开 PR、CI 全绿；合并与部署由用户决定。

---

# 记忆质量与整理节奏（2026-11-10）

- 用户授权按[计划](../architecture/design/2026-11-10-memory-quality.md)全部实现，含迁移 0067、ADR 0036、真实模型评估、推送、开 PR 并修到 CI 全绿；不授权合并、部署、改动本机运行中的服务或数据库。分支 `feat/memory-quality`，base `main@36b17590`；[PR #177](https://github.com/shijunan126-creator/ailab-lite/pull/177)。
- 已完成 A–F 六步（每步一个提交）：每晚每用户一条整理链；删除来源保留记忆；中文标准、转变与理由代号（ABI 6）；来源项目标签与独立项目文件隔离；提示词版本与旧文档整理一次；记忆页分类卡片。决定见 [ADR 0036](../architecture/adr/0036-memory-quality.md)，验证、评估数字和偏差见[记录](updates/2026-11-10-memory-quality.md)，坑见 HANDOFF 2026-11-10。
- 第二轮（用户要求修正并补覆盖）：0068 把旧夜间 explicit 行改回 inferred，一次性整理可删低价值推断行；真实库副本新旧对比、一次性整理、真实服务链路缓存（Chat 97.3% / Work 98.4%）已做；修复调度循环被慢链阻塞。整夜压测按用户要求取消。数字见[记录](updates/2026-11-10-memory-quality.md)。
- PR #177 已合并（`80f62af6`）并部署本机。之后修复「立即整理」不做一次性整理：PR #178 已合并（`2a29fffe`）并部署本机。
---

# CI 与测试提速（2026-11-10）

- 用户要求排查 tests/CI 的低效和无用部分并修复。分支 `ci/faster-tests`，base `origin/main@37850623`，[PR #176](https://github.com/shijunan126-creator/ailab-lite/pull/176)；未改产品代码，未部署。
- 已完成：Python 双库套件改为 pytest-xdist 并行，每个 worker 独立库与 Redis db；E2E 分 3 片，Portal 单测只跑一次；去掉测试里的固定等待（服务器轮询、LLM 退避、本机代理）；修复 `lock_deps.sh --check` 恒通过的问题。根因、数字和边界见[记录](updates/2026-11-10-ci-speed.md)，坑见 HANDOFF 2026-11-10。
- 本机：SQLite 11:21 → 91 秒，PG+Redis `-n 4` 185 秒，全部通过。CI `533ae0cd` 11 项全绿：整轮 12.2 分钟（原约 20 分钟），双库各约 7 分钟，E2E 三片 6.5–11.3 分钟（最慢一片含 3.8 分钟 apt 波动）。之后 CI 暴露了并行下的时序依赖和 apt 下载慢，已一次性排查修复（见记录“并行暴露的时序依赖”）。下一步：确认 PR #176 最终 head 的 CI 全绿；合并由用户决定。

---

# 分支与工作区清理、仓库卫生规则（2026-11-10）

- 用户要求工作区切到最新 main 并清掉过期分支。已 fetch：`main = origin/main @ e9d0056d`（含 #172/#174 记忆 v2）。上一轮工作区停在 `fix/source-pill-alignment`（落后 30 个提交），据此得出的记忆页结论已在对话中按最新 main 更正。
- 已删除全部 226 个本地分支（只留 `main`）、origin 上 134 个 PR 已合并/已关闭的分支（保留 `main` 和唯一 open PR 的 dependabot 分支）、`review-fork` 的 7 个工作分支。删除前唯一未推送到别处的提交是切 main 前的文档快照 `12de4a0d`，仅存于 reflog。
- 清理 296MB 的 `.local/file-context-ocr` 调查暂存（含 `ocr-token` 凭据文件）、2.4MB `platform/build`、散落的 `.DS_Store` 与 `vendor/zcode-office-runtime/runner.mjs` 残留；`platform/e2e-logs/` 保留，2026-10-24 审查仍引用其中的截图。
- AGENTS.md §10 新增 `Repository hygiene`：分支不留档、远端只留默认分支与 open PR、worktree 与 prune、构建/暂存输出不入仓库、凭据目录及时删除、Docker 只回收构建缓存不动业务卷。未改产品代码，未跑测试，未部署。

---



- 分支 `feat/memory-doc`，base `origin/main@c4e88613`，本轮全系统审查基线 `02f0e066`（保留 `a79c712a` Portal提交）（已拉取 Portal 移动布局/新到旧排序）；[PR #174](https://github.com/shijunan126-creator/ailab-lite/pull/174)。授权实现、真模型、只读部署库副本、前端 merge、推送与 CI；不合并、不部署。
- 已完成有界文档/ABI5/单调用 Dreaming、程序融合/保留撤销、历史行索引回收、会话失效/条件空闲刷新、前序 turn context 与脱敏；76项旧失败已按新路径迁移。前端 `4bf62947` 经 merge commit 合入并保留菜单修复。
- 测量、检查、最小 Portal 修正与边界统一见[本轮记录](updates/2026-11-09-memory-document.md)，设计见[ADR0035](../architecture/adr/0035-memory-v2.md)。PR 已 ready；本轮修复暂停写入、原因拆分、无变化/行围栏、无效维护、closed隐私、空闲压缩；最新决定撤销用户删除重建，删除仅推送handle通知、保持前缀，list为当前事实。本轮修复shared作用域/重定向/保护/review/逐op并发/维护无进展/轻量设置，补两个revision旧断言；下一步核对最终head双库/E2E CI并修复失败；不得把历史通过当作最终验收。

# Memory v2 后端质量与评测（2026-11-09）

- 分支 `feat/memory-v2-b`，实施基线 `feat/memory-v2-a@c6fe1d46`，base `origin/main@df83d8e`；已含前轮Portal。最终产品/测试冻结`72efcf3153576ee6533f9ec8ef140ff6210f1326`。仅本地提交，无部署、推送或PR。
- 已完成质量修复、0064持久队列/恢复/计量、测试来源排除、无时钟常驻块与确定增量。检索/时效、ABI2真实触发及六场景真实缓存达到数值门槛；真实历史副本已删除。审查修复、§3–§15完成/偏差、提示词各轮数字和逐调用缓存只维护在[实施记录](updates/2026-11-09-memory-v2.md)。
- 最终验证：SQLite2672/247跳过，PG/Redis2849/70跳过，前缀132/0跳过，全部0失败；隔离E2E194/15跳过，Portal274测试/build通过（各自基线见记录）。一次性PG/Redis及E2E服务已停止，临时数据/调试输出已清理。
- 剩余边界：模型仍偶发无效JSON并被拒绝；提取质量未独立人工标注，2条兴趣价值偏弱；未测300真实用户整夜负载及真实服务链路缓存。下一步交主代理审阅本地提交，不自动推进部署或其他路线图。

---

# 首条消息与会话原子创建（2026-11-09）

- 用户报告 `/c/cnv_bf66cafddac34d6a8b4a` 空白，原有 PRO 正文和失败记录消失。证据：Conversation 已创建，但 `runs=0`、`run_events=0`、`queued_messages=0`；首条消息返回 `429 too many active runs for this user`。工作树 `.worktrees/first-message-admission`，分支 `fix/first-message-admission`，base `origin/main@e47d22d8`；主工作区既有修改保留。
- 已修复：新增 `POST /v1/conversations/start`，在一个用户锁和事务内完成 Run 准入、Conversation 与首条 Run 创建，失败回滚；同一 `client_send_id` 重放幂等，内容变化 409。Portal 新会话首条发送改走原子接口，并让已发送项保留到导航完成。根因、验证和边界见[记录](updates/2026-11-09-first-message-admission.md)。
- 合并与部署：PR [#170](https://github.com/shijunan126-creator/ailab-lite/pull/170) 的 head `a3d49234` 九项 CI 全绿，squash merge 为 `main@ad756f92`；已增量部署 `release-ad756f9`。Portal `/login`、API `/readyz` 返回 200，OpenAPI 已注册新接口；12 running / 10 healthy，数据库 revision `0063`。
- 验证：定向 SQLite 回归、Portal 272 项测试与生产 build、完整 E2E 通过。完整本机 Python 套件仍有 16 项 Office/document 运行时环境失败，不在本改动路径。
- 边界：历史空会话不会自动恢复；待发送 outbox 仍只在浏览器内存，完整刷新会丢失未提交的失败提示。下一步等待用户实际使用反馈，不推进其他路线图项。

---

# GenUI 主动使用与分档组件（2026-11-08）

- 用户要求：模型更主动但不滥用 UI 组件，GenUI 初始提示放宽到约 3000 token；新增截图式“色块 + 说明”分档组件。工作树 `.worktrees/genui-proactive`，分支 `feat/genui-proactive`，base `origin/main@34b1e58e`；主工作区既有修改保留。
- 已实现：索引按内容形状触发并写明不用界面的边界；13 个常用组件说明直接进系统提示（其余仍按需 `ui_components`）；语法与对错规则移入索引，工具指南只剩交互；新增 `Segments`。根因、token 与评测见[记录](updates/2026-11-08-genui-proactive.md)。
- 追加：模型目录支持可选 `session_header`，可接入要求会话头的 OpenAI 兼容端点（OpenCode Go）；“为什么”类问题按内容形状判断（因果链可用 Flow），不再整类列为纯文字。最终提示词经 OpenCode 复测：主动 9/9、多余 UI 0/14、闭合块 15/15 合法。
- 验证：真实模型主动用例 5/8 → 9/9（最终版），未缓存输入 113,912 → 32,425。Portal 270/build、Ruff、全套 SQLite 通过（唯一时序抖动已修），详见记录。
- 下一步：用户已授权提 PR、CI 通过后合并，并一次性部署本机。
---

# 网页图片短 ID 与无落盘代理（2026-11-08）

- 用户授权按前序讨论实施；工作树 `.worktrees/web-image-refs`，分支 `feat/web-image-refs`，base `origin/main@e3122ae3`。主工作区修改保留；用户随后追加授权提 PR、CI 通过后合并并增量部署。
- 已实现：16 字符图片 ID、API 地址去重/attempt 围栏、候选 24h 与已采用消息引用、分享路径权限、分叉保留与引用回收；列表缩略图/大图按需流式转发，不再持久化图片。新链路取消签名 key 要求，保留旧路径兼容。
- 检查与边界统一见[实现及验收](updates/2026-11-08-web-image-refs.md)。真实模型已搜索配图并在后续一轮复用 ID，浏览器桌面/手机浅深色与大图通过；最终图片/索引回归 SQLite 22/1 跳过、PG 23/0；Portal 267/build、Ruff 通过，全套首轮失败与复核边界见记录。
- 下一步：提交 PR，CI 通过后合并并增量部署最新 main；本轮临时验收环境已清理，不重复部署验收，不推进其他路线图项。
- 前序 UI/技能 PR #165 的范围与历史验收见[原记录](updates/2026-11-08-ui-polish-media.md)。

## 上一轮交接（2026-11-08 文件输入 PR #161 已合并）

- 最新授权：用户在 PR #161 上问预览开关是否会让页码错位；同意按“每种格式一套编号”修复后，要求提 PR、合并并部署最新版。PR head `4338d69d` 的 13 项 CI 全绿后 squash 合并为 `main@2bf86144`。
- 本轮完成：Word 页图只走服务端；PPT 服务端导出包含隐藏页；修复 CI 的两个根因（Portal `jszip` 锁文件不一致、`get_storage` 关闭后并发首次创建的竞争）。根因、验证和风险见[页码一致性记录](updates/2026-11-07-document-page-numbering.md)。
- 验证：Ruff 通过；PostgreSQL/Redis 全套 2739 通过 / 69 跳过；E2E 193 通过 / 15 跳过，唯一失败是旧的 Word 浏览器截图期望，已改并重跑通过。
- 剩余：已渲染过、并且含隐藏页的 PPTX 不会自动重渲（页图缓存没有渲染器版本）；本机现有 PPTX 都没有隐藏页，不受影响。listener 转换路径已在合并后的 sbx-office 镜像里实测；Portal 浏览器路径没有真实跑过。
- 下一步：等用户使用反馈。若要处理旧 PPTX 页图缓存，先给 `RenderedPage` 和文件预览元数据加按格式划分的渲染器版本。

# 工作区工具对齐 Pi（2026-11-11）

- 用户要求：把 `read_file`/`write_file`/`list_files`/`exec` 换成 Pi 的 `read`/`write`/`edit`/`bash`，补 `grep`/`find`（底层 `rg`/`fd`），逻辑与优化照着 Pi 源码做；随后追加调整 bash 的等待与期限。分支 `feat/pi-aligned-workspace-tools`，base `origin/main@7d17db92`，工作树 `.worktrees/pi-aligned-workspace-tools`。未授权推送、合并、部署。
- 设计（含 Pi 源码事实与取舍）：[工作区工具对齐 Pi](../../architecture/design/2026-11-11-pi-aligned-workspace-tools.md)。

## 做了什么

**工具目录**（Work 24 项，Chat 不变）：`read`、`write`、`edit`、`ls`、`bash`、`bash_wait`、`bash_kill`、`grep`、`find` 取代 `read_file`、`write_file`、`list_files`、`exec`、`exec_wait`、`exec_kill`。

- `read`：`offset` 是 1 起算的**行号**，`limit` 是行数；2000 行 / 50KB 头部截断，续读提示写在正文里；首行超 50KB 时给 `sed -n 'Np' … | head -c 51200`；二进制文件拒绝并指向 `view_workspace_file`。
- `write`：整份覆盖，父目录由 Broker 的 `mkdir -p` 建。
- `edit`：`{path, edits:[{oldText,newText}]}`，也接受模型实际会发的三种形态（`edits` 是 JSON 字符串、单个对象、或顶层 `oldText`/`newText`，GLM 会发字符串）；全部 `oldText` 对同一份原文匹配；先精确、失败后 NFKC + 去行尾空格 + 弯引号 + Unicode 破折号 + 特殊空格归一化再匹配；唯一性、重叠、空 `oldText`、无变化都按 Pi 的报错原文；CRLF/BOM 与"裸 CR 不吞 LF"的接缝规则逐条移植（含 3000 轮随机混合行尾不变量）；同文件写入按 `(lease, path)` FIFO 串行。
- `ls`：一层、字母序、目录带 `/`、含点文件、上限 500。
- `grep`/`find`：沙箱内跑 `rg --json` / `fd`（`fd` 缺失回退 `fdfind`），达到 100 条 / 1000 条即杀子进程，单行 500 字符、总量 50KB 封顶。
- 输出被跳过时返回环形缓冲路径 `log_path`（命令输出最近 `log_bytes`），模型想自查更多输出时可用 `bash` 读它。
- 前端：Portal 的步骤名与图标补齐新名字；旧名字保留为历史事件的显示兜底。

**bash 的等待与期限（临时调整，覆盖原设计）**

- `wait_seconds`（`bash` 1–300，`bash_wait` 0–300，默认 30）是本次等待窗口；`background: true` 立即返回句柄。`timeout`/`timeout_seconds` 明确拒收并说明理由。
- 一次工具调用内部连续发多个 30 秒有界 Broker 等待请求：不调用模型、不写上下文、不发持久事件；沙箱提前返回时补足剩余窗口，不忙轮询。
- 窗口到点返回 `running` + `handle` + 累计输出 + `idle_seconds`/`elapsed_seconds`/`remaining_seconds`，并明确说明这不是失败也不是超时。
- 平台硬期限 = `min(租约剩余预算, run_max_seconds=14400)`，启动时固定；由沙箱内的 supervisor 自己到点 SIGKILL 进程组（`deadline` 文件 + `expires_at`），不依赖 Agent 存活；被杀返回 `exit_code=-9`、`killed_by=deadline`、原因和有界输出。没有"固定 5 分钟就杀"的规则，也没有按 `idle_seconds` 自动杀进程的路径。
- 恢复边界：句柄 = `sha256(run_id, 调用序号, 命令)`，启动前写进 checkpoint；重启/接管后对同一调用发的是 `bash_wait`，沙箱内 helper 对已存在的句柄只等待、绝不重跑；作业记录随租约丢失时返回"后台命令已不存在"并给 `UNCERTAIN_EXEC`，不盲目重跑。
- 用户新消息可以打断 `bash` 与 `bash_wait` 的等待，命令留在后台。

## 怎么验证的

- `ruff check` / `ruff format --check`：通过（`vendor/office-suite/build.py` 的 E402 是既有问题，未改动）。
- 单元套件（`platform/tests`，除 `contracts/`）：**3002 项，16 失败，189 跳过**。这 16 项当时判为「缺 OCR/渲染依赖」，实际是本地没构建 `vendor/office-suite/runtime/runner.mjs`；构建后同一批测试 0 失败（见第五轮更正）。
- 定向用例：`test_text_edit.py`（含 3000 轮随机行尾不变量）、`test_text_truncate.py`、`test_workspace_query.py`（用假 `rg`/`fd` 跑真实沙箱程序）、`test_workspace_tools.py`、`test_file_mutation_queue.py`、`test_bash_wait_window.py` 全绿；`test_agent_loop.py` + `test_background_exec.py` + `test_bash_wait_window.py` 85 项通过；`test_agent_loop.py` + `test_prefix_cache.py` 190 项通过。
- Portal：`npm test` 286 项通过；`npx tsc --noEmit` 干净；`node office/build.mjs` 通过。
- 重新生成：`build.py` 重跑后 `images/sbx-office/skills/**`、`office_guides_v2.json` 与源码一致（`test_office_suite_resources.py` 逐字节重建通过）；`Dockerfile` 变更后 `inventory.py` 重算 `capabilities.json` 的 `image_baseline_sha256`。

## 真模型验收（2026-11-11，DeepSeek V4.1 Flash / OpenCode Go）

工具走生产代码（`system_prompt_for(mode="work", document_tools_version=2)`、`tools_for_turn(2)` + `search_capabilities`、`execute_tool`），文件与命令跑在本机临时目录（`/workspace` 映射），`ls`/`grep`/`find` 真调 `rg`/`fd`，`bash` 真跑 helper 子进程。16 个任务、29 次模型调用。

| 任务 | 结果 |
|---|---|
| 先看目录再进去 | `ls` ×2，2 次调用完成 |
| 第 10–12 行（要行号） | `cat -n`（按提示词的例外规则），3 次调用 |
| 6 万字节 6000 行文件的行数 + 第 3000 行 | `bash wc -l` + `sed -n`，3 次调用 |
| 读不存在的文件 | `ls` + `find`，明确说"不存在"，未编造 |
| 二进制文件 | 试图用视觉工具，本机没有视觉通道，改用字节检查；结论是"不是可用图片"（fixture 本身就是假 PNG） |
| 重命名两处 | `read` → 一次 `edit` 两个 oldText → 跑脚本验证，5 次调用 |
| 同一行出现两次 | `read` → `edit` 用 `def answer():\n    value = 1` 消歧 → 复读确认，并提示第二行会覆盖 |
| 新建含父目录的文件 | 一次 `write` + 一次 `read` 复核 |
| 按内容搜索 | `grep`，结果准确 |
| 按文件名搜索 | `find`，1 次调用 |
| 非 0 退出的脚本 | 读脚本后运行，正确报 exit 3 与 stderr，并指出"libfoo 是脚本写死的文案" |
| 40 秒命令（等待窗口） | `bash` → `running` + handle → 3 次 `bash_wait(10s, offset)` → 完成；正确说"中间 40 秒没输出是因为 sleep" |
| 强制 10 秒窗口连续等待 | 同上，窗口到点没有被当成失败，也没有被当成超时 |
| 管道统计 | `bash` 一次算出 |

改提示词之前的同一批任务：`read`/`write`/`edit` 一次都没被调用，模型全部用 `bash` + `cat`/`sed`/`python3 - <<EOF` 完成，并把改好的源码复制进 `outputs/` 后 `deliver_file`。改后 `read` 出现在 9/16 个任务里，`edit`/`write`/`find`/`grep` 各自按意图使用。

由此改的三处（`ae467aad`）：

- `grep`/`find` 无结果时写明搜的是什么（`No matches found for "x" under /workspace`）并补 `matches`/`files` 计数：原来的裸 "No matches found" 被模型当成"没搜完"，会再用 `bash grep`、`find` 复检两三次。
- `bash` 非 0 退出补 `Command exited with code N`，描述里说明结果自带 `exit_code`/`stdout`/`stderr`；此前模型习惯在命令尾部加 `echo $?`，把失败伪装成 `ok: true`。
- 交付限定成"用户要成品文件时"，改代码/改配置不再复制到 `outputs/` 并 `deliver_file`。

提示词体积（`prompt_tokens`，同一次调用含 system + 用户 "hi"）：system 2591 → **2765**（+174），加 28 个工具 schema 后 **7283**。compat：`test_prefix_cache.py` 的 work 锚点断言随文案更新。

## 容器内验证（2026-11-11，colima / Debian trixie / uid 10001）

按 `images/sbx-office/Dockerfile` 的同一个 base 与 `DEBIAN_SNAPSHOT=20261101T000000Z` 建了一个只装 `ripgrep fd-find` 的最小镜像，`docker run -u 10001 -w /workspace` 后跑生产代码：

| 检查 | 结果 |
|---|---|
| 包名与快照 | `ripgrep` 装成 `/usr/bin/rg` 14.1.1；`fd-find` 装成 **`/usr/bin/fdfind`**（没有 `fd`），探针的 `fd`→`fdfind` 回退是必需的 |
| `ls` | 含点文件、目录带 `/` |
| `grep` | 命中 `.hidden`、路径相对搜索根、`matches` 计数正确 |
| `find` | `**/*.csv` 3 条、相对路径 |
| `read`/`edit`/`write` | 在容器里读写正常，`write` 自动建父目录 |
| `bash` 短命令 exit 3 | `ok:false`、`exit_code:3`、`stderr`、`error: Command exited with code 3` |
| `bash` 2 秒窗口 + 长命令 | `running` + handle → `bash_wait` → `done`，`exit_code:0` |
| 平台期限 | `exec_lifetime_seconds=3` 时 2.5 秒被 `killed_by: deadline` 杀掉，`exit_code:-9`，保留 `stdout` 并给出期限说明 |
| `bash_kill` | `killed_by: caller`、`exit_code:-9`、`error: 命令已被终止` |

容器里暴露并修掉的一处真 bug：短命令（helper 的 short-command 形态）走的是 `_final` 的提前返回分支，非 0 退出的 `error` 和 `killed_by` 都被丢掉——也就是说模型看到的失败命令只有 `ok:false` 和裸 `exit_code`。现在提前返回也带上判定，`killed_by` 也进结果。

## 补齐的整栈验收（2026-11-11 晚）

外置 SSD 上起了一个专门的构建 VM（`COLIMA_HOME=~/AILabDisk/local/colima`、profile `sbxbuild`、60 GiB 盘），镜像层全部落在 SSD。

- **完整 sbx-office 镜像**：`docker build -f images/sbx-office/Dockerfile` 成功，`sbx-office:check` 4.85 GB。`--network none --memory 2g --cpus 1` 跑仓库自带 `smoke/smoke.sh`：OFFICE READER PASS、pptx/xlsx 生成、LibreOffice 渲染 1 秒、三条外网探测全部被拒、内存峰值 199 MiB、`SMOKE PASS`。镜像里 `rg 14.1.1`、`fdfind 10.2.0`（无 `fd`）、Python 3.12.16、`/opt/skills` 索引齐全（构建期 `skill_index.py --check --requirements` 通过）。
- **沙箱程序在真镜像里**：以 uid 10001 跑 `LS_PROGRAM`/`GREP_PROGRAM`/`FIND_PROGRAM`，`/workspace` 的 inputs/outputs/work 正常列出，`grep` 命中 `.hidden`、`find` 返回相对路径。
- **contracts**：起了临时 PostgreSQL 16 + Redis 7，`AILAB_TEST_OFFICE_IMAGE=sbx-office:check`。**135 项、0 失败、52 跳过**；跳过项全是缺外部服务的（OpenSandbox、MinIO/S3、clamd、crawler、OIDC、storage-docker tmpfs）。四格式真实 create/edit/convert 契约在真镜像里通过。
- **E2E**：`AILAB_E2E=1 pytest e2e`（四服务 + Redis + Portal 生产构建 + Chromium），**210 项、0 失败、15 跳过**。
- **Next 生产构建**：工作树内 `npm ci --ignore-scripts && npm run build` → `✓ Compiled successfully`，路由表完整。
- **run_subtasks 并发写同一文件（真模型）**：用真 `AgentLoop`（`FakePlatform` + 真 Broker + 真 ModelTransport）跑一次 Work run，状态 `succeeded`；模型起 3 个子任务，每个子任务都 `edit` 同一份 `/workspace/report/findings.md` 的不同小节，三节全部落盘、数值正确（30/70/50）。另加了一条确定性回归 `test_two_concurrent_edits_of_one_file_both_land`：两个并发 `edit` 走 `execute_tool`，读改写之间插 50 ms 延迟，两次都成功且文件内容完整（靠 `(lease, path)` 队列串行）。

## 独立审查后的修正（2026-11-12）

外部独立审查（读 diff + 小脚本实测，未重跑套件）给出 3 条必须修、4 条应该修和若干补充项，全部照改，每条都补了"不修就失败"的用例。

| 项 | 问题 | 处理 | 用例 |
|---|---|---|---|
| `edit` 模糊匹配 | 一处匹配走归一化后，**整行**被归一化写回：全角标点/数字、弯引号、行尾空白全变半角 | 折叠字符带原文区间，只替换匹配到的那一段 | `test_a_fuzzy_edit_does_not_rewrite_the_rest_of_the_line`（审查给的原文）等 2 条 |
| 唯一性计数 | `str.count` 不重叠，`"}\n}\n"` 在 `"}\n}\n}\n"` 里算 1 次 | 改成重叠计数 | `test_overlapping_occurrences_are_refused` |
| 子任务句柄 | 调用编号不含父 step，第二批同一命令撞上第一批句柄，helper 当重试直接返回旧结果 | 编号带父 step | `test_the_same_command_in_a_second_batch_is_a_new_execution` |
| 恢复判定 | 已正常结束但 exit≠0 的命令被当成"丢失"报 `UNCERTAIN_EXEC` | 只有 `lost`/`exit_code == -1` 才算丢失 | `test_a_finished_failing_command_keeps_its_result_across_a_restart`、`test_a_command_whose_state_was_lost_stays_uncertain` |
| 恢复事件名 | 换名成 `bash_wait`，Portal 按名字关不掉原来的 `bash` 步骤 | 事件沿用原调用名 | `test_a_resumed_wait_keeps_the_original_tool_name_for_the_ui` |
| `grep` glob | `lstrip("./")` 删的是字符集合，`.env*`→`env*` | `removeprefix("./")` | `test_grep_keeps_a_leading_dot_glob_and_ignores_like_find` |
| `grep` 忽略规则 | 只有 `find` 传 `--no-require-git` | rg 也传，语义对齐 | 同上 |
| `bash_kill` | 成功终止却返回 `ok: false`，UI 显示失败、卡死检测记一次失败 | kill 成功即 `ok: true` + `killed_by: caller` | `test_a_kill_that_found_the_command_reports_success` |
| 结果形状 | 短命令与长命令两种形状；`output_tail` 与 `stdout` 同段重复；跨段缺口无标记 | 统一 `ok/exit_code/running/stdout/stderr`+可选字段，去掉 `output_tail`，缺口插 `...[elided N bytes]...` | `test_every_finished_command_has_the_same_shape`、`test_a_gap_between_what_was_read_and_what_arrived_is_marked` |
| `write` 未排队 | 与 `edit` 并发时整份写会覆盖读改写 | 同一条 `(lease, path)` 队列 | `test_a_write_and_an_edit_of_one_file_cannot_interleave`（去掉修复即失败，已实测） |
| `timeout` 参数 | 直接报错，Pi 系模型每次多浪费一轮 | 忽略并附 `note` | `test_a_retired_timeout_argument_is_ignored_with_a_note` |
| 主参数名 | `cmd` 与 Pi/其它工具不一致 | 主参数 `command`，`cmd` 兼容，句柄哈希取同一文本 | `test_the_command_may_be_named_command_or_cmd` |
| 提示词重复 | 同一句"窗口结束不是失败/长时间没输出不等于卡死"出现 5 次 | 删两处复述 | system 2765 → **2657** tokens，含目录 7283 → **7175** |
| Portal 图标 | `StepIcon` 只认新名，历史事件图标退化 | 新旧名都认 | `tsc --noEmit` + vitest 286 项 |

拼接缝规则改用穷举（`{a, \r, \n}` 长度 ≤ 6 的全部文件 × 全部 oldText × 5 种 newText），随机那版保留。
另修 `config.py` 两处注释仍写着 `read_file`/`write_file`。

**验证**（基线 `feat/pi-aligned-workspace-tools` 工作树，arm64 macOS）：

- `ruff check` / `format --check` 干净（704 文件）。
- 全量 `pytest tests -q -n auto`：**3166 项 / 16 失败 / 276 跳过**；当时判为「缺 OCR/渲染依赖」，实际是缺 `vendor/office-suite/runtime/runner.mjs` 这个构建产物（见第五轮更正）。跳过项全部因为没设 PG/Redis/OSB/S3/Docker 等外部服务。
- 改动模块定向：`tests/test_bash_wait_window.py`、`test_text_edit.py`、`test_workspace_tools.py`、`test_workspace_query.py`、`test_subtasks.py`、`test_background_exec.py`、`test_agent_loop.py`、`test_prefix_cache.py`、`test_stuck.py` 共 **342 项 0 失败**。
- Portal：`npm ci --ignore-scripts`、`tsc --noEmit` 干净、`npm test` **48 文件 / 286 项通过**。
- 整栈 E2E 重跑（四服务 + Redis + Portal 生产构建 + Chromium，mock 沙箱）：**210 项 / 0 失败 / 15 跳过**，与修正前一致。
- 真模型（DeepSeek V4.1 Flash / OpenCode Go）重跑 9 个任务：`bash` 已用新参数名 `command`，40 秒脚本按 10 秒窗口分 4 次等待后拿到 `exit_code 0` 与最后一行；`edit`/`write`/`read`/`grep`/`find`/`ls` 按意图使用，未出现退回 `bash cat/sed` 的情况。

## 第三轮审查后的修正（2026-11-12）

审查确认上一轮修对了 10 项，但 `edit` 的实现、子任务句柄、恢复后的界面、`exit_code == -1` 的判定都没到根上。

| 项 | 问题（审查实测） | 处理 | 用例 |
|---|---|---|---|
| `edit` 逐字符映射 | 1 MB 文件 1.56 s / 80 MiB，5 MB 8.33 s / 403 MiB；pod 上限 256 Mi、`read`/`edit` 允许 10 MB，一次模糊 edit 就能 OOM 整个 Agent；且同步执行堵住事件循环 | 不折叠文件：`oldText` 编译成正则（1:1 折叠字符类），直接在原文上搜索；折叠表首次模糊匹配时建一次并缓存 | `test_a_five_megabyte_chinese_file_edits_quickly_without_a_second_copy`（<0.5 s、tracemalloc <32 MiB；实测 5 MB 16 ms / 峰值约 8 MiB） |
| oldText 末尾空格 | 空格被裁掉不进匹配，原文空格留在结果里（`say “y” =  1`） | 末行原本带尾空白时，正则会匹配到原文的空白为止 | `test_quoted_trailing_spaces_are_consumed` |
| 10 MB edit 的持有量 | 同时要有字节、原文、新文、编码四份 | 新增 `max_edit_bytes`（2 MiB），超限提示用 bash 改；执行放 `asyncio.to_thread` | `test_edit_refuses_a_file_over_its_own_memory_budget` |
| 所有 400 都当 ENOENT | 12 MB 的现有文件会让模型以为文件不存在，改用 `write` 覆盖 | `SandboxFileNotFound`（docker/opensandbox/mock）→ Broker 404；`TransferLimitError` → 413；其余 400 带 detail；Agent 分别给 ENOENT、">10MB 用 sed -n/head -c"、原样 detail | `test_a_too_large_file_is_not_reported_as_missing`、`test_a_broker_400_hands_the_detail_to_the_model`、`test_file_errors_carry_the_reason_the_agent_needs`（Broker 路由 404/413/400）、`test_a_missing_file_is_its_own_error_on_every_provider`（mock 与 contracts） |
| 一条回复里两个 `run_subtasks` | `step{step}` 相同，两批同槽位算出同一句柄，第二批拿旧结果 | 编号改 `{invocation}/sub…` | `test_two_batches_in_one_reply_are_four_separate_executions`（去掉修复即失败） |
| 恢复多发一次 `tool_call` | Portal 新建步骤，结果只关新步骤，崩溃前那个永远转圈 | `_run_tool(announce=False)`：只发结果事件，名字与 step 沿用原调用 | `test_a_resumed_command_does_not_open_a_second_ui_step`（Python 重放）、`tests/work-process-resume.test.ts`（两条：正确的序列一个步骤、多发一次会留下转圈的步骤） |
| 跨部署的旧目录 | 冻结目录还列着 `exec`/`read_file`，恢复后每次调用都是 unknown tool | 恢复时发现退役名就重建目录并记 `tool_catalog_changed` 边界；不升 `CONTEXT_ABI_VERSION` | `test_a_checkpoint_with_retired_tool_names_is_rebuilt_at_a_recorded_boundary`（work/chat 各一） |
| `context.py` 的边界标签 | 模型没变、只有目录变时记成 `model_changed` | 工具变、提示词未变 → `tool_catalog_changed` | 同上 + 既有 `test_deploy_prompt_and_tool_changes_preserve_resumed_checkpoint` 仍通过 |
| `exit_code == -1` 当丢失 | 被 SIGHUP 杀的命令 returncode 也是 -1 | helper 在"退出状态丢失"记录里写 `lost`；`_final` 透传 `lost` 与原文案；loop 只看 `lost` 或没有 `exit_code` | `test_a_lost_exit_state_keeps_the_sandbox_message_and_says_lost`、`test_a_killed_by_signal_command_is_not_reported_as_lost` |
| 缺口字节数 | 用重新编码后的长度算，非法 UTF-8 会算错 | helper 返回读取起点 `from`，`gap = from - offset` | `test_the_gap_uses_the_helpers_read_start_not_a_redecoded_length` |
| 运行中叫 `stdout` | 环形日志里两个流混在一起，结束时才分开 | 运行中用 `output`，事件读 `stdout or output` | 既有窗口用例 |
| MockLLM 还在发 `cmd` | E2E 走的是兼容路径 | 改发 `command`；`cmd` 兼容仍有单测 | `tests/test_llm.py`、`test_the_command_may_be_named_command_or_cmd` |
| 精简目录与提示词矛盾 | 提示词禁止 cat/sed 代替 read，精简目录却不给 `read` | `REDUCED_V1` 补 `read`/`write`/`edit` | `test_the_reduced_catalog_still_fits_the_smallest_model_budget` |
| 提示词版本 | Work 规则变了，版本号没变 | `PLATFORM_PROMPT_VERSION = "2026-11-12-workspace-tools"` | `test_prefix_cache.py` 的录制请求断言 |

**验证**（同一工作树，arm64 macOS）：

- `ruff check` / `format --check` 干净（704 文件）。
- 全量 `pytest tests -q -n auto`：**3181 项 / 16 失败 / 276 跳过**。这 16 项当时记作「既有失败」，真实原因是本地没有构建 `vendor/office-suite/runtime/runner.mjs`；构建后这批是 0 失败（见第五轮更正）。
- Portal：`tsc --noEmit` 干净、`npm test` **49 文件 / 288 项通过**（新增 2 项）。
- 整栈 E2E 重跑：**210 项 / 0 失败 / 15 跳过**，与本轮改动前一致。
- 真模型多轮（DeepSeek V4.1 Flash / OpenCode Go）：第一轮 21 次请求 `prompt_tokens` 10173 → 14319 单调增；第二轮带 `previous_model_context` 继续，**前缀逐字节保留**、工具列表一致、无多余边界事件，`prompt_tokens` 14699 → 14935 继续增长；两轮都 `succeeded`。（当时没读 `cached_tokens`：网关是报的，字段在整理后的 usage 里。见第五轮一节。）

## 第四轮审查后的修正（2026-11-12）

上一轮的 `edit` 正则方向对、代价错；另外三处新问题靠对照代码与实测发现。

| 项 | 问题 | 处理 | 用例 |
|---|---|---|---|
| `edit` 正则是二次的 | 2 MB 全 `a` + 长 oldText 14.3 s，期间 GIL 不释放（另一线程只被调度 1 次） | 折叠后用 `str.find`（两路算法，线性）；行尾空白去一次并记删除点，`bisect` 映射回原文；ASCII 文件跳过折叠 | `test_a_repeated_two_megabyte_file_is_searched_in_linear_time`、`test_a_csv_of_repeated_lines_edits_in_linear_time`、`test_the_worst_case_does_not_hold_the_gil` |
| 内存 | 5 MB 折叠副本 | 2 MB 中文模糊编辑峰值 8 MiB | `test_a_two_megabyte_chinese_edit_stays_inside_the_agent_memory_budget` |
| `_file_error` 吞掉 410/503 | 租约丢失变成"Could not read file"，run 不中断 | 只映射 400/404/413，其余原样抛出 | `test_a_lost_lease_during_read_or_edit_stops_the_run`（去掉修复即失败） |
| `search_images` 只认 400 | 每个工作区第一次搜图都会失败 | 按 404 判断；fake 与路由共用 `contracts/files.py` 常量 | `test_search_images_puts_pictures_and_credits_into_the_sandbox`、`test_file_errors_carry_the_reason_the_agent_needs` |
| `save_skill(path=…)` | 文件不存在只有"工具请求失败" | 404 → "文件不存在：…" | `test_a_missing_skill_file_is_named_as_missing` |
| 提示词版本共用 | 升版本号会让所有 Chat 对话重建 | `PROMPT_VERSIONS` 按模式拆开 | `test_a_chat_conversation_keeps_its_prefix_across_this_deploy`（Chat 系统提示哈希钉在 origin/main） |
| 旧目录重建但规则没换 | 模型看到"用 exec_wait"却只被提供新工具 | 在 `ContextManager.build` 的同一个边界里重建目录并刷新 `messages[0]` | `test_a_checkpoint_with_retired_tool_names_is_rebuilt_at_a_recorded_boundary` |
| 边界原因记错 | 只有提示词变也记成 `model_changed` | 依次判断 model / 工具 / 提示词；API 的结论优先 | `test_deploy_prompt_and_tool_changes_preserve_resumed_checkpoint`、`test_below_trigger_rebuild_validates_checkpoint_and_first_wire_request[model_changed]` |
| `edit` 先传 10 MB 再拒绝 | 白传一份自己说放不下的文件 | Broker `get_file` 支持 `max_bytes`，先 `file_size` 再决定 | `test_file_errors_carry_the_reason_the_agent_needs`（`max_bytes=1024`） |
| 只读调用串行 | 每次多一次沙箱往返 | 连续 `read`/`ls`/`grep`/`find` 用 `gather` 并发，结果按 ordinal 回填 | `test_read_only_calls_in_one_reply_run_at_the_same_time`、`test_a_read_never_runs_beside_a_write` |
| `read` 整份持有 | 2000 行也要拿 10 MB | Broker 支持 `offset`/`length`，Agent 取 2 MiB 窗口 | `test_a_large_file_is_read_through_a_byte_window`、`test_an_offset_beyond_the_read_window_points_at_grep_instead_of_lying` |

**验证**（同一工作树，arm64 macOS）：

- `ruff check` / `format --check` 干净（705 文件）。
- 全量 `pytest tests -q -n auto`：**3192 项 / 16 失败 / 276 跳过**。当时又重复了「既有失败」的错误判断；构建 runner 后全量为 0 失败（见第五轮更正）。
- 改动模块定向：15 个文件 **471 项全过**。
- 整栈 E2E：**210 项 / 0 失败 / 15 跳过**。

## 没覆盖到 / 风险

- 镜像只在本机 arm64 构建并冒烟，没有做 amd64/双架构构建，也没有导入 k3s/gVisor 跑 `tests/t1_image.sh` 的第 3、4 步。
- E2E 用的是默认 mock 沙箱（真实栈 + 真实 Portal，沙箱替身）；`AILAB_E2E_SANDBOX_PROVIDER=docker/opensandbox` 那一档需要 disposable OpenSandbox 服务，没跑。
- contracts 的 52 项跳过需要 OpenSandbox、MinIO、clamd、crawler、OIDC、storage-docker 等外部服务。
- 真实沙箱的等待时序没跑：多段 30 秒等待、`remaining_seconds` 递减、期限到点杀进程组，目前用假 Broker 和本地进程验证（`LocalBroker` 真跑 helper），未在 Docker/OpenSandbox 里跑。
- 未跑 `tests/contracts`（需真实后端）与 `platform/e2e`。
- 真模型验收在本机映射目录里跑（不是容器），容器内的验证用脚本直接调 `execute_tool`，没有让模型参与。
- 真模型没有触发平台期限与 `bash_kill`（4 小时的期限无法在验收里等），这两条在容器里用 3 秒的 `exec_lifetime_seconds` 验证过；`run_subtasks` 内并发写同一文件仍只有单测覆盖。
- `idle_seconds` 在真模型里长到 34 秒时模型没有误判卡死，但更长的静默（分钟级）没有实测。
- 未跑 Next.js 生产构建：该工作树没有 `node_modules`（用主工作树的软链跑完 vitest 后已删除；Turbopack 不跟随软链）。CI 覆盖。
- ~~ADR 0030 的 ≥95% 缓存命中率测不了~~ **这条是错的（2026-11-12 更正）**：网关会返回缓存命中，整理后的 usage 里就有 `cached_tokens`（`model_transport.py:142` → `llm._cached_tokens` → `model_usage.cached_tokens`），不要去 `prompt_tokens_details` 里找。第五轮已按 ADR 0030 标准实测，见文末。
- `model_context` 里的旧工具名只做了显示兜底，没有做调用别名：在跑的 run 调用 `read_file` 会得到 `unknown tool`（用户明确表示测试环境可不管）。
- `edit` 的展示 diff 与 patch 没有接线（用户决定不进上下文、暂不渲染），`domain/text_edit.py` 只实现匹配与写回；32 行窗口 diff 未实现。
- `read` 是整份读入（受 `max_sandbox_read_bytes=10MB` 约束）后切行窗口（第六轮已改为沙箱内按行定位，见文末），不是 Pi 的流式只保留展示部分；`bash` 输出保留的是既有环形缓冲 + 头 1000/尾 8000，不是 Pi 的 2000 行/50KB 尾部截断加完整输出临时文件。这两处是刻意保留的既有边界，不是遗漏。

## 下一步

- 等用户决定是否推送、开 PR；镜像重建与真模型检查在 PR 之后按原流程补。
- 若继续：`grep`/`find` 的沙箱镜像验证、真实沙箱等待时序、前缀缓存复测。

## 第五轮审查后的修正（2026-11-12）

A–F 通过；问题都出在上一轮新加的"只读并行"和"`read` 按字节窗口"上。

| 项 | 问题 | 处理 | 用例 |
|---|---|---|---|
| 并发 checkpoint 撞车 | 每个 `_durable_tool` 执行前写 started 记录并 checkpoint，`expected_step` 被几个协程同时读，除第一个外全部 409 stale_step | `_checkpoint` 按 run 加锁，锁内读 `expected_step` 再写；只读工具不写 started 记录也不单独 checkpoint | `test_two_concurrent_checkpoints_never_share_one_expected_step`（拿掉锁即 409）、`test_concurrent_reads_do_not_collide_on_the_checkpoint_step`（三条并发 read 成功、结果按 c0/c1/c2、三条 read 与一条 read 的 checkpoint 次数相同） |
| fake 不模拟网络 | `CheckpointFake.put_checkpoint` 没有 await，协程之间不会交错 | CAS 前加 `await asyncio.sleep(0)`，所有 agent 测试都按真实往返交错 | 上面两条 |
| 结果配错步骤 | `agent.tool_call`/`tool_result` 没有调用 id，Portal 从后往前找"同名同 step 还在跑"的步骤，并发时第一个结果写进最后一行的 | 两类事件都带 `call_id`；前后端按 id 配对，没有 id 的历史事件沿用旧规则，且当已有行带 id 时不再按名字回退 | Portal `work-process-resume.test.ts` 两条、后端 `test_results_pair_by_call_id_not_by_last_matching_name`、`test_tool_events_carry_the_call_id_that_pairs_them` |
| 大文件仍整份 413 | Broker 的 `get_file` 即使带 `offset`/`length` 也先 `_download`，按文件总大小拒绝 | provider 层新增 `get_file_range_to_path`（一次 exec 把需要的切片写进沙箱临时文件再传，`finally` 删除）；带范围时上限按 `length` 判，`max_bytes` 只 `file_size` 一次 | `test_a_twelve_megabyte_file_is_read_through_the_window_not_refused`、Broker 路由用例（同一个大文件整份 413、范围成功、范围超限 413） |
| 窗口末尾没有提示 | 读到窗口末尾时 `next_offset=None`，模型以为文件结束 | `partial` 且没有 `next_offset` 时给"只读到 2MB 窗口末尾…用 grep -n / sed -n" | `test_the_end_of_the_read_window_says_how_to_go_on` |
| `edit` 起点多算删除量 | 起点正好落在删除点上时 `bisect_left` 不算这一处，上一行行尾空白被一起改掉 | 起点用 `bisect_right`，终点保持 `bisect_left` | `test_a_match_that_starts_after_line_end_whitespace_keeps_that_whitespace` |
| 行中空白被当行尾 | `oldText` 尾部的空格把行中空格整段吃掉 | 只有终点正好在行尾时才延伸到原文空白结束处，否则尾部空白必须逐字符相同，不同就不算匹配 | `test_mid_line_whitespace_is_content_not_a_line_end`、`test_mid_line_whitespace_that_differs_is_not_a_match`、穷举 `test_every_short_file_with_spaces_edits_inside_its_own_match` |
| "16 项既有失败" | 根因是本地没构建 `vendor/office-suite/runtime/runner.mjs` | `tests/conftest.py` 新增 autouse 检查：四个 Office 读取测试模块缺 runner 时直接失败并给出构建命令，不再 skip | 构建后 `test_document_context`/`test_document_reading_reliability`/`test_library_render`/`test_native_text_ranges` **76 项 0 失败** |

**验证**（同一工作树，arm64 macOS，已按 CI 方式构建 Office 读取器）：

- `ruff check` / `format --check` 干净（705 文件）。
- 全量 `pytest tests -q -n auto`：**3202 项 / 0 失败 / 252 跳过**（跳过项全因未设 PG/Redis/OSB/S3/Docker）。
- Portal：`tsc --noEmit` 干净、`npm test` **49 文件 / 290 项通过**。
- 整栈 E2E：**211 项 / 0 失败 / 15 跳过**（新增一条"同一回复两条 `read`"的用例，走真实 API 的 checkpoint CAS）。

### ADR 0030 真模型验收（DeepSeek V4.1 Flash / OpenCode Go）

网关**会**报缓存命中：整理后的 usage 里是 `cached_tokens`（第一次跑时我读错了字段，写成"网关不报"，这里更正）。同一对话第 N 轮和第 N+1 轮的完整请求逐字节比对，并从 `usage` 取命中：

| 场景 | 调用数 | 第 2 次起 `cached/prompt` | 前缀 | 每次未命中（新增 token / 未复用的上一轮尾部） |
|---|---|---|---|---|
| Work（3 轮，其中两轮同一回复发两条 `read`） | 13 | **136704 / 142586 = 95.9%** | 逐字节为前缀，工具列表不变，无边界事件 | 新增 86–586；上一轮尾部丢 140–261 |
| Chat（3 轮） | 5 | **30976 / 32080 = 96.6%** | 同上 | 新增 180–247；上一轮尾部丢 38–118 |

未复用的尾部是供应商的 128 token 块量化（对照实验：5 次连发、每次只加 12 token 时，命中稳定在 3200，新增 token 要攒满一块才被缓存）。Work 的丢块比 Chat 多，因为工具轮次一次增长 150–600 token，更容易跨块。请求本身没有任何重写：`prefix_holds` 对每次调用都比对了 `messages` 与 `tools`。

真模型崩溃恢复：子进程在一条 45 秒后台命令运行中被 SIGKILL，父进程用同一 checkpoint 恢复。恢复后的 `bash_wait` 接的是原来的 handle（`duration_ms 45039`、`exit_code 0`、`stdout: crashed-run-done`），整个过程中后台 `start` 只出现 **1 次**，命令没有被重跑。

## 第六轮审查后的修正（2026-11-12）

上一轮的 P0-1、P0-2、ADR 0030 真模型验收与崩溃恢复核对通过。新问题集中在范围读：

| 项 | 问题 | 处理 | 用例 |
|---|---|---|---|
| 真实沙箱里 `read` 全部失败 | 基类 `get_file_range_to_path` 把切片写到 `/tmp` 再调 `get_file_to_path`，而 Docker/OpenSandbox 的下载第一步就 `normalize_path`，只收 `/workspace`；`read` 每次都带 `length`，所以每次都走这条路。mock 重写了这个方法，单测、E2E（mock 沙箱）和真模型（本机映射目录）都没执行过基类实现 | 改成抽象方法 `get_file_lines_to_path`，各 provider 自己搬运字节、共用守卫脚本 `lines_script`：Docker 一次 `docker exec` 直接写 stdout；OpenSandbox exec 写 `/tmp/.ailab-lines-*` 后用 SDK 直接流式读取这个内部路径、`finally` 删除；mock 本地实现 | `tests/test_sandbox_file_transfers.py`：两个**真实** provider 类，只换传输层，守卫脚本真的在 bash 里执行（窗口、越界为空、404、目录、父目录链接、文件链接、`/tmp` 路径、临时文件不残留） |
| 范围按字节、窗口固定在开头 | 50 MB 文件里 `offset=20000` 读不到，只能让模型改用 `grep`/`sed` | 改成按行定位：Broker `get_file(line=N, length=L)`，沙箱执行 `tail -n +N \| head -c L`。`read_lines` 接受 `first_line`/`complete`：到末尾时照 Pi 报 `of N`，否则只给下一个 offset（`[More lines in file. Use offset=K to continue.]`）；从 offset 起为空时回到开头窗口按 Pi 原规则判断 | `test_an_offset_deep_in_a_big_file_is_read_where_it_is`、`test_the_tail_of_a_big_file_reports_its_exact_line_count`、`test_the_window_edge_continues_instead_of_reading_as_end_of_file`、`test_an_offset_past_the_end_says_so` |
| 窗口请求多次往返 | 先 `file_size` 再切片再下载再 `rm`；OpenSandbox 4–5 次 | 窗口请求只调一次 provider（守卫脚本自己区分 404/非普通文件/链接）：Docker 1 次 exec，OpenSandbox exec + 流 + `rm`；`max_bytes` 整份读取只经 `_download` 判断一次（上一轮说"只 `file_size` 一次"，实际仍是两次） | `test_a_window_is_bounded_by_its_length_not_by_the_file`（Broker 路由：整份 413、窗口 200、任意行、越界为空、窗口超限 413、404、400）。上一轮记的"Broker 路由用例"在仓库里并不存在，这条是新加的 |
| `edit` 行尾分支走不到 | "终点在行尾"按原文判断，而映射回原文的终点停在被去掉的空白之前，原文那里是空格：原文行尾多一个空格，三个引用空格就"找不到"；行尾多出的空白也被留下 | 按去尾后的文本判断是否在行尾 | `test_line_end_whitespace_matches_whatever_the_file_has_there`（4 种行尾）、`test_line_end_whitespace_is_consumed_not_left_behind`；换回旧判断三条失败 |
| 每个 run 留一把锁 | `_checkpoint_locks` 在 run 结束时没有清 | `process()` 的 `finally` 一并清理 | `test_a_finished_run_leaves_no_checkpoint_lock_behind`（去掉清理即失败） |
| fake 返回整份文件 | 两个 Broker fake 忽略 `line`/`length`，窗口逻辑在单测里永远"读完" | 共用 `tests/runtime_fakes.py:sandbox_window` | 上面的 read 用例 |

**验证**（同一工作树，arm64 macOS，已构建 Office 读取器，coreutils gnubin 在 PATH）：

- `ruff check` / `format --check` 干净（706 文件）。
- 改动模块与 `tests/faults` 定向：**481 项 / 0 失败 / 35 跳过**。
- 全量 `pytest tests -n auto`：3215 项 / 252 跳过；连续 11 次里 10 次 0 失败，第 2 次有 1 项失败。那次的报告没有保留，后面 10 次都没有复现，**未能定位是哪一项**；如果 CI 出现偶发失败，先看是不是同一项。
- 真实 Docker 容器（colima，`python:3.12-slim-trixie`，uid 10001）直接调用 `DockerSandbox.get_file_lines_to_path`：52 MB、400 万行的文件，第 1 行 34 ms、第 350 万行 63 ms、2 MiB 窗口 66 ms，只传窗口；超出末尾为空；缺文件 404，目录、父目录链接、文件链接都拒绝；容器 `/tmp` 无残留。

**未覆盖**：

- ~~OpenSandbox 只在替换了 SDK 的单测里执行~~：已在下一节用真实 OpenSandbox 服务补测。
- E2E 未在本机重跑（需要 Redis、Portal `node_modules` 与 Chromium），交给 CI；它走真实 Broker + mock provider，Broker 窗口路径已由路由用例覆盖。
- 本轮没有重跑真模型：请求构造没有变，`read` 的结果格式变了（没到末尾时不再报窗口内的行数）。

## 真实 OpenSandbox、真模型与调用链路优化（2026-11-12）

环境：本机 OpenSandbox server/SDK 1.1.1（Docker 运行时，colima `sbxbuild`），镜像 `sbx-office:check`；四服务 + SQLite + 一次性 Redis；模型 `deepseek-flash`（OpenCode Go）。代码基线为本分支工作树（本节改动之后）。

**真实服务上的第一轮结论**：第六轮的按行窗口在真实 OpenSandbox 上可用（execd 文件 API 能读 `/tmp`）。整条调用链很顺，但慢在 exec：OpenSandbox 上一次 exec 固定约 1 秒，文件 API 只要几毫秒，而写一次文件要 1 次 exec、读要 2 次、`edit` 3 次、每轮快照 4 次，续轮的第一个工具还要等约 10 秒建沙箱。

**改动**（设计见[设计文档](../../architecture/design/2026-11-11-pi-aligned-workspace-tools.md)"OpenSandbox 上少用 exec"）：

- OpenSandbox provider：读写前先用 `get_file_info`（lstat）逐级确认路径，能证明安全就直接用文件 API，否则仍走原守卫脚本；新增 `remove_file`（OpenSandbox 用 `delete_files`）；SDK 对"路径不存在"的 ERROR 日志只过滤这一条。
- Broker：恢复工作区时解包、清 `.template`/`.brand`、预览指纹合成一次 exec；全新沙箱不再清理；快照后的删除走 `remove_file`。解包失败只丢连续性，清理失败让租约失败（与原来一致）。
- Agent：继续上一轮工作区的 Work 轮在第一次模型调用期间取沙箱（原来只限 Office 查看）。

**实测**（同一 3 轮 Work 对话，前后各跑一次）：

| 操作 | 之前 | 之后 |
|---|---|---|
| provider `put_file` / `file_size` / 第 1 行起的读取窗口 | 约 1000 ms（各 1 次 exec） | 3–15 ms |
| 工具 `write` / `read` / `edit` | 约 1.05 s / 2 s / 3.1 s | 0.04 s / 0.04 s / 0.1 s |
| 续轮第一个工具等沙箱 | 9–13 s | 约 1 s（沙箱 3.9 s 内就绪，与模型调用并行） |
| 每次快照 | 4.1 s | 1.1 s |
| 第 N 行起的读取窗口、替换已有文件的上传 | 约 1 s | 不变（各 1 次 exec，前者要 `tail`，后者要原子 `mv`） |

- 真模型：Work 3 轮、Chat 3 轮全部成功，工具结果全部 ok、每个事件带 `call_id`、过程步骤全部闭合、无错配输出、无上下文边界。缓存命中（去掉标题调用和冷启动的第一次调用）：Work 134400/136454 = **98.5%**（之前 95.4%），Chat 20608/20797 = **99.1%**。
- 真实契约（Docker + OpenSandbox + mock）：`tests/contracts/test_sandbox_lifecycle.py` 与 `test_sandbox_transfer.py` **27 passed / 1 skipped**（新增的链接写入用例在 mock 上跳过：链接会指向宿主 `/tmp`）。新增契约覆盖链接父目录/链接文件的写入拒绝、原子替换、`remove_file`、第 1 行窗口。
- 单测：`tests/test_sandbox_file_transfers.py`（原 `test_sandbox_line_windows.py`）用照实测语义实现的 SDK 文件对象替身执行真实 provider 代码，断言 exec 次数；Broker 恢复合并、Agent 提前取沙箱各有"不改就失败"的用例。相关模块 256 项全过，ruff 干净。

**没覆盖到**：

- 父目录缺失的写入仍是 1 次 exec（execd 可能以其他用户建目录，见 HANDOFF）；第 N 行起的窗口和替换已有文件的上传也各要 1 次 exec。
- 续轮如果最后没用工具，提前取的沙箱白占约 4 秒沙箱时间（不做快照）；Office 查看轮原本就是这样。
- 只在本机 Docker 运行时的 OpenSandbox 上测过，未在 Kubernetes 运行时或 gVisor 下测 exec 与文件 API 的耗时。

**PR #188 首次 CI（`68f479cc`）**：E2E 三片与其他检查全绿；两个 Python 套件各 3 失败 + 8 错误，与数据库无关，均为测试或可选依赖问题，已修：

- `test_sandbox_file_transfers.py` 8 项错误：把沙箱路径映射到本地目录时逐个 `replace`，Linux 上临时目录本身在 `/tmp` 下，根目录被改了两遍；改为一次、只匹配路径起点的替换（本地用 `--basetemp=/tmp/...` 复现并验证）。
- `test_osb_transfer.py` 2 项：`put_file_from_path` 在方法开头 import SDK 的 `MoveEntry`，CI 不装 SDK；import 移到 `move_files` 的 try 里，失败退回 `mv`。
- `test_a_csv_of_repeated_lines_edits_in_linear_time`：0.5 s 墙钟上限在 CI 上是 0.73/0.84 s（本机 0.21 s，覆盖率不影响）。这个上限从没对应过真实回归（被替换的旧实现在这个输入上也只要 0.17 s），改成断言"行数翻倍、线程 CPU 时间 < 3 倍"。
- 第二轮 CI（`dc108083`）SQLite 只剩 `test_the_worst_case_does_not_hold_the_gil` 1 项（上一轮通过）：它数另一线程在搜索期间醒来几次，而线性搜索只要约 6 ms，繁忙的 runner 上两次之间就跑完了，和是否持有 GIL 无关。改为断言另一线程看到的最长间隔 < 1 s；持锁 2 s 的正则会产生 2.06 s 的间隔，回归仍会失败。
- 第三轮 CI（`41113c2c`）15 项全绿：SQLite 3006 passed / 225 skipped，PostgreSQL 3140 passed / 91 skipped，E2E 三片 196 passed / 15 skipped。

## 小文件的第 N 行起窗口免 exec（2026-11-12，分支 `perf/osb-offset-read`，基线 `main@d6651ce2`）

- 改动：OpenSandbox provider 的 `get_file_lines_to_path` 在文件不大于窗口时，用文件 API 流整个文件、在 Broker 里按块跳过前 N−1 行（`_from_line`），截到窗口字节数；更大的文件仍走沙箱内 `tail`。Broker 搬运和内存上限不变：最多一个窗口（2 MiB + 1）的字节，按块落临时文件。
- 真实 OpenSandbox（本机 Docker 运行时）：199 KB 文件从第 3000 行读，文件 API **5 ms**（中位 6 ms）对 exec + `tail` **1024 ms**（中位 1026 ms），两条路输出字节一致（80,040）。
- 契约（Docker + OpenSandbox + mock）：`test_sandbox_lifecycle.py` + `test_sandbox_transfer.py` **27 passed / 1 skipped**，新增小文件跨行与越过末行的窗口断言。
- 单测：新增与真实 `tail` 逐字节对照（空文件、无结尾换行、连续空行、CRLF、多字节中文，行 1–6 × 窗口 1/2/5/100 字节）与 1/2/3/7 字节分块的跳行用例，并断言只有"行 > 1 且文件大于窗口"才起 exec；去掉改动后 9 项失败。相关模块 78 passed / 2 skipped，ruff 干净。
- 没覆盖到：Kubernetes 运行时/gVisor 下的耗时；本机整栈用 Docker provider，这个改动不影响它。
- 首轮 CI（`4d4e79a6`）：SQLite 只挂 `test_a_retried_start_waits_on_the_running_command_instead_of_launching_it_twice`（PostgreSQL 同一用例通过），与本改动无关。原因：后台 `start` 在进程登记（`meta`）后就返回，不等它的 shell 跑完 `echo`，测试立刻读标记文件，赶上就报文件不存在。改为断言第二次 start 后登记的进程（pid + 启动时间）不变、只有一个任务；去掉 helper 的重试保护时仍会失败。
- 第二轮 CI（`320ac94e`）11 项全绿；squash 合并为 `main@c783fd80`（提交树与 `320ac94e` 相同）。

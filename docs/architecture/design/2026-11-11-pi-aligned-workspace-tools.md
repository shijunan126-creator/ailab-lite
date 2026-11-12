# 工作区工具对齐 Pi（read / bash / edit / write + grep / find）

状态：设计待确认，未实施。

源码依据：`/Users/michael/AILabDisk/extra/AILab/pi`（`packages/coding-agent`，1.5.2，与已安装的 `dist` 一致）下的 `src/core/tools/`：`read.ts` `write.ts` `edit.ts` `edit-diff.ts` `bash.ts` `grep.ts` `find.ts` `ls.ts` `truncate.ts` `file-mutation-queue.ts` `output-accumulator.ts` `path-utils.ts`；行为以 `test/tools.test.ts`、`test/edit-diff-window.test.ts`、`test/file-mutation-queue.test.ts`、`test/read-streaming.test.ts` 为准。

## 目标

把工作区工具换成 Pi 的名字与语义：`read`、`write`、`edit`、`bash`，并新增 `grep`、`find`（底层 `rg` / `fd`），停用 `read_file`、`write_file`、`list_files`、`exec`。效果和逻辑与 Pi 一致，包括它的容错匹配、局部 diff、并发串行化和输出截断。

非目标：不改文档工具（`read_document`/`search_document`/`view_document`/`office-read`）、不改 `view_workspace_file`/`review_artifact`/`deliver_file`、不改沙箱镜像的 Office 依赖。

## Pi 的事实（照抄，不发明）

### read

- 参数：`path`、`offset`（**1 起算的行号**）、`limit`（**行数**）。
- 输出：内容按 `2000 行 / 50KB` 两个上限中先到的那个做**头部截断**（`DEFAULT_MAX_LINES=2000`、`DEFAULT_MAX_BYTES=50*1024`）；绝不返回半行。
- 续读提示写在正文里：`[Showing lines X-Y of N. Use offset=Z to continue.]`；被 `limit` 提前截断时是 `[K more lines in file. Use offset=Z to continue.]`。
- 首行本身就超过 50KB 时不返回内容，而是 `[Line N is 1.2MB, exceeds 50.0KB limit. Use bash: sed -n 'Np' path | head -c 51200]`。
- offset 越界报错：`Offset N is beyond end of file (M lines total)`。
- 不截断时不加任何提示；`limit` 截断时是 `[K more lines in file. Use offset=Z to continue.]`（不是 `[Showing …]`）。
- `details` 只在截断时出现（`{truncation: {truncated, truncatedBy, totalLines, outputLines, …}}`）。
- 图片按附件返回（AILab 已有 `view_workspace_file`，见下文取舍）；按**文件头**判类型，不看扩展名。
- **不返回行号**，也不做 `cat -n`。

### write

- 参数：`path`、`content`。自动 `mkdir -p` 父目录，整份覆盖。
- 返回 `Successfully wrote to <path>`，没有 diff。

### edit

参数是 `{path, edits: [{oldText, newText}]}`，一次调用可含多处修改。核心在 `edit-diff.js`：

1. **行尾与 BOM**：先剥 BOM（模型不会把不可见 BOM 写进 `oldText`），再 `normalizeToLF`。写回时若原文含 CR，用 `applyReplacementsToOriginal` 把替换落回原文偏移，未改动的行保留原始行尾；插入的换行继承被替换区间所在行的行尾。
2. **容错匹配**（`fuzzyFindText` + `normalizeForFuzzyMatch`）：先精确匹配；失败才在归一化文本上匹配。归一化顺序固定：`NFKC` → 逐行 `trimEnd` → 弯引号 `''""` → 破折号（U+2010/2011/2012/2013/2014/2015/2212）→ 特殊空格（NBSP、U+2002–200A、202F、205F、3000）。它不是任意模糊匹配：匹配到之后仍要求**唯一**。
3. **唯一性**：`countOccurrences` 在归一化文本上数，>1 直接报错 `Found N occurrences of the text in <path>. The text must be unique. Please provide more context to make it unique.`
4. **都以原文为准**：所有 `oldText` 都对同一份原始内容匹配，不逐条改完再匹配。排序后检查区间是否相交，相交报 `edits[i] and edits[j] overlap in <path>. Merge them into one edit or target disjoint regions.`
5. **走了容错匹配时**：替换先在归一化文本上做，再用 `widenReplacementsToLines` 把被触及的行整体从归一化文本重建、其余行从原文取回，所以未改动行的字节（行尾、空格）原样保留。
6. **空匹配**报 `oldText must not be empty in <path>.`；**结果与原文相同**报 `No changes made to <path>. The replacement produced identical content.`
7. **diff 在写之前算**（写成功后再算 diff 就可能失败）。`generateEditDiff` 只围绕修改点建窗口：每个替换前后各带 `DIFF_WINDOW_MARGIN_LINES=32` 行上下文边界，多个窗口重叠就合并，然后只对这些窗口跑 jsdiff；展示仍只给 4 行上下文。返回 `{diff, patch, firstChangedLine}`。
8. **同文件串行**：`withFileMutationQueue(absolutePath, …)`，键是 `realpath`（文件不存在时退化成解析后的路径），先进先出，释放后清表；不同文件互不阻塞。中断不用 abort 监听器 reject（那会在文件操作还没落地时释放锁），而是在每个 `await` 之后检查 `signal.aborted`。
9. 参数容错：模型把 `edits` 当 JSON 字符串发、或直接发单个 `{oldText,newText}` 对象、或发老的顶层 `oldText/newText`，都会被 `prepareEditArguments` 归一化。注释里写明这是 Opus 4.6 与 GLM-5.1 的真实行为。

### edit 的拼接缝规则（最容易抄错的一处）

`applyReplacementsToOriginal` 里有一条：**保留下来的裸 CR 和紧随其后的 LF 不能在拼接处合成一个 CRLF**。用户没改的那一行末尾若是一个裸 CR，而插入文本以 LF 开头（或删除区间两边的 CR 与 LF 相接），写入前会补一个 LF，让写出的行尾与 diff 展示的行尾一致。Pi 用 3000 轮随机混合行尾（`\n` `\r\n` `\r` 空）的不变量测试守这条：`normalizeToLF(written) == applied.newContent`。

### 性能契约

`edit-diff-window.test.ts` 里有一条硬约束：在 **20MB 文件**上改 10 个字节，必须在 **`--max-old-space-size=96`（96MB 堆）**的子进程里算完 diff。退化成整份 diff 会 OOM 直接失败。这不是"优化"，是设计约束；移植时要按同样方式验证（例如 `resource.setrlimit` 或子进程限额）。

### bash

Pi 在本地跑，`command` + `timeout`（到点杀）就够了。我们的命令在远端沙箱、单次 HTTP 不能挂很久，所以照抄它的**名字和阻塞语义**，但等待与期限分开（下一节）：

- 参数：`cmd`、`wait_seconds`（本次等待窗口）、`background`（立即返回句柄）。
- 输出增量读取、有界保存（环形缓冲 `log_bytes`，默认 1MB），单次返回有界（助手侧每路 8000 字符，头部 1000 字符）。
- 退出码非 0 是错误结果：`{ok: false, exit_code: N, stdout, stderr}`。
- 子进程整组管理（`start_new_session` + 进程组 SIGKILL）。
- `bash_wait`（`handle`、`wait_seconds`、`offset`）与 `bash_kill`（`handle`）保留：显式后台执行和"确认终止"是模型必须能做的两件事，不能为了只留四个工具而删掉。

### bash 的等待窗口与平台期限（2026-11-11 调整）

三个概念必须分开，任何两个都不能混用同一个参数名：

| 概念 | 谁定 | 实现 | 到点后果 |
|---|---|---|---|
| HTTP 单次等待 | Agent | `WAIT_CHUNK_SECONDS = 30`，一次 Broker 调用 | 释放响应，按计划发下一次 |
| 等待窗口 `wait_seconds` | 模型 | 1–300 秒（`bash`），0–300（`bash_wait`），默认 30 | 返回状态和句柄，**不终止命令** |
| 平台硬期限 | 平台 | `min(租约剩余预算, run_max_seconds)`，启动时固定 | 沙箱内的 supervisor 自己 SIGKILL 进程组 |

- Agent 在一次工具调用里连续发多个有界的 Broker 等待请求，中间**不调用模型**、不把每个 `running` 写进上下文、不发持久事件；一次响应提前返回时补足剩余窗口，不做忙轮询。
- 窗口结束返回 `running` 状态：`handle`、累计输出、`idle_seconds`、`elapsed_seconds`、`remaining_seconds`、`wait_seconds`，以及一句"这是等待窗口结束，不是失败也不是超时"。
- 长时间没有输出不是卡死：没有任何代码路径按 `idle_seconds` 杀进程，工具描述也明确写了这一点。
- 硬期限由沙箱内的 supervisor 执行（`deadline` 文件 + 自己的 `expires_at`），即使 Agent 停止轮询也会到点杀进程；被期限杀掉的命令返回 `exit_code=-9` 加"超过平台期限"的原因和已有输出，不伪装成正常完成。
- 默认值与理由：`run_max_seconds = 14400`（4 小时，Run 的上限）是命令期限的来源；**没有**"所有命令固定 5 分钟"的规则。等待窗口上限 300 秒是因为一次模型调用不应阻塞更久而不做判断。
- 参数名：`timeout` / `timeout_seconds` 明确拒收（报错说明它既不指定等待窗口也不终止命令），`yield_seconds` / `seconds` 只作为旧名字继续接受。
- 恢复边界：句柄由 `(run_id, 调用序号, 命令文本)` 哈希得到并写进 checkpoint。Agent 重启/接管后对同一调用发的是 `bash_wait`，不是 `start`；沙箱内 helper 对已存在的句柄目录只等待、绝不重跑。租约重建导致 `/tmp` 里的作业记录消失时，返回"后台命令已不存在"并给模型 `UNCERTAIN_EXEC` 提示，不盲目重跑。
- 用户新消息可以打断等待（`bash` 与 `bash_wait` 都可被 steer）：命令留在后台，返回句柄让模型处理新消息后再等。

### grep / find

- `grep`：`rg --no-config --json --line-number --color=never --hidden`，可选 `--ignore-case`/`--fixed-strings`/`--glob`/`--context`；逐行解析 JSON，**达到 `limit`（默认 100）就 kill 子进程**；单行超 500 字符截断并附提示；总输出 50KB 上限；找不到时返回 `No matches found`；`--no-config` 是为了不让用户的 `RIPGREP_CONFIG_PATH` 改变工具行为。
- `find`：`fd --color=never --hidden`，`--max-results limit+1`（用来区分"正好 N 个"和"多于 N 个"），默认 `limit=1000`；不在 git 仓库里时加 `--no-require-git`；路径模式补 `**/` 前缀并加 `--full-path`；结果相对搜索根返回。
- `ls`：按字母序、目录带 `/` 后缀、含点文件、默认上限 500 条 —— 对应现在的 `list_files`。

## 落到 AILab 的取舍

### 1. 名字与工具目录

| Pi | AILab 现在 | 之后 |
|---|---|---|
| `read` | `read_file`（字符 offset / limit） | `read`（行 offset / limit） |
| `write` | `write_file` | `write` |
| `edit` | 无 | `edit` |
| `bash` | `exec` + `exec_wait` + `exec_kill` | `bash` + `bash_wait` + `bash_kill` |
| `grep` / `find` | 无 | `grep` / `find` |
| `ls` | `list_files` | `ls` |

`bash` 为什么保留三个：Pi 在本地跑，一个调用可以一直等到命令结束；我们的命令在远端沙箱，单次 HTTP 请求不能挂那么久。区别只在传输层：Agent 在一次工具调用内部发多次有界的等待请求，模型看到的仍然是"一次调用 —— 完成或一个检查点"。等待窗口、平台期限与恢复边界见上面「bash 的等待窗口与平台期限」。

### 2. 重活放在哪一侧

- `read` / `write` / `edit`：**Agent 侧**。`read` 走 `broker.get_file`（整份，受 `max_sandbox_read_bytes=10MB` 约束）后在 Python 里做行窗口；`edit` 的核心算法放在 `ailab_platform/domain/text_edit.py` 的纯函数里（`edit-diff.ts` 的直译），I/O 仍走 Broker。
  代价：最坏情况 2 × 10MB 常驻（读 + 写），但在 384MiB 的 Agent 限额内；换来的是不用动镜像、可以在进程内单测整条匹配/拼装逻辑。
  与 Pi 的差异要写明：Pi 是流式读、只留展示出来的行；我们是整份读入后切窗口。**上限由 10MB 配置保证**，不是流式保证。
- `grep` / `find`：**沙箱侧**。必须走 `broker.exec`（照 `output_manifest` 的做法，60 秒阻塞）跑 `rg`/`fd`，输出经过一段内联 Python 过滤（单行 500 字符、总量 50KB、到 limit 就提前退出，让 rg/fd 收到 SIGPIPE）。不经过 `exec` 工具的句柄协议，因为它是同步查询，不该让模型去轮询。
- `edit` 的**串行化**：Pi 是进程内 Map；我们在 Agent 进程内按 `(lease_id, realpath)` 建 asyncio 锁，先进先出、用完清表。`run_subtasks` 的子任务在同一进程里并发写同一文件，正是这个锁要挡的场景。

### 3. diff 与 patch 的去向

Pi 把 `{diff, patch, firstChangedLine}` 放进 `details`，不进模型上下文。AILab 的工具结果只有一份 `content` JSON，模型和 Portal 读同一份。建议：模型侧只返回 `Successfully replaced N block(s) in <path>`，外加**展示 diff**（只有 4 行上下文的窗口 diff，十几行，既能让模型自检也不占多少 token）；`patch` 不进上下文，只留在 run 消息里给 Portal 用。要不要让 Portal 渲染这段 diff 是独立的一步。

### 4. 图片

`read` 只读文本，二进制报错并指向 `view_workspace_file`（Office/PDF 分页渲染）或 `ocr_workspace_image`。不把 Pi 的图片分支搬进来，避免和已有的文档视觉工具重复。

## 改动清单

- `platform/ailab_platform/agent/tools.py`：`read`/`write`/`edit`/`ls`/`grep`/`find` 的 schema 与 handler；`SANDBOX_TOOLS`；`_text_window` 换成行窗口；删除 `READ_LIMIT_CHARS` 的字符语义。
- 新文件 `platform/ailab_platform/domain/text_edit.py`：`normalize_to_lf`、`split_bom`、`normalize_for_fuzzy_match`、`fuzzy_find_text`、`apply_edits`（唯一性/重叠/无变化）、`apply_replacements_to_original`（CRLF 保留）、`generate_edit_diff`（32 行窗口 + 4 行展示 + patch）。
- 新文件 `platform/ailab_platform/domain/text_truncate.py`：`truncate_head` / `truncate_tail` / `truncate_line` / `format_size`，常量 2000 / 50KB / 500。
- 新文件 `platform/ailab_platform/agent/file_mutation_queue.py`：按 `(lease_id, path)` 的 FIFO 锁，中断用 `await` 后检查而不是监听器。
- 新文件 `platform/ailab_platform/agent/sandbox_search.py`：`grep` / `find` 的命令拼装、沙箱内过滤程序、结果解析与提示文案。
- `platform/ailab_platform/agent/prompts.py`：`BASE_PROMPT` 里 `read_file` 的句子改成 `read`，并补 `edit`/`grep`/`find` 的一句话用法；锚点断言同步改（改漏会 `ValueError`）。
- `platform/ailab_platform/agent/subtasks.py:37-39`、`loop.py:76/82/218`、`discovery.py` 的 `REDUCED_V1/V2`、`metrics.py:93` 的 `KNOWN_TOOLS`、`skill_drafts/service.py:124`、`api/process_view.py:47-87`、`llm.py:459` 的 mock。
- `platform/portal/lib/work-process.ts`、`components/chat/work-process.tsx`：新名字的中文步骤文案。
- 测试：`tests/test_prefix_cache.py`、`test_context_band.py`、`test_stuck.py` 里写死的 `read_file`。

## 兼容与迁移

- 工具目录是请求前缀的一部分（ADR 0030）。改名等于新的工具目录：**必须 bump `CONTEXT_ABI_VERSION`**，让在跑的 run 在下一个边界重建系统提示词与目录（`context.py:711` 的 `abi_mismatch` 分支已是这个机制）。
- 历史消息里仍会有 `read_file` 这类旧名字，模型会照着学。`execute_tool` 保留一张别名表（`read_file→read` 等），命中时返回迁移提示而不是 `unknown tool`；度过一个版本再删。
- `read` 的 `offset`/`limit` 语义从"字符"变成"行"，旧 run 的冻结目录仍走旧 handler，两者不能共用 `_text_window`。

## 镜像与部署

- 镜像需要 `rg` 和 `fd`：`images/sbx-office/Dockerfile` 的 apt 清单加 `ripgrep fd-find`（trixie 里 `fd-find` 装的是 `/usr/bin/fdfind`，工具串按 Pi 的做法先找 `fd` 再找 `fdfind`）。这会改变镜像的 apt 闭包，需要重建。
- `sbx-document-reader`（Chat 文档读取）不需要，Chat 目录里没有 shell。
- Agent 与 sbx-office 镜像同步上线：新工具在旧镜像上会失败（缺 rg/fd），旧 Agent 在新镜像上无影响。

## 测试

Pi 的测试就是规格，可以逐条搬过来（`packages/coding-agent/test/`）：

- `tools.test.ts` 的 `read tool`：行限截断（2500 行 → `[Showing lines 1-2000 of 2500. Use offset=2001 to continue.]`）、字节限截断（500 行 × 200 字符 → `[Showing lines 1-N of 500 (50.0KB limit). Use offset=N+1 to continue.]`）、`offset`/`limit`/`offset+limit` 的续读提示（`[90 more lines in file. Use offset=11 to continue.]`）、offset 越界报错、截断时才有 `details`。
- `tools.test.ts` 的 `edit tool fuzzy matching`：行尾空格、**全角标点**（`你好，世界` ↔ `你好,世界`）、NFKC 兼容形（`ＡＢＣ１２３` ↔ `ABC123`、组合字符 `café`）、弯引号（单/双）、Unicode 破折号（U+2013/U+2014）、NBSP；精确匹配优先；归一化后仍需唯一（`hello world   \nhello world` → `Found 2 occurrences`）；多编辑同时走容错；容错命中后未改动的行保留原字节（`keep before  ` 的空格要还在）。
- `tools.test.ts` 的 `edit tool CRLF handling`：LF 的 `oldText` 匹配 CRLF 文件；CRLF 保留；LF 保留；跨行尾重复要检出；BOM 保留；多编辑同时保留 BOM+CRLF；CRLF 文件里插入的行写 CRLF、LF 文件里写 LF；混合行尾文件只改目标行；裸 CR 不被吞；多编辑时插入行继承被替换行的行尾；容错加宽到整行时仍保留未改动行的行尾；拼接缝不合成 CRLF；3000 轮随机行尾不变量。
- `tools.test.ts` 的 `edit tool` 主体：三处非连续修改的 diff 会折叠大段未改动内容；所有 `oldText` 对原文匹配而不是逐条累加；空 `edits` 报错；重叠报错；一条失败时不做部分应用（原子性）。
- `edit-diff-window.test.ts`：2000 轮随机用例上，窗口 diff 必须等于整份 diff 与整份 patch；行重复时 patch 仍能 `applyPatch` 还原；150000 行替换不爆栈；20MB 文件 + 96MB 堆。
- `file-mutation-queue.test.ts`：同文件串行、不同文件并行、符号链接别名共用一把锁。
- `read-streaming.test.ts` / `bash-executor-truncation.test.ts` / `grep-operations.test.ts`：流式读取、尾部截断、rg 解析的边界。

移植到 Python 时要补的：

- 上面全部行为断言；
- 旧 run 的 `read_file` 得到迁移提示而不是 `unknown tool`；
- `grep`/`find` 的 `--no-config`、limit 提前退出、单行 500 截断、无结果文案、路径相对化、`fdfind` 回退；
- `sed`/`head` 的字节限提示在中文（多字节）文本上不会切出半个字。

性能验收：`read` 10MB 文件的峰值内存、`edit` 10MB 文件耗时、`edit` 20MB 的 diff 内存上限、`grep` 在大目录上的耗时与 Broker 阻塞时间、前缀缓存 `cached_tokens / prompt_tokens` 复测（ADR 0030 要求 ≥95%）。

## 已定（2026-11-11）

1. `read` 不返回行号；需要行号时用 `grep -n` 或 `bash`。
2. `edit` 的展示 diff 不进模型上下文，也暂不渲染；`domain/text_edit.py` 只实现了匹配与写回。
3. `grep`/`find` 只给 Work。
4. `ls` 照 Pi：字母序、目录带 `/`、含点文件、上限 500。
5. bash 的等待与期限按上面「等待窗口与平台期限」执行，不再由模型设置终止时间。

## 独立审查后的修正（2026-11-12）

审查提出三类问题，全部照改，其中前两条改变了实现选择而不是参数。

### `edit` 的模糊匹配只改被匹配的那一段

Pi 把整份文件归一化后就地替换并整体写回，被碰到的那一行里所有全角标点、全角数字、弯引号、行尾空白都会被折成半角/python 形态；面向中文用户等于悄悄改坏文件。现在按字符建立「折叠字符 → 原文区间」的映射（NFKC 只保留 1:1 折叠，组合字符向前合并），匹配到哪一段就只替换那一段的原文，未命中的字节一个不动。

- 归一化后唯一性、重叠、空 oldText 的判定不变；计数改成能统计重叠出现（`"}\n}\n"` 在 `"}\n}\n}\n"` 里算两次），避免 `str.count` 把重复的右括号当成唯一。
- 拼接缝规则用穷举取代随机：字符集 `{a, \r, \n}`、长度 ≤ 6 的全部文件 × 全部 oldText × 5 种 newText，断言写回后归一化等于计算结果（毫秒级）。

### bash 的三件事继续分开，但返回值只有一个形状

- 结束（含短命令、等待中完成、kill）统一返回 `ok/exit_code/running/stdout/stderr` 加可选 `error/killed_by/duration_ms/truncated/log_path/idle_seconds/elapsed_seconds`；不再有 `output_tail`，同一段输出不会在一份结果里出现两次。
- 运行中返回累计的 `stdout`；某一跳的字节数超过 helper 单次上限时插入 `...[elided N bytes]...`，不让拼接悄悄跨过缺口。
- `bash_kill` 是"按用户要求终止"，成功即 `ok: true`（带 `killed_by: caller`）；之后再 wait 才报"命令已被终止"。
- 主参数改名为 `command`（`cmd` 仍接受，句柄哈希取同一份文本）。
- `timeout`/`timeout_seconds` 不再报错，忽略并在结果里附 `note`：报错只会浪费一轮。

### 恢复、子任务与其它

- 只有完全观察不到结果时才换算成 `UNCERTAIN_EXEC`：句柄不存在（helper 返回 `lost`）、退出状态丢失（`exit_code == -1`）、或等待本身失败（没有 `exit_code` 也没在跑）。正常结束但退出码非 0 的命令把真实结果交给模型。
- 恢复时的 `agent.tool_result` 沿用原调用名 `bash`，否则 Portal 按名字关闭步骤时会让那条 `bash` 永远显示"运行中"。
- 子任务的调用编号带父 step：两次 `run_subtasks` 里同一位置同一命令不再算出同一个句柄（否则 helper 把它当成重试，直接返回上一批的结果）。
- `write` 与 `edit` 共用 `(lease, path)` 队列：整份写与读改写并发时，后写的不会覆盖掉另一个。
- `grep` 的 `glob` 用 `removeprefix("./")`（`lstrip` 会吃掉 `.env`/`.github` 的点），并加 `--no-require-git` 与 `find` 的忽略规则对齐。
- Portal 的步骤图标新旧工具名都认，历史事件的图标不再退化。
- 提示词去掉重复表述：system 2657 tokens（含 28 个工具 schema 共 7175），比修正前少 108。

## 第三轮审查后的修正（2026-11-12）

审查指出上一轮的两处修复没到根上，并补了四条。

### `edit` 不再折叠文件，改成把 oldText 编译成正则

上一版为每个字符建一个带原文区间的对象：1 MB 文件 80 MiB / 1.56 s，5 MB 文件 403 MiB / 8.33 s，而 Agent pod 上限 256 Mi、`read`/`edit` 允许 10 MB——一次模糊 edit 就能把整个 Agent OOM 掉；而且 `edit_text` 在 async handler 里同步执行，会把事件循环一起堵住。

现在：`oldText` 编译成一个正则，字符类接受所有折叠到同一字符的源字符（1:1 折叠表首次模糊匹配时建一次，约 150 ms，进程内缓存），直接在原文上搜索。偏移天然是原文偏移，没有第二份副本，也没有映射表；5 MB 中文文件实测 16 ms、峰值 21 MiB（12.7 MB 文件同量级）。组合字符（`e`+U+0301）不再折叠：模型发的是合成形式，为它付逐字符代价不值得，这一点写进了模块 docstring。

顺带修掉尾空格 bug：oldText 末尾带空格时该空格被裁掉、原文的空格留在结果里；现在末行原本带尾空白时，正则会一直匹配到原文的空白为止。`edit` 另有 `max_edit_bytes`（2 MiB）：它至少要同时持有字节、原文字符串、新字符串和编码结果四份，不能吃满 `read` 的 10 MB。执行放到 `asyncio.to_thread`，不再阻塞事件循环。

### 文件错误的 404 / 413 / 400 分开

Broker 过去把 `get_file` 的所有 `SandboxError` 都映射成 400，Agent 又一律当成 `ENOENT`。于是对一个 12 MB 的现有文件调用 `edit`，模型听到"文件不存在"，接着用 `write` 从头覆盖——数据就丢了。

新增 `SandboxFileNotFound`（docker/opensandbox/mock 三个 provider 在路径不存在时抛它）；Broker 映射为 404 / 413（`TransferLimitError`）/ 400；Agent 分别给出 Pi 的 `ENOENT`、"> 10MB 读取上限，用 bash 的 sed -n / head -c 分段查看"、以及原样的 detail（目录 → "用 ls 查看"）。

### 子任务句柄带父调用

`step{step}:...` 只带父 step：同一条回复里两个 `run_subtasks` 的 step 相同，两批的 `sub0:1:0` 算出同一个句柄，第二批拿到上一批的结果。改成 `{invocation}/sub{index}:{used}:{ordinal}`——父调用重放时同一槽位的同一条命令仍接回原执行（想要的幂等），不同的父调用则一定是新执行。

### 恢复不再多发一个 tool_call

恢复时 `_run_tool` 又发了一次 `tool_call`，Portal（`pushStep`）和 `process_view.push_step` 都会新建步骤，结果只关掉新步骤，崩溃前那个永远转圈。`_run_tool` 新增 `announce`，恢复时传 `False`：只发 `tool_result`（名字仍是原工具名、step 沿用原值），关掉的就是原来那一步。

### 跨部署的未完成 run 会重建目录

冻结在 checkpoint 里的 `tool_catalog_v1` 可能还列着 `exec`/`read_file` 这些 Agent 已不执行的名字，恢复后每次调用都是 unknown tool。恢复时若目录含退役名（`tools.RETIRED_TOOLS`），重建一次并记为 `tool_catalog_changed` 边界；消息保持原样。不升 `CONTEXT_ABI_VERSION`：Chat 侧没有任何变化，升了会让所有 Chat 会话白重建一次、丢一次缓存。`context.py` 里"模型没变、只是目录变了"的分支也改记 `tool_catalog_changed`；`PLATFORM_PROMPT_VERSION` 升到 `2026-11-12-workspace-tools`。

### 其余

- `lost` 由 helper 写在 exit 记录里，`_final` 原样透传并保留"请先检查文件"的文案；loop 只看 `lost` 或"根本没有 exit_code"。SIGHUP 被杀的命令 returncode 也是 -1，不能再拿 -1 当"丢失"。
- 缺口字节数用 helper 返回的读取起点 `from - offset` 算，不再用重新编码后的长度（非法 UTF-8 会算成负数）。
- 运行中的结果字段叫 `output`：环形日志里 stdout 和 stderr 混在一起，结束时才分开。
- MockLLM 改用 `command`，E2E 现在走的是 schema 里的主参数。
- `REDUCED_V1` 补 `read`/`write`/`edit`：提示词禁止用 cat/sed 代替 read，而精简目录原本只给 bash——规则恰好堵死唯一的路。加完后精简目录仍在小上下文模型的预算内。

## 第四轮审查后的修正（2026-11-12）

上一轮把 `edit` 从逐字符映射改成正则，方向对、代价错：正则的耗时是"文件长度 × oldText 长度"，2 MB 全是 `a`、oldText 是 `a…ab` 时要 14.3 s；而 CPython 的 `re` 在整个搜索期间持有 GIL，`asyncio.to_thread` 并不能让事件循环继续跑（同一次搜索里另一线程只被调度了 1 次）。另外三处：`_file_error` 把所有状态码都吞成 ToolError、`search_images` 还按 400 判"不存在"、`PLATFORM_PROMPT_VERSION` 一起升会让所有 Chat 对话重建。

### `edit` 改成线性匹配

折叠都是一对一字符，所以 `str.translate` 保持长度、下标即原文下标：先把文件和 oldText 都折叠，再用 `str.find`（CPython 的两路算法，线性）找；命中即原文区间。行尾空白不建逐字符表，而是把每行行尾的 `[ \t]` 去掉一次，同时记下"被删掉的位置 + 累计删除量"两个数组（`array('l')`），用 `bisect` 把去尾文本的下标映射回原文；`oldText` 末行带尾空白时把终点延伸到原文这一行的空白结束处。

两条捷径：文件是纯 ASCII 时折叠是恒等（所有折叠源都是非 ASCII），直接跳过 `translate`——2 MB 省 130 ms；文件里没有行尾空白时跳过去尾。

实测：`a`×2 MB + 长 oldText 从 14.3 s 降到 6 ms；12 万行 CSV 的模糊编辑 220 ms；2 MB 中文模糊编辑峰值 8 MiB。单测断言两个最坏用例各 < 0.5 s，并用一个每 1 ms 计数的线程证明搜索期间 GIL 没有整段被占住。`to_thread` 留着（慢磁盘、大编码），注释改成"它不负责让搜索变便宜"。

### 文件错误的 404 / 413 / 400 只映射这三种

上一轮把 `_file_error` 写成"任何 ClientError 都变 ToolError"，于是 410（租约丢失）、503（所有权重建）、401/403、5xx 在 `read`/`edit` 上都变成"Could not read file"，run 不中断也不恢复，模型继续在一个失效的租约上工作。现在只有 400/404/413 转成给模型的提示，其余原样抛出，由 `_run_tool` 把 410 变成 `LeaseInterrupted`。用例：fake broker 抛 410 时 `read` 与 `edit` 都让 run 以"租约中断、自动继续"暂停（去掉修复即失败）。

### 状态码契约的两个旧调用方

`search_images` 只把 400 当"`credits.json` 还不存在"，404 一出来每个工作区第一次搜图都会失败；`save_skill(path=…)` 路径不存在时只剩一句"工具请求失败"。两处都按 404 判断，并把 fake 的状态码与真实路由共用 `contracts/files.py` 里的常量，`test_broker_routes.py` 断言"不存在"确实是那个常量——fake 与路由再也不会各说一套。

### 提示词版本按模式拆开

`PROMPT_VERSIONS = {"chat": 2026-11-08-genui-core, "work": 2026-11-12-workspace-tools}`：Chat 的规则文本这次一个字都没改，共用版本号却会让每个 Chat 对话的 `messages[0]` 变一次，整段上下文重建、前缀缓存全丢。用例把 Chat 的系统提示逐字节钉在 origin/main 的哈希上，并断言一个 Chat 对话跨这次升级不产生边界事件。

### 旧目录重建时规则一起刷新

`tool_catalog_v1` 里还列着 `exec_wait`/`read_file` 时，上一轮只换了工具列表，`messages[0]` 还是部署前那套"用 exec_wait…用 read_file 阅读"的规则，模型看到的规则和实际提供的工具对不上。现在这段逻辑在 `ContextManager.build` 里，重建目录的同一个边界里用当前的 `prompt_for` 重新生成 `messages[0]`，`messages[1:]` 一个字节不动。工具列表变了前缀本来就已经失效，规则刷新不多付代价。

### 边界原因不再一律记成 model_changed

进入那个分支的前提是工具或提示词变了，模型在这里并没有被比较。现在依次判断：上一轮的 model 与这一轮不同 → `model_changed`；只有工具变 → `tool_catalog_changed`；提示词变 → `platform_rules_changed`；API 已经给的结论（模型切换、访问被撤销）优先。`previous_model_context` 也补了 `model` 字段，恢复时能比较。

### 顺带

- `edit` 带上 `max_bytes`（2 MiB）：Broker 先 `file_size` 再决定，超限直接 413，不再先传 10 MB 再拒绝。
- `edit` 收到 413 的文案改成"用 bash 运行脚本修改"，不再让它拿 `sed -n` 去改文件。
- 唯一性/重复计数用 `find_all(limit=…)`，命中 100 次就停；旧代码一直数到 100，上一轮的汇报说"数到第 2 处就停"，与代码不符。

### 两个后续项

- **同一回复里的只读调用并行**：`read`/`ls`/`grep`/`find` 的连续前缀用 `asyncio.gather` 并发执行，结果按 ordinal 顺序回填（前缀稳定），`bash`/`write`/`edit` 仍严格串行，每次调用照旧单独写 `tool_log` 并逐个 checkpoint。用例断言三条 `read` 的并发峰值是 3、结果按 `c0/c1/c2` 追加；`read` 与 `write` 相邻时峰值是 1。
- **`read` 走字节范围**：Broker 的 `get_file` 接受 `offset`/`length`，Agent 只取开头 2 MiB（多取 1 字节用来区分"正好读完"），按行窗口返回；文件更大时结果带 `partial: true`，不再返回会误导的 `total_lines`，`offset` 超出已读范围就明确告诉模型用 `grep` 定位或 `sed -n`。Agent 不再为 2000 行内容持有 10 MB。

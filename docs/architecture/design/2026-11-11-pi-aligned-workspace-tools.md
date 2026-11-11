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

- 参数：`command`、`timeout`（秒，可选，**默认不超时**）。
- 输出按 `2000 行 / 50KB` 做**尾部截断**（错误和结果都在末尾），截断时把完整输出落到临时文件并给出路径与 `[Showing lines X-Y of N. Full output: <path>]`。
- 退出码非 0 是**错误结果**（`isError`），正文追加 `Command exited with code N`。
- 子进程整组管理（`detached` + 进程组 kill），超时连后台子进程一起杀。

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

`bash` 为什么保留三个：Pi 在本地跑，可以一直等到命令结束；我们的命令在远端沙箱，HTTP 请求不能挂 10 分钟。`exec` 的 `yield_seconds`（≤120）+ 句柄轮询是现有唯一能撑过长命令的机制，改名但不改协议。同理 `bash` 不设默认超时，由 `run_max_seconds` 兜底。

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

## 未决

1. `read` 是否返回行号（Pi 不返回；`edit` 用 `oldText` 定位不需要行号，但模型引用行号时更省事）。
2. `edit` 的展示 diff 是否进模型上下文（建议进），Portal 是否渲染（建议另一步）。
3. `grep`/`find` 是否只给 Work。Chat 没有 shell 也没有 `rg` 镜像，默认只 Work。
4. `list_files`→`ls` 是否照 Pi 的"字母序 + 目录带 `/`"改输出格式（现在返回 JSON 结构，前端可能在用）。

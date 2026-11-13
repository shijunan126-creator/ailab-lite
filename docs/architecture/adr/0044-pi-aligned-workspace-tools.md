# ADR 0044：工作区工具对齐 Pi 的语义
状态：已采纳（2026-11-12）。

相关：[ADR 0003](0003-broker-exec-proxy-and-leases.md)、[ADR 0030](0030-prefix-stable-model-requests.md)、[Agent 模块](../../modules/agent.md)。

## 决定

Work 的工作区工具使用 Pi coding agent 的名字和语义：`read`、`write`、`edit`、`bash`（加 `bash_wait`、`bash_kill`）、`ls`、`grep`、`find`。模型对这套接口熟悉，参数和报错文案照 Pi，不另起一套。和 Pi 不同的地方都来自一件事：命令跑在远端沙箱里，中间隔着 Broker 的 HTTP。

## 取舍

**重活放在哪一侧**
- `read`/`write`/`edit` 在 Agent 侧：字节经 Broker 取回，匹配与拼接是 `platform/ailab_platform/domain/text_edit.py` 里的纯函数，可以在进程内单测，不用动镜像。代价是 Agent 进程持有窗口或整份文件；`edit` 受 `AILAB_MAX_EDIT_BYTES`（默认 2 MiB）限制，超限直接 413，提示改用 `bash` 跑脚本。
- `grep`/`find` 在沙箱侧，一次同步 exec 跑 `rg`/`fd`，过滤程序在沙箱内截断（单行、总量、条数到上限就停），不走 `bash` 的句柄协议（`platform/ailab_platform/agent/workspace_query.py`）。
- 同一文件的写操作按 `(lease_id, path)` 先进先出串行（`platform/ailab_platform/agent/file_mutation_queue.py::with_file_mutation_queue`），挡住并行子任务同时改同一文件。

**`read` 按行窗口取**
- Broker 只把从第 N 行起的 2 MiB + 1 字节送出沙箱（`READ_WINDOW_BYTES`）。多出的 1 字节用来区分“读到末尾”和“后面还有”：到末尾才报总行数，否则只给下一个 `offset`。任意大的文件都能一窗一窗读完，Agent 不为 2000 行内容持有整份文件。

**`edit` 必须线性**
- 先精确匹配；失败才折叠（NFKC、引号、破折号、特殊空格、行尾空白）后匹配，匹配仍要求唯一，所有 `oldText` 都对同一份原文匹配，区间不能重叠。
- 折叠都是一对一字符，`str.translate` 保持长度，用 `str.find` 找；行尾空白用“删除点 + 累计量”加 `bisect` 映射回原文。不用正则，也不建逐字符映射表：正则的耗时是“文件长度 × oldText 长度”，而且搜索期间一直持有 GIL，会卡住整个事件循环。
- 只重写被匹配的那一段，未改动行的行尾和空白原样保留。

**`bash`：等待窗口、平台期限、HTTP 单次等待是三件事**
- `wait_seconds`（模型给，最多 300 秒）只决定这次调用何时返回，到点返回句柄和已有输出，不终止命令，也不是失败。
- 硬期限由沙箱内的 supervisor 执行，取 Run 剩余预算与 `AILAB_RUN_MAX_SECONDS` 的较小值。Agent 停止轮询，命令照样会被按时杀掉。
- Agent 在一次工具调用里连续发多次有界的 Broker 等待（`WAIT_CHUNK_SECONDS`），中间不调模型、不写事件。
- 句柄由 `(run_id, 调用序号, 命令)` 哈希得出：恢复后对同一调用只会继续等待，绝不重跑。

**OpenSandbox 上少用 exec**
- 一次 exec 固定约 1 秒，文件 API 只要几毫秒。凡是文件 API 能证明安全的读写（逐级 lstat 确认是真实目录和普通文件），就不起 exec；证明不了才走守卫脚本（`platform/ailab_platform/sandbox/base.py::lines_script`）。

**不搬进来的**
- Pi `read` 的图片分支：二进制文件报错，指向 `view_workspace_file` 或 OCR 工具。
- Pi `edit` 的 patch：不进模型上下文，只返回替换结果和短 diff。

## 后果

- 换工具名会改变工具目录，旧 Run 在恢复边界重建（[ADR 0042](0042-single-tool-catalog.md)）。
- 只读工具（`read`/`ls`/`grep`/`find`）在同一回复里的连续调用可以并发，结果按顺序回填；有副作用的工具严格串行。checkpoint 按 Run 加锁，一次只有一个在途。

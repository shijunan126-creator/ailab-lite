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
- 单元套件（`platform/tests`，除 `contracts/`）：**3002 项，16 失败，189 跳过**。16 项失败与未修改基线逐条相同：在 `origin/main` 工作树跑同样的 `test_document_context.py`、`test_document_reading_reliability.py`、`test_library_render.py` 得到同一组失败（本机缺 OCR/渲染依赖，`ocr_executor_unavailable`、`stat` 环境差异），与本改动无关。
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

## 没覆盖到 / 风险

- 没有重建完整 sbx-office 镜像：只验证了 base + 快照里能装出 `rg`/`fdfind`，以及这些程序在该用户下工作；Office/TeX/Chromium 那一层、`requirements.lock`、`skill_index.py --check` 未在本机重建。
- 真实沙箱的等待时序没跑：多段 30 秒等待、`remaining_seconds` 递减、期限到点杀进程组，目前用假 Broker 和本地进程验证（`LocalBroker` 真跑 helper），未在 Docker/OpenSandbox 里跑。
- 未跑 `tests/contracts`（需真实后端）与 `platform/e2e`。
- 真模型验收在本机映射目录里跑（不是容器），容器内的验证用脚本直接调 `execute_tool`，没有让模型参与。
- 真模型没有触发平台期限与 `bash_kill`（4 小时的期限无法在验收里等），这两条在容器里用 3 秒的 `exec_lifetime_seconds` 验证过；`run_subtasks` 内并发写同一文件仍只有单测覆盖。
- `idle_seconds` 在真模型里长到 34 秒时模型没有误判卡死，但更长的静默（分钟级）没有实测。
- 未跑 Next.js 生产构建：该工作树没有 `node_modules`（用主工作树的软链跑完 vitest 后已删除；Turbopack 不跟随软链）。CI 覆盖。
- ADR 0030 的 ≥95% 缓存命中率**测不了**：OpenCode Go 网关的 usage 里没有 `prompt_tokens_details`（每个调用都是 `cached=None`）。能看到的是结构：同一任务 10 轮里 `prompt_tokens` 从 7320 单调增到 9726，没有任何一轮是重写前文；字节级的"上一请求是下一请求的前缀"由 `tests/test_prefix_cache.py` 的录制请求守着。
- `model_context` 里的旧工具名只做了显示兜底，没有做调用别名：在跑的 run 调用 `read_file` 会得到 `unknown tool`（用户明确表示测试环境可不管）。
- `edit` 的展示 diff 与 patch 没有接线（用户决定不进上下文、暂不渲染），`domain/text_edit.py` 只实现匹配与写回；32 行窗口 diff 未实现。
- `read` 是整份读入（受 `max_sandbox_read_bytes=10MB` 约束）后切行窗口，不是 Pi 的流式只保留展示部分；`bash` 输出保留的是既有环形缓冲 + 头 1000/尾 8000，不是 Pi 的 2000 行/50KB 尾部截断加完整输出临时文件。这两处是刻意保留的既有边界，不是遗漏。

## 下一步

- 等用户决定是否推送、开 PR；镜像重建与真模型检查在 PR 之后按原流程补。
- 若继续：`grep`/`find` 的沙箱镜像验证、真实沙箱等待时序、前缀缓存复测。

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

## 没覆盖到 / 风险

- 未重建 sbx-office 镜像。`ripgrep`/`fd-find` 加进了 apt 清单，但快照里能否解析出这对包、以及 `fd-find` 实际装成 `fdfind` 的路径，只有在 CI 的镜像构建和真实沙箱里才能确认。
- 真实沙箱的等待时序没跑：多段 30 秒等待、`remaining_seconds` 递减、期限到点杀进程组，目前用假 Broker 和本地进程验证（`LocalBroker` 真跑 helper），未在 Docker/OpenSandbox 里跑。
- 未跑 `tests/contracts`（需真实后端）与 `platform/e2e`。
- 未跑 Next.js 生产构建：该工作树没有 `node_modules`（用主工作树的软链跑完 vitest 后已删除；Turbopack 不跟随软链）。CI 覆盖。
- 未按 ADR 0030 做真模型前缀缓存复测（工具目录变了，`cached_tokens / prompt_tokens` 需真模型确认）。
- `model_context` 里的旧工具名只做了显示兜底，没有做调用别名：在跑的 run 调用 `read_file` 会得到 `unknown tool`（用户明确表示测试环境可不管）。
- `edit` 的展示 diff 与 patch 没有接线（用户决定不进上下文、暂不渲染），`domain/text_edit.py` 只实现匹配与写回；32 行窗口 diff 未实现。
- `read` 是整份读入（受 `max_sandbox_read_bytes=10MB` 约束）后切行窗口，不是 Pi 的流式只保留展示部分；`bash` 输出保留的是既有环形缓冲 + 头 1000/尾 8000，不是 Pi 的 2000 行/50KB 尾部截断加完整输出临时文件。这两处是刻意保留的既有边界，不是遗漏。

## 下一步

- 等用户决定是否推送、开 PR；镜像重建与真模型检查在 PR 之后按原流程补。
- 若继续：`grep`/`find` 的沙箱镜像验证、真实沙箱等待时序、前缀缓存复测。

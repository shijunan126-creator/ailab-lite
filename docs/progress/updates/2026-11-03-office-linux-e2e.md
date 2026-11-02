# Office 原生索引 Linux CI 修复（2026-11-03）

## 基线与授权

分支 `feat/office-suite`，进入 `1455630108934f93e727fb6941c5839f7d91c869`，base `origin/main@bf0af4f`，进入时工作树干净。当前用户授权仅复现、修复、检查与本地提交；PR #148 由用户审阅和推送，不沿用旧交接的合并授权，未部署。

## 根因与证据

原生测试的正文 reader 和 viewer index 是独立入口。下载的 CI traces 按实际上传 file_id 对齐后，DOCX/XLSX/PPTX/PDF 的 `meta.extract` 分别为 ready、524/742/512/160 chars；原生 `/document` 响应则为 failed，reason 为 `viewer_reader_unavailable`。不能把同一 worker.log 里其他文件的 `kind:error` 当作这些原生测试的失败原因。

本次全套 Linux 路径另读到 `季度销售数据汇总表*.xlsx` 的 `meta.json` 为 `document_parse_failed`；对应 `test_genui_layout.py` 明确上传 `b"x" * 2000`，测试附件排版而非真实 XLSX。该类预期坏文件错误与原生 fixture 的成功正文必须分开判断。

Ubuntu 24.04 / Python 3.12.3 / Node 24.11.1，按 e2e workflow 安装 Python/Portal/runtime 依赖、执行 `npm ci --ignore-scripts && npm run build`、安装 Playwright Chromium 后，在同一 checkout 复现：原生 DOCX 单例 1 failed，148.97 秒（含 Portal 测试/生产构建）。实际读取 `derived/<file_id>/meta.json` 为 docx、524 chars、coverage ready；同目录 `viewer.json` 为 failed / `viewer_reader_unavailable`。独立 DOCX 与 CJK PDF 的 runtime reader 均可执行，不支持缺字体、native module 或 ignore-scripts 为本次根因的猜测。

fixture 默认把 `/opt/ailab-viewer/reader.mjs` 传给 Worker/Broker；这是平台镜像中的安装路径，GitHub checkout 不存在该文件。workflow 构建的是正文 `runner.mjs`，其 argv/输出合同不同，不能直接拿来替代 viewer adapter。

## 修复与成本

代码提交 `c5e9639`：mock E2E 在 session 临时目录从当前 `sandbox_tools/viewer-reader.ts` 构建生产 adapter，链接现有固定 runtime 依赖；显式 override 或真实 sandbox 仍要求所配置 reader 存在。缺 runtime/reader 时在 Portal build 前报错。该 adapter 不新增系统依赖、线上行为或后台任务；实测 adapter build 约 6 ms，仅一次，复用依赖，没有重复 Portal build。

上传原生测试先检查 `/document`，failed/unsupported 立即带响应原因失败；frame 等待发现“无法预览”立即报告界面原因。新增回归验证终态不等待、pending 可转 ready、显式缺 reader 提前失败。

完整套件暴露第二个独立环境缺口：PPTX Chat 的 `chat/prepare` 从 409 / `chat_attachment_processing` 变为 422 / `chat_attachment_unavailable`，消息是视觉页生成失败；当时容器无 `soffice` / Poppler。`ab7d59e` 为 CI 增加 `libreoffice-writer libreoffice-impress poppler-utils fonts-noto-cjk`，执行实际 Office→PDF→页图，不跳过原生测试。该 Ubuntu 安装约下载 168 MB、增加 482 MB 磁盘，仅 CI fixture 成本，不改生产镜像。普通 Python suite 的索引回归不在模块导入时依赖 Playwright。

浏览器步骤实测约 12.4 分钟，job 还包含依赖安装；20 分钟 job 上限给实际转换依赖与失败产物留出余量。保留单次 Portal build，无新增固定 sleep、重复文档生成或串行等待。

## 验证与边界

Linux 容器为专用 Docker 上的本次 `c3-*` 资源，2 vCPU，ARM64；GitHub runner 是 x64，耗时不能直接等同。VM 磁盘已满曾使 apt 报无效签名，改用本次容器内可执行 tmpfs 保存 checkout/浏览器，不清理已有镜像、缓存或用户服务。未操作 `ailab-local-*`。

- Linux 完整 `AILAB_E2E=1 pytest e2e -ra --durations=10 --junitxml=…`：代码 `c5e9639`，162 passed / 1 failed / 7 skipped，742.75 秒（12 分 22.75 秒，外层 wall 743.34 秒），包含 Portal tests / production build。唯一失败为上述缺视觉页依赖的 PPTX Chat；7 skip 为 perf 6 项及缺 Office 工具的页面预览 1 项。原生索引缺 reader 已消除，其余 8 项原生用例通过。最慢 setup 109.73 秒（包含构建），最慢 call 20.22 秒；无另加长超时掩盖原生故障。
- 补齐依赖后仅复核上述失败项与此前未执行的 Office 页面预览，2 passed / 0 skipped，126.00 秒（含 fixture 再次执行的 Portal 174 项/生产构建），外层 wall 126.37 秒。九项原生场景已全部有成功结果；功能用例跨轮合计 164 项通过，仅 6 项显式性能 benchmark 未执行。按用户只跑一次全套的要求，不重复整套；不把跨轮定点结果表述为最终 head 的单次全绿全套。
- Portal：174 passed / 36 files；全量 E2E 内测试耗时 22.72 秒，production build 成功；复核 session 的同一测试/build 也成功。
- 本机 SQLite Python 全套：`c5e9639`，2091 passed / 175 skipped，exit 0；不宣称 Redis/PostgreSQL/真实后端通过。最后仅调整 Playwright import 位置，当前额外回归 4 passed / 1.11 秒，并用拒绝导入 Playwright 的进程验证索引 helper 可供普通 Python CI 导入。
- Ruff check / format（545 files）、服务导入边界、ShellCheck 通过；workflow 修改后 `python3 -m unittest discover -s .github/tests` 为 28 passed。actionlint 本机不可用，未宣称通过；尚未推送，因此远端 Actions 未验证。
- 本轮启动的 c3 容器和两个 c3 临时镜像已删除；用户服务未改动。原始 trace/输出仅用于仓库外排障，仓库只留本摘要。

下一步：用户审阅本地提交后 push PR #148，确认最终 Actions；不自动 push、merge 或部署。性能容量、VBA、Microsoft Office 保真及多浏览器边界仍沿用 P5 记录。

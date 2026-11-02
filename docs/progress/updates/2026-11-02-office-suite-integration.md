# 办公能力整合（2026-11-02）

## 基线与范围

工作树 `.worktrees/office-suite`，分支 `feat/office-suite`，进入 HEAD `12e6c86`，共同 base `origin/main@bf0af4f`，未 fetch。合入 P3 `c0d6166`（合并提交 `505b13d`）和 Portal `6fe23e9`（合并提交 `1df0de7`）。最终产品/测试代码基线 **`93cb215`**；后续交接提交仅修改文档。两来源提交均为当前分支祖先；Portal 工作树未提交的 `app/office-test/` 未改动、未纳入合并。

仅本地实现、验证和提交；没有 push、PR、部署、启动 VM、修改 Docker daemon 或操作既有用户服务。P5 真模型缓存指标不在本轮。

## 合同与修复

- 合并冲突保留 `review_artifact` 与 `document_view` 两种固定工具；工作区查看同时保留 `review` 和 `trusted_snapshot`，两侧录制请求回归保留。P3 与 [04 合同](../../architecture/design/office-suite/04-viewer-and-file-contract.md) 按代码统一：file/run-path 两种引用，reveal 的 filename/path，截图复用 `client.call kind=capture_view`，不另建截图上传端点。工作区 version 为内容 hash，不返回独立 workspace_revision；Agent 追加冻结引用，不自动注入相邻正文。
- `0360ed9`：构建拒绝任意 SKILL.md 超过 **100,000 bytes**；其他可读文本资源超过 **256 KiB 或 2000 行**。覆盖导航、原文模块和兼容资源；二进制素材不按文本行数误判，modules.json 是内部清单而非可加载资源。精确边界、UTF-8 字节数、整次构建失败和可执行 AST 等价共 11 项新增回归。三个 PDF Python 脚本原先超行数，经显式 `007` 补丁压缩排版并保持 AST；固定原始源码不改，生成树、manifest 和根 index 同步重建，最长可读非 SKILL 模块 1969 行。
- `5d33f7a`、`cf9df69`：Excel viewer 引用是 TSV，reader 的模型阅读文本却含表格符号、行号与公式附注，原子串比较拒绝真实引用。受限 reader 现在从同一授权范围的确定性表格序列化中还原单元格正文，两端公式统一引用**源表达式**，不接受仅浏览器临时算出、源文件没有缓存的值；仍严格验证 owner/version/locator/quote。回归含多行、竖线、伪造值、行号、单格及多格未缓存公式。
- `d9a228b`：Portal 保留 `Preview.path`，显示文件名不再充当工作区源路径。收到已授权 reveal 即建立原生标签，不等待异步分页预览；没有源路径的历史工作区预览仍走分页回退。
- `93cb215`：viewer 启动优先处理同一文档的实时 reveal 快照；原先恢复路径时丢弃 buffered targets，首次打开只有正文、没有高亮。仍保留其他文档手动恢复的优先边界。
- `a01ab55`、`0e80f15`、`23ab7f1`、`c713fb8`：隔离 E2E 副本复制 office/ 并保持 runtime 默认相对路径；补 Worker 非默认地址、reader 路径和独立 tools+vision mock 模型。鼠标选择使用真实可视坐标，定位检查实际高亮/工作表，截图检查 ready/capture_id。旧 PPT 验收接受真实原生 viewer；修正测试错误 class、绘制 canvas 坐标、首次命令过长、无 note 时误等浮层、断言缩进及异步高亮等待。最后 Work 用例等真实查看状态 PUT 成功，不把运行结束才汇总的 previews 当作实时状态。

## 集中验证

原始日志/JUnit 均在仓库外 `/tmp/office-suite-integration`；未提交 stdout、trace、截图或构建输出。Python 完整套件各执行一次，随后没有重跑整套。完整套件基线 `a01ab55`；后续实际整合缺陷通过原失败 E2E 和必要新增 reader 回归验证，不把定点结果写成最终 HEAD 全套首次全绿。

| 检查 | 结果 |
| --- | --- |
| 最终 Ruff | `cd platform && .venv/bin/ruff check . ../vendor/office-suite/{build,inventory}.py`；同范围 `ruff format --check`：通过，537 files。 |
| SQLite 全套 | `.venv/bin/pytest tests -q`：**2075 passed / 168 skipped / 1 failed**，386.795s。唯一失败 `test_render_lifecycle_does_not_retain_records_after_1000_cycles`：tracemalloc 增长约 7.36 MiB 超过 6 MiB。只复跑该项：**1 passed**，25.956s。未改产品或门限；与 P1 已记录的顺序/全局分配波动一致，未证明全套稳定性。 |
| PostgreSQL/Redis 全套 | 同一完整命令配置下：**2184 passed / 59 skipped / 1 failed**，466.481s。失败 `test_asset_candidate_query_uses_indexes` 未命中精确索引名断言；只复跑该项 **1 passed**，1.629s。未改查询或放宽断言，执行计划波动风险保留。 |
| 新 reader 修复 | `tests/test_viewer_native_reader.py`：**14 passed**，3.917s；追加公式一致性断言后只跑新增/修改的单一回归：**1 passed**。 |
| Portal 默认路径 | `cd platform/portal && npm ci --no-audit --no-fund && npm test && env -u OFFICE_RUNTIME_SRC npm run build`：**161 passed**，构建通过。未提供 runtime 环境变量，实际使用 `../../vendor/office-suite/upstream/office-runtime`。后续前端修复由 E2E 隔离 fixture 的 Portal test/build 再次覆盖（每次 161 passed）；最后 bundle `18ef356ab076b7e1`，4.05 MiB。 |
| Portal Docker | 仓库根上下文执行 `docker build -f platform/deploy/portal.Dockerfile -t ailab-office-integration-portal:local .`：**未完成**，daemon 不可达，尚未进入 Dockerfile 构建。默认 socket 与本机交接指定 socket 均无法连接，没有启动/修复 VM 或 daemon。不能以本机 Next build 代替镜像验收。 |

Python 命令使用 GNU 工具 PATH；完整套件和真实 reader 的环境为：

```bash
export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/opt/findutils/libexec/gnubin:$PATH"
export AILAB_VIEWER_READER_PATH="$PWD/viewer-runtime/reader.mjs"  # platform cwd
.venv/bin/pytest tests -q
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16472/15 \
AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15472/ailab_test \
AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15472/ailab_migrations \
.venv/bin/pytest tests -q
```

Docker 不可用，因此 **没有完成要求中的 throwaway 容器方式**；替代为全新本机 PostgreSQL 16/Redis 进程及独立数据目录，15472/16472 非默认端口。PG 使用 `initdb -A trust -U ailab -E UTF8 --no-locale`，独立创建 ailab_test/ailab_migrations；Redis 禁用持久化。未使用开发库，既有 15461/16461 服务未触碰。PG/Redis 和所有 E2E 服务均已停止，相关七端口无 listener；临时 PG 数据、测试 reader bundle、截图、包含截图的 trace 及本轮 E2E 临时树已清理。

## 真实本地端到端

使用仓库 Playwright Chromium、真实 API/Agent/Broker/Worker/Portal、隔离 SQLite/Redis、mock sandbox 和显式 vision mock 模型。上传、扫描、Worker 原生索引、引用校验和 SSE/client-call 没有 mock API 响应。内置 preview 能打开登录页，但 snapshot 报 `Preview automation snapshot timed out after 15000ms.`；交互证据来自 HANDOFF 的 Playwright 路线，内置 tab 已关闭。

```bash
AILAB_E2E=1 AILAB_TEST_REDIS_URL=redis://127.0.0.1:16472/13 \
AILAB_E2E_BASE_PORT=18072 AILAB_E2E_PORTAL_PORT=13072 \
AILAB_VIEWER_READER_PATH="$PWD/viewer-runtime/reader.mjs" \
AILAB_E2E_SHOTS=/tmp/office-suite-integration/screenshots \
AILAB_E2E_LOG_DIR=/tmp/office-suite-integration/e2e \
.venv/bin/pytest e2e -q
```

| 运行 | 结果与用途 |
| --- | --- |
| 唯一完整 E2E | **154 passed / 6 skipped / 6 failed**，709.107s。6 skip 为未启用性能配置。5 个新增联合用例和 1 个旧分页 viewer 断言失败。 |
| 原失败项复核 | 首次因 Worker 地址缺失的 503 中断，0 个完成结果（156.004s）；修正后六项 **1 passed / 5 failed**（298.039s），旧 PPT 用例通过。 |
| 五项联合复核 | **1 passed / 4 failed**（248.877s），Word 完整链路及 viewport capture 通过；暴露公式缓存差异和测试断言问题。 |
| 剩余四项 | `pytest e2e/test_office_native.py -k 'not docx' -q`：**3 passed / 1 failed**（144.582s）；Excel/PPT/PDF 全部引用链路通过，Work 首次高亮仍丢失。 |
| Work 首次修复复核 | 单项运行：**1 failed**（131.874s）；原生打开和高亮已经通过，失败来自测试等待实时 API 尚未汇总的 previews。 |
| 最后 Work 复核 | `pytest e2e/test_office_native.py::test_live_work_file_native_agent_reveal_and_capture -q`：**1 passed**（114.744s），包含原生打开、模型 reveal 高亮、查看状态及真实 capture 回传。 |

E2E fixture 每次定点复跑都会重建自己的 Portal 副本并执行 Portal tests；这些重复构建是测试启动前置，不是重复执行完整 Python/E2E 套件。测试模型新增后，旧模型菜单测试的期望列表静态同步为三项，该已通过用例未另外复跑。

| 用户验收项 | 最终结果 |
| --- | --- |
| Chat DOCX 上传→原生侧栏→鼠标选区→quote chip→发送→点击定位 | **PASS**，report.docx，p:2，检查实际 CSS highlight。 |
| Chat XLSX 同链路 | **PASS**，budget.xlsx，Revenue!A2:E4；含公式列，引用源公式；先切 Notes 再点引文，回到 Revenue。 |
| Chat PPTX 同链路 | **PASS**，launch.pptx，slide:1/shape:2，实际形状/文本高亮。 |
| Chat PDF 同链路 | **PASS**，cjk.pdf，真实鼠标文字选择，page:1，实际高亮；不是分页 fallback。 |
| Work 活动工作区文件原生打开 | **PASS**，outputs/draft.docx，真实 exec 生成，当前 active lease；不先交付、不等分页结果。 |
| Agent document_view reveal 到达侧栏 | **PASS**，Work 首次打开即定位 p:2 并高亮。 |
| capture_view 经 client.call 往返 | **PASS**，Chat 与 Work；实际 claim/result HTTP、PNG 校验与截图持久引用，模型返回 ready/capture_id。 |

## 成本、边界与下一步

没有新增依赖、数据库查询或常驻任务。资源限额仅增加构建时线性检查；脚本排版补丁不增加运行开销。XLSX 规范化复用已有受限 reader 的范围文本，不再解析一次文件或在 API 计算公式，仍受既有 200,000 字单选区/400,000 字总文本限制。Work reveal 提前触发原有原生索引请求，只增加一个有界 UI 描述，不新增后台轮询。真实 Linux RSS、准入上沿、并发容量沿用 P3/P5 未验边界，未以小文件浏览器成功替代容量验收。

- **外部阻塞**：Docker daemon 恢复后，只补 Portal 镜像构建及一次性容器方式的 PG/Redis 验证；本轮本机进程结果不能替代容器环境证据。成功项不默认重跑，不部署。
- **未执行**：P5 真模型 cached_tokens/prompt_tokens、模型视觉质量、Linux 压力、复杂模板/宏、Safari/物理手机、多标签页截图竞争与分享 UI 联合验收。数字/日期等复杂单元格显示格式与原值差异尚未专项验收，仍可能明确拒绝引用，不放宽来源校验。
- SQLite 内存测量及 PG 精确索引名两项均存在首次失败、定点通过的波动证据；没有把它们宣称为根因已修复或全套首次全绿。当前无已知未修复的本轮产品链路失败；完整套件不是在最终代码再次执行。

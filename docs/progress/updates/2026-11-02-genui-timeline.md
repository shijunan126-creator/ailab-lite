# 时间线对齐与模型上下文核对（2026-11-02）

- 范围：用户要求解释搜索结果、组件说明、记忆管理及项目模式往返切换，并修复截图中的时间线对齐。只本地修改/验证，不发布、不部署。
- 基线：`fix/genui-timeline-alignment`，`main@82ff8285`；本地 `origin/main@d7e2058` 比基线多 3 个提交，未混入其技能/前缀缓存变更。原有未跟踪文件全部保留。
- 根因与修改：每个 li 独立计算日期轨道，日期长短使标题列漂移。父列表统一定义两列，li 使用 CSS subgrid；容器 ≤420px 时父列表切为一列。仅样式，无新增模型请求、JS、依赖或后台开销。
- 模型输入核对：`web.search` 默认 5 条、硬上限 8 条，按供应商顺序选有效 HTTP(S) 结果，每条 title/snippet/url 最多 300/500/2000 字符；不自动读取全文。`read_url` 工具默认 8000 字符，上限 60000。多次工具调用的结果保留在历史直到压缩，后续输入会再次携带。
- GenUI：支持 tools 时系统提示仅放 57 组件索引；`ui_components` 每次指定 1–16 名，返回文档及递归依赖，当前 Run 首次加通用指南。所选文档没有跨调用去重；指南标记也不等于跨 Run 去重。不支持 tools 时使用 full_prompt，包含完整组件文档。当前索引/指南/Timeline 文档分别为 1192/4305/300 字符，不是实际供应商 token 数。
- 记忆：事实才是模型输入，页面摘要只给人看；`model_view(query=...)` 按作用域、状态、相关性选择，最多 20 条/4000 字符预算（每条另算 160 字符开销）。明确用户偏好可不要求相关命中，但仍受预算限制。历史对话另按工具检索（默认5、最多10条），不整体灌入。
- 项目切换：shared 读取账号+自身事实；isolated 只读自身。isolated→shared 保留项目事实，标记后台融合：去重合证据、冲突按有效时间失效旧事实、通用事实升账号、专属事实留项目；失败保留待融合。shared→isolated 同事务把证据非空且全部属于本项目的账号事实迁入，混合来源/无证据事实留账号，并取消待融合。因此往返不是原状恢复；融合后混合证据事实不能完整搬回。历史池也随模式变化；切换不能抹除已进入当前对话的正文。
- 截图 token 标签：Portal 显示 Run 内模型调用的累计 prompt+completion，包含重复前缀及 cached prompt，不能视为单次上下文大小或未缓存计费量（`quota.run_usage_totals`、`chat/turn.tsx`）。未查询该截图会话实际调用明细，不归因其 167410 tokens。

## 验证

- Portal：135 tests / 29 files 通过；生产构建通过。Ruff check/format 通过（511 files）。
- Chromium：用当前样式和 Timeline DOM 结构检查 900/421/420/375px，标题横坐标一致、无横向溢出，窄屏日期在标题上方；旧样式负对照复现 13.9px 漂移。此为独立组件布局检查，不是已部署会话端到端验收。脚本、截图只留 `/tmp`。
- SQLite 全套：1927 passed / 163 skipped / 19 failed（2109 项，按 pytest 进度字符汇总）；复用 `.worktrees/memory-summary/platform/.venv`，`PYTHONPATH` 指当前 platform，本工作树没有 `.venv`。18 项集中在 document_context/document_reading_reliability/library_render/ocr，本工作树缺少 `vendor/zcode-office-runtime/runner.mjs`，解析结果为空/缺元数据；未逐项证实是否还存在其他原因。另一项 runtime_dependencies 报 trafilatura/rapidfuzz 不在已安装发行包依赖闭包，当前 pyproject 已声明两者，复用环境的包元数据不匹配。没有把这些失败记为通过，也没有修改无关后端来迎合环境。未跑 PostgreSQL/Redis 整栈或真模型；未改请求构造，无需新增真模型缓存验收。
- 下一步：本地样式补丁可审阅；后端全绿须先在独立环境构建 reader、安装当前 platform 元数据，再仅复核失败项。未提交、push、建 PR、合并或部署。

## 追加：预览页间距

- 用户要求 PPT 预览每页之间留一点空隙。根因是原有 `.pdf-scroll { gap:12px }` 只作用于 Document 这个整体，不作用于其内部 Page。新增 `.pdf-scroll > .react-pdf__Document` 纵向 flex 布局与 12px gap，容器不收缩，保留外层滚动与原有页样式；PDF 及转为 PDF 的 Office 预览共用此规则。
- 验证基线：同分支未提交补丁，base `82ff8285`，包含前述时间线修改。Portal 135 tests/29 files、生产 build、diff check 通过；Chromium 按实际 Document/Page 嵌套检查 900/375px，连续三页的两处间距均为12px且仍可滚动。此为布局检查，未连接已部署文件或重新验证 PDF 渲染器。
- 仅追加一条 CSS，无新请求/依赖/后台工作。未重复上一轮 Python 全套；后端19项失败仍按上文保留，不据此视为已修复。未提交或部署。

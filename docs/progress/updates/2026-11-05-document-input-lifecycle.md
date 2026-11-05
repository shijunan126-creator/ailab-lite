# 文件读取与预览优化实施记录（2026-11-05）

状态：部分实施，未整体完成，不满足新链路发布门槛。

## 基线与范围

已 fetch 核对 `origin/main@fdc0a05f68c89aa884ce3682939f70e0337c45c4`，在隔离分支 `feat/document-input-lifecycle` 实施。来源 `.worktrees/ocr-lifecycle-pr` 的完整[实施任务](../../architecture/design/2026-11-05-chat-document-input-implementation-prompt.md)、[规格](../../architecture/design/2026-11-05-chat-document-input.md)、[源码研究](../../reviews/2026-11-05-document-reading-workflow-research.md)、[覆盖缺口](../../reviews/2026-11-04-chat-visual-coverage-followup.md)原样带入；没有复制其 dirty 产品代码。只授权本地实现/迁移/验证，未 push/PR/合并/部署；未操作用户栈/业务数据。

## 实施与资源所有权

- `api/routes_library_tools.py::document_images`：选页、返回总页数与截断共用有效总页数，最终 manifest 未发布时复用已知 `render_page_count`；缺任何所需缓存页仍 pending，未知总数不会把单页缓存当整份完成。按 RenderedPage 规范 key 返回 PNG/JPEG 类型。没有取消旧工具的20页整包限制；新分档尚未实现。
- `portal/lib/js/client-calls.ts`：active 请求终态 finally 删除；最多32个活跃请求，终态只保存最多256个小收据、最长60秒且不超过调用deadline，访问时淘汰过期项；被淘汰或失败请求再次执行必须重新通过服务端持久 claim，不能绕过多tab互斥。claim/result HTTP均有AbortSignal和截止时间，finally清计时器。图片大body不入全局表。
- `portal/lib/office-views.ts` 与 `components/preview/office-view.tsx`：截图等待者归frame/source effect所有，每viewer最多1个等待者；成功/失败/取消清超时和abort监听，关闭时拒绝并清空pending map、清未消费bytes token、abort下载、解除注册；关闭后不再向旧frame发迟到回复。client capture的deadline竞争结束时abort等待者。此取消只结束桥接等待，不证明已停止上游编码计算。
- 不改模型请求构造、工具schema、消息/checkpoint或持久资产语义，不引入服务、依赖、迁移、常驻后台任务或额外文档扫描查询。

## 后续实施落点与硬门槛

| 阶段 | 代码落点 | 必须完成的合同 | 当前状态 |
|---|---|---|---|
| 安全/派生就绪 | worker/pipeline.py、worker/parsers.py、API文件回调/preview_requests、jobs/outbox | 普通上传仅安全检查；native/page/OCR独立状态；Chat/预览懒准备，专用导入/成品合同保留 | 未实现 |
| 状态/容量迁移 | models、migrations、document_cache、reaper、document_sources/viewer_captures | 1GiB全量账本/原子预留、分批回填、失败补偿、30天TTL/24小时旧代际、墓碑/CAS与规范截图引用 | 未实现 |
| 模式/新目录 | agent/tools、loop、subtasks、discovery、skill_modules、vendor/office-suite/build.py | 新Chat三工具，旧run固定目录；Work主子任务仅工作区Office/受限lease OCR；生成skill按模式导航 | 未实现 |
| 有界内容 | contracts、routes_library_tools、document_content、broker/documents、runtime适配器 | native/显式OCR独立覆盖、类型化selector/版本cursor、100块/20命中无漏分页、24K/20%与8图分档、4图view、600秒累计预算 | 未实现 |
| browser/server | routes_document_view、routes_client_calls、viewer_captures、Portal OfficeView/host | 首开不等全文索引，browser优先，CAS generation接管后备，版本/region/像素核验，临时文件与资源补偿 | 未实现 |
| 集中验收 | tests/contracts/faults/prefix、e2e、真实隔离栈 | 四格式/混排/导航、双用户、故障/删除/恢复、真实50轮heap/CPU/RSS/盘/队列/流、真实模型缓存≥95% | 尚未完成 |

不能提前启用仅换工具名或取消解析的中间态，否则会破坏Chat消费、旧run与缓存容量合同。新路径完成前不修订ADR0026为已实现，不预占新ADR编号。现有第一组没有新索引/迁移，因此无新增EXPLAIN要求。

## 验证

验证使用本分支独立venv，Office runtime先在本工作区安装并build。首次误用旧工作区editable venv的Python/E2E已主动中止，不计为最终验收；后续已核对 `ailab_platform.__file__` 指向本工作区。原始运行日志仅在仓库外 `/private/tmp/ailab-document-*`，不提交。

- Ruff check/format通过。
- Portal全套258通过；production build通过。QuickJS压力用例输出既有assertion stderr但套件通过，不能把它当作本次浏览器内存验收。
- 新增真实SQLAlchemy行/现有上传流水线回归：Word/Excel已知8/30页、最终manifest缺失、部分缓存缺页、未知总页数、显式子集及PNG/JPEG。
- 新增浏览器桥接单元回归：50次截图取消计时器回到同一基线、关闭注销；过期/非法deadline零claim、失败claim可重试、并发重复只执行一次、256收据淘汰后重新走server claim。它们不是50轮真实viewer/heap验收。
- SQLite、PG/Redis、E2E运行结果待收敛。

## 未测边界与风险

完整任务尚未完成。没有冻结/测得真实设备冷暖性能门槛，没有CPU/RSS/heap/盘和50轮真实资源稳定性证据；未做真实模型/移动设备/真实OCR与容器故障验收，不声称缓存≥95%或资源无泄漏。Docker默认socket不可连接；没有启动/修改现有Docker环境。内置preview状态报告available=false，真实浏览器以隔离E2E可用性为准。上游编码、Canvas/Worker及规范截图存储生命周期仍须完整实现，不能把桥接Map清空视作整个客户端资源治理完成。

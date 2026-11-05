# 文件读取与预览优化实施记录（2026-11-05）

状态：完整实施目标进行中，未整体完成，不满足新链路发布门槛。用户已再次明确全部实现并达标，继续原有本地授权，不新增发布/部署授权。

## 基线与范围

已 fetch 核对 `origin/main@fdc0a05f68c89aa884ce3682939f70e0337c45c4`，在隔离分支 `feat/document-input-lifecycle` 实施。来源 `.worktrees/ocr-lifecycle-pr` 的完整[实施任务](../../architecture/design/2026-11-05-chat-document-input-implementation-prompt.md)、[规格](../../architecture/design/2026-11-05-chat-document-input.md)、[源码研究](../../reviews/2026-11-05-document-reading-workflow-research.md)、[覆盖缺口](../../reviews/2026-11-04-chat-visual-coverage-followup.md)完整带入（随后仅更新设计状态行并链接本记录）；没有复制其 dirty 产品代码。只授权本地实现/迁移/验证，未 push/PR/合并/部署；未操作用户栈/业务数据。

## 实施与资源所有权

- `api/routes_library_tools.py::document_images`：选页、返回总页数与截断共用有效总页数，最终 manifest 未发布时复用已知 `render_page_count`；缺任何所需缓存页仍 pending，未知总数不会把单页缓存当整份完成。按 RenderedPage 规范 key 返回 PNG/JPEG 类型。没有取消旧工具的20页整包限制；新分档尚未实现。
- `portal/lib/js/client-calls.ts`：active 请求终态 finally 删除；最多32个活跃请求，终态只保存最多256个小收据、最长60秒且不超过调用deadline，访问时淘汰过期项；被淘汰或失败请求再次执行必须重新通过服务端持久 claim，不能绕过多tab互斥。claim/result HTTP均有AbortSignal和截止时间，finally清计时器。图片大body不入全局表。
- `portal/lib/office-views.ts` 与 `components/preview/office-view.tsx`：截图等待者归frame/source effect所有，每viewer最多1个等待者；成功/失败/取消清超时和abort监听，关闭时拒绝并清空pending map、清未消费bytes token、abort下载、解除注册；关闭后不再向旧frame发迟到回复。client capture的deadline竞争结束时abort等待者。此取消只结束桥接等待，不证明已停止上游编码计算。
- 不改模型请求构造、工具schema、消息/checkpoint或持久资产语义，不引入服务、依赖、迁移、常驻后台任务或额外文档扫描查询。

## 后续实施落点与硬门槛

| 阶段 | 代码落点 | 必须完成的合同 | 当前状态 |
|---|---|---|---|
| 安全/派生就绪 | worker/pipeline.py、worker/parsers.py、API文件回调/preview_requests、jobs/outbox | 普通上传仅安全检查；native/page/OCR独立状态；Chat/预览懒准备，专用导入/成品合同保留 | 未实现 |
| 状态/容量迁移 | models、migrations、document_cache、reaper、document_sources/viewer_captures | 1GiB全量账本/原子预留、分批回填、失败补偿、30天TTL/24小时旧代际、墓碑/CAS与规范截图引用 | 账本/暂存发布/引用保护基础与0053迁移已实现；生产写入、缓存失效和调度尚未接入 |
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
- 最终产品代码基线为本地提交 `f9aa2279`；之后只修订状态/验证文档。独立venv的Ruff check与format check通过（569文件）。
- SQLite/临时Redis全套退出0：2423通过/156跳过；PG16/临时Redis全套退出0：2511通过/68跳过，包含迁移往返合同。本次没有产品迁移。命令为 `PATH="$PWD/.venv/bin:/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/opt/findutils/libexec/gnubin:$PATH" .venv/bin/pytest tests -q`，分别设置 `AILAB_TEST_REDIS_URL=redis://127.0.0.1:26379/13`，及PG测试数据库 `127.0.0.1:25432/ailab_test`、迁移库 `ailab_migrations`、Redis DB14；仅使用本次新建临时库。双quiet日志未印summary，数量由终态progress标记核对；无失败，既有SQLite expression-index反射警告保留。
- 真实Chromium、真实五服务/Portal的隔离E2E全套退出0：194通过/6跳过；模型/sandbox按既有E2E默认mock，不当作真实模型/容器/OCR验收。使用 `AILAB_E2E=1`、临时Redis DB15、端口28080–28083/23000、仓库外日志目录运行 `.venv/bin/pytest e2e -q`。
- 内置preview成功打开该临时Portal；snapshot返回 `Preview automation snapshot timed out after 15000ms`，未将此操作算作功能验收；所开tab已关闭。功能浏览器覆盖来自上述Chromium E2E。
- 成本边界：不新增解析/渲染/OCR/模型调用；返回页图查询规模未扩张。收据访问最多检查256个小记录；同frame最多一个截图等待者。未测生产CPU/RSS/延迟，不能用这些静态边界替代设计性能门槛。
- 清理：E2E自身终止五服务及浏览器；本次临时PostgreSQL/Redis已停止，25432、26379、23000、28080–28083共7端口均确认空闲。删除本次3个已识别Worker测试profile、pytest-31/32/33测试目录和临时PG数据目录；未触碰既有栈、业务数据或其他worktree。已归纳原始日志/调试输出后清除，开发venv、node_modules和build产物留作本分支开发依赖。

## 完整目标续作：容量基础（2026-11-05）

用户明确全部实现并达标，目标继续进行，不能在此基础阶段宣告完成。fetch再次确认main仍为fdc0a05。

- 新迁移0053：全局`derived_budget`、`derived_assets`与`derived_asset_refs`。占用/预留非负约束；状态+期限、文件/Run、引用及既有截图对象key索引，SQLite/PostgreSQL往返保留原业务表。资产行不随来源FK级联消失，实际删除之前持续承担容量。
- API独占账本写入，预算行UPDATE在两库串行化并发预留。默认1GiB/30天缓存/24小时代际宽限；正常放弃立即登记删除并释放执行租约，崩溃按写租约到期+900秒回收；对象存储失败保留账本和容量。collector先持久领取，再在事务外执行删除，再CAS结算，避免在存储等待中占用业务事务。
- `writing→staged→ready`：传输成功只将实际占用结算为staged；必须与业务元数据在同一事务调用publish才能ready。崩溃于上传完/绑定前会被回收。历史截图可由message/checkpoint引用保护，最后引用释放登记删除；引用丢失补偿与传输租约有界清理。源版本/删除/当前job claim均有围栏。
- 内部reserve/finish/abandon接口仅Worker/Broker具有新`files:derived_write`scope；文件写入绑定活跃Outbox job和claim。Agent/Portal不获此scope。客户端方法已镜像，尚无生产消费者调用。历史回填持久保存prefix/cursor，每次仅一页；列表I/O在DB事务外，已入账对象幂等跳过，批量读来源/截图不逐对象查整表。
- **尚未接入生产写入与自动回收调度**。历史回填完整标记默认false，新租约在回填前拒绝；此基础接口不能被描述为全量容量治理已生效。还须接Worker/Broker/API所有生产路径、墓碑与业务缓存失效、规范截图引用继承/删除及周期校准。原生块不得简单套逐对象HTTP（最多10000块会造成大量RPC），应按有界generation bundle接入。LocalStorage的现有list_prefix虽限返回/内存，仍遍历前缀树；全平台回填/孤儿扫描的时间/I/O有界性尚待处理，不能称已达标。

本组检查使用本分支独立venv、一次性本地PG16的随机测试schema与本地临时对象存储：最终账本+全部迁移合同116通过（两库，含5000资产行EXPLAIN命中状态/期限索引、跨用户并发容量、当前claim/源版本、上传完绑定前崩溃、存储故障保留占用、历史截图引用释放、回填重启游标）。服务身份合同40通过。Ruff检查/格式通过。首轮106通过/2失败的共同原因是abandon未清lease导致正常清理延后，已修并在最终基线复核。额外一次错误文件名选择导致未收集测试，不计作通过。未重复产品全套/Portal/build/真模型，因为其他必做核心链路尚未落地；前节全套数字仅属于第一组历史代码。

临时测试根为仓库外`/private/tmp/ailab-document-goal-k26axmuc`；日志仅在该处，不提交。该组结束将停止自己的PG25432并删除其数据，后续验收重新建隔离资源。

## 未测边界与风险

完整任务尚未完成。没有冻结/测得真实设备冷暖性能门槛，没有CPU/RSS/heap/盘和50轮真实资源稳定性证据；未做真实模型/移动设备/真实OCR与容器故障验收，不声称缓存≥95%或资源无泄漏。Docker默认socket不可连接；没有启动/修改现有Docker环境。内置preview初始available=false，随后open可用但snapshot超时；真实浏览器功能回归已通过隔离Chromium E2E。上游编码、Canvas/Worker及规范截图存储生命周期仍须完整实现，不能把桥接Map清空视作整个客户端资源治理完成。

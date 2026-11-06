# 文件读取与预览优化实施记录（2026-11-05）

本记录保留实施过程与当时检查基线；后续固定依赖修复、真实Office/截图50轮及清理结论见[2026-11-06收尾记录](2026-11-06-document-input-final-acceptance.md)。未覆盖边界及发布授权仍按收尾记录明确保留。

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

本组临时PG25432已停止，端口空闲；仓库外任务目录`/private/tmp/ailab-document-goal-k26axmuc`的PG数据与原始日志已在归纳后删除，后续验收重新建隔离资源。

## 样本、门槛与真实native组件基线

容量基础产品提交`159de80b`；其后尚未改生产解析/上传流程。新增[验收样本与门槛](../../../platform/tests/acceptance/document_input/README.md)并在改这些路径前冻结。10份合成样本两次独立生成逐字节一致，manifest SHA256为`7579a1c5cfb5d996aa9c4a6b3fc0b77bf7c5bd747b8826c31c652afe9021989a`。覆盖四格式、无目录、带页眉扫描混排、40页扫描PDF、500页原生PDF、50001单元格图表/公式工作簿和原始扫描PNG；原图中文字期望值在生成器中，不以native页眉替代OCR覆盖。更大/超3000页、隐藏文字层与故障样本仍需扩展并单独冻结，不改已有样本去迎合结果。

实测设备arm64、宿主8核/16GiB、Chromium156.0.8078.4；Docker VM为2CPU/约5.77GiB，与既有栈共享，但未操作既有栈。发现可用Docker context，默认socket错误不再是容器验收阻塞。新建容器基线使用image ID `sha256:5bd2e9250ddbc139916067007b477399204e6e4d79a955746d17923bbf1cc5b3`，网络禁用、只读rootfs、1CPU/768MiB，注入本分支当前runner；每次新进程、清空自己的输出目录，先1次首进程再5次重复。下列是**native解析组件**，不是上传/首屏/派生缓存/完整回答指标。

| 样本 | 重复解析p95秒 | 6次最大Node RSS MiB |
|---|---:|---:|
| 原生3页PDF | 0.412 | 132.45 |
| 原生500页PDF | 1.186 | 169.93 |
| 扫描40页PDF | 0.466 | 142.09 |
| 页眉+扫描10页PDF | 0.472 | 136.07 |
| 无目录Word | 0.392 | 96.64 |
| 混排12页Word | 0.383 | 94.86 |
| 混排12页PPT | 0.323 | 83.36 |
| 图表/扫描Excel | 0.314 | 79.99 |
| 50001单元格Excel | 4.811 | 201.37 |

首个PDF进程14.531秒；后续格式首进程已共享容器/OS缓存，不能称各格式独立的磁盘冷启动。大表实际提取1415697字符，超过设计Chat 200000字符上限；最终应有准确有界拒绝并保留Work可用，不能以原始字节小就假称适合Chat。Node峰值均未突破原320MiB监视上限，未放宽限额。第一次基线设置因docker cp不能写只读rootfs失败，未进入解析/不计性能；改为现有Broker同类的stdin流式写tmpfs，保留只读和资源限制，随后9场景全部完成，自己的容器已移除。

原始样本/基线/设备记录仅在`/private/tmp/ailab-document-validation-oghcidq0`，保留单份冻结样本供后续最终比较；原始JSON/日志不提交。**浏览器首开/完整上传与模型成本的基线尚未完成**，须在对应生产实现前补测；低内存移动设备、端到端真实OCR与50轮稳定性尚未验收，不能以本表宣称方案已达标。

## 未测边界与风险

完整任务尚未完成。没有冻结/测得真实设备冷暖性能门槛，没有CPU/RSS/heap/盘和50轮真实资源稳定性证据；未做真实模型/移动设备/真实OCR与容器故障验收，不声称缓存≥95%或资源无泄漏。此前默认Docker socket错误已定位为context选择问题；已在可用daemon上用独立临时容器完成native组件基线，未启动/修改既有栈。内置preview初始available=false，随后open可用但snapshot超时；真实浏览器功能回归已通过隔离Chromium E2E。上游编码、Canvas/Worker及规范截图存储生命周期仍须完整实现，不能把桥接Map清空视作整个客户端资源治理完成。

## 有界存储目录与两对象原生代际（2026-11-05续作）

代码基线：`97cb761f`之后本组本地产品差异；最新fetch的main仍为`fdc0a05f`，未push/PR/合并/部署。新增0054兼容空值的UTF-8字节范围列；0053账本不变。此组尚未由普通上传/新Chat入口启动，不宣称新链路已开放或总容量全量生效。

- LocalStorage前缀列表改为适配器SQLite元数据索引；不持有业务授权/容量事实。旧对象按每次至多1000目录项、约0.1秒增量发现，持久完成目录与租约；迁移空页带续游标。目录句柄、SQLite连接正常close；正常写入立即清私有tmp和pending，崩溃按180秒写租约+900秒宽限由周期维护清理。rename/目录发布与删除在同一适配器事务串行，避免并发删除使活对象丢登记。旧版直写者/外部手改磁盘不受索引追踪，切换时必须停止旧writer；不声称自动覆盖任意外部文件修改。
- Chat profile使用parser `office-a60ba6a-ailab4`，每代仅`manifest.json`+`text.txt`，块引用UTF-8范围并逐块校验SHA256；旧块仍兼容。200000字符/10000块/200000非空格超限显式失败。Work `office-read`默认legacy，不套用Chat限制；旧平台解析显式重置legacy，避免消费模式污染。
- 新增持久native job、claim/attempt/start/complete内部合同，Worker有界流式tar校验/解包、两对象批量预留/暂存，再在API业务事务内发布索引/元数据；不改变安全原件ready。manifest对象I/O先完成再取得发布锁。发布请求指纹支持响应丢失后的同请求重放；代际身份包含generation，避免重建回指已回收旧索引。Broker传输tar有预留与结束结算，归还render lease；故障保留持久补偿。
- API/Broker/Worker生命周期关闭storage，流式/前缀循环增加总deadline。Worker当前只保证空闲consumer退出后关闭；运行中重型任务的主动取消/进程组回收仍待补，不能当成完整shutdown验收。

本组验证：SQLite/PostgreSQL迁移、事务job、账本、native、服务身份和本地目录组合203项通过（原始输出双-q不显示计数，由收集点/进度确认）；最终微调后的native/storage定点23项通过（含并发rename/delete和完成重放）。此前 storage/版本/备份/删除135通过/21跳过，双库job/迁移/storage140通过/9跳过；这些是阶段结果而非最终全套。S3尚无本组真实端点。Ruff全平台check与584文件format通过。一次最后补丁缩进错误使9项setup失败，修正后9项通过；未把错误启动计作成功。完整Python/Portal/E2E/真实验收保留至功能组完成后集中进行。

冻结样本同一指纹、同一镜像/1CPU/768MiB/Node heap160MiB/RSS320MiB，无新限额；每次新进程并清自己的输出，1首进程+5次重复，不是派生缓存命中或各格式独立冷磁盘：

| 样本 | Chat重复解析p95秒 | 最大Node RSS MiB | 正常产物 |
|---|---:|---:|---:|
| 原生3页PDF | 0.412 | 133.06 | 2 |
| 原生500页PDF | 0.791 | 170.96 | 2 |
| 扫描40页PDF | 0.468 | 142.26 | 2 |
| 页眉+扫描10页PDF | 0.513 | 136.18 | 2 |
| 无目录Word | 0.375 | 91.95 | 2 |
| 混排12页Word | 0.497 | 94.53 | 2 |
| 混排12页PPT | 0.320 | 83.82 | 2 |
| 图表/扫描Excel | 0.354 | 79.31 | 2 |
| 50001单元格Excel | 1.547 | 未采到失败进程RSS | 0份发布manifest；6次均document_text_limit |

大表字数超过200000，明确拒绝符合合同，不当作成功解析；不能以较短拒绝时间宣称全部性能更快。部分小样本p95比旧组件基线升高，未以提高阈值消除；冻结的viewer首交互门槛仍需最终端到端测量。500页PDF原组件p95为1.186秒，新0.791秒；规范输出189392字节。对象数由逐块文件降至2，避免每块HTTP/文件操作。

5000个128字节合成对象、limit20、30次LocalStorage分页：旧适配器p95 93.126ms，新索引1.319ms，CPU总2750.59ms→33.5ms；单次旧目录发现0.186秒/10页。原始测量在仓库外；存储基准脚本可重现。50次LocalStorage打开/close的FD回归是组件检查，不能冒充50轮浏览器/全栈资源稳定性。

原始新native测量保留`/private/tmp/ailab-document-validation-oghcidq0/native-chat-v4.json`，脚本拒绝覆盖冻结测量；自己的基准容器finally已删除。自己的PostgreSQL25432用于双库检查，已停止；其专属临时目录已清理，冻结样本与新旧native测量继续保留供最终对比。下一步继续安全/派生拆分与消费入口、全生产writer记账/失效/回填、Chat独立工具与Work隔离、浏览器优先与引用回收；当前所有未验收边界仍成立。

## Chat读搜看契约与原生分页内核（2026-11-05续作）

在本地`7a8828a`之上新增共享类型化selector/region/三工具请求契约，互斥pages/Word anchor/Excel sheet-range，页号从1开始，region绑定revision/rotation并拒绝NaN/越界/零面积；read默认native，OCR必须显式pages/region，view最多4页。旧目录/路由未改，**新契约与服务尚未挂到Agent/对外路由，不构成新Chat功能完成**。

纯分页内核每批最多100块/20命中/200字片段、read最多8000字并优先完整块；超长块与20命中边界都有块内位置。零命中但尚有块时仍返回cursor。游标绑定操作、file、源版本、表示代际、query和selector，换其中一项返回cursor_stale；游标不是权限票据，消费入口仍须授权。关键词是NFKC+casefold字面匹配，返回明确标记的规范化位置/片段与真实block/locator；精确quote必须另走原文read，不能直接把规范化snippet当精确来源引用。

API原生文本服务按现有derivation/ordinal与unit/anchor索引取至多101行，规范文本只读一个有界buffer、逐块核验SHA，read不自动补OCR；实际native/视觉/OCR覆盖分开。仅消费已ready且绑定源版本的索引；缺失/损坏明确错误。Excel范围、Word单元格仍返回requires_scoped_preparation，OCR/search合并与导航仍待接通，不以整sheet假冒精确范围。

验证：首8项纯内核/类型契约通过。API/storage联合组第一次42通过/1失败，根因新测试与已有测试在共享临时数据库创建了同名native.pdf，触发真实folder name唯一约束；改独有fixture名称，未放松产品约束。修正后联合43项通过（16.10秒），含原生API/存储读取、旧读取完整性和新分页。Ruff check全平台通过、588文件format通过。本组无请求构造变化，真实前缀/最终全套仍待完整目录接通后集中验收。

## 真实上传链路基线与安全检查子进程（2026-11-05续作）

基线产品`ef436436`，普通上传行为修改前，以冻结样本通过真实API/Worker/Broker、Redis、ClamAV与Docker Office链路完成六场景、各首次上传+同字节独立副本。新建313xx端口、独立临时SQLite/对象目录、随机服务凭据与唯一Broker owner；未使用或修改现有栈/用户数据。ClamAV固定镜像`d7ede3cefeaf`内置病毒库；Office镜像与原组件基线相同。宿主三个服务与Redis进程，ClamAV限0.5CPU/1GiB，重型Office仍1CPU/768MiB；不是把整栈限制在2CPU/8GiB的联合容量证明。

| 样本 | 首次上传至ready秒 | 私有缓存副本ready秒 | 首次ready时派生对象数 |
|---|---:|---:|---:|
| 原生3页PDF | 4.537 | 1.018 | 6 |
| 原生500页PDF | 9.370 | 1.244 | 503 |
| 扫描40页PDF | 2.161 | 0.260 | 43 |
| 混排12页Word | 2.928 | 1.141 | 39 |
| 混排12页PPT | 3.051 | 0.926 | 15 |
| 混排Excel | 2.651 | 0.392 | 5 |

全部12个上传为真实ClamAV clean且ready。第二次明确disposition=copy，是新文件实体的私有缓存复制，不冒充同对象命中或独立冷解析；仅两观测/样本，不报告p95。ready轮询采样的API/Broker/Worker峰值分别约136.2/98.5/107.5MiB，可能漏瞬时峰值，不包含ClamAV和沙箱Node。未测浏览器首交互、模型完整回答、CPU/查询数、移动设备和50轮稳定性。原始数据在冻结目录的upload-baseline.json，不提交对象/日志。

初次基线装置用了legacy共享服务token，API未返回Broker专属render_owner_id，读取立即503，未获得样本。已停止该确切进程/清理其容器后，改为服务独立身份与SHA256验证矩阵，重新启动独立新栈后完成上述基线；不是产品解析失败后反复重启求通过。另一次端口预检拒绝TIME_WAIT，未启动服务；以SO_REUSEADDR允许已无监听者的旧端口，保留对活监听者的拒绝。仓库保留可复现stack_benchmark.py，拒绝仓库内原始输出及覆盖冻结测量；转换后的仓库脚本另做一次运行验证，12/12真实上传再次ready且扫描clean，结果另存upload-baseline-replay.json，不覆盖本表。自己的进程/容器均由finally结束，专属state检查后清理。

新增Worker限定子进程`safety`操作：流式摘要/类型签名与OOXML必要入口核对，先流式检查ZIP central directory再允许ZipFile分配目录；10000项/100MiB解压总量，另限8MiB目录元数据与1024字节单路径，拒绝加密/ZIP64/分卷/不一致目录/路径越界/重复成员。逐成员流式CRC与实际展开量核验，不导出正文、图片或任何包成员。ZIP64在上述小包边界内不需要，当前明确unsupported，不能把它当损坏或假成功。9项回归通过，含真实受限子进程、目录超项、扩展量、类型伪装、CRC、假EOCD及路径；Ruff全平台通过、591文件format通过。

**安全操作尚未替换普通上传pipeline**：需要与Chat/预览消费、Work原件与图片能力、专用导入/交付保持一起接线；不能提前把只安全ready接到仍期待正文的旧消费入口。普通上传仍可能启动旧解析/prewarm，此表是修改前基线而非达标证据。下一步切换pipeline并完成旧/新消费懒准备、全生产派生账本与全局槽/预算/期限；整体目标继续进行。

## 安全入库与消费准备已接通（2026-11-05续作）

本组产品基线为`f4f8708`之后工作树；当前受测Platform子树`9c9f68d0248c3d687e2386aae8096b76f4d041a6`，API/Agent/Worker/Broker源码指纹`dac2121539105d6bd6f01c26f3946f0c808d869c8b69721d66d4bd81611be50e`。真实阶段数据的差异边界见下文；最新fetch main仍`fdc0a05f`。没有部署、发布或修改既有栈。此前“尚未切换pipeline”的记录为历史阶段，本节取代其当前状态。

- API为普通新输入冻结input_lifecycle=2；专用技能/模板/品牌及成品流程不套该标记。Worker新路径只有扫描、受限安全检查与身份核验，原件ready时三类表示分别absent。取消新普通输入预热、解析、私有派生复制、图片缩略图和自动摘要。API以可信安全结果校正识别格式的MIME。旧v1排队记录仍兼容原流程；新v2在scan clean但类型/完整性检查未完成时不能消费预览。
- Chat发送/旧读取工具首次消费才请求持久native job；旧read_library_file增加native-text依赖，配对调用和首次等待deadline保存在原checkpoint，完成事务唤醒，周期reconcile补偿。图像消费时才请求规范表示，不因safe ready让模型收到“已看图片”的假消息。native失败保留安全原件，Work可继续引用；普通文本流式输出字节范围，严格200000字，不偷偷截断或借Office沙箱。
- Native批次采用源版本/attempt/generation与完成指纹；正文+manifest两个对象先预留/暂存，再原子发布索引。正常失败先取得API的未发布删除权才物理删暂存，避免丢失成功响应时删坏ready资产。临时目录、tar和传输账本在完成前清理。按实际安全类型识别无扩展名PDF；结构/范围/原生内容仍非图片或OCR覆盖证明。
- 启动和≤300秒周期（默认60秒）做持久回填，单tick最多10个有界页面并受约0.5秒回填预算；未发布writing/staged/deleting补偿可用。**发布缓存TTL回收未启用**，须先补所有writer与业务行/元数据失效，不能把迁移完成标志冒充全平台预算已覆盖。单native job目前600秒总期限；同run累计墙钟、全局公平槽/取消传播仍待完成。

验证：相关SQLite组256通过/14个未配置PG的跳过（含录制前缀、文档、旧兼容、预览、账本）；PostgreSQL独立临时库相关107通过（新消费链、账本双adapter/claim/服务身份），无复用开发库；最新图片/安全边界19通过。测试会话成功结束会关闭storage/engine并清掉自己的独立profile；失败保留并打印目录供检查，避免每次pytest遗留数据库/对象，且不以该会话清理代替产品job退出前的资源断言。Ruff check及595文件format通过。最初组合52失败主要来自旧fixture把上传当作已解析/已生成预览，以及假OOXML字节；改为显式消费或明确构造v1迁移记录、有效最小文件。没有削弱产品类型检查。后续两失败是stream测试尚未消费预览、共享测试库同名fixture冲突，已分别补显式预览和唯一名字；修正后组通过。真实故障与最终全套仍未验收。

真实隔离栈再次使用固定ClamAV/Office镜像与同一合成样本，6场景×2上传均clean/ready，所有上传阶段对象数与字节均0；Broker render-prewarm/read-document/render调用均0。以下是首次观察，不是p95：

| 样本 | 新安全上传至ready秒 | 旧首次扫描+解析ready秒 |
|---|---:|---:|
| 原生3页PDF | 0.479 | 4.537 |
| 原生500页PDF | 7.308 | 9.370 |
| 扫描40页PDF | 1.049 | 2.161 |
| 混排12页Word | 1.544 | 2.928 |
| 混排12页PPT | 1.660 | 3.051 |
| 混排Excel | 1.262 | 2.651 |

500页PDF真实scan阶段6.220秒，不能把扫描时间当解析泄漏。重复副本：scan40新0.882秒/旧0.260秒、Excel新0.790秒/旧0.392秒较慢，其他4个观察改善；包含dispatch等待和安全子进程启动，只有各2观察，**不足以证明冷/暖p95及整体性能门槛**。后续在冻结提交上增加同工作集采样，查稳定开销，不修改gates或靠重启抹掉慢值。

随后用独立验证镜像`094e9ca605ef`（只在既有固定Office镜像之上复制当前V4 runner，未改变用户镜像）完成12次真实native准备。每份首次消费才启动一次read-document，共12次；render-prewarm/render均0。各格式消费准备观察为PDF小3页4.732/2.149秒、500页2.625/2.623秒、scan40页2.075/2.097秒、Word1.604/2.321秒、PPT1.557/2.123秒、Excel1.806/2.057秒。第二次为独立新文件的再次native准备，不称同file缓存命中。真实库最终24个ready规范资产、443664字节used、0 reserved，transfer行0，outbox0；专属临时目录、tar和LocalStorage partial均0。对象清理与账本对应已检查，不仅看健康检查。该证据不包含浏览器/模型，也不是50轮或所有服务RSS稳定性证明。

原始阶段测量在仓库外upload-safety-v2.json/lazy-native-v2.json；当时脚本记录HEAD但包含本组未提交差异，不能称最终冻结SHA验收。脚本现记录API/Worker/Broker源码指纹和实际Office image ID，后续最终验收可精确定位；本次不会倒填不存在的旧指纹。SQLite源代码/分组验证与真实阶段结果严格区分。自己的栈进程/容器均已停，临时数据库、对象与新增测试镜像清理后保留冻结样本/必要测量供最终比较。

剩余：新Chat三工具目录/权限与OCR/范围/导航接线、24K/20%/8图分档、Work工具与skill隔离/lease OCR、浏览器首开与优先截图/CAS后备、所有派生writer记账与TTL/引用/版本恢复、全局公平/600秒累计预算、S3总deadline/运行中shutdown，以及完整Python/Portal/E2E/真模型≥95%/多用户故障/50轮/移动设备验收。未据本组局部成功宣称目标达标。


补充收尾核验：在冻结应用提交`28b85762a0d5afc111728e93cc719c2e20c32b8f`、源码指纹`dac2121539105d6bd6f01c26f3946f0c808d869c8b69721d66d4bd81611be50e`上，12次真实安全上传再次均ready/零派生；**服务仍运行时**实查Outbox=0、transfer=0、reserved=0、临时文件=0、partial=0、model_usage行=0，三个Broker文档/预热/渲染调用均0，随后按依赖顺序停止。数据在upload-safety-before-shutdown.json；不是依赖进程退出后清空来冒充正常路径回收。测试profile清理另以1项API/子进程定点检查验证，打印的目录实际已不存在。

旧上传基线没有单独计数mock摘要usage；其“无模型”口径仅可解释为未启动Agent/未使用外部付费模型，不能据此声称旧路径完全0次mock调用或已测完整回答成本。新的脚本单独记录该计数并对v2后台usage非零失败，不改写旧冻结数据。该澄清不影响真实ClamAV/Office上传延迟的观测性质，模型完整回答/前缀成本仍待验收。

## 新目录、原生读搜与模式围栏（2026-11-06续作）

代码基线：`55f6cdf7`之后本组本地修改，基础main复核仍为`fdc0a05f`。API尚不默认写入`document_tools_version=2`，新链路未开放；不能把本组当作三工具/OCR/浏览器全部完成。

- V2目录独立构造，旧目录对象/固定checkpoint不变；新Chat提供三工具schema，新Work主/子任务去掉旧平台读/看/OCR与三工具入口，保留原件查找/复制、Office与工作区查看。Agent执行和旧API入口双重校验模式/目录；原生读搜新路由增加当前attempt、run状态、用户/对话/文件删除围栏。
- 新读搜接通已完成native与持久native-text等待；导航只读最多101条元数据、不读正文，100条分页；search保留100块/20命中和块内续游标。游标增加API私钥派生HMAC，绑定操作/源/派生/query/selector；页码用有符号32位协议边界防DB溢出，不以物理总页数准入。默认native无隐式OCR；显式OCR当前仍明确不可用，view尚待接线。
- `vendor/office-suite/build.py`生成按模式V2导航/viewer指南，作为Agent包数据；旧沙箱技能包与上游完整操作资源未改。保留格式locator、Excel公式语义、用户冻结引用和渲染局限；新版指南另有revision，恢复后变更须显式重建。Work子任务原件复制与视觉查看使用父任务同lease，图像仅追加到子任务请求。
- 请求构造新增录制验证：V2多轮read/load_skill、兼容追问追加、V1→V2目录边界、旧checkpoint精确保留；附件指导只修改平台说明，不能替换用户prompt/原文中的旧工具字符串。新读搜结果也登记上下文来源供撤权校验。

已实际检查（阶段检查，非最终全套）：首组117通过；扩展组210通过/4失败，旧边界分类回归限定V2后相关10通过；后组236通过/3失败，2项是自定义checkpoint fake未模拟事件（改查持久边界），1项是SQLite自动唯一索引名（按真实列组合识别），对应5项通过。最后新增主/子任务/指南组30通过。隔离PostgreSQL相关51通过，导航/模式/签名更新后35通过。全平台Ruff/format曾通过600文件，之后仍有修改，最终统一重跑。不是整体已绿，也不是实际模型缓存证据。

临时PostgreSQL仅本任务25432/独立目录，已停止。用户最新要求“直接做、减少测试、最后再全部测试”：已启动检查收尾，此后连续完成剩余实现，最终集中验收，不再逐组反复运行。剩余关键路径为显式OCR/范围、浏览器claim-generation与后备、所有writer记账/TTL/历史截图引用、全局公平槽/600秒预算及输入分档。Work工作区OCR仍复用平台现有OCR执行器，沙箱只准备受限图片/区域；不复制OCR权重、不向模型/沙箱暴露密钥、不另租系统render沙箱。

## Work工作区图片OCR与持久调度接线（2026-11-06，实现中）

代码在`397ba38`之上继续本地实现。按用户最新要求，本组不运行单元/回归/真实栈测试；只有编辑时格式化，所有功能、迁移、性能和清理检查留到整体实现后集中完成。以下是代码行为，**尚非已验证结论**。

- 新Work由API选V2目录，主/子任务使用`ocr_workspace_image`；发送所有附件（含图片）只传原件身份/工作区路径，不为Work做模型图片准备、隐式OCR或正文装配。旧run仍按原选项/固定目录恢复，Chat默认目录仍待完整接齐后切换。
- Broker在当前lease内通过受限Python子进程读取PNG/JPEG/WebP并裁剪，检查源文件变化/路径与符号链接、20MiB输入、16MP与2MiB输出；返回实际裁剪范围、像素尺寸与源/输出摘要。EXIF原地校正、透明图片铺白，释放Pillow/编码缓冲；不另租系统render沙箱，不安装OCR权重，不向沙箱暴露密钥。
- API持有现有OCR执行器凭据；新增二进制传输、当前run/attempt/版本围栏、持久operation身份、按用户轮换/老化的全局OCR槽和有界等待、首次重型操作起算600秒、4批/80唯一页计数。迁移0055增加operation/lane和索引。**当前该调度只接通新Work OCR；既有文件OCR、native和render生产路径仍需纳入，不能声称全平台统一调度已完成。**
- OCR执行器把模型会话保留在单个暖推理子进程；正常请求复用，取消/超时才终止拥有该请求的子进程。API逐步检查来源/attempt并传绝对deadline，断开上传调用也触发取消；执行槽保留有界进程回收时间，防API超时后直接释放仍在计算的槽。实际CANN/CPU行为和故障终止仍待最终真验收。
- Work OCR规范文字写前预留容量、发布与message引用同事务；长结果用绑定operation/source的签名cursor读取。失败只删未发布grant；正常沙箱scratch立即清理，路径/截止时间登记在Broker租约及provider元数据，崩溃由有界reconcile补偿。会话删除使该Work OCR引用/资产登记失效，物理删除后才归还容量；所有其他生产writer/TTL/截图引用合同仍在待办。
- Chat读搜补了已有页级OCR缓存读取与搜索，不隐式执行OCR；读默认native，明确选OCR页才读OCR，region不能借用整页缓存冒充。native+OCR共用100块/20命中续游标，分别报告覆盖；Word物理页映射未知时不把原生结构当物理页。OCR变化使游标失效。原生索引丢失/退役不能一直复用ready状态而形成恢复忙循环。

下一步继续完整实现：Chat显式OCR页/region持久job及独立范围缓存、原生结构范围/真实PDF范围、输入分档和发送前累计预算；浏览器优先截图/唯一generation后备；所有生产派生记账/TTL业务失效和历史引用回收。完成后一次集中运行完整检查并处理失败，不能用旧阶段测试数代替本组验证。

## Chat显式OCR页与区域任务接线（2026-11-06，实现中）

在`715f77d`上连续实现，仍按用户要求把测试留到最终；本组未运行测试/真实模型/浏览器或性能检查。代码说明不等于验收通过。

- 新增0056：文件版本/引擎/selector绑定的OCR文本单元、共享operation消费者及查询索引。0055仍是本分支未部署/未执行的新增迁移，其run外键改为可空SET NULL，避免共享任务因首个对话清理而连带丢失；不重建任何既有SQLite业务表。
- 新Chat显式`read_document(text_source=ocr)`复用已有完整范围，否则以outbox创建`document_ocr`任务；新目录仍未默认启用。页和region独立缓存，region必须绑定页或图片ID、表示revision与旋转；不能用整页文字冒充区域结果。图片ID可指已授权的浏览器规范capture，Word物理页映射未知不猜页号。裁剪返回实际范围/像素尺寸和来源。
- 同文件/版本/完整请求范围/引擎合并在途任务，消费者各保留原run截止时间；取消单个消费者不取消其他有效消费者。工作量在真正领取执行槽时计算，仅计算仍需处理的页，排队时被其他任务填好的缓存不再计执行。相同已失败请求在恢复时不能因剩余范围变化自动再排一轮；用户明确选择新范围才形成新操作。
- Worker先聚合所需页图请求，再领取OCR槽；逐范围下载、校验、受限裁剪、上传选定图片，复用现有OCR执行器。每批沿同一90/120秒绝对执行期限，不按页重置；规范文字写前预留、单元/摘要/文件OCR revision与发布同事务。成功范围重投跳过，失败保留已完成部分并唤醒付费工具；图像准备失败明确终止，不以反复重试掩盖。
- 新OCR单元参与有界搜索，和native/旧页级OCR共享100块/20命中及块内续游标，结果附typed selector和实际来源/revision。旧缓存只增补强文本摘要/元数据，不复制OCR正文对象；缺一页不能把其余页缓存当完整结果。
- 图片裁剪抽到同一受限处理模块，由Work沙箱和Worker子进程复用。输入资产有短期传输引用；浏览器来源的不可确定重建OCR文字另有文件引用。请求断开先取消并等所属执行关闭后再结束DB session；正常临时目录退出即清，未发布grant保留失败补偿。永久删除同步释放新OCR单元/必要引用，不逐单元HTTP/读整库。**输入页图/capture的生产记账与全平台回收仍需接通，新路径遇到未登记输入应失败，不能绕过保护。**

仍需完成：原生结构与PDF真实范围、Chat发送分档/首次准备跨发送重试600秒、native/render/旧OCR统一持久槽；浏览器首开基线及优先capture/唯一后备generation；所有生产writer容量登记/TTL元数据失效/历史截图引用；最后集中全套及真实性能、故障和50轮资源验收。没有新增当前绿色测试结论。


## 原生真实范围与生成源恢复（2026-11-06，未验收）

在`f30d31e5`及续作断点的未提交修改上继续；重新fetch后main仍为`fdc0a05f`。本组只实现、生成及格式化，没有运行功能/迁移/回归/浏览器/模型/性能检查，遵守用户“最后统一测试”的要求。旧阶段绿色结果不适用于本组。

- V5 runtime从生成源维护：恢复此前只在生成目录存在的紧凑正文改动，新增009补丁；runtime provenance覆盖补丁序列及adapter源。已重新生成并`npm run build --prefix vendor/office-suite/runtime`成功，不把构建当作功能通过。首次用系统Python加载生成器缺pydantic，改用本工作区venv后生成成功，没有安装或改动全局依赖。
- 新Chat PDF profile在实际总页数≤8时提取原生文字；长PDF只探测元数据/有界outline，不提全部页正文或遍历页面绘图算子。显式scope最多20页，仅对所选页调用同源PDF解析；legacy及chat-full保留旧完整有界消费语义。物理总页数不是准入上限。视觉unknown不再由空visual_units误判none，也不再为unknown生成全文件页码数组。
- 新迁移0057为不可变源/基础索引代际/parser/selector保存范围索引。PDF逐页缓存共用每批text+manifest两个对象；Word单元格与Excel区域复用同源整包parser后投影，记录source_chars/source_blocks/cells及整包限制，不称增量解析。原生读合并基础/已准备页，搜索仅合入PDF原生新页，Office结构投影不重复充当全文。
- 范围任务使用render持久公平槽、Outbox、同源在途合并、attempt/基础代际/绝对deadline围栏、批量容量预留/发布、native-scope文件等待及恢复。解析执行状态不确定时明确失败，不静默重复执行；缓存被其他操作填好时跳过。Worker临时目录/tar退出清理沿用两对象流程，发布失败由API授予未发布删除权；未验证取消/崩溃和真实清理时限。
- 长PDF无selector默认返回有界真实页导航，100条定位与签名续游标，不读正文。PDF分批累计200000字/10000块；API新读搜按完整文件大小与已知源结构累计本轮20/40MiB、10000块/200000字/200000非空单元格，不允许通过小范围绕过。完整输入分档/发送前首次600秒仍待接通。
- 已补写真实runtime页范围、Word单元格与整包预算、超大页数导航、跨scope原生检索/代际、重复消费预算等回归，尚未执行。0055–0057迁移、本组API/Worker/运行时与最终真实性能均未验证。

下一组继续Chat分档与跨发送重试截止、base native/render/旧OCR全生产调度、浏览器首开基线后优先截图和CAS后备、全writer容量/TTL业务失效/历史规范截图引用。Chat默认仍V1，不在资源合同未接齐时开放半成品新路径。仅本地修改/提交，未push/PR/合并/部署，未操作用户既有栈。


## Chat输入快照与发送前预算（2026-11-06，未默认启用/未验收）

在`8d96499`上继续集中实现。新迁移0058持久化发送身份、首次准备起点/600秒绝对截止、策略快照及有界过期索引；准备记录先提交再启动任务。兼容无发送ID的旧准备请求，后续正式发送可接管同一未绑定准备记录。Run与排队消息快照保留原预算，准备小行过期不抹掉历史/排队预算。API接受client_send_id字段，Portal尚未改为传入，待浏览器基线后同组完成。

- V2装配按本轮min(24K token,20%输入预算)、8张文档页图与实际请求余量选择整份inline或按需；最大输入成本优先移到按需，平局用固定附件序号。独立图片数量仍沿现有5张限制，不把8张文档图当会话累计上限。所有附件共用2000token导航预算，先删可选outline/名称，不删除身份或伪造覆盖。
- 纯文字Office不为页数检查转PDF；PDF probe partial/视觉unknown走按需，只有被选中inline的明确页或图片才请求渲染/规范化。非视觉模型不自动OCR；工具能力不足时明确拒绝按需档。策略/版本/索引代际/图片digest冻结在API-owned options；Run/队列/插话装配复核，图片未成功送达不能当已看。
- 旧目录继续走旧输入语义；V2新装配有内部接线，但**Chat默认仍V1**，完整资源和浏览器合同未齐不能提前开放。新增录制请求回归覆盖inline文字+按需图片不会重取/改写前缀，尚未执行；真实模型缓存仍未检查。
- 010补丁为PPT区分普通textbox与图片/图表/连接线/组合/几何对象；master/layout未映射视觉保留unknown，旧legacy视觉facts不改。Word/Excel已有完整结构且未发现视觉可标none，不把所有Office无图都误标unknown。runtime重新生成/构建成功；新增普通textbox/几何对象、分档、准备重试/预算继承等回归尚未执行。
- 已知原生结构超限持久绑定源版本，后续小范围不能绕开整份Chat拒绝。原件安全READY和Work可用性不变。

本组没有运行pytest、迁移、Portal或真实浏览器/模型/性能验收；构建和格式化不是功能结论。下一步仍为base native/render/旧OCR统一持久调度与取消、浏览器首开基线和capture/CAS后备、全部writer记账与TTL业务失效/规范资产引用；全部实现后集中验收，不用历史测试数声明完成。


## 基础native、预览与旧OCR统一调度接线（2026-11-06，未验收）

在`7b8ad34`上继续，仍未运行功能/回归/迁移/真实组件检查。没有部署、修改既有栈或提升限额。

- 增加file_work适配，把文件持久job与已有document_lanes合并：native、主动preview/page render和旧OCR在同一API持久槽/队列准入；准备/Run消费者与公共文件需求区分，记录原截止并绑定worker attempt。新旧OCR共用OCR lane；旧OCR不再只靠API进程semaphore作为全局限制。base native与范围、预览共用render lane。
- 原生/预览实际执行前领取slot，Worker忙时release claim再投递；Broker按秒复核API执行权和源版本/消费方，失权删除所属专用render沙箱并等待执行线程退出。执行deadline传到Broker/本地受限子进程，取消中的任务保留slot直到回收或固定期限，不把HTTP取消直接当计算已停。当前并发/取消时延与各provider真实回收仍未验收。
- 旧OCR输入页图改成真实Outbox preview任务，Worker不再用OCR attempt嵌套执行未领取的preview；每批推理沿同一90/120秒执行deadline和可终止的平台执行器。公共显式提取仍保留原接口/缓存语义；在途不确定失败不会自动重复已派发整批。尚需全链路故障/旧恢复回归与全部OCR writer记账。
- office_exec使用独立进程组，正常wrapper结束也清残留后代，SIGTERM/INT走finally回收；Worker isolate支持拥有者回调并在失权时杀组。新增退出后后代进程、共享slot/取消占位、预发送native deadline回归尚未执行。图片处理路径尚需和所有writer/取消合同一起完成。
- 新旧任务期限不再因render_waiting自动延长（已登记document operation时）；周期维护使过期pending native/preview元数据终止并唤醒等待者。公共旧未登记任务保留迁移兼容行为，不以兼容逻辑延长新目录工作。

剩余硬门槛仍包括所有生产writer容量/发布/TTL失效、截图单规范资产及消息/checkpoint引用、浏览器首开基线/优先capture/CAS后备/客户端资源释放，以及最后全套/双库/真实模型前缀/50轮性能故障验证。Chat默认仍V1；不能把调度接线称为资源验收通过。


## 生产写入预留与发布接线（2026-11-06，部分writer完成/未验收）

在`5819337`上继续。0059增加writer job/attempt/state索引；共用AccountedWrites适配负责首字节前API预留、写后staged、业务事务发布与正常失败补偿。写入库存留在持久账本，不在请求内积累所有receipt；补偿按32项领取、有绝对时间预算，失败仍计容量。Broker成功交回Worker时显式移交暂存所有权，不能在Broker返回时删掉尚待Worker绑定的字节；发布响应丢失只清API确认可删的对象。

- 已接普通/旧兼容处理、缩略图、模板/品牌派生、私有缓存复制、Worker本地preview、Broker页图/PDF转换缓存与旧Office传输。FilePatch/preview/page publication同事务发布对应writer，内部文件读返回API确认的writer身份以兼容旧排队记录，不从模型推断job。新native原有两对象批量记账继续使用自己的完整合同，未改成逐块RPC。
- 去掉Worker预览删除竞态中的全file-prefix扫描/删除，只处理本writer未发布grant，其他代际由API删除流程管理。legacy处理中的早期preview不再绕过独立claim；legacy Office必要解析也进入render lane，不能一边新调度一边让旧处理旁路重型槽。
- 补充取消收据：不确定在途先canceling保留槽，Broker仅在确认所属render资源已移除后ACK释放；删除失败依旧保留到租约/补偿。Worker本地native/preview的拥有者探测有正确服务scope。新增容量写序/发布响应丢失/移交/物理删除失败回归已写，尚未执行。

**未完成**：旧OCR页与聚合正文的API writer、viewer索引/Work preview/capture等持久writer还未全部纳管；TTL业务元数据/行失效和必要消息/checkpoint资产引用仍未接齐，reaper仍只回收未发布写入，不能声称全平台1GiB/30天治理已达标。本组没有运行pytest/双库/迁移/真实栈/浏览器/性能；仅编辑/格式化。下一步继续这些writer与引用/TTL，再做浏览器首开基线/优先截图/CAS后备和最终集中验收。


## API OCR规范正文记账（2026-11-06，未验收）

在`a715d93`上继续接线。API-owned写入帮助器在所有者/来源复核后预留并提交，存储I/O后再复核与正文行/元数据同事务发布；相同canonical内容重绑需验证完整digest，缓存命中不再预留一份相同占用。失败物理删除成功后才结算；已发布对象不因响应失败误删。

- 旧OCR页正文改用该协议，生成/attempt/operation围栏与OcrPageResult/ocr_cache_revision一起发布；每页确认后可保持原有progress回传，后续页沿同一已领取slot与执行deadline，不能每页再开90秒。
- OCR聚合正文不再同时加载所有页文本并拼出多份字符串；受限流式读取每页≤800000字节，单个≤20页bytearray聚合，写入按64KiB切片，退出释放大缓冲，不新增临时聚合文件。聚合与本次选页/代际同事务绑定，缓存选页改变只在发布后更新请求身份，拒绝迟到范围覆盖。
- 原生旧partial代际缺source_blocks时，范围累计预算从实际基础索引计数补齐，不能漏掉既有3000页块而允许逐批绕10000块。
- 新增真实LocalStorage/DB正文与聚合单份占用、迟到代际/正常失败清理回归，**尚未执行**。本组只有代码编辑和格式化，未做功能/迁移/性能或资源验收。

剩余仍包括viewer/Work预览/浏览器capture等writer、TTL业务失效/规范截图历史引用、客户端资源回收与browser-first/CAS后备；之后才启用新Chat并集中验收。部分旧入口外层用户锁仍可能跨聚合I/O，需在统一review时连调用方拆分，不能把已移出数据库事务等同于全部锁/I/O性能合同已完成。


## 浏览器基线与轻量首开/资源回收（2026-11-06，接线未验收）

浏览器生产改动前创建独立detached `fdc0a05f`基线工作区，构建旧Portal成功。第一次node_modules符号链接被Turbopack根目录边界拒绝，改为专用APFS clone依赖后成功；未改产品配置。真实ClamAV/Docker栈尝试在启动前因原`colima-ailab-verify` socket不存在而终止，没有启动/重启既有VM。故仅执行**真实Portal/API/Worker+合成种子文件**的本地夹具浏览器基线，不把它算作扫描/Broker/模型验收。

设备为内置T3浏览器Chrome152/Electron44、1280×800。冷口径是已启动浏览器首次文件viewer/index，非OS清缓存；暖口径是同file/index复用、每次关闭销毁iframe。指标为点击至首个1024px capture_view成功，100ms轮询及1000ms单次reply等待会影响数值，不能直接冒充纯首交互时间。旧Word/PPT/scan40首次可用capture约2.698/2.683/2.673秒；各3次有效暖观测分别Word0.258–0.279秒、PPT1.247–1.253秒、scan40为1.212–1.251秒，关闭iframe均0。没有足够样本计算稳定p95，没有子frame heap/CPU/50轮/移动设备或泄漏结论。native-small首样本因夹具Portal工作目录让assets返回404而无效，修正专用资源路径后只有暖观测1.284秒；未重启服务抹掉该失败。mixed.xlsx和50001单元格Excel的旧Worker viewer索引真实返回viewer_reader_unavailable，回退表格不能算原生capture成功，仍记失败。两次snapshot超时，使用DOM与实际capture验证；一次工具evaluate超时后续测量重叠，相关样本显式排除，不冒充有效暖数据。原始无密钥摘要`/private/tmp/ailab-document-validation-oghcidq0/browser-baseline-main-fixture.json`保留；全部专用进程、标签页、临时数据库/对象/凭据与基线工作区已清理，没有更改用户栈。

在`7c0bde5`后接入新代码（未跑功能回归/Portal完整build/真实性能）：

- 库/分享的document默认仅返回当前授权、安全原件身份和类型字节准入元数据，不创建全文索引任务；Work open在当前lease中做有界snapshot/摘要后返回同类元数据，无新系统沙箱。未知页数保持null。显式structure=true才请求现有完整viewer索引。Word在首次选区/精确定位时请求同源位置映射，原生画面先显示；引用仍经既有服务端quote/版本核验。结构映射失败不伪造位置。
- 父frame资源缓存增加项数/16MiB预算及请求截止；文件bytes未消费Map只允许一个；capture取消消息传到frame，host pending≤64、单重型capture、请求/执行计时器与监听在结果/取消/dispose释放。主frame卸载显式dispose，晚截图不回挂。
- PDF首开只取第一页实际尺寸，后续页实际尺寸随请求准备；浏览器导航结构限定5000，超出走受控后备，该限制不是Chat物理页数准入。离开窗口取消/清Canvas及文本层，所有Canvas合计约64MiB像素预算；loading task/doc/render/范围/字体Worker来源URL有退役清理，渲染资源失败交受控后备。Word仍整包浏览器排版，未宣称增量或低性能设备已过。
- 共用raster适配对DOM/像素/编码有界，按序处理图像并释放Canvas/临时DOM/图像引用；资源错误不得静默留白。保留原生viewer架构，不自建排版/OCR。生成源仍office/build.mjs，新的office:build成功仅证明bundle生成，不是功能或内存验收。Portal发送准备现传client_send_id。

**仍未完成**：view_document浏览器优先/持久generation后备CAS、多tab和版本/region全链路、规范截图与历史引用、全部writer/TTL业务失效；通用静态资源与Office renderer额外内存/Worker边界仍需统一review和50轮实际证明。Chat默认仍V1，不能提前称整体方案达标。


## 浏览器查看、规范截图及回收接线（2026-11-06续作，集中验收前）

基线 `30a156a7`，重新fetch确认 `origin/main@fdc0a05f`；独立实施分支继续，其他工作区未改。依用户要求连续实现后集中检查，以下代码接线不是验收通过声明。

- `view_document`已接Agent/API/Worker：持久操作与client-call绑定源版本/范围/表示revision/generation/绝对deadline；在线PDF/PPT/Word页图串行领取，多tab共用claim。失败/关闭/超时后在同一事务撤销浏览器执行权、递增generation，再请求既有系统后备；浏览器阶段不占服务端重型槽、不投机转换。Excel打印页不假用浏览器网格，按原有受限Office后备处理。截图来源与客户端像素可信度分别报告。
- 区域/图片按revision核对，经Worker受限子进程处理；整页后备复用规范RenderedPage，不再次编码/另存模型副本。新Chat默认V2三工具；显式旧目录与checkpoint定义保持，旧等待窗口改为校验持久原始截止时间，新增页图等待复用既有恢复机制。
- 浏览器截图接容量预留与原子发布，消息/运行引用与对象同事务绑定；V2必要截图不再按10分钟临时TTL删除。旧Run保留原临时过期语义。传输不把base64长期写到ClientCall；checkpoint继续引用同一规范资产。迟到/撤权/取消校验在写前和发布前各做一次；提交响应丢失可幂等重传。取消/部分浏览器结果转后备时撤销其未使用资产。
- Worker viewer索引改为不可变代际，ACK事务才发布metadata；Work viewer/后备缓存接API容量预留与发布。页图改为job子前缀，避免未发布的新字节覆盖旧引用；旧代际进入24小时回收。对象实际删除后结算账本，失败保持占用和删除意图。迁移0060为按资产key清理的查询加索引；TTL先使相应元数据/RenderedPage/文字索引等失效，再物理删除，周期回收开启。
- 浏览器新增复用JSZip 3.10.1的临时准入Worker：检查实际流式解压边界、结构/内嵌图资源，超限走后备，不替代安全扫描。初值为单成员4MiB、包16MiB、Word1000段、Excel100000单元格、PPT200页、已支持内嵌图解码合计32MiB；不支持的图片明确后备。源ArrayBuffer转移后归还，取消/超时/关闭terminate并revoke URL。PDF/PPT页面和缩略图按可见邻窗释放；截图取消传到PDF render task与异步PNG编码，释放Canvas、FileReader、监听与迟到Blob。
- Worker及Broker主机临时目录新增独立本地资源租约索引，正常退出立即清理，硬崩溃按原job截止+15分钟由有界扫描补偿；Work viewer/view/warm/preview目录接已有沙箱scratch登记。没有清理用户目录或成品。

本组Ruff check/format和viewer生成检查通过（最终版本仍需集中复核）；未运行本组功能/双库/真实浏览器/模型/性能及50轮资源验收。此前性能数据只属于原阶段，不能替代本组最终门槛。接下来集中检查，修复失败后记录实际基线和资源证据；fork旧预览复制的容量覆盖、全链路故障与所有历史引用边界必须纳入复核，未通过前不宣称整个方案完成。未push/PR/合并/部署。


## 集中验收与共因修复（2026-11-06，本地4a9139e）

产品提交`f319613`及共因修复`4a9139e`，未push/PR/合并/部署。以下为本轮实际检查，不覆盖尚未完成的整体验收。

- Ruff check与636文件format、diff空白检查通过。Portal首轮全套257通过/1失败，失败为新增表示revision后的旧断言；该组55项复核通过。Portal production build首次因JSZip流接口类型与专用查看器别名失败，修复后build通过，并在最终Portal源码再build通过。没有宣称最终全套Portal重新运行。
- 首轮完整Python SQLite有181失败、PostgreSQL有189失败；原命令重复-q未输出通过数量，不能凭进度点推算。失败清单集中重跑先为126失败/55通过，补齐真实claim/预留/ownership测试适配、隔离各测试拥有的持久重型任务、显式旧Run目录夹具及专用技能导入后，再对这126项集中重跑为56失败/70通过。此后只复核本组实际修复的失败项，尚未收敛完整剩余清单，**全套未通过，整体任务未达标**。
- 真实SQL/LocalStorage生命周期组SQLite+PostgreSQL共21通过，覆盖规范截图保留、撤销途中发布、后备generation、部分结果清理、TTL业务失效、存储故障容量保留、正常与硬崩溃临时目录补偿。迁移组两数据库94通过（包含upgrade/downgrade/模型漂移检查）。这不是浏览器/真实执行器资源稳定性证明。
- 预览/资源组75通过/5失败后定位两处产品遗漏：发布schema漏WebP；交付成品已扫描但FileObject未记录扫描凭据。修复后原5项通过。旧查看器/工具目录/流组63通过/4失败，补齐已完成fixture的安全凭据/原上传ACK，并使用仓库规定GNU PATH后原4项通过。纯原文件fixture保留真实授权，不降低产品校验。
- PostgreSQL暴露`datetime.max`在+08时区读取越过10000年的问题，迁移0061将截图到期设为可空；V2规范截图按有效引用保留，旧截图仍按原TTL。新增WebP与成品扫描合同修复；图片显式OCR在尚无display派生时先走按需标准化preview，随后才允许执行器工作，1项真实API/Worker受限子进程回归通过。
- Office技能从生成器重新生成索引，复现检查已通过。共享单元fixture的API适配补齐native_owned、preview claim、writer补偿；旧Run工具合同fixture显式选v1，V2测试显式选v2。测试任务清理只作用本test新建的Operation/对应Outbox/Preparation，不改产品队列阈值、不绕过公平准入。

剩余集中失败仍含旧原生/视觉/OCR/前缀恢复、fork、模板/成品和部分模式预期；不能将其全部归因夹具。最后56项清单及复核日志保留仓库外`/private/tmp/ailab-document-final-dVcZZC`。其中新目录显式OCR不可用原因及图片fixture凭据已随后修复，但未再滚动宣称全绿。下一步逐类核对真实失败，补完fork预览复制容量及引用生命周期，并完成最终录制前缀/真实模型≥95%、真实四格式与50轮浏览器/服务资源验收。

本轮Docker专用VM实际Stopped且socket不存在；未启动共享既有栈。真实Docker/OCR/模型/移动设备/E2E与浏览器50轮仍未跑，早期夹具或组件结果不得替代。临时PostgreSQL25432/Redis26379为本轮独立目录/进程；检查结束停止，仅保留有复现价值的仓库外失败证据。无提高限额、忽略失败、周期重启或模拟真实验收。


## 最终共因修复与集中复核（2026-11-06，48c78a07）

分支`feat/document-input-lifecycle`，再次fetch确认`origin/main@fdc0a05f68c89aa884ce3682939f70e0337c45c4`未变。产品修复依次为`3a1f06f7`、`a47be008`、`48c78a07`。仅本地提交，无push/PR/合并/部署；不修改其他工作区或既有业务栈。

本组完成的共因修复：

- autoflush关闭时，准备身份/预览登记先flush再刷新，避免幂等发送碰UNIQUE、取消被覆盖，以及页图job已登记但文件指针丢失。等待Chat view/OCR的Run只继承经用户、文件版本、consumer、parent和render_job_id共同核验的子页图需求，不让无关中断Run保活任务。
- 预览容量拒绝只有Broker明确返回“未开始执行”的429回执才可撤回dispatch并重排；503/传输不确定不能假装没执行。OCR重投沿用原执行截止和工作量；取消回执丢失时保留canceling及全局槽，直到执行/回收截止，不以HTTP结束代替推理终止。
- 模板分析ready与预览排队拆开，队列满不回滚已完成分析；修正不可变缩略图路径。Work图片在消费时标准化并实际进入模型图像输入；非图片仍保留原件引用。
- fork预览先持久预留、事务外复制、随分支原子发布；失败对象物理删除前继续计占容量。规范截图分叉只复制身份/引用、共享原字节，删除原分支不能破坏另一分支的历史截图。迁移0062把capture索引改为(run_id,id)，含双库EXPLAIN、upgrade/downgrade/模型漂移回归。
- 旧Run/旧解析夹具显式冻结V1，V2测试遵循独立读/搜/看及Work当前沙箱合同。E2E不再调用移除的document_view；Work复制路径仍受inputs约束，工作区预览由用户显式展开，Word精确映射在实际选区时加载。合法生成Excel的后备测试显式让浏览器拒绝准入，不把旧native能力限制当成永久事实。长解析夹具心跳复用其真实TestClient API；未调产品限额或放松文件校验。

### 自动回归结果与基线

为避免重复跑无变化的全套，完整套件后只复核失败及受后续修复影响的模块；以下不拼接成“最终SHA完整全绿”的声明。

| 检查 | 实测基线 | 结果 |
| --- | --- | --- |
| Ruff check/format | 48c78a07，640文件 | 通过 |
| Portal完整测试/production build | 本轮未再改Portal的固定源码 | 258通过，build通过 |
| 完整Python SQLite | 3a1f06f7 | 2523通过、1失败、234跳过；唯一失败为长解析夹具心跳误连宿主API |
| 完整Python PostgreSQL/Redis/迁移 | 3a1f06f7 | 2688通过、2失败、68跳过；两项同一心跳夹具根因 |
| 上述失败及影响模块8文件 | a47be008，SQLite / PostgreSQL | 115通过5跳过 / 120通过 |
| 完整E2E | 3a1f06f7 | 186通过、8失败、6跳过；使用真实服务/Chromium、仓库mock模型/沙箱/扫描，不冒充真实后端验收 |
| native/引用/后备E2E整组 | a47be008 | 30通过、1失败；剩余Excel暴露等待父操作不能保活子页图job |
| 父子需求/OCR生命周期/传输组 | 48c78a07，SQLite / PostgreSQL | 17通过6跳过 / 23通过 |
| 最后Excel Chat/Work E2E两项 | 48c78a07 | 2通过；已知失败清单清零 |

未在48c78a07重复完整Python/E2E；完整记录保留上述失败过程及后续有针对性的复核，不把旧成功或最终局部成功倒填为全套结果。前两次缺GNU PATH/venv的命令不计验收。原始XML、失败trace与服务日志位于仓库外`/private/tmp/ailab-document-consolidated-_tu080cb`，不提交stdout/heap/逐点数据。

### 真实链路与资源测量

真实装置独立使用32500/32580–32584、ClamAV32510、Redis26380/13、SQLite/对象/tmp专属目录，唯一Broker owner；专用Colima `document-final`为2CPU/6GiB。原有`ailab-verify`始终Stopped，未启动或修改。真实服务源码为3a1f06f7；后续修改的取消异常分支/父子页图依赖有以上双库回归，不能冒充在真实栈中已复测。

- 十份冻结合成样本通过真实ClamAV安全入库；初始快照派生资产、页图、模型usage与重型job均为0。浏览器登录用户另持有一套合成副本；统计明确区分用户身份，未使用业务文件。
- 真实模型四轮Chat附件对话成功。首轮10602 prompt tokens/0 cached；三次追问为10752/10893、10752/10951、10752/11012，命中率98.7%、98.2%、97.6%，均达到95%。冷输入单列，未把首轮算成暖缓存。正文来自V2真实安全入库/按需准备链路。
- 真实模型显式调用read_document(text_source=ocr, selector=pages[1])，经API/Worker/CPU RapidOCR识别scan.png；唯一chat_ocr操作ready，返回中英文与金额1280.50。队列恢复重发工具调用不等于重复推理。设备为CPU，未宣称CANN/NPU验收。
- Chromium156.0.8078.4、1280×800，固定四格式，3次预热后50轮打开/关闭，每轮关闭后观察10秒；无服务重启。保留堆增长0.479MiB，回归斜率0.00731MiB/轮；EventListener保持557，iframe/Worker净增0，documents净增0、DOM nodes净增2（测量同时新增模型对话侧栏条目）。ArrayBuffer由6到7并在后25轮保持7；基线/25轮/50轮有真实heap snapshot佐证。暖打开最大1.025秒；每格式仅12–13次，首开样本不报冷p95，未得到旧版同装置暖延迟比。
- 正常临时目录为空。50轮中不要求截图，所以这只证明打开/关闭循环；不能替代page-tool capture、API规范截图引用或所有私有请求Map计数。

补充测量同一真实栈/同一进程期间执行，实际OCR镜像`sha256:ce72145b6385448aec46a5157c6445c3e68928835551a54453ad31b8fc2416c7`：

- 四格式再做3次预热+50轮实际page-tool capture/关闭，每轮观察10秒。53次图像结果均成功，最大编码解码后的上界76467字节，未超过2MiB。保留堆+0.488MiB、斜率0.00710MiB/轮；EventListener567→567，iframe/Worker/documents净增0、DOM nodes+2，ArrayBuffer6→7→7。基线/25轮/50轮heap已实际采集。该测试从真实viewer组件的parent消息入口调用capture，不经过client-call的API claim/规范资产发布，不能算完整端到端截图50轮；Portal私有capture Map仍只有自动回归覆盖。
- 独立CPU RapidOCR执行器3次预热+50次实际推理，逐次检查扫描图的金额和文字，每轮10秒观察；50次均成功，无重启。容器工作集采样301.7–313.4MiB，最后301.8MiB，延迟最近秩p95=1.932秒，最大2.500秒。该容器保持原1GiB/0.75CPU限制；这是CPU执行器稳定性，不是NPU证明，也不是包含所有服务的2CPU目标机压力。
- 同期宿主服务的**轮后空闲RSS采样最大值**：API94.45、Broker27.69、Agent46.63、Worker30.25MiB；CPU累计分别+4.29/+1.10/+1.99/+1.62秒。没有采样重型Office解析峰值，不能称其峰值RSS达标。ClamAV约988MiB/1GiB，独立计入VM总成本。宿主为8核/16GiB，不能把这些数冒充整栈被限制到2CPU/8GiB的验收。
- 停止前数据库revision0062；5个ready派生对象合计38457字节，全部有正常业务用途（两对象native文字、按需图片和OCR结果），预留0，账本used_bytes与对象总量一致。chat_ocr 1个、file_job 2个均ready；tmp文件0。浏览器打开/截图本身未生成额外后台全文/页图。

### 未完成验收与具体阻塞

完整生产Office镜像两次构建失败：Debian固定快照源批量获取反复返回503/No healthy backends，apt-get随后cgroup OOM；第二次使用更低的2GiB构建上限，dmesg显示apt匿名RSS约2089120KiB。单包256MiB探针成功、无重试批量探针明确503退出；官方备用快照域也返回503。未提高限额、替换包版本/哈希或反复重启。这个构建故障不能称产品运行时泄漏，也不能声称真实Docker Office后备通过。

尚未达到整体验收：当前生产Office镜像的真实四格式读/转换/后备与Work沙箱复核；50轮完整浏览器→client-call→API规范截图发布/引用循环及私有请求Map细粒度计数；真实双用户重型争用与所有取消/崩溃/磁盘故障组合；冷p95和旧版同装置延迟比；S3/OpenSandbox、真实移动设备与NPU。已有双库/故障/mock沙箱E2E仅覆盖对应合同，不替代这些真实边界。下一步先恢复固定快照依赖获取，再在48c78a07或后继冻结基线上补上述缺口；不调验收门槛。


### 隔离资源清理

两组压力结束后才停止服务；无周期重启。API/Broker/Agent/Worker/Portal、ClamAV/OCR容器、一次性PostgreSQL25433与Redis26380均停止；14个本任务监听端口复核关闭。内置预览tab已关闭，测试Chromium由脚本finally/context退出。

专用Colima `document-final`已停止并删除，其Docker context/镜像随专用VM清理；Docker当前context仍为default。再次只读检查原`ailab-verify`为Stopped。临时token/session/model配置文件、专属业务DB/对象/tmp、一次性PG数据、复制的OCR权重及重复trace/截图/中间heap已删除；保留仓库外约38MiB必要证据（最终测试XML、必要日志、合成代表图、capture基线/末轮heap、指标与复现脚本）。没有向仓库提交原始运行输出，也没有清理用户数据或其他工作区。

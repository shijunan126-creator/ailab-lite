# 网页来源多标签、渲染与正文提取

日期：2026-10-26。授权为本地审核/修复/验证，不含推送、PR、合并、部署或付费模型。工作树 `/private/tmp/ailab-web-source-tabs`，分支 `fix/web-source-tabs`，base `origin/main@412168d`，产品与测试提交 `eb506d942e763c93fb1061b681a3178672485b90`；末次元数据/换行容错提交 `e6cfe60`。主目录原分支 `fix/full-thinking-rendering@ab0e8d9` 的已有存储交接修改和未跟踪文件原样保留；原部署未改。

## 根因与行为

1. `.viewer-mode` 未覆盖全局hover文字颜色，浅背景叠上accent-text后失去对比。显式覆盖hover前景和背景，浏览器在亮/暗主题验证对比度至少4.5。
2. 网页只保存在单个对象中，新URL替换旧页面，标签写死“网页”。改为URL去重独立标签，来源标题/普通Markdown可见文字作为名称。选中标签只调整标签栏水平滚动，不滚动对话；关闭非当前标签不抢选中状态。文件/来源关闭后回退到剩余内容，避免最后文件数组为空时访问末项。
3. 原来切换模式/侧栏标签就卸载并重新请求。最近4个文档保留iframe、正文和各自模式/滚动，正文仍按首次选择加载；较早标签仍存在，但内容淘汰后再次选择会重载。后台标签不预取；没有新增服务端缓存/数据库查询。关闭整个侧栏释放网页资源。
4. 原版允许站点脚本，站点hydration/导航/刷新可能离开所抓取的页面。原版保留HTML/CSS、禁用脚本/子框架/表单；移除meta refresh和已有base，注入转义后的最终URL，链接默认外开。保持doctype顺序，批量修改以片段拼接，避免多次复制全文。Portal代理同步透传CSP与referrer-policy，否则API策略不能抵达浏览器。
5. 正文原先把所有header丢弃，包括文章标题；hidden布尔属性的None值被误判；无匹配开始的闭合标签会弹空栈；单段文字能越过总字符上限，表格合并还重复扣字符。已修复并传递提取限额truncated；区域评分通过前缀和计算嵌套容器总字数。

## 验证与性能

- 基线412168d的独立提取对照：文章header、hidden、无匹配闭合、单块字符限额4个场景均失败；修复后专项测试通过。
- 最终代码网页专项：`pytest tests/test_reader.py tests/test_web.py -o addopts='' -q`，25通过，含无值meta属性/独立CR换行、doctype、base转义、CSP、超时/鉴权和提取边缘路径。
- Portal42测试通过，生产build与TypeScript通过。Ruff check/format均通过，370 Python文件。
- 最终五服务浏览器组：来源列表→网页、两网页独立标签/关闭、最后文件与来源关闭不丢网页、模式保持、亮/暗hover对比、手机布局下切换、桌面和390×844长回答真实点击，4通过/0失败/0跳过（6个无关长会话场景未选），28.29秒。固定两个URL只触发3次请求：两次原版、一次正文，来回切换不重复请求。用真实临时服务/账号与数据库；外部HTML/正文HTTP使用确定性路由夹具，未冒充真实外网验收。
- 中间全套SQLite：1277通过/135跳过/0失败，186.68秒；`eb506d9` 的SQLite+独立Redis14库全套1317通过/95跳过/0失败，186.01秒；`e6cfe60` 仅补异常meta/换行容错，25项网页专项复验通过，未在此2文件变化后再重复完整套件。外部依赖未配置的95项明确跳过。
- 对570913字节合成页（10000个meta refresh＋120段正文）5次本机测量：原版标记重写中位63.35ms/max69.04ms，正文提取中位47.30ms/max58.17ms。此为病理标签密度输入的CPU耗时，非目标机容量证明。新增原版解析会消耗CPU；正文评分避免重复扫描；原版禁用站点JS与最近4文档上限限制重复执行/累积DOM。不新增后台任务/依赖/磁盘缓存/业务表查询。

## 失败历史与边界

- 最初复用旧工作树venv因新主线缺rapidfuzz收集失败，随后在隔离工作树按当前依赖创建venv；不改原venv。
- 首轮多tab手机测试直接从桌面resize，展开的全局导航拦截点击；按实际UI先收起导航后通过。长回答旧断言也暴露普通Markdown未传label，已修正产品标题传递，后续4项合并验收通过。
- 内置preview初始无服务时连接拒绝，临时服务启动后导航成功，但连续两次snapshot均报 `Preview automation snapshot timed out after 15000ms.`。按照仓库Playwright路线完成验收，保留截图/日志在本机 `/private/tmp/ailab-reader-final-e2e`。
- 原版定义为HTML/CSS静态预览，禁用站点JS，因此SPA/登录/反爬/只靠脚本填充的页面可能不完整，保留外部原文入口。正文仍为启发式抽取，60k字/400块/代码块上限存在；未穷尽所有站点/编码/异常HTML。截图缺少精确BBC URL，本轮未宣称已复现那个外网页面的完整内容。
- 未跑PostgreSQL/外部存储/OpenSandbox/付费模型/真实手机/目标2vCPU整栈容量；没有SQL/迁移/Agent工具行为变化。未运行整套所有浏览器场景；仅相关4项，本次没有扩大为全站验收。
- 专用五服务由夹具停止；核对PID42284为本轮16471 Redis后shutdown nosave，13470/18470–18473/16471均无监听。无产品后台进程残留，原部署未动。
- 下一步：本地实现/验证/交接完成，等待用户明确发布授权；不push/PR/merge/deploy，不启动其他路线项。


## PR83 首轮失败与CI复查

用户随后授权推送、PR、合并和部署。同步主线93b94b3后PR83 head e9fc23f触发run40287501480：SQLite/PostgreSQL/quality/storage/shell成功；E2E108通过/1跳过/1失败，总门禁跟随失败。失败为手机预览打开→Esc→重开→立即浏览器Back退到about:blank。相同代码本机2场景中桌面通过、手机失败，证明是产品时序回归。修复使用layout effect在交互前同步push历史，generation与microtask清理支持Strict Mode重放；不加测试sleep、不降断言、不重跑当修复。

修复后相关6浏览器场景（原桌面/手机预览返回、网页多tab、长回答链接）通过；Portal42测试/生产build/TypeScript随五服务fixture验证。仅Portal生命周期变化，不重复上一轮不受影响的Python全套；最终集成以PR和main完整CI为门禁。

CI只读复查：PR取完整base...head范围并fetch全历史，main/manual全套；matrix fail-fast=false；每job独立服务、临时DB/临时Portal构建、每测试独立浏览器context；E2E未配置自动重试；失败always保留日志/trace；CI gate always执行且拒绝应跑却skipped/failed/cancelled任务。门禁/范围/JUnit验证28项单测本机通过。CI失败信息正确，不需要workflow改动。现有边界：该E2E使用生产build，开发模式Strict Mode不属于这6项验收；不把配置检查等同全部CI永远无缺陷。

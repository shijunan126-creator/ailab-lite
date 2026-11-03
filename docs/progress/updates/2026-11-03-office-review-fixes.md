# 独立审查两项修复（2026-11-03）

分支`t3/sidebar-office-file-features`，HEAD/base `8cbc216`加前序未提交修改，用户授权[独立审查](../../reviews/2026-11-03-office-sidebar-independent-review.md)两项全部修复；无提交/push/PR/部署。前端由新Claude Opus5.5 medium任务实施，主代理负责API/存储、集成验证与文档。

## 后端顺序合同

每次PUT在昂贵授权之前原子登记一次token与sequence，授权完成后仅当前token、未关闭且120秒提交窗内可写；更新/DELETE使旧token失效。DELETE保留600秒有界关闭记录，拒绝迟到请求。Redis脚本和memory同锁实现等价；活跃上限16、包括关闭/待授权记录的单会话上限128、memory全局会话上限4096。满额拒绝新身份，不驱逐仍有效关闭标记；全局memory驱逐丢失token后也不可提交。旧Redis十分钟快照兼容读取。

新Portal每次flush递增sequence（续期同样递增），sequence不进入模型查看状态或续期快照比较。旧客户端省略sequence时按服务器接收顺序登记，仍防关闭后复活与迟到完成；新客户端另外防网络到达顺序逆转。沿用归属/会话/来源权限与版本复验，不放宽授权、不新增业务表/事件/常驻清理。过期记录只在读写时过滤/清理，Redis键仍600秒过期。提交窗拒绝过长授权，不靠延长活跃TTL保住过期请求。

## 当前验证计划

增加Memory/真实Redis确定性顺序回归：双DELETE先结束旧授权后结束、seq2先完成seq1后完成、关闭前尚未接收的PUT、到期token、活跃/关闭数量上限与旧客户端。API路由用事件阻塞授权复现，不依赖sleep。前端补握手后终态错误与真实abort立即reject、旧nonce/版本、会话切换等回归。真实E2E对library/share/workspace只破坏浏览器原件下载，使索引成功后renderer失败；服务器原件保持完整，验证回退实际API/Broker渲染可读。

本轮执行结果：

- 完整SQLite：2111 passed / 182 skipped / 0 failed，386.18秒。该轮基线在新增rolling-upgrade两参数回归和到期记录正文清理/内存读快照拷贝之前；最终PG完整套件覆盖这些收尾调整。
- 真实Redis顺序专项：10 passed，1.26秒（两种后端×5组）；后加旧格式快照兼容的两参数测试由最终PG覆盖。
- 前端集中检查：39文件/226 tests通过、tsc/build通过；无产品依赖和vendor原始文件变更。前端测试初版有fake timer二次挂载超时和延迟附catch导致警告，修正测试装置，未用产品延时/重试掩盖。
- Office E2E：26 passed / 0 skipped / 0 failed，280.43秒，包括原22项和新增library/share/workspace握手后真实渲染失败、library权限失败不启动导出。真实浏览器/API/Worker/Broker/Agent，scripted模型、mock沙箱；仅浏览器收到的原件字节被同长度替换，索引ready/native、服务器原件和实际授权渲染链完整。不是通过unsupported准入模拟终态失败。
- 整合时发现字节数不符只记录错误却仍传给renderer，已改为立即拒绝bytes请求，并加强组件断言；该收尾位于26项E2E fixture复制产品源码之后。最终access/size两项定点E2E通过，118.87秒；fixture重跑Portal226项/build通过。没有再重跑26项。最终PG全套在之后执行，避免同时重负载。

最终产品/测试基线为HEAD `8cbc216`加56个platform/images/vendor修改/新增文件；排序`[path,sha256]`的compact JSON SHA256为`3d94bdc1436870372c87a370af23415f37f4f74357485d753401e5c7e1bf7f04`。之后仅更新文档。

终态桥协议：frame发送带nonce/version的`failed {stage:load|render,message,code?}`；Portal仅接收当前window/nonce/版本，停止一次并卸载frame/中止下载/解除capture注册。403/404、409/410/字节数不符、网络/服务错误和密码加密显示明确错误；兼容渲染失败才挂载既有授权回退。旧nonce/version和普通全局警告不会使新文件回退。每个会话绑定新的Portal view_id，释放或A→B→A重新创建，capture registry用相同身份；每次flush递增sequence。移除finally第二次DELETE，正确性来自服务端围栏。

运行成本：可见静止视图仍每3分钟一个PUT，无新模型消息/事件/常驻清理。每个PUT增加一次Redis原子登记，后续提交CAS；有界元数据扫描最多128条（正常仅活跃视图数），到期正文释放，仅保留短序号/关闭记录。相同快照续期仍不访问Broker。无CPU/RSS具体实测，不声称零开销。

部署兼容：新API可读旧快照，新Portal可向旧API发送sequence（旧API忽略）；多API进程升级时应统一切换新版本，不能让旧实现读取新记录格式。Portal先更新，再统一更新API；已有旧浏览器应刷新以启用新绑定身份/序号，省略sequence的请求仍按接收顺序兼容并受关闭围栏约束。无迁移、新服务或依赖，本轮不部署。


## 最终结果与边界

最终完整PG16/Redis套件：2228 passed / 67 skipped / 0 failed，454.40秒（2295项）。使用本轮一次性UTF8 PostgreSQL15636的ailab_test/ailab_migrations及Redis16636/15，覆盖最终56文件基线、两种后端顺序/关闭/旧快照回归。SQLite2111/182为前述稍早阶段；不将两个阶段合成“相同树重复全套通过”。最终Ruff check/format（537文件）、diff和新增文档链接/产品基线摘要一致性检查通过。

两项P2均已实施并本地验证：握手后终态错误进入授权回退；已接收的旧PUT不能在关闭/更新后提交，新客户端递增序号也拒绝网络逆序到达。浏览器abort不再作为服务器取消的依据。既有绑定/截图/引用用例同批26项通过，最终字节数拒绝路径另2项通过。没有重复全量183项E2E；本次变更集中在view状态/桥接，运行全部Office原生E2E及新增边界。未重新做真模型/真实Docker、浏览器真实静止10分钟、隐藏恢复长测、移动多浏览器或CPU/RSS容量实测。67/182跳过保留未配置后端/外部依赖及专用reader路径边界；E2E自行构建当前reader，未跳过Office原生场景。

顺序竞态由事件控制的实际API路由（只stub归属/阻塞昂贵授权）与真实Redis Lua/内存回归确定性证明；没有注入真实Broker延迟并在浏览器中复现服务端继续运行的完整长延迟链路。E2E使用真实浏览器/服务栈、受控scripted模型和mock沙箱，三个renderer故障只替换浏览器收到的同长度原件，权限/size失败为受控响应注入；不称生产真实文档全格式保真验收。历史活动工作区间歇失败仍缺旧日志，本轮未复现，不能把此次两项P2认定为其已证实根因。

未提交/push/PR/部署。PG15636、Redis16636已停止，本轮E2E38530–38533/33530均已退出，七端口无监听；测试浏览器由fixture关闭。本轮仓库外原始日志/XML/trace及临时数据库目录在摘要落库后清理，未提交运行输出。用户既有服务未操作。


## 发布授权（2026-11-03）

用户追加授权本线程全部修改提PR、最终CI绿后合并及增量部署最新main。发布前fetch确认origin/main仍为8cbc216，56个产品/测试文件摘要未变化；无依赖/迁移变动。后端/Portal和Office技能包需更新，其他运行时/服务复用；视图存储格式统一切换API版本，新Portal先兼容上线。部署不追加备份、测试套件、浏览器或真模型验收，只记录构建/启动结果；实际merge/deploy状态以current及本机私有记录为准。

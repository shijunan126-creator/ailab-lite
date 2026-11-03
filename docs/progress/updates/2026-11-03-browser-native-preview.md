# 文件库与Work浏览器原生优先（2026-11-03）

分支`t3/sidebar-office-file-features`，HEAD/base `8cbc216`加前序未提交[侧栏修复](2026-11-03-office-sidebar-fixes.md)。用户追加授权落实预览迁移与减少重复导出、最终整体E2E及遗留间歇失败调查；新Claude Opus 5.5 medium任务只负责Portal，其余由主代理实施。未提交/push/PR/部署。

## 行为与成本

- 文件库/项目/分享四格式共用聊天隔离原生viewer。超准入、索引/脚本失败才挂载分页回退并请求服务器；没有会话时隐藏引用/提问，不上传view，文本仍可选中复制。PDF页和PPT slide原生定位；Word/Excel物理分页引用直接走分页。
- API在library扫描完成/ready/交付事务中不再为DOCX/XLSX/PPTX/PDF提前排分页作业。显式分页请求仍以同一文件的durable outbox幂等生成，OCR/视觉页和模板缩略图路径不变，旧格式保留有界eager策略。
- Work自动变化检测只发送`preview.updated {filename,path,version,kind:native}`，不上传渲染脚本、不复制/导出文件、不进入render调度及其预热回调。原生失败才GET owner-bound `workspace/preview?path=`：API复核Run/会话/归属，Broker拒绝越界/符号链接并固定快照，在当前Run沙箱有界渲染，按hash/格式/renderer与镜像版本缓存。输出继续走既有owner-bound预览资产端点。数字资产版本在执行前预留，失败也不复用URL。
- 快照20MiB/输出既有限额不提高，导出65秒截止、队列等待2秒；更大活动文件明确不可用，交付后可按文件库准入处理。没有新增常驻服务、依赖、迁移或业务表查询。原生查看仍有正文解析、viewer索引、下载带宽和浏览器CPU/RSS成本；成品检查及既有Work listener预热继续保留。未测CPU/RSS数值，不声称零服务器开销。
- 一处整合修正：Work回退换版本时先清除旧画面，避免等待过程中把旧内容显示为新版本，新增回归。

产品/测试最终基线：HEAD `8cbc216`加53个已修改/新增的platform/images/vendor文件；按路径排序的`[path,sha256]`数组，以compact JSON编码后的SHA256为`2cadfc7012052a0a0208239ed17c79f3439eb863e1df8e0bdff358aef60e1677`。该摘要排除docs/HANDOFF，用于区分前序35文件基线；最终PG和定点E2E均以此产品代码为准。

## 当前证据

- 后端专项`test_workspace_previews/test_eager_previews/test_file_previews/test_document_view/test_viewer_native_reader`：99 passed / 6 skipped / 0 failed，27.83秒。验证自动刷新没有render/下载/预热、按需真实快照/表格渲染与缓存、源变化生成新资产、失败清理、权限/路径围栏，以及旧分页/OCR复用合同。跳过包含宿主默认reader未配置的独立native-reader测试；E2E已从当前vendor源码构建reader。
- 前端代理交付时Portal210项、tsc、production build通过；生产构建的真实浏览器（API响应由Playwright提供）检查DOCX/PPTX/PDF浅深色、真实选区无引用栏、无preview/view请求。该代理检查不含完整API/Worker；XLSX与工作区端点由后续整合E2E覆盖。
- 完整SQLite：2105 passed / 177 skipped / 0 failed，386.98秒；基线为本轮Python主体，随后只调整回退缓存命中绕过render调度/预热，最终PG完整套件覆盖该调整。Portal最终211项（39文件）、类型检查和build通过（含新增换版本回归）。PG/Redis完整套件2214 passed / 1 failed / 67 skipped，456.21秒；唯一失败是临时集群模板编码，重建UTF8集群后该用例1 passed（1.84秒）。不是重新跑全套，失败/环境原因见下段。E2E补四格式library原生不请求分页/不排后台预览，以及Work原生不可用时实际API/Broker快照渲染与缓存。原生故障通过浏览器准入响应注入，后续链路不mock。

整体E2E首轮：175 passed / 2 failed / 6 skipped，765.53秒，共183项；全部22项Office native用例（含活动工作区、四格式库预览和真实Work回退）通过。首轮收集后把四个新增library用例改用唯一文件名，以排除同名历史文件导致的假阳性；随失败修正集中复核这4项及2项失败，不重跑整个套件。最终定点6项全部通过，141.87秒（2个修正用例＋4个唯一名称的原生library用例）。不是重新跑第二遍全套；前端未再改，fixture再次执行的Portal211项/build也通过。最终PG/Redis完整套件覆盖最后的缓存命中不预热调整。

## 失败与边界

本轮整体E2E首个失败为测试更新错误：单工作表的原生viewer按设计隐藏sheet tabs，而我修改的旧library用例等待“销售”标签。失败trace/截图显示工作表和数值均已渲染，核对XlsxView的tabs.length <= 1确认原因；测试改等待实际grid canvas，普通产物用例另有独立原因，见下段。不放宽超时、不加重试；整套结束后复核失败/受影响项。

第二项失败为普通产物测试被我改成强制native：真实生成的7.4KiB销售表含图表，当前ExcelJS reader报`Cannot read properties of undefined (reading anchors)`，索引failed后已经正确进入sheets分页回退，trace截图及sheets资产均有华东/合计/公式。直接对失败原件运行同一reader可稳定复现；不是环境缺reader，也不是服务器预览失败。按本次“复杂/解析失败走服务器”合同保留此回退，不为表面通过修改解析器或放宽准入；恢复原测试对可读取内容、关闭/重开、多轮修改的检查。原生含图表XLSX兼容性仍是明确限制。

PG首次启动在conftest连接初始化前退出4，未执行测试：临时initdb用`--no-locale`默认建立SQL_ASCII数据库，psycopg返回bytes导致SQLAlchemy读取server_version报类型错。先把本轮两个空临时数据库用template0/UTF8重建；全套中备份用例又从SQL_ASCII的template1新建数据库而复现同错。最终停止并用`initdb --encoding=UTF8`重建整个一次性集群，确认server_encoding=UTF8后该失败用例复核通过。重启期间一次误启动的复核在conftest报database shutting down，未运行用例，不计通过。未改产品、测试合同或连接实现。

前序活动工作区E2E曾失败一次且日志过早删除，根因仍未定位；此次成功不能抵消旧失败。整套检查失败时保留仓库外日志/trace和必要服务证据，定位后仅复跑失败/受影响边界，不加retry/skip或放宽阈值。

未做真实浏览器静止10分钟、隐藏恢复、移动多浏览器、部署/真实模型/真实Docker完整链路验收，复杂Office保真等P5边界不变。焦点在iframe时Escape不能关闭宿主对话框，使用宿主关闭按钮；不把此行为声称已修复。原始结果只在调试期存仓库外，根因摘要留库后清理。

## 最终交接

最终Ruff check/format（536文件）与diff检查通过。原生优先与按需回退已本地实现并按上述基线验证；六项跳过为未开启AILAB_PERF的性能基准，本轮E2E用真实Portal/API/Worker/Broker/Agent与确定性scripted模型、mock沙箱，不称真实模型/真实Docker全面验收。Python组合中的67/177跳过保留原有未配置后端/外部依赖边界。

前序间歇活动工作区失败在本轮整套未复现；缺旧日志仍不能确认旧根因。两次E2E失败为本轮测试断言更新错误，原因明确后定点复核通过；PG失败为本轮一次性环境编码错误，修环境后定点复核通过。无需继续重复全套直到“看见绿”。

发布需要配套更新Portal与API/Broker；新Portal兼容旧预览事件，可先更新Portal，再更新后端以避免旧Portal遇到kind=native。无迁移、新依赖或常驻服务。本轮未提交/push/PR/部署。本轮PG15626（含两个测试库与默认模板）/Redis16626已停止并移除其临时数据；E2E38520–38523/33520均已退出，七端口无监听。两轮E2E的测试浏览器已由fixture关闭；仓库外原始日志/XML/trace、诊断原件及脚本在摘要落库后清理，未入Git。用户的既有15461/16461服务未操作。

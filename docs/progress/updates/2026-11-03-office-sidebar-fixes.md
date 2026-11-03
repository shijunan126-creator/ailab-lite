# 办公侧栏修复与加载体验（2026-11-03）

基线`8cbc216`，分支`t3/sidebar-office-file-features`。用户授权修复完整性审查缺口和预览加载体验，前端指定Claude Opus 5.5 medium全权实施；无push/PR/部署授权。此前审查文档为修复前结论，不作为当前仍未修复的事实。

已实施模型侧patch008与生成包，修正四格式工具名/文件身份/显式引用和普通选区问答/截图合同。能力清单更新当前实现与证据，不把每个保留脚本命令都声称已执行验收；原上游全局delivery protocol仍blocked，本地review_artifact单列。后端对相同未过期工作区状态的续期复核归属/Run而不再次走Broker文件快照；首次、状态变化/过期与实际使用仍做完整验证，无迁移/新服务。

前端：共用DocLoading按page/slide/sheet/text/media绘制骨架，180ms后显示，阶段交接延续动画，不显示多余文字/伪进度；浅深色/全屏使用现有颜色，reduced-motion静止。接入文件库、聊天、文本/表格/HTML/PDF等等待与分页继续生成；原生frame脚本失败明确回退。续期每3分钟发送相同快照，交互600ms去抖，隐藏期间包括已排队上报也不发送；恢复时有等待或上次已超过60秒即同步，关闭/版本切换释放。PUT为单请求在途，合并最新状态，15秒abort，失败等待下一续期；卸载abort并处理迟到写入的第二次DELETE。原生viewer自身骨架接管时工具栏出现仍有轻微位置变化。

## 当前检查

产品/测试基线：`8cbc216`加本轮未提交更改，35个修改/新增产品测试文件的路径→SHA256排序JSON摘要为`2cc69d670f1d5537fc8756351cf4d16eccbbb4cb4afbd3f7da58aa5f9b70feb9`（不含docs/HANDOFF）。Python完整套件执行后只追加前端生命周期/对应E2E测试，未再改Python产品逻辑；最终Ruff/diff复核。没有commit/push/PR/部署。

- Python专项：`pytest tests/test_document_view.py tests/test_office_suite_resources.py tests/test_prefix_cache.py -q`，117项通过。
- 完整SQLite：`pytest tests -q --junitxml=<仓库外>`，2096 passed / 177 skipped / 0 failed，436.34秒。
- 完整PG/Redis：同命令，显式三个`AILAB_TEST_*_URL`，2206 passed / 67 skipped / 0 failed，510.98秒。全新PG16端口15616的ailab_test/ailab_migrations与Redis16616/15；结束后均停止、临时数据清理、两端口无监听。SQLite跳过包括未配PG/Redis，PG组合仍跳过未配置的外部后端/隔离合同，不称这些已验。
- 最终`.venv/bin/ruff check .`、`ruff format --check .`通过，536文件；新增说明/测试的字符串格式问题已修。`git diff --check`通过。
- Portal最终：`npm test` 38文件/203项通过，`tsc`与`npm run build`通过。初版boot失败测试在负载下先发消息再装listener，修测试等待listener；阶段重复跑过三次201项，不能当额外独立验收。E2E fixture也会重建/检查Portal，后续仅复核新增/受影响项。
- 浏览器：原生Office E2E初版14 passed（8个Chat/Work选区添加、5个直问、1个活动工作区）；补隐藏/超时边界和Work另外3格式直问后，最终定点5 passed（Work XLSX/PPTX/PDF直问、Chat DOCX截图、活动工作区）。组合证据覆盖Chat/Work四格式直问；不是最终树重新跑全部E2E。使用真实Portal/API/Agent/Broker/Worker、受控scripted模型和mock sandbox，不冒充真模型/真实Docker联合验收。
- 内置浏览器production检查五种骨架、浅深色与全屏，临时页删除后重新build；无额外请求。未做手机多浏览器。

## 失败与未验边界

- 活动工作区E2E同一五例组合曾失败1次；原始失败日志被前端实施者过早删除，无法确认根因。该例原生组合、单例120.90秒、最终组合有三次成功，不能用通过抵消失败，也不把同期PG检查推断为根因。保留为未定位间歇失败；下次复现须先保留每测试/服务证据，只复核此边界，不加skip/retry/延长阈值。
- 定点选择曾用中文`-k`而pytest参数ID为Unicode转义，实际只选1项；随后按实际ID选择目标。不将误选当五项通过。
- 续期/隐藏/15秒超时/关闭竞态有模拟时间回归，后端跨720秒复核且确认重复续期无Broker调用；未用真实浏览器静止超过10分钟，未做部署环境或浏览器隐藏/恢复端到端验收。
- 模型端自然选区探针的合成范围见下段；复杂Office保真、Windows/VBA、长期容量、多端竞争仍沿用P5未验边界，不在此次局部修复后宣称全面完成。

真实deepseek-flash合成探针：Chat/Work四格式8组均自然调用get_view，完成选区解释与续问。使用当前技能/固定工具，查看/读取为合成结果，不是浏览器/API/Broker完整验收，不执行模型生成命令。初轮装置只支持单工具、次轮未完整提供读取coverage且不支持合法reveal，部分样本未完成；按实际读取形状补齐并仅复核4个未完成样本后8组通过。成功样本后续调用合计cached/prompt=231680/239332（96.80%），不是大文档/真实视觉场景结论。包括装置准备/未完成在内全部76次探针：prompt500401、cached464000、completion17240；首轮凭据文件选错在模型调用前退出，未打印或提交密钥。只保留摘要，不提交原始请求/回答。

渲染路径核对见[合同](../../architecture/design/office-suite/04-viewer-and-file-contract.md#当前预览与渲染路径)：聊天原生viewer、文件库分页、Work实时预览与视觉检查不是同一执行流水线。文件库优先原生浏览器预览是建议的新范围，本轮未据询问自动实施。

运行成本：可见静止视图每3分钟一次小快照（每小时约20次），交互重置计时；同一工作区快照重复续期不发Broker调用、不复制/hash文件、不启动解析或渲染；首次/变化/过期与消费时仍复核。既有Redis状态上限/TTL不变，无业务表/事件写入，无迁移、新依赖或常驻服务。模型正文只在按需加载viewer指导时增加，不向旧系统消息插入可变选区。

技能指导按需加载的新增估算成本：DOCX/XLSX/PPTX/PDF约588/590/590/581 tokens，包内正文约增加2.3KiB/格式，不预载全部正文。无新依赖，lock未变。部署时需更新平台/Office技能包与Portal；本轮不部署。

清理：本轮两个数据库、前端Redis16480、E2E38480–38483/33480、视觉页33491均停止，最终九端口无监听，临时页/preview tab清理，无仓库e2e-logs或原始输出。模型探针日志/中间脚本在摘要落库后删除，凭据未复制。

下一步：本轮实现与所列检查已交接，保持未提交/未发布；如用户要求发布，再走PR/CI。活动工作区间歇失败保留未定位状态，未来复现优先保存证据；文件库原生预览迁移等待用户单独决定，不自动实施。


后续用户已追加授权文件库浏览器原生优先与Work按需分页，实施/整体验证见[后续记录](2026-11-03-browser-native-preview.md)；本页“迁移待授权”仅描述前序范围，不代表当前仍未授权。

# 全删除生命周期修复

用户授权修复[会话删除审查](../../reviews/2026-11-04-conversation-deletion-audit.md)和[补充审查](../../reviews/2026-11-04-all-deletion-audit.md)的全部六类问题；已完成本地实施与验证；用户随后授权提PR并部署本次分支固定提交，PR保持开放供评审。未处理真实业务数据。

[PR #158](https://github.com/shijunan126-creator/ailab-lite/pull/158)，产品提交 `c0541f5`，分支 `fix/deletion-lifecycle`，base `origin/main@e4479cd96bf5f41ca4ab718cc34f349df9acf664`。主工作区的无关改动、前序发布/空间清理文档保留。本记录验证的是base之上的产品差异；17个产品文件按路径排序、以NUL分隔路径/内容的SHA256为 `564493b306da99ea53358073c2f0357c0a81217ab9fc5ea803999f3f525c3518`（不含测试/文档）。

## 修复合同

| 审查项 | 最终实现 |
|---|---|
| D1 会话删除后仍可读取Run内容 | 统一Conversation存活判定；Run详情/列表/事件/过程/预览/SSE及内部bootstrap/context拒绝删除会话，已连接SSE在下一次poll结束。standalone/其他会话/独立资料库字节保留；不把账本结算接口整体封死。项目连带删除沿用相同围栏。 |
| D2 迟到压缩可写回摘要 | 在用户锁内fence attempt后刷新Run，只有queued/running且会话存活时允许摘要更新，UPDATE再带deleted_at条件。canceled及其他终态均拒绝；既有祖先/epoch/frame/CAS规则保留。 |
| D3 前端404保留缓存正文 | 删除成功废弃对应缓存；项目删除同步废弃项目缓存和当前页；确认404时清正文/预览/reader，取消详情、分页、stream与action回调并推进写版本，阻止迟到响应复活。网络暂时故障保留可用内容。 |
| D4 恢复没有处理任务 | 恢复整链时为未完成且可处理的文件生成新durable file job；重复恢复不重复排队；旧扫描claim与新job不匹配即done，处理写/文档准备/复用/缓存写只接受新claim。仅清除source_unavailable预览失败，由下一次打开懒生成；真实解析失败、病毒拒绝与purged状态不重置。旧版复制恢复同样补任务，不复制旧job身份。 |
| D5 正式技能删除遗留可编辑草稿 | 来源失效通过有索引base_file_id同时废弃依赖的未确认个人/定制草稿，清除内容清单及claim，记录前缀删除意图；包含用户修改过的依赖草稿。已确认成果、独立副本按原合同保留。 |
| D6 草稿丢弃后解包迟到写入漏GC | Worker按claim token隔离attempt对象；complete只接收本claim命名空间或升级前合法hash路径；GC按宽限分页检查skill-drafts的精确清单所有权和活动租约。先删后写、进程崩溃等未发布对象最终回收；保留有效/confirmed清单，锁内重验mtime避免误删编辑器刚复用的hash。 |

既定保留期、保存事实、平台系统历史版本、模板/品牌冻结素材、定时任务已开始轮次和账号删除暂停等产品决定不变。无新依赖、迁移或配置项。

## 验证

- Ruff check/format及diff检查通过。
- PostgreSQL+Redis关键删除/恢复/草稿/生命周期组：初轮134通过/3失败；失败均为测试装置/旧断言，修正后对应4项通过。两库EXPLAIN另2项通过，确认Run用户过滤与草稿base_file_id使用索引。
- Portal完整246项通过，`npm run build`在E2E隔离副本中通过；真实Chromium桌面1280/手机390共4项通过（删除后Back无旧正文、已删除附件/产物不可打开）。后端和模型为隔离测试环境/确定性mock，不是真模型或当前用户环境。
- 完整SQLite套件：2333 passed / 5 failed / 205 skipped，440.66s；产品代码此后未变。5项均是旧可见性断言与测试编辑误匹配，修正测试后定点4项通过，最后1项（已删会话answer应404）再通过。对应2338个非跳过用例均已通过；未把定点复核写成第二遍完整全绿。
- 运行命令：`ruff check .`、`ruff format --check .`；SQLite `python -m pytest tests -q -o addopts=''`；PG/Redis为test_deletion_lifecycle/test_deletion_queries/contracts.test_lifecycle/test_deletion_runtime/test_deletion_review/test_file_versions/test_skill_drafts/test_platform_skills/test_view_state_fencing；E2E为test_conversation_deletion_ui和test_deleted_files_ui。均使用独立临时DB/存储和非默认端口的一次性PG/Redis，未接开发数据。

测试装置修正：首轮恢复围栏暴露API未安装RuntimeContextMiddleware，改为显式读取X-Run-Attempt；编辑旧测试时曾误匹配另一条相同断言，已精确修正，保留未删会话的旧文件引用检查。EXPLAIN不能把SQLAlchemy Row的截断repr当完整计划；jsdom中的import.meta URL不适合直接传给Node readFile，前端探针改用明确文件路径。初次E2E因此在npm test前置失败，修正后完整重启隔离栈通过。

## 成本、边界与下一步

- 无新增常驻任务或进程；复用现有lifecycle阶段，每轮新增最多gc_batch_size个草稿对象检查。对象按需清理，解包内容不新增副本；claim命名空间允许重试短期并存，超出宽限且无有效引用后回收。
- 会话检查增加主键查询，列表和草稿来源查询沿用现有索引；GC清单数量受200条技能包限制，存储分页沿用适配器。LocalStorage枚举前缀的遍历CPU、巨大历史删除和长期磁盘容量未压测，不把响应分页冒称完整遍历CPU有界。
- 本轮未测真实S3/OpenSandbox、真实模型或全套E2E；未运行历史数据批量删除。已有失效草稿的历史记录不做擅自数据迁移，新增GC会在部署后按既有宽限逐步回收无主对象。
- 修复与本地验证完成；本次追加授权提PR并部署固定分支快照，PR保留供评审。本机部署结果不写入产品仓库，依照部署约定仅汇报必要组件启动结果。临时测试服务和原始日志已清理，不提交构建产物。


## PR158 CI收尾修复（2026-11-04）

- 远端head `5a7ca0b`：SQLite/PG全套与其他5项检查成功；E2E为188 passed/6 skipped/2 teardown errors，CI gate失败。两个浏览器用例本身通过；失败来自test_conversation_ui清理已删除会话的Run，cancel按新合同返回404。第一次assert中止还留下模块级种子清单，使下一用例重复清同一Run。
- 修复测试fixture：进入收尾先移走/清空本例资源清单和attempt表；404不直接放行，必须在同一隔离测试DB核实Conversation.deleted_at及Run终态。未删会话、缺失种子或仍运行的Run均会报错。删除浏览器回归新增active=True，覆盖删除活动任务及其收尾；不改产品可见性或加入重试/skip。
- 验证基线：5a7ca0b加本节fixture/回归差异；Ruff check/format、diff检查通过；`AILAB_E2E=1 python -m pytest e2e/test_conversation_ui.py -q -o addopts=''`在独立SQLite/Redis/mock栈中10 passed（126.05s），前置Portal246项与生产构建通过。产品源码仍等于已验证的c0541f5/5a7ca0b，本次不重复Python全套、不重新部署；新head CI按既有工作流重跑。


## PR158 PostgreSQL任务时限修复（2026-11-04）

- head83fcab7的PostgreSQL job126473456824标记cancelled；GitHub annotation明确为“exceeded the maximum execution time of 15m0s”。原始日志显示从08:31:45开始全套测试，持续无断言失败地推进，08:46:05到93%，08:46:13被任务期限取消，未生成最终JUnit。不是测试通过，也没有证据支持单个测试卡死。
- 相同产品源码的head5a7ca0b此前PG全套2460 passed/83 skipped，耗时780.61s，最慢单项17.09s；本轮初始化约45s，15分钟总期限不足以容纳runner耗时波动。调整python矩阵任务预算15→20分钟，覆盖准备、完整套件、JUnit门禁与artifact收尾；不修改断言、测试覆盖、性能验收阈值或重试规则。无产品/部署变更。
- 验证：`.github/tests` 28项通过，工作流YAML解析/任务预算及fail-fast核对通过，diff检查通过。未安装本机actionlint，新head的shell工作流负责该项；等待完整CI复验，不为任务预算改动重复本机Python全套或部署。

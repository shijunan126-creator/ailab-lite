# 缓存、删除、权限与迁移

> 实施状态（2026-10-29）：用户已授权本方案实施，覆盖下文历史“只设计”限制；不授权push/PR/merge/部署。OCR只计量、不计费、不设用户累计配额，资源隔离仍保留。当前实现、验收及剩余缺口以[实施记录](../../../progress/updates/2026-10-29-file-context-core-validation.md)和[验收逐项记录](../../../reviews/file-context-2026-10-29/acceptance.md)为准。本文其余历史状态不代表当前完成度。

状态：设计修订稿，未实施。基线 `main@e71a60f`。本轮明确Chat预处理、Work自主操作、图片按视觉能力分流、RapidOCR与昇腾NPU优先；正文中的资源与大小数值均为待验收候选。

[总览与模块入口](README.md)。

## 缓存复用：三种不同情况

| 用户行为 | 设计行为 | 是否重新处理 |
| --- | --- | --- |
| 从资料库重新选同一个file_id到Chat | 复用扫描、正文、OCR、页图；只按最新模型/selector装配chat_readable | 不重复解析/OCR，除非版本/配置失效或所选范围未处理 |
| 同文件从Chat切Work | 给原文件和派生索引，不给整份正文；Agent按需读 | 模式本身不使缓存失效；新增范围才处理 |
| 再从电脑上传相同bytes，得到新file_id | 先完成新上传的安全检查，再查当前授权范围内的同源派生物；安全克隆到新file私有命名空间 | 命中时不重复模型推理/OCR；上传、扫描和复制成本仍有 |

当前实现只对同file_id的已有derived/OCR有复用基础；upload_file每次创建新FileObject并排file job，没有看到完整的跨上传内容复用协议，不能称三种情况已全部实现。

### 缓存键与并发

源键包含`owner_scope + sha256 + size + representation + parser/OCR/model/dictionary版本 + language + renderer参数 + selector/config`。可影响数值的精度/backend配置也进入fingerprint，不能把不同模型/FP16/FP32结果无条件当完全等价。chat_view额外绑定selector与格式版本；模型预算改变只重新装配，通常不重跑OCR。

FileObject字节和版本一经冻结不改；Work编辑后是新file_id/sha，旧缓存不能命中。重试用derivation唯一键和job claim合并并发请求；任务已完成的页直接复用，未完成页再做。失败不永久缓存成空文本，按失败原因/重试合同执行。

相同内容候选只查同用户、purpose及当前允许的项目范围，依靠`(user_id,purpose,project_id,sha256,size,status)`候选索引和已ready派生记录；模型不能按任意hash查询他人是否有文件。新文件即使同hash也要验证自己的安全扫描、配额、大小和授权。

### 新上传命中时如何避免共享对象删除风险

首版不建立跨FileObject共享大对象/refcount层。命中后使用存储侧copy或流式复制，将经过验证的页/文字结果写入`derived/{new_file_id}/{new_derivation}/`，重写manifest的file/derivation/block绑定。不能直接把旧file_id的storage_key塞进新meta，否则删旧文件会损坏新文件，也会破坏读取绑定。

两次短事务之间做复制：准备时冻结源revision/来源epoch，事务外复制，提交时重查源和目标的授权/删除状态及claim。失败只清理目标staging对象，不改来源；来源在途删除则不发布候选。复制计入派生磁盘预算，原文体积不在API内存中拼接。可观察指标区分cache_hit、copied_bytes、actual_ocr_pages，不把复制命中计作新推理。

用户单独上传两个相同文件，它们是两个独立文件；删除其中一个不等于删除另一个。对同一lineage的版本删除仍沿用产品语义，不能因复用偷偷绑定成同lineage。

## 删除的含义和当前代码

当前`DELETE /v1/files/{file_id}`调用`trash_lineages`：移入回收站，整lineage的bytes、配额与历史引用仍保留。`purge_lineages/delete_file_rows`才移除DB内容/对象，派生目录`derived/{file_id}/`整前缀加入LifecycleDeletion持久删除意图，reaper失败会重试；版本淘汰可以只留tombstone。

因此当前已有“原文件和派生目录最终清理”，但没有本设计新增的块索引/能力缓存/来源摘要清理合同；源文件在sandbox复制、快照或模型文本里的副本，也不能凭目录删除就宣称全部消失。本轮仅源码审查，未做真实存储故障清理测试。

### 移入回收站

同事务设置deleted_at、递增context_source_epoch、写来源失效意图；立刻从在线搜索/读取/Chat装配/历史工具回放排除，拒绝新job和在途结果提交，已排OCR取消或丢弃结果。字节按回收站保留政策保存以支持恢复，不向用户声称已物理擦除。

恢复后重新验证当前权限、派生fingerprint和依赖；可复用仍完整且匹配的结果，但不自动复活旧摘要、旧运行或删除前的pending操作。活动会话下一次装配重读来源状态。

### 彻底删除/到期清理

API持久删除意图与在线失效在一个事务；后续有界批处理完成以下清单，失败可续且可审计：

| 内容 | 清理或保留合同 |
| --- | --- |
| 原对象、页图、显示图、缩略图、原生文字、OCR、chat_readable、临时转换/编译含用户数据缓存 | 删除精确file命名空间；共享模型权重/无用户内容的图编译缓存不随文件删除 |
| document_blocks、derivation manifest、FTS/词项、候选查找缓存 | DB删除/失效；有界批次，外部结果不得把记录复活 |
| message_file_refs | 清除filename/可读元数据，保留最小无内容tombstone或显示“文件已删除”，旧file_id不可再解析 |
| context_summary_sources、摘要条目、工具大输出、checkpoint图像/正文缓存 | 清理含已删来源的派生内容；混合来源条目整体失效后重建，不只删file_id仍留原句 |
| 资料库自动摘要、embedding/检索缓存 | 作为文件派生数据同删；不因已有摘要文字就留在模型上下文 |
| job/outbox/lease结果 | 取消或标终态，保留最小错误/计量身份，不保留正文；过期attempt不能提交 |
| usage账本/审计 | 保留金额、时间、引擎/状态、不可逆身份；不保留正文、签名URL、明文文件名；维持账本历史真实性 |
| 用户独立新上传的同内容文件、已交付的新文件、用户自己写的聊天文字 | 各有独立保留合同，不级联误删；若产品提供删除所有副本则是另一个明确操作 |

### 沙箱、快照与备份

Broker记录注入来源file_id→精确lease/path和workspace快照依赖。删除后先撤销新访问；活动lease可能继续读已注入bytes时，终止或冻结受影响租约并按绑定清理，完成前不能宣称副本已清。不能信任Agent自行承诺“删完了”。快照含已删原文件且无法安全原位过滤时整份快照失效、安排删除，恢复时明确workspace_missing；已独立交付文件仍可从资料库取回。

无法识别的用户代码私有中间副本是风险：受影响任务终止并销毁该工作区/快照，避免仅删inputs仍留下work副本。跨任务共用workspace时按依赖域确定影响，不全库/跨用户删快照。浏览器内已显示的内容和已经发给上游模型的请求无法追回；取消在途输出并避免后续回放，不能承诺抹除已发生的外部调用。

备份按独立保留周期到期清理，恢复必须先应用删除墓碑再开放服务，防止备份复活来源。不能把异步物理清理、备份到期或第三方保留政策写成“点击即全局永久擦除”。

删除完整性验收必须同时检查DB表、对象前缀、索引、缓存、在途job、checkpoint、sandbox和快照；仅返回204不算通过。

## 安全、性能与失败合同

| 场景 | 必须行为 |
| --- | --- |
| 压缩模型慢/失败 | 有限总deadline；旧frame/cursor不变；调用如实结算；保留或暂停任务 |
| OCR服务不可用/队列满 | unavailable/queued/retry_after；原文件READY且可下载；不触发付费降级 |
| DB提交前来源被删除 | epoch/权限重验失败，丢弃候选；模型计费保留，内容不发布 |
| 文档恶意像素/ZIP/PDF对象膨胀 | Worker受限进程与OCR容器硬资源上限，固定总deadline；不解析于API |
| Prompt injection藏在文件/摘要 | 明确不可信来源，summary不升级为系统权限；工具授权只认API principal |
| 多API进程 | job claim和数据库唯一键控制，全局OCR准入在执行器；不依赖进程内semaphore保证全局唯一 |
| Sandbox过期 | 原文件/派生物/引用仍在资料库；Work按授权重新注入，不能要求用户重新上传 |
| 大历史/大文件 | DB keyset分页、块索引与有界消息装配；不得先list全会话ID或完整文件再裁剪 |

原文和OCR结果均不写日志/metrics label；仅低基数method/status/reason。日志记录请求身份只走受控trace，禁止用户内容、密钥、签名URL。流在finally关闭，超时覆盖DNS/连接/读取/清理整次调用。模型await期间不持用户锁或数据库事务。

业务服务侧仍按2vCPU/~8GB目标约束API/Worker/Broker的编排开销，OCR执行放到已有NPU智算资源；不能把NPU显存、图编译、网络与执行器host RSS算作零成本。候选业务侧门槛：OCR运行相对空闲时普通Chat TTFT p95劣化≤20%、文件/块元数据API p95≤200ms。NPU侧冷热延迟、显存、并发和吞吐见[OCR模块](05-ocr.md)，待目标卡实测定容量；原同机CPU1.5GiB方案不再作为默认验收条件。所有指标均为候选目标，非当前成绩。


## 数据库、迁移与兼容

新增表按[消息与派生对象模块](01-message-relations.md)定义；所有owner由API从Run/File推导，不接受模型user_id。核心索引候选：

| 查询 | 索引/边界 |
| --- | --- |
| 单轮附件、有序显示 | message_file_refs `(run_id,message_kind,message_id,ordinal,file_id)`；唯一 `(message_kind,message_id,file_id,role)` |
| 已应用消息位置/队列提升别名 | applied_message_positions唯一`(message_kind,message_id)`，顺序`(run_id,application_seq,message_id)`；与应用同事务 |
| 删除文件关联轮次 | message_file_refs `(file_id,run_id)` |
| 文件派生状态/缓存命中 | derivations唯一 `(file_id,kind,fingerprint)`；claim按job表已有状态索引 |
| 文档连续块/指定页 | blocks `(derivation_id,ordinal)`；`(derivation_id,unit_type,unit_number,ordinal)`；locator关键查询字段实体化，不能只藏JSON |
| 某分支摘要帧 | summaries `(conversation_id,branch_anchor,scope,revision)`；run scope `(run_id,revision)` |
| 来源失效 | summary_sources `(source_type,source_id,frame_id)` |

祖先路径查询复用conversation_tree语义，但改为有界递归CTE/窗口，不沿用model_context先`path_ids()`全量建列表再分页。旧分支编辑/重试要由ancestor cursor绑定，不按created_at/id猜顺序。100k轮/10k文件/100k块，SQLite与PG各跑真实优化器EXPLAIN；不强制相同计划，不关闭顺序扫描来“证明”索引。

迁移在实施时根据实际head分配，**不在设计中占用0035号**。分两个可回滚逻辑变更：文件引用/派生块索引；摘要frame/来源及指针。SQLite为已有大表加列只用ALTER；新表可独立创建，不batch rebuild被引用的files/runs/users。每次升级/降级/再升级和model drift双库验证。

渐进兼容：新记录双读旧 `input_file_ids` 和新run refs，但只有API一个写入口；分批幂等回填JSON数组，历史没有可靠上传来源则标`unknown_legacy`，不伪造“新上传”。旧text_key/OCR键可作为legacy derivation有界读取，但PDF必须标可能只覆盖前20页。新访问按需重建，不启动全库OCR或全文件付费摘要。旧摘要保留为legacy背景，不能捏造来源handle；必要时从有界原历史重建，完成才启用新frame。

Downgrade保留FileObject和原始文件/旧text_key、回写兼容summary；丢弃新索引前停止新job生产并排空/取消claim。不能降级DB后让新worker继续写入。回滚不恢复已删除来源，不把新模型调用重复执行一遍。发布需要API/Agent/Worker协议版本兼容门禁；本文不执行发布。


## 备份恢复的墓碑接入（2026-10-29）

`platform/scripts/file_source_tombstones.py export/apply`只通过ops scope `files:restore_tombstones`的内部API读取/应用；清单为每批最多100条的JSONL，只含opaque user/file身份和是否永久删除。脚本不直写业务DB，秘密从指定环境变量读取，清单权限0600，不覆盖已有文件。运维需持续将清单保存在备份代之外，恢复API应先隔离Portal/Agent/Worker入口，应用清单后再开放流量；永久删除沿用API的410及持久清理意图。应用幂等，跨owner身份冲突拒绝。此流程在隔离库验过，未对个人部署执行恢复。

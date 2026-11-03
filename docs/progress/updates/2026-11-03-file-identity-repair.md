# 文件上传与成果身份修复（2026-11-03）

用户授权从远端最新main修复。基线 `origin/main@fc63e5f019f3c5b42e8f8b19cdc78d9c45163cce`，分支 `fix/file-identity-repair`，独立worktree；原工作区dirty修改保持。问题证据见[首次诊断](2026-11-03-library-duplicate-diagnosis.md)，产品合同和实现取舍见[设计](../../architecture/design/2026-11-03-file-identity-repair.md#7-实施合同2026-11-03)。未发布、部署或清理业务数据。

## 改动

- API/Portal：普通文件两个入口与个人技能上传共用稳定操作key；资料库保留失败操作的文件与原项目供直接重试。收据、新文件和outbox同事务。重复命中只哈希请求临时文件，不再写第二份存储对象。READY同范围同名/同类型/同内容默认复用，明确“保存副本”保留独立身份；同名不同内容及显式parent版本更新保持不同合同。预签名预约和完成回放canonical结果，并核对冻结对象；复用后预约不显示为资料库/回收站文件。永久删除清除收据文件名，重试墓碑保留；不把临时响应失败误当未提交而删除原件。
- API/Broker/Agent：output_key与显示名称分离，最新可见槽位决定复用/版本，不全局合并hash；兄弟分支不覆写彼此head。自动收存按SHA256核对，修订意图先checkpoint；恢复先对账API收据，覆盖同大小修改和A→B→A。枚举完整性和超限显式暴露，交付id内容冲突422不伪装attempt失效。个人技能同类交付收据冲突一并修复。
- 历史与运维：0049新增UploadReceipt、Artifact.output_key、RunDelivery.fingerprint及实际查询索引，SQLite仅ADD COLUMN；可升降级。repair_file_identity.py默认预演、单批1–500条、keyset游标、用户锁，只补可信原名metadata，不改文件id/字节/历史引用。旧目录歧义不猜测。

## 验证

本轮基线为main@fc63e5f上的本分支工作树；先整体检查，再仅重跑失败/后续审查改动涉及的项，不把不同阶段数字拼成同一最终全套。最终产品/回归文件清单内容指纹（排序路径与每文件SHA256的JSON再SHA256）为 `82d1dfb2091c2c8e850120743f03d6428db0ab390f75082764c4829b3bf73dc0`，base为上述main。原始日志、临时服务、截图和业务原始数据不入库。

- SQLite全套：2125 passed / 187 skipped；隔离PG/Redis全套：2244 passed / 69 skipped，均exit0。这两次全套早于最终分叉来源复制、上传命中免对象写入、资料库重试与预签名隐藏预约的审查修正；末轮只针对变化补测，末轮隔离PG专项152 passed（收据/分叉/版本/双库迁移与计划），SQLite关联45 passed、分叉复制15 passed、丢响应保护2 passed、预签名预约2 passed、最终API身份矩阵13 passed；这些是各自命令结果，不相加成新的全套计数。
- Portal全套229 passed，生产build通过；Ruff check/format、导入边界与git diff --check通过。浏览器E2E在构建临时副本时再次执行Portal test/build。
- 新索引与查询计划在代表性2000文件/400Run/20用户数据上ANALYZE后验证SQLite/PG实际上传复用SQL、收据操作/预约/file回收及output_key查询使用索引；0049迁移双库upgrade/downgrade与元数据无漂移通过。

已完成的独立证据：

- 真实DeepSeek＋实际API/Broker HTTP处理器（进程内ASGI/TestClient传输）＋Redis租约＋Docker沙箱：三轮输出AA→BB→AA，均为2字节，同一lineage版本1/2/3，内容SHA精确匹配；扫描使用测试stub，不冒充ClamAV或五个独立服务部署验收。三轮耗时11.01/8.61/7.97秒；后两轮缓存40704/41852=97.26%，首轮单列。测试数据库、身份、Redis db13和sandbox owner均与用户环境隔离；临时数据库已清理。
- 真实Chromium隔离五服务栈：默认重复上传复用与显式副本独立通过；上传等待期间消息先显示、文件就绪才创建会话通过；桌面与390宽手机布局、页头按钮实际不透明通过；资料库服务端成功后丢弃浏览器响应、点重试保持同一操作/一个文件的故障验收通过。模型为deterministic mock，Worker实际处理；不冒充真实模型浏览器联合测试。
- 真实Docker：深层目录、中文/制表符/换行文件名、同大小修改的指纹变化与符号链接拒绝通过；Broker暂存期间内容变化拒绝提交并清理staging。OpenSandbox未配置。
- 性能探针：本机独立Broker进程、mock文件系统和真实子进程hash，空闲/5MiB新建/未变/同大小修改/恢复的核对延迟32.7/31.1/32.5/33.7/35.4ms；子进程CPU25.6–29.6ms，父进程peak RSS79.1MiB；5MiB压缩快照5649字节。真实模型联合进程peak RSS171.3MiB。这里只证明本机有界开销，不代表目标2CPU/8GB整栈384MiB Pod容量验收。

## 已发现并修正的验收问题

- 共用旧venv会让子进程导入别的checkout，首次试跑中断；改用任务独立venv、constraints和明确PYTHONPATH，补齐dev依赖defusedxml。macOS子进程固定GNU coreutils/findutils PATH。
- 独立文件测试夹具原先依靠相同上传必定新建；改为显式copy，保留原配额/清理/预览断言。恢复fake补齐CommitOut本来就有的SHA256；迁移链断言扩展0049，不改产品阈值或跳过失败。
- 浏览器上传拦截原只匹配无query URL；新增操作key后匹配POST含query，并放行GET轮询。页头按钮不能套.row-actions（该类默认透明），使用普通flex布局。

## 边界与下一步

真实S3本轮未验：已尝试固定MinIO镜像和官方历史二进制，分别返回鉴权/仓库不可用与410；使用本地存储模拟预签名URL验证API冻结/回放分支，不能等同真实S3。没有新增依赖到产品。真实Docker最终3 passed / 1 skipped（OSB）；跳过原因与假设保持明确。

API v1无key的旧客户端仍不能获得操作级幂等，不同操作同时遇到PENDING仍允许独立文件；永久删除共享库文件会依既有合同使所有复用引用失效。历史旧记录目录无法恢复时保持歧义，不自动合并；本轮不运行真实回填或删除诊断残留。

本轮临时Redis/PostgreSQL、Docker沙箱与浏览器子服务均在检查结束后清理，未操作业务服务。

下一步：审阅本分支改动；另获授权后才发布/部署与执行历史回填/清理。新版本需要后端迁移/API/Broker/Agent与Portal同步发布。

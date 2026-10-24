# 2026-10-24 内部分享与资料库摘要后端（C）

本分支 `feat/shares-library`，起点 `00c5cc9`，共同基线 `main@74e66c0`。产品代码与测试提交 `c142108d6df43b433030ebd9eed4780b42d6da4b`；本记录的后续提交仅改交接文档。范围为[共同契约 §5、§6](../../reviews/2026-10-24-workspace-features-design.md)，没有修改 Portal、推送、PR 或部署。由父 Agent 审核、接通知、改迁移链并完成 Portal 集成。

## 已实现

- `0027_shares.py` 暂接 `0024`：shares、share_recipients、所有者/受众/对象/接收人列表索引与用户 display_name 索引。分享对象外键删除时 SET NULL，避免永久清理被分享的文件被外键阻塞。父 Agent 集成时改为 `down_revision="0026"`，重跑双库迁移。
- 完整分享创建、mine/received 列表、对象分享查询、修改/刷新/撤销、只读 view、用户查找及分享授权的 content/preview/preview asset/thumbnail。所有权取认证主体；body 的 user_id 被拒绝；people/everyone、撤销、删除/回收站、所有者停用、快照之外产物、输入附件内容均有拒绝测试。路径固定通过 through_run_id 追溯祖先；新轮次和其他分支不在旧快照内。
- view 只返回提问、最终回答、时间、mode、产物和附件文件名。没有 events、thinking、工具日志、用户记忆或附件内容。分享文件交付复用原 routes_files 的流关闭、manifest allowlist、ETag/304 和预览作业；响应 private/no-store 防止撤销后继续使用浏览器不可变缓存。
- 分享写操作与审计同事务。通知接缝位于 `platform/ailab_platform/api/share_notifications.py:create_notification`，目前明确 no-op。父 Agent 将此函数接到 B 的同事务 helper；routes_shares 已在创建/增补接收人时调用，参数包含 conversation_id、share_id，重复设置同一接收人不重发。
- READY patch 与 OCR 完成的事务调用 request_summary；只 stage_job/outbox，不提交后执行模型。Worker 经 JobEnvelope→claim→API 摘要→业务结果→ACK 消费；无文本文件直接 skipped，避免空模型任务。正文不足200字符不推理；原先跳过且 OCR 文字稍后到达时只再排队一次。
- 摘要使用 ModelPolicy.summary_model、quiet、既有 transport 的 disable_reasoning_for_quiet，≤6000估算输入 token、≤200输出 token，最终文本≤120字。file_job/summary 账本归文件所有者、无 Run，冻结定价；依赖故障持久化 failed，Worker 留待后续投递，最多3次不同模型调用；成功重投只调用一次。
- `GET/PATCH /v1/admin/settings/library` 仅 platform_admin，`{summaries:true}` 为默认。search_library（含空查询列表）增加 summary≤120字。read_library_file 支持可选 pages 输入，流式筛选明确的物理页标记，保留既有输出页边界/Unicode offset。
- Worker 不再把运行分派标志写回收到的信封对象；使用本地副本。否则同一个 InMemoryQueue 消息重投会因 extra=forbid 拒绝已经污染的 JobEnvelope。

## 父 Agent 使用的最终 API 补充

契约修改已写入设计文档的 **C 分支对契约的修改**。

- Share DTO 保持约定字段，时间使用 UTCModel；url 为 `/s/<id>`。
- ShareFile 为 `{id,file_id,filename,content_type,size,content_url,download_url,preview_url,thumbnail_url}`。content_url 与 download_url 同值；全部 URL 为 `/api/v1/shares/<sid>/files/<fid>/...`。文件分享的 view 额外包含 `file:ShareFile`；会话分享不包含 file。turns、share 等其余形状按原契约。
- `view?cursor=<上一页最后轮次id>`，旧到新每页20轮、路径最多500；`/v1/shares` 用 `{items,next_cursor}`。`/v1/shares/for` 是数组，接受 cursor/limit（默认/最大50），续页通过 X-Next-Cursor；用户查找最多8条、username/display_name 大小写敏感前缀。
- read_library_file 输入 `{file_id,offset?,pages?:[1,2]}`；最多20页、offset 基于选页后的文本。输出 pages 仍为 `[{page,offset}]`，没有猜测无标记的页码。
- 首次 call_id 为 `summary:<file_id>`；明确失败的后续重试为 `summary:<file_id>:retry:1|2`，同一调用身份绝不重放。不可变账本无法把同一已结算身份同时当作新推理重试；因此这一点作了最小契约修改。meta.summary 保留 status/text/model/at，另含 source/job_id/attempt/reason 等内部状态。

已只读检查 Portal 代理：`app/api/v1/[...path]/route.ts` 将 `/api/v1` 映射到 `/v1`、从 httpOnly cookie 加认证，并转发 ETag/If-None-Match/Cache-Control/X-Next-Cursor。测试按此映射请求接收人的产物 URL，返回文件字节而非签名地址/重定向；owner 的普通文件 URL 对接收人仍404。没有运行 Portal 或浏览器，此处不是前后端集成验收。

## 当前验证

使用共享 Python 3.12 虚拟环境，PYTHONPATH 指向本工作树；conftest 指向临时目录/不存在的 env 文件。没有读取开发者 `.env`，没有使用开发数据库。

| 检查 | 结果 |
|---|---|
| Ruff check / format --check（最终305个 Python 文件） | 通过 |
| 完整 SQLite suite（最终产品代码，最后两个测试新增之前） | 1047 passed / 113 skipped，166.10s |
| 完整 PostgreSQL + Redis suite（相同产品代码） | 1127 passed / 33 skipped，215.30s |
| 最终专项：全部分享、摘要设置/队列/故障、扫描 PDF 选页、0027迁移；含最后新增的固定版本/三次失败上限以及续页/停用查找断言 | SQLite 16 passed / 1 skipped，7.08s；PG+Redis 17 passed，8.29s |
| 0027 与既有完整迁移套件 | 两库 upgrade/downgrade/upgrade、既有数据保留、metadata drift 通过 |
| 新查询 EXPLAIN | 两库覆盖 owner/object/received 分享列表、用户前缀、产物授权；小表 PG 强制禁用 seqscan 检查索引适用性 |
| OCR 回归 red/green | 将同一新测试复制到 `00c5cc9` 隔离归档：失败（忽略 pages、返回全部页）；本分支通过 |
| git diff --check | 通过 |

全套 SQLite 的跳过主要为 PostgreSQL、Redis、真实外部组件；PG+Redis剩余33项跳过属于真实 OSB/对象存储/其他未配置后端等环境边界及一项针对 SQLite 并发行为的测试。未把 skip 算作验收通过。SQLite 表达式索引的 SQLAlchemy reflection 警告沿用现状；新 shares 索引与外键可正常比较。

PostgreSQL UTF8/C locale 临时实例监听15637（测试与迁移独立库），Redis16637/15；两者已停止，并核对端口没有监听。中途全套暴露了空摘要作业增加和测试预览客户端只返回一页的问题，已修复后重新完成以上全套；调整分享外键时中止过一次运行，未把中止运行算入成功计数。

## 成本与边界

- 正常文件最多一次摘要调用，≤6000估算输入/≤200输出 token，最终≤120字；空/短文件0调用。最多两次失败重试时总预算≤18000输入/≤600输出 token，逐次计费；供应商实际 token 以账本为准，估算不是供应商硬 token 保证。测试 HTTP 模型报告100输入/10输出，账本归属/quiet/冻结权重核对通过，非真实付费质量证明。
- API 每进程只有一个摘要模型槽，和 OCR 一样不在 Worker 装模型凭据；不新增依赖、进程或业务表。摘要读取前24000字符后再裁估算 token，不读完整文件进内存。本机10 MiB源文件微测：保留24000字符，wall0.525ms、CPU0.534ms、Python traced peak161168字节。只计文本提取，不代表模型耗时或全进程RSS；未做目标2vCPU/8GB的长期容量压测。
- 500轮快照首屏（20轮）包含认证共10条SQL：本机 SQLite13.79ms，PG12.62ms；单次测量，不是p95/SLA。模型正文或附件不会因查看分享而被加载。分享列表最多50条，每条另查询接收人（最多200），没有无界列表接口。
- 未验：真实付费摘要质量、真实扫描PDF图像质量、浏览器预览跳页/Portal页面、B通知实际入库、集成0025→0026→0027后的全链路；五服务E2E按本子任务要求未跑。扫描PDF测试使用真实PDF解析、API OCR及受控HTTP推理，预览客户端提供两页确定性JPEG占位，不冒称真实渲染验收。
- 已派发请求的响应丢失属于不确定结果；不重用原call_id，按失败后续尝试分别计费，最大3次。最终失败或设置关闭后不做全库后台回填；不会绕过用户额度。

下一步由父 Agent cherry-pick 产品提交与记录提交，接通知 helper、重链0027并审核契约修改，随后进行 Portal/集成验收。本分支无部署授权。

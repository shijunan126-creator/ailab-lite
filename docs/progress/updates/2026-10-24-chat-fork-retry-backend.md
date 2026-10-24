# 2026-10-24 Chat fork / retry backend

后端专属工作树：`t3-ae343bbd`，分支 `feat/chat-backend-fork-retry`，起点 `94d3e228988e1a303ae7b23da3b934786bff7a2f`。本轮只授权本地实现、测试、提交；未改 `platform/portal/`，不推送、不建 PR、不部署。Portal 与最终集成由父 Agent 负责。

## 接口与事件

- `POST /v1/conversations/{id}/fork`：`{"run_id": str, "mode": "chat"|"work"}` → 201 `ConversationOut`；新增 `forked_from: {conversation_id, run_id}|null`，列表和详情均返回。未知/不属于此会话/已替代轮次 404；未终态、模式冲突、超界、工作区不可恢复 422，中文 detail。源会话后续活动轮次不阻止历史分叉。
- `POST /v1/runs/{id}/retry`：`{"model": str|null, "mode": "chat"|"work"|null}` → 201 `RunDetail`。新增顶层 `retry_of: str|null`、`superseded_by_run_id: str|null`（所有 Run 输出）。旧轮次仍可按 ID 审计；不再进入对话详情/分页/last_status/模型历史/工作区候选/最新预览/分叉。非末轮/已替代/未终态 409；活动轮次 detail `先停止当前回答`；附件不可用 422 `附件已不可用`。额度、模型、技能、并发限制沿用 `start_run`。技能始终隐含 work，并按实际模式写 outbox topic。
- `conversation.titled`：`{"conversation_id": str, "title": str}`，与空标题 CAS 同事务写入当前 Run 事件。Agent 从问题与附件名并发生成，不使用答案；保留 quiet、2048 上限、独立 call_id 和账本。结束时取消并 await；慢/失败不阻止正文终态。手工命名优先，回答 heading 仅为空标题兜底。
- `agent.thinking`：既有 `{"step": int, "text": str, "seconds": number}` 不变；兼容 reasoning_content / reasoning 字符串 / thinking / reasoning_details 的 text 或 summary；别名同时出现只采用一个表示，避免重复。计时和尾块 flush 复用现有独立 flusher；事件历史完整保存，result 的摘要截断不影响事件。
- `agent.tool_result`：web_search/read_url 增加 `sources: [{title, url, site}]`，最多10条、去重、仅 http(s)、不含用户凭据、title≤200字；site 为 hostname。保持既有 step/name/ok/stdout 等字段。Chat/Work prompt 要求主张后的 Markdown 来源链接。
- `GET /v1/admin/settings/context` / `PUT`：平台管理员专用；其他角色403。PUT body `{"auto_compact": bool, "threshold_ratio": float}`，ratio∈[0.2,0.95]；返回与GET相同的 `{"auto_compact", "threshold_ratio", "models": [{id,name,context_tokens}]}`。默认false/0.5。按运行 bootstrap 读取一个主键设置并冻结在 Agent 的 run 字典，无每步轮询。PUT成功与设置同事务审计；拒绝/校验错误也有审计。修复审计中间件未支持PUT导致拒绝变500的问题。
- 内部接口：`PUT /internal/runs/{id}/conversation-title` body `{title}` → `{ok:bool}`；带 runs:status scope + attempt fence。模型摘要的业务边界/CAS冲突改为422，409仅由 attempt fence 返回。

## 资料库工具

Chat/Work：`search_library(query, limit=10)` 的工具结果为 `{ok:true, files:[{file_id,filename,content_type,size,project,updated_at}]}`；limit≤20，project是项目名或null，updated_at是不可变文件版本的created_at（UTC）。仅本人、READY、library用途、未删未purge的head版本，优先当前项目。复用已有user索引与转义filename子串查询。

`read_library_file(file_id, offset=0)` → `{ok:true,file_id,filename,text,next_offset:int|null,truncated:bool}`；offset为Unicode字符，单页≤30000字。仅流读Worker派生正文/OCR或历史Worker excerpt，不在API/Agent解析原文件；offset≤10000000。

Work专用 `copy_library_file(file_id,path=null)` → `{ok:true,file_id,path}`。API按Run用户授权，最多50份/Run；Broker仅能读取当前Run显式授权的资料，复用download_file/inject_file限额、落盘暂存与流式provider上传。默认 `/workspace/inputs/{filename}`，相对path以inputs为根，绝对path也必须在inputs内；拒绝路径逃逸。Agent不接收完整文件字节。

内部资料库POST接口：`/internal/runs/{id}/library/search` body `{query,limit}` → `{files:[...]}`；`/read` body `{file_id,offset}` → 文本分页对象；`/authorize-copy` body `{file_id}` → `{file_id,filename}`，需runs:status scope + 当前attempt。搜索/阅读需files:read_for_run。Broker新增 `/internal/leases/{id}/library-file` body `{file_id,path}`，需sandbox:use，Run身份由租约确定。

## 数据、生命周期与边界

迁移 `0022_chat_fork_retry`（revision `0022`，down `0021`）：runs新增nullable superseded_by_run_id（索引）、retry_of；conversations新增nullable JSON forked_from；新platform_settings主键表。SQLite仅ADD/DROP COLUMN，不重建被引用表；双库迁移与漂移按下方结果验收。

Fork在同一个用户锁/数据库事务中创建Conversation、Run、Artifact和事件副本，不创建ModelUsage、计费预留或任务。Artifact复用file_id，资料库字节不重复计额。usage显示快照放在result.forked_usage。summary_through_run_id映射到新Run。保留原created_at顺序；极端同时间戳按微秒递增保证排序稳定。

过程事件选择**物理复制**（每批500条）；源会话GC不删除副本；事件本身仍按原事件时间受现有事件TTL约束。预览对象复制到新Run前缀。Work只复制分叉范围内最近已保存快照（包括失败/取消轮次的checkpoint快照），目标为新Run自有key，保存时间与对象mtime均重置；沿用workspace TTL/会话GC。Chat不复制workspace。跨DB/对象存储无分布式事务：失败回滚DB并尽力删除新对象；进程硬退出的孤立对象由既有preview/workspace GC收敛。

上限：500轮、100000事件、10000对象、对象累计2GiB。复制循环60秒截止（单次存储请求仍受adapter连接/读取/重试时限约束）。超过限制422。每轮快照不再因后轮完成而立即删除；现有默认三天TTL统一回收。历史版本已删除/TTL过期的快照不能重建，Work分叉明确422。未留下任何快照的纯聊天/无沙箱轮次从空工作区开始。

## 验证

针对性回归已覆盖标题并发/取消/失败、真实Agent/API事件链路、思考字段与尾块、分叉对象独立/失败清理/模式/摘要/usage、并发重试、资料库权限/Unicode页/路径/流式注入、平台管理员设置与迁移。完整SQLite首轮971通过/107跳过/6失败：旧工具白名单、已移除的删除快照调用、TTL辅助函数依赖ID；保留失败记录并修复，最终结果见下方追加。

未运行Portal npm/build、浏览器或五服务E2E（本轮明确后端范围）；父Agent负责前端集成。Docker daemon不可用，因此使用一次性本机PostgreSQL16与Redis，独立临时目录、非默认端口，完成后关闭。未使用开发.env或开发数据库。

### 固定基线与最终检查

运行时代码固定为 `2078445bccf61994ebffbd3f619811f86222051e`；此前主实现为 `e18da1371db0371f4470c63aeb29e0476ddf0f53`。最后的交接提交只加强账本/无outbox断言并更新文档，无运行时代码改动。

- Ruff check、format --check：全部通过，285个Python文件；git diff --check通过。
- SQLite完整套件：`e18da13`，**985 passed / 108 skipped**，118.67秒。随后`2078445`增加旧版已清除workspace的422保护，其Fork/Retry回归**11 passed**；最终以真实ModelUsage替代显示缓存的加强断言再次**11 passed**（SQLite），覆盖与主体全套不同的最终增量，不合并重复数字。
- PostgreSQL16 + Redis完整套件：`2078445`，**1061 passed / 33 skipped**，154.45秒；迁移0022升/降/再升、迁移漂移、并发重试和双库索引EXPLAIN均执行。最终账本/无任务加强断言再次**11 passed**（PG）。
- PostgreSQL首次全套：1056通过/33跳过/2失败，根因为Integer is_head用了`IS true`；改为`= 1`后上述最终全套通过。保留首次失败，不把SQLite接受此语法当双库正确性证据。
- 剩余33跳过项：真实Docker/OpenSandbox及恢复/网络/传输/技能、S3、ClamAV、Keycloak和外网获取契约；另Redis不支持的scan cursor通用契约与PG行锁不适用的并发claim注入各1项。真实Redis契约/队列与PG专用用例已执行。
- 使用本工作树源码和主检出已有的Python3.12 venv解释器，无安装/依赖变更；GNU coreutils/findutils加入PATH。临时PG监听15624、Redis16624，隔离目录与一次性test/migrations库；两项服务均已停止。未运行任何开发数据库或开发.env。

### 成本与残余边界

独立Mac/LocalStorage探针（`e18da13`，tracemalloc开启）：100轮、1000条过程事件、20×64KiB预览、8MiB压缩workspace；fork 201，wall **0.727秒**，进程CPU **0.723秒**，Python峰值分配 **1.26MiB**，进程最大RSS增长 **2.44MiB**（全进程最大RSS122.84MiB，含框架初始化），新对象 **9.25MiB**。原始库文件未复制、未重复计额。事件另占新数据库行；这是单次小样本，不是2vCPU/8GiB、上限2GiB、S3或长期并发容量验收。

Fork CPU随元数据/事件数及本地复制字节线性增长；本地storage.copy按64KiB流式复制并哈希，S3走服务端copy，API不整份缓冲。新磁盘成本为预览副本+一个最近workspace+事件副本。用户锁持续覆盖复制，较大分叉会占用同用户写入；数量/2GiB/60秒边界限制单次工作。单次存储调用可能超过循环截止时间，仍有adapter连接/读取/重试时限；不是严格60秒HTTP SLA。杀进程后的孤立对象清理仍依赖GC，未做真实进程kill+S3组合验收。

保留历史快照会增加磁盘：默认单快照压缩上限60MiB，三天TTL；新增保留成本上界随期间工作轮数增长，且既有运行内checkpoint快照也受同一TTL回收。`workspace_ttl_days=0`现在保留各轮快照直至会话GC，不能再按“只保留最新一份”估算。已在旧版本清除或已过期的历史工作区无法重建，显式422；没有沙箱活动/快照的轮次允许空工作区。事件副本保留原事件时间，因此受现有事件TTL约束。

标题任务每个空标题会话额外一次模型调用（独立reserve/settle和call_id）、一次CAS与一个成功事件；没有大文件/新后台进程/存储对象。输入最多1000字问题+50×200字附件名，输出上限沿用quiet预算2048。Python CPU/RSS为有界文本与HTTP开销，真实模型CPU/RSS/计费与provider延迟未实测；不能用fake LLM测试代替。生成timeout10秒，结束时cancel+await及账本清理；主回答不await标题，正文首个额度预留先于标题，避免可选元数据抢占临界余额。失败/慢标题不阻止正文终态，但额外上游并发仍可能竞争provider资源，账本收尾可延长job清理时间。

仍待父Agent：按本记录契约完成Portal集成，运行前端测试/build、真实浏览器和五服务E2E；本轮没有发布、推送、PR或main合入。

## Round 2：标题完成时序修复（2026-10-24）

本轮工作树 `t3-2e563082`，实际起点 `814c541b46e5b3a15ffe4985bc25432f8567193c`（父Agent已合并后的代码），新分支 `fix/chat-title-timing`；旧记录中的 `t3-ae343bbd`/`2078445` 是上一轮证据，不是本轮基线。不修改 `platform/portal/`，不推送/PR/部署。

- 正文先写 succeeded/failed；job 收尾保留并 await 标题的剩余预算，任务不脱离 job/client/attempt 生命周期。worker shutdown/正文取消/attempt丢失取消并回收模型子任务、账本与heartbeat。标题预算从标题任务启动算10秒，包含主额度reservation gate、模型生成和标题写入；慢gate时有意放弃可选标题，既不抢正文额度也不重置截止。取消后的账本结算仍可能使用既有有界HTTP重试，10秒不是job清理SLA。
- 原 `fence_attempt` 不因正文终态自动结束；同一有效claim且未ended的attempt可写晚到标题，不需削弱fence。API额外禁止 canceled Run 写标题，deleted conversation仍由CAS条件拒绝；foreign/stale/expired attempt仍409。轻量 `/state` 在携带attempt时用只读索引查询返回409，模型轮询不再吞409，失去attempt后停止上游调用（按既有轮询间隔，不是即时中断）。
- **送达路径为SSE**：`run.completed`后，空标题且有效attempt时最多保留终态后10秒窗口，title/ACK/attempt过期后关闭。先检查pending、后读取事件，避免CAS提交竞态漏事件。真实Agent/API/SSE测试强制正文先完成且流已进入终态空轮询，再返回标题，确认 `run.completed → conversation.titled → stream.end`。Portal既有stream.end列表刷新继续可作兜底，本修复不依赖它抢在晚到标题之后；未修改Portal。
- `title_text.normalize_title` 为无ORM依赖的共享纯函数：Agent、内部标题端点、result fallback统一去heading/emphasis/code/link语法、折叠空白并截40字符。内部原始输入有4096字符上限，最终持久化仍≤40；手动标题不受自动清洗影响，空标题CAS仍保留手动rename优先。
- retry与复制非空标题的fork bootstrap均明确不请求标题；此行为此前已正确，本轮加回归锁定。

### 本轮验证与成本

修复前 `814c541` 运行新增回归：9 failed / 2 passed / 37 deselected。为允许缩短预算测试，仅暴露原有10秒常量，未改变旧控制流；失败复现晚到标题丢失、取消标题写入、Markdown/截断、gate时限及终态SSE契约缺失。首轮新测试两个失败来自测试自身：ClientError构造参数、把running误当正文完成；已修正。最终代码验收结果待下方追加。

SSE仅在短暂终态且空标题时多一次有界索引查询，limit 1；`/state`携带attempt多一次只读所有权查询。不新增依赖/进程/迁移/存储对象；每个未命名会话仍最多一个并发title task和同样一次标题模型账单。代价：Agent槽位/client/claim在正文完成后最多继续占用标题剩余10秒预算及既有账本清理时间，SSE连接最多延长终态后10秒加正常poll调度开销；这不延后正文内容/终态。SQLite独立临时profile EXPLAIN两项查询均SEARCH，使用run_attempts run_id/PK、outbox claim_token与conversation既有索引，无全表扫描。100次完整 `_poll_events` 本机median 0.526ms、p95 0.780ms；不是2vCPU/8GiB长期容量、真实模型成本或PG计划证据。

不运行PG/Redis、真实模型、五服务E2E、Portal npm/build或浏览器；本工作树无Portal node_modules，前端及统一验收仍由父Agent负责。没有使用开发.env/开发数据库，SQLite与查询探针均用测试引导的临时profile。父Agent下一步：集成本修复，按最终提交执行前后端统一验收；保持不自动发布。

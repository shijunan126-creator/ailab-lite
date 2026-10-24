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

过程事件选择**物理复制**（每批500条）；源事件TTL/会话GC之后仍可读。预览对象复制到新Run前缀。Work只复制分叉范围内最近已保存快照（包括失败/取消轮次的checkpoint快照），目标为新Run自有key，保存时间与对象mtime均重置；沿用workspace TTL/会话GC。Chat不复制workspace。跨DB/对象存储无分布式事务：失败回滚DB并尽力删除新对象；进程硬退出的孤立对象由既有preview/workspace GC收敛。

上限：500轮、100000事件、10000对象、对象累计2GiB。复制循环60秒截止（单次存储请求仍受adapter连接/读取/重试时限约束）。超过限制422。每轮快照不再因后轮完成而立即删除；现有默认三天TTL统一回收。历史版本已删除/TTL过期的快照不能重建，Work分叉明确422。未留下任何快照的纯聊天/无沙箱轮次从空工作区开始。

## 验证（待最终固定提交）

针对性回归已覆盖标题并发/取消/失败、真实Agent/API事件链路、思考字段与尾块、分叉对象独立/失败清理/模式/摘要/usage、并发重试、资料库权限/Unicode页/路径/流式注入、平台管理员设置与迁移。完整SQLite首轮971通过/107跳过/6失败：旧工具白名单、已移除的删除快照调用、TTL辅助函数依赖ID；保留失败记录并修复，最终结果见下方追加。

未运行Portal npm/build、浏览器或五服务E2E（本轮明确后端范围）；父Agent负责前端集成。Docker daemon不可用，因此使用一次性本机PostgreSQL16与Redis，独立临时目录、非默认端口，完成后关闭。未使用开发.env或开发数据库。

性能与最终测试提交/结果将在验收完成后填入本记录。

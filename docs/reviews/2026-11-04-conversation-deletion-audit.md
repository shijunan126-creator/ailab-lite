# 对话删除与隐藏版本审查

后续：本报告保留修复前证据；六项问题的本地实施和最终检查见[修复记录](../progress/updates/2026-11-04-deletion-lifecycle-fixes.md)。

范围：用户询问删除逻辑与缺陷，本轮只审查/隔离复现，未修产品、发布、部署或删除真实业务数据。产品基线为已部署main@e4479cd96bf5f41ca4ab718cc34f349df9acf664；检查worktree为fix/message-branch-consistency@162a899，产品树与该merge相同，已有部署/清理文档修改保留。

## 正常合同与已验证边界

- 当前UI只有删除整段对话，没有单独删除某条消息/某个版本，也没有会话回收站恢复入口。资料库文件回收站是另一套操作。
- 删除Conversation时在用户锁内软删会话，按conversation_id取消全部未结束Run（包含waiting_user/interrupted）和排队消息，覆盖隐藏分支；不按当前active路径截断删除。
- 默认30天后后台分批清理全部Run和Conversation，包括隐藏旧版本、过程记录、摘要frame及checkpoint/workspace/preview删除意图。等待期限不等于提供恢复功能。隔离探针创建旧/新分支，删除后推进保留时间，确认全部分支Run行与Conversation行清除。
- 上传文件、已交付资料库产物与用量账本保留。已保存记忆事实按ADR0019保留，清除原对话的episode/vector/引文和证据引用；事实需在记忆页另删。独立fork是新Conversation，不随原对话连带删除。

## 确认缺陷

### P2：删除后的读取围栏不一致

位置：`api/routes_runs.py::_get_owned_run`（第60行）、`_owns_run`；`memory_recall/policy.py::source_available`（第83行）；`api/routes_model_context.py::model_context`。

隔离API复现：创建A→B→C，编辑B得到A→B′并结束；删除会话返回204。会话GET和切换版本POST均404，但已删除会话的旧B、旧C、新B′的单轮GET、events/history、process仍200，原预览仍200，GET /v1/runs还列出B′；internal model-context仍返回祖先正文。

原因：这些入口只检查Run所有权或文件来源失效，未检查Conversation.deleted_at；source_available在epoch=0时直接返回owner存在。软删期间保留数据库行是既有合同，但多个内容读取入口对同一墓碑给出相反可见性，且无对应的恢复/查看已删除对话UI合同。此复现是同一已登录所有者可读，不是跨用户泄露；未测SSE真实连接，SSE使用同一缺失会话检查的拥有权/轮询函数，静态上亦受影响。

根因修复建议：统一Run所属会话的存活检查，贯穿详情、列表、事件、过程、预览及内部模型上下文；保留非当前分支的合法版本读取、standalone Run和独立资料库文件下载。回归须断言删除前旧/新版本均可查，删除后所有内容入口拒绝，其他用户原本就不可查。

### P2：删除后迟到的压缩仍可写入摘要

位置：`api/routes_model_context.py::_save_model_summary`（第230行）。

隔离API复现：A已完成，B取得有效attempt并处于running；读取B的model-context，随后删除会话，B状态变为canceled。模拟此前在途的压缩返回，用原attempt PUT B/model-summary，响应仍200，已删除会话summary实际写入`LATE_MODEL_SUMMARY_AFTER_DELETE`。

原因：写接口校验attempt租约、source_epoch和祖先边界，但未检查Run仍在允许写入的运行状态，也未检查会话存活；删除时终止业务状态不等于立即令该attempt租约失效。与已检查RUNNING的checkpoint/memory写入路径不一致。

该请求不会把会话重新放回列表，后续保留期GC仍可清掉；但它明确证明删除完成后仍接受迟到模型产物，并可能重新创建frame/evidence状态。未运行真实模型，不声称观察到用户线上会话实际发生。

根因修复建议：在同一用户锁与事务内重读Run/Conversation并校验运行状态和墓碑，再做CAS/frame写入；过期请求拒绝，不能仅清空当前摘要或靠Agent轮询最终停止。回归覆盖delete/cancel与LLM返回交错，并保留正常压缩、重试/幂等与独立分支合同。

### P2：前端收到404仍保留缓存正文

位置：`portal/lib/conversation-cache.ts`；`components/conversation-menu.tsx::remove`；`components/chat/chat.tsx::loadDetail`（第192行）。

删除操作刷新列表/导航，但无Conversation缓存清除接口；Chat初始化会先读缓存，GET失败只setError，不清detail/侧栏内容。直接执行原缓存模块与原loadDetail函数，用404模拟已删除会话，返回`error=404`但detail和cache仍含`DELETED_CHAT_BODY`。

因此同一SPA后退/重新进入已缓存的删除会话时，存在错误提示与旧正文一起留下的路径。证据是原函数/模块级探针和界面代码链路；本轮未跑真实浏览器操作，不冒称已完成端到端复现。

根因修复建议：删除成功清除对应缓存，详情确认404时废弃detail/预览/stream并转到明确空态或离开；取消已删除会话的迟到回调。不要在普通网络错误时误清正常对话。回归覆盖删除后Back、被另一页删除后的加载404、以及网络短暂故障。

## 验证

- 现有deletion_review/deletion_runtime/conversation_tree：25 passed。
- 已删除会话召回/整理/孤儿episode专项：3 passed / 58 deselected。
- lifecycle契约SQLite：16 passed / 15 skipped（PG/S3等未配置）；不是双库/S3验收。
- 新增仓库外API探针2项通过，表示确认缺陷存在并验证全部分支可被最终purge；另有原前端函数探针确认404保留缓存。
- 首轮探针误引用不存在的start_background设置，未执行该测试业务步骤，已移除夹具错误；不计作产品失败。全部使用独立临时SQLite/存储，未接当前用户数据库。
- 本轮未改产品，未跑完整套件/build/浏览器/PG/S3/真实模型或大历史删除负载。超大历史删除会物化全部Run id是已知性能边界，不在本轮宣称新增实测。

下一步：经用户授权后统一补齐会话墓碑的读写和前端缓存围栏，再集中验证；不沿用上次PR/部署授权。

# 全部删除入口补充审查

后续：本报告保留修复前证据；六项问题的本地实施和最终检查见[修复记录](../progress/updates/2026-11-04-deletion-lifecycle-fixes.md)。

本轮授权为审查与隔离复现，未修改产品、发布、部署或删除真实业务数据。检查分支 `fix/message-branch-consistency@162a899f28e9b743fc6d4fdace94b9e376785622`，与本地 `origin/main@e4479cd96bf5f41ca4ab718cc34f349df9acf664` 的整个提交树相同；主工作区及此前部署/清理文档 dirty 保留。以下为代码与临时 SQLite/本地存储证据，不代表已在线上观察到故障。

## 结论

除[会话删除审查](2026-11-04-conversation-deletion-audit.md)的三项 P2 外，又确认三类 P2。共同缺口是删除、恢复和异步任务没有形成完整生命周期：恢复只清墓碑，草稿来源失效覆盖不足，存储清理结束早于在途写入结束。本轮没有发现跨账号读取缺陷。

### D4 / P2：恢复只清删除标记，扫描任务或预览状态不会恢复

位置：`platform/ailab_platform/library.py:232`（restore_lineages）；`api/jobs.py:147`（trashed 文件 claim 视为 done 并删除 outbox）；`api/source_lifecycle.py:78`（预览取消）；`api/routes_files.py:507`（终态预览直接返回）。

两条确定性 API 复现：

1. 上传 pending 文件，找到 file outbox；移入回收站；Worker claim 返回 `done`，outbox 被移除；调用单文件恢复或批量恢复均 200。文件重新出现在库中，但 status 仍 pending、没有任何 file outbox，下载 409。现有 reaper 不重建丢失的 file job，因此不是等待扫描即可恢复。
2. READY 文本文件 GET preview 得到 pending；删除使 preview 变成 `failed/source_unavailable` 并清掉 job_id；恢复 200；连续 GET preview 仍返回原失败，没有生成新 preview job。原始字节保留且可下载，预览失败不会因关闭重开解除。此复现使用文本预览，不把 Office/PDF 原生 viewer 的不同路径算作已验证。

根因：删除时停止处理符合要求，但 restore_lineages 只恢复文件名与 deleted_at/purge_at/project 标记，未对取消状态和任务所有权做恢复事务。不能在删除侧放开 Worker 继续处理来绕过问题。

修复方向：恢复时区分仍有效的扫描/预览任务与因删除已终止的任务；在同一事务重建必要 outbox，重置仅由删除造成的派生失败。保留病毒拒绝、真实解析失败和 purged tombstone 的原语义；新代次必须拒绝旧 completion。回归覆盖单个/批量恢复、claim 前后及 completion 与删除/恢复交错，避免重复任务。

### D5 / P2：永久删除个人技能，依赖它的编辑草稿仍标为 ready

位置：`api/routes_skills.py:130`（remove_skill）；`library.py:37`（delete_file_rows）；`skill_drafts/service.py:416`（invalidate_drafts 只覆盖 conversation origin）；`api/routes_skill_drafts.py:151`（read_file）、confirm 的 parent 校验。

隔离复现：上传带唯一标记的个人 Markdown 技能并完成扫描；打开编辑草稿，status=ready；DELETE `/v1/skills/files/{id}` 返回 204，GET 原文件为 404，但草稿 GET 仍 ready，草稿 SKILL.md 仍 200 并包含原内容；confirm 则 409（目标技能已不存在）。

影响：正式技能确已删除、不会被旧草稿直接复活，但界面留下貌似可保存的失效草稿；它继续占活动草稿名额，保留正式删除路径未覆盖的内容副本。没有跨账号访问；不能把该证据扩大为所有用户独立编辑副本均应随来源删除。

根因：来源清理从 MessageFileRef 的会话后代进入，只失效 conversation 草稿；直接由 skill 文件打开的草稿用 base_file_id 关联，却未被删除流程处理。确认时才发现失效，读写/列表状态没有同步。

修复方向：按被删版本/谱系定位未确认的依赖草稿，使其显式失效并处理对象；若需要保留用户新增修改，应明确转成独立草稿，不能继续显示有效 update。confirmed 草稿的历史重放与独立成品另按合同处理，不连带删除正式副本。回归覆盖 ready/preparing、版本链、修改过的草稿及独立副本。

### D6 / P2：草稿丢弃后，在途解包能写回文件且永久漏出 GC

位置：`worker/skill_unpack.py:28`、`:38`、`:50`；`api/routes_skill_drafts.py:262`；`api/reaper.py:181`（一次性删除意图）、`:258`（孤儿扫描命名空间）。

确定性交错复现使用实际 Worker 解包函数、真实受限子进程及本地 storage，仅在 execute 返回后插入删除动作：

1. 上传合法 ZIP 个人技能，打开 preparing 草稿并取得有效 claim；Worker 已通过 source 校验并完成解包。
2. 在其写对象前丢弃草稿；立即删除前缀，并执行 drain_object_deletions，确认该前缀与持久删除意图都已清空。
3. 恢复 Worker：其照常 put_file 两个 blob，随后 complete 正确返回 409/stale_claim；草稿仍 discarded。
4. 将时钟推进两天，运行三轮 lifecycle_once：两个 blob 仍存在，且没有对应删除意图。

这不会复活草稿或让前端读到它，但丢弃成功后的内容重新落盘，占空间且不会最终收敛。当前 orphan GC 扫 derived/previews 和 uploads/versions/runs/skills/templates/brand，不扫 skill-drafts；失败 completion 的 Worker 也没有 finally 清理。单纯校验数据库 claim 不能围栏已经拿到 storage 权限的外部写入。

修复方向：将 Worker 写入绑定到隔离的 attempt 命名空间，发布清单时接管有效对象；拒绝/失败时清理该 attempt，并给未知/终态草稿对象提供有宽限期、有界分页且复核所有权的孤儿回收。不能只再延迟几秒删除一次或无条件删共用内容 hash，以免误删重试仍有效的数据。回归应覆盖先删后写、先写后删、Worker 崩溃、旧 claim 与重试重叠。

## 其他入口与正确保留合同

| 范围 | 核对结果与证据边界 |
|---|---|
| 资料库、历史版本、单个/批量删除、清空回收站 | 删除按整条 lineage；purge 清理版本/派生对象并留历史占位，事务内记录删除意图；存储故障可重试。版本/身份/删除/生命周期专项通过。恢复处理缺陷见 D4。 |
| 删除项目 | 取消所有分支未结束 Run/队列，软删对话，文件整链进回收站；已在回收站的版本也清 project FK；任务解绑项目并停用；项目作用域记忆副本清理。项目 Run 内容仍可读属于前报告 D1 的同一墓碑缺口。 |
| 个人技能、平台订阅/重置/管理员删除 | 专用个人删除永久清版本链；移除订阅/重置清自定义副本并废弃平台草稿；管理员删除按已定合同把已确认自定义副本转成个人技能。D5/D6 是新增缺口。 |
| 平台系统版本 | 设计明确管理员软删除保留系统历史身份和内容，不是物理回收；不将此既定保留误报为 GC 缺陷。系统文件归 SYSTEM_OWNER，普通文件删除 API 不授予用户操作权。 |
| 模板、品牌素材 | 取消当前可选入口，冻结 RunAssetRef 保留旧字节；无引用后经宽限由 GC tombstone 清理。引用存续时占空间属于既定合同。专项包含到期清理及当前/pending 保护。 |
| 分享 | 撤销使后续授权失败；文件/会话删除的在线存活检查有效；撤销分享不删除所有者文件。未做真实下载流中途撤销的网络实验。 |
| 定时任务 | 删除任务记录阻止未来调度；按用户既定决定，不取消已开始的一轮。专项通过。 |
| 记忆清空、项目作用域、撤销 | 清理当前条目、summary、历史快照/plan/delivery/undo，围栏迟到编辑；会话删除保留已保存事实是 ADR0019 决定。相关专项通过，未测真实模型竞态质量。 |
| 排队消息 | 仅允许 queued 状态取消，用户锁内改 canceled；已开始消息不能误当排队项删除。取消保留幂等收据，不声称物理抹除消息内容。 |
| 部门、额度策略 | 有成员/子部门、有引用/默认策略拒删；权限矩阵与管理专项通过。账号只有停用，没有真实删除入口，按既定决定暂缓。 |
| 临时文档视图、截图、后台对象 | view 有 begin/commit/close 顺序围栏；截图到期清理、普通文件孤儿及持久删除意图有现成保护。D6 显示 skill-drafts 不在同一回收闭包。未做 Redis/S3 外部后端本轮验收。 |

## 验证与未测边界

- 两组既有专项共 **448 passed / 25 skipped**：第一组305/19，第二组143/6；代码基线162a899。覆盖 file_versions/file_identity/deletion_review/deletion_runtime/projects_brand/templates/lifecycle_assets/personal_skills/platform_skills/skill_drafts/skill_draft_files/shares/schedules_citations/user_memory/memory_summary/memory_hardening/contracts.lifecycle，以及 identity_admin/inbox_queue/projects/reaper/document_view/view_state_fencing/run_sources。
- 仓库外缺陷探针5项：单个/批量恢复扫描2项、恢复预览1项、迟到解包孤儿1项、个人技能草稿失效1项；均确认上述现状。首轮4通过/1失败，失败是探针把 file job 混入 preview job 集合；改为按 kind=preview 筛选后仅复跑该项通过。此为探针断言修正，不是产品修复。
- 所有探针使用 tests.conftest 的独立临时 SQLite/本地存储，未读取真实用户 DB。迟到写入探针采用确定性交错注入，不是真实调度概率或长期空间压测。
- 未改产品，未跑全套Python/Portal build/浏览器/真模型/PG/Redis/S3；25项跳过涉及外部后端等条件，不算通过。448项绿不能证明所有删除交错无缺陷。超大单事务删除、全量ID物化/前缀扫描的实际CPU/RSS/延迟仍为既有未测性能边界。
- 下一步：用户决定是否实施。若实施，应把前报告D1–D3与本报告D4–D6按会话存活、来源依赖、恢复代次、存储写入/回收闭包统一修复，再集中验证；不沿用旧PR/部署权限。

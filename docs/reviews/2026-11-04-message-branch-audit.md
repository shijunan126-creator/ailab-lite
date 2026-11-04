# 历史消息编辑、版本切换与压缩分支审查

## 范围与基线

用户授权检查实现及缺陷，包括编辑、切换、画面与后端分支一致性、压缩上下文和工作区恢复。本轮为审查，未改产品、清理业务数据、发布或部署。

- 当前检出为 `fix/source-pill-alignment@c033664c4d9d5837283193f44fbd9ce2ede306e9`，进入时已有 UI/文档等 dirty 修改，全部保留。
- 当前检出落后于部署资料。本轮进一步检查本机发布源码快照 `release-db7e2e1`，元数据 commit 为 `db7e2e14a254e918ddec45e763a34d2e080d073a`；测试实际导入该快照的 Python 包。没有将旧 current.md 中的 release-3cd5a61 当成当前运行事实，也未以元数据代替容器内源码核验。
- 本地已有 `origin/main@40150395aa7772736e92feb306dc20712e56ec14`；上述发布 commit 到该引用的六个相关文件（routes_runs/routes_conversations/routes_model_context/summary_evidence、chat.tsx、conversation-pages.ts）无差异。未 fetch，不声称远端已实时刷新。

## 结论

正常路径的父节点、历史查询和工作区祖先选择正确；旧消息不会被编辑接口物理删除。但确认四个缺陷，不能再笼统称为“编辑和切换没有问题”。前三项会使实际上下文与用户看到或选择的分支不一致。

### 1. P1：失效摘要的文件复核队列会跨分支继承

位置：`api/routes_runs.py::_new_version`、`api/routes_model_context.py::_save_model_summary`、`api/summary_evidence.py::_excerpt`。

编辑较早轮次时，旧摘要边界若不在新祖先链，代码仅清空 `Conversation.summary` 和 `summary_through_run_id`，仍保留 `summary_frame_id`。写新摘要时无条件读取这个旧 frame，并通过 `advance` 继承其 pending 复核队列。run 句柄会校验 allowed_runs，但纯 file_id/block_id 句柄只检查所有权、文件是否删除，没有检查是否属于新分支。

最小复现：

1. 对话 `root → old`；文件只由 old 的工具读取，登记 agent_read 关系，不作为编辑后沿用的输入附件。
2. old 的合法摘要 frame 带该文件事实及尚未消费的文件复核项。
3. 编辑 old 得到 `root → edited`。首次 model-context 正确返回空摘要、空 evidence。此路径用于父轮快照不可用、模型变化等需要从API历史重建上下文的场景；可直接延续兼容父轮快照的正常路径不必读取共享摘要。
4. edited 对 root 保存不含文件来源的新摘要，API 返回 200。
5. 再读取 edited 的 model-context，`summary_evidence` 出现旧分支独有文件及其原文 `ONLY_OLD_BRANCH_EVIDENCE`。

隔离 API 探针已复现。Agent `context.py::_assemble_context` 会把返回的 revalidation 放进压缩模型输入；如果模型引用这个文件，新摘要提交还可能被 API 的 allowed-file 检查拒绝。已证明原文进入 API 返回，未运行付费模型，不声称观察到真实回答受污染。切换到不包含旧摘要边界的路径后重新摘要，也存在相同的旧 frame 继承路径。

根因修复：摘要正文、边界和 frame/evidence 状态必须作为一个兼容单元选择或失效；新摘要只能继承当前有效前驱 frame。复核文件/块还应绑定当前允许祖先的文件引用，不能仅凭同一账号可访问就充当本分支历史。回归覆盖编辑、切换及 pending 的 file/block/run 三种句柄。

### 2. P1：发送请求没有绑定用户看到的分支

位置：`portal/lib/outbox.ts::dispatch`、`api/routes_conversations.py::send_message`、`api/routes_runs.py::start_run`。

发送只携带 conversation_id，不携带用户看到的 active_run_id/分支修订。start_run 的 CAS 仅校验服务端读到的 active_run_id 在本次事务中没变，不能识别请求来自旧页面。

最小复现：A 标签页显示 `root → old`，B 标签页编辑并完成 `root → edited`；A 不刷新，发送“继续原分支”。请求成功 201，但新轮次 parent_run_id 为 edited，而不是 A 看到的 old。隔离 API 探针确认。单页在附件上传准备期间切换版本也有同类窗口，因为 outbox 保存了 conversation_id，未冻结分支身份。

根因修复：在用户提交时冻结预期父节点或分支修订，贯穿 outbox/上传等待/失败重发，并由 API 在用户锁内校验。失配明确冲突，不能静默转接；同时保持现有 client_send_id 幂等和有序连续发送合同。回归覆盖双标签页、上传等待、失败重发及切换后继续发送。

### 3. P1：并发版本切换的迟到响应会覆盖新画面

位置：`portal/components/chat/chat.tsx::switchTo`、`turn.tsx` 的版本按钮。

switchTo 没有独立操作序号或全局互斥；loadSeq 仅使详情 GET 失效，不会使另一个切换 POST 的回调失效。按钮的 switching 状态属于单个 Turn，不能阻止点击另一轮的版本按钮。

最小复现：依次触发切 B、切 A，后端最终选 A；人为延迟第一个响应，让 A 响应先到、B 响应后到。直接提取发布快照的原 switchTo 函数执行受控 Promise 探针，结果为 `server=A, displayed=B`。无需修改函数逻辑即可复现。未运行真实浏览器网络限速，所以该证据是函数级异步交错，而非浏览器端到端记录。

根因修复：对分支变更统一串行或使用操作世代围栏，忽略失效响应，并以服务端确认的分支修订校验画面与后续发送。仅 abort 请求不能保证已提交的服务端变更被撤销。回归用两个不同 Turn 的切换操作和反序响应。

### 4. P2：分页刷新会保留非当前路径的旧消息

位置：`portal/lib/conversation-pages.ts::refreshConversation`（第24行）。

合并只按 created_at/id，保留所有早于新页面首轮的旧消息，没有判断祖先关系。另一个标签页切换分支、旧标签页刷新最新页时，只要分歧点落在最新20轮之外，就会把旧分支和新分支拼成一条假历史；还可能错误沿用旧 has_more=false，阻止加载真正祖先。

直接运行发布快照原函数，旧画面 `[root,A,A-child]`、新分页 `[B]` 得到 `[root,A,A-child,B]`。单条页仅用于最小复现；实际 Portal 的20条页可将 B 替换为20个新分支后续轮次，错误保留规则相同。

根因修复：分页合并依据明确父链/分支修订，只有证明仍属当前祖先链才保留旧页；无法证明时替换并重新分页，不能按时间推断成员关系。回归覆盖分歧点在分页之外、相同时间戳和 has_more 边界。

## 已核对的正确行为

- 编辑创建 sibling，新轮次 parent_run_id 等于被编辑轮次的 parent_run_id；同事务更新 active_run_id、消息引用和 outbox。
- 正常版本切换选择目标并沿其最新后代到叶节点，详情按该叶的祖先链返回；不会删除另一版本。选择较早共同节点时沿最新子节点继续是现有合同，并非保存每个节点上次选过的后代。
- model-context 按执行轮次的 parent_run_id 查询祖先，不是按全会话时间顺序取最近消息；摘要正文边界必须属于该祖先链。
- model-input 的 previous_model_context 来自确切父轮快照，工作区从该父链寻找最近快照；正常分支不会直接取另一 sibling 的 checkpoint/workspace。
- 编辑/切换要求对话 idle；所有权、失效附件、50版本上限、500轮深度、同用户并发修改都有现有守卫。切分支会暂停待发队列，不自动把原队列全部发往新分支。

## 验证与边界

- 初始旧检出：conversation_tree/prefix_cache 58项通过；仅用于理解旧基线，不替代发布基线。
- 发布快照：conversation_tree、prefix_cache、fork_document_state、chat_fork_retry 共138项通过；context_isolation、context_contract、inbox_queue 63项通过，共201项现有专项通过。
- 新增仓库外 API 缺陷探针2项通过，含更完整的旧分支文件引用/frame fixture；“通过”表示成功断言缺陷存在，并非修复成功。原函数 JS 探针分别复现分页串线、切换响应乱序。
- 复现脚本暂存 `/tmp/ailab-branch-audit-20261104/`；不提交测试输出或业务数据。测试用独立临时 SQLite、内存队列、mock sandbox；借用已有 venv 的解释器，PYTHONPATH 和 cwd 指向待测发布快照，未修改其他 worktree。
- 未做浏览器、真实模型、PG/Redis/S3、完整Python套件或Portal build；本轮未改产品。尚未证明用户现有具体会话已经触发上述问题。
- 下一步：围绕“分支身份贯穿请求、分页、摘要及复核队列”集中修复，补失败回归后整体验证；不逐点补 UI 症状，不沿用旧发布/部署授权。

实施跟进：用户随后授权修复全部4项；已在独立分支完成本地修复与验证，具体合同、基线和边界见[实施记录](../progress/updates/2026-11-04-message-branch-consistency.md)。上文为修复前审查事实。

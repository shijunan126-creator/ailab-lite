# T14 模型网关、上下文和自动标题实现

独立 worktree `/Users/michael/.t3/worktrees/ailab-lite/t3-e8a45c40`，分支 `t3/agent-model-context`，干净基线 `a8843a4bb0c475b39982e06d2b4dd7abd1daf342`。未集成 Gemini 未提交修改，未 push / PR / 部署。按主 Agent 指令只维护本报告，不改全局交接记录。

## 行为与集成点

- `model_policy.py` 选择能力匹配的模型、遍历有界去重 fallback、维护按别名校准的轻量 token 估计；`model_transport.py` 只处理 OpenAI HTTP，`llm.py` 保留 DTO/mock 和旧导入兼容。
- TOML 增加 fallback、context/output token、capabilities，旧 tools 配置兼容。真实摘要优先在真实可用别名里按 weight 选最低者；只有全 mock 目录才用 mock，避免生产摘要被演示文本替代。
- 每次发送都有独立 call_id 和预留/结算。计费别名是该次选择的别名，实际后端从响应 model 记录；失败调用根据到达情况释放或估算，不冒充精确计量。reasoning_content 协商重试也移出已计费调用内部。
- connect 5s、首个有效输出默认30s（心跳/空delta不算）、流读取 idle60s、总deadline沿用旧llm_timeout或新llm_total_timeout。文字/推理/有效工具片段任一出现即禁止重播。使用结构化 retryable 标记，避免错误正文恰好出现503导致非重试错误重播。
- 账本失败阻止下一尝试；失败结算亦更新Run软预算。每个尝试前检查该预算。摘要与标题均走同一记账链。
- Agent异步客户端 bootstrap 改用 **`GET /internal/runs/{id}/model-input`**，公开GET及旧内部GET不改。新bootstrap不加载全会话，只查最新已保存工作区；同样返回附件/技能与`needs_conversation_title`。旧同步客户端仍使用旧GET。
- **`GET /internal/runs/{id}/model-context?after=...`** 按(created_at,id)顺序取100条、101条探测has_more；摘要游标之后增量读取。系统/技能/附件/历史/工具定义一起检查input预算，至多70%context且预留声明的output。当前输入或单条历史无法压进预算时明确失败，不能静默丢弃当前问题或工具协议。
- **`PUT /internal/runs/{id}/model-summary`** 用expected_through_run_id CAS，校验同会话、边界前进和早于当前Run。摘要调用静默不流入用户回答；持久化成功才推进边界，只有再次超预算才增量摘要。
- 自动标题仅在`needs_conversation_title`为真、用户回答成功时运行。基于首轮问题+确认回答，分别截1000字，输出最多64token/40字，总额外等待最多10s；普通文本标题无需heading。独立call_id/预留/结算。异常/额度不足/空标题跳过；结果放`result.conversation_title`。API只有空title才CAS应用由UI子任务实现；现有API首句默认标题也由UI任务协调替换。
- T16：`ModelCall.ttft_seconds`在传输首个有效输出处捕获，结算路径通过可选`metrics.MODEL_TTFT_SECONDS.labels(metrics.model_label(model)).observe(...)`报告**实际调用别名**，失败无输出不记。未修改metrics.py/obs.py；常量未集成时跳过，防止独立分支不能启动。上游任意model字符串不直接做标签，保持低基数。traceparent由主Agent补clients._req。

## 迁移交接（必须由集成方完成）

提交的 `0016_conversation_summary.py` **down_revision="0014"**，本独立基线只有0013，因此提交树独立直接upgrade head会因缺0014失败，这是明确的待集成依赖。没有提交空stub。验证期间仅临时把0016的down_revision改为0013，完成后恢复0014；最终完整迁移链必须在主Agent合并0014后重验。

新增nullable summary / summary_through_run_id，SQLite普通ADD/DROP COLUMN，无表重建；增加`runs(conversation_id,created_at,id)`索引。双库独立用例包含已有Run→Conversation引用、外键开启、升级→降级→再升级，以及原全套schema drift检查。

## 验证与成本

测试均使用conftest临时目录/数据库；未读取开发.env。PostgreSQL16仅15584，Redis仅16584。详细计数和清理状态在下方最终记录。

- 回归先在`git archive a8843a4`临时副本执行同一测试：首字deadline、429/慢上游fallback、正常标题/空标题/标题异常等6项失败；quota容错基线本来成功，不算红证据。实现后同组通过。
- 真正localhost TCP上游（非仅MockTransport）：429、首字永久等待、已输出后断流、工具片段后断流、流中idle、全部失败；真实API账本验证每次alias/backend/weight/call_id，标题另验证真实账本与64token请求。
- 200轮历史用例检查每次摘要请求和最终输入预算、摘要确被使用、第二次同历史不重新摘要；独立API用例检查200轮分页/CAS/权限。
- 单元标题覆盖纯文本、空输出、异常、quota不足，不污染用户delta，不改变成功回答。结算失败用例验证不继续fallback。
- Portal Node 11 tests和production build通过；无Portal/UI代码改动，未另跑浏览器E2E。
- [复现脚本](t14-model-context/query_plans.py)：合成10万行，SQLite/PG历史页p95 4.85/3.49ms，工作区0.09/2.69ms；两者均索引扫描，无额外sort。脚本含建库/种数总CPU2.29/3.51s、进程peak RSS46.9/57.6MB，不是API服务的RSS或生产容量承诺。
- 无新增依赖/后台任务。正常轮次多一次有界内部历史请求；超预算才增加付费摘要调用，每次保存一次摘要行；标题在首次成功回答额外一次小调用。每页历史prompt/reply/error各数据库侧截16000字符，摘要上限16000字符；摘要天然有损，超长单轮不会无限占内存。工作区未保存记录很多时仍可能向后扫描较多索引项，但只返回一行。

## 未覆盖边界与复用注意

- 未使用付费真实模型；token估计是按别名启发式+上游prompt_tokens校准，不承诺与所有供应商tokenizer精确一致。摘要事实质量与实际模型上下文窗口需外部验收。
- 未验真实NPU/GPU网关、跨实例长期并发、生产小机RSS；模型流仍在单次响应中累积内容/工具，不宣称输出大小与内存无关。
- T09 attempt栅栏/T07 checkpoint、UI标题CAS与新迁移链需主Agent集成复验。新摘要端点使用现有服务scope和run绑定语义，不新增公开Portal代理。
- TTFT在模型调用的finally记录，包含有输出后失败的调用；无输出失败没有样本。主Agent可提供指标常量后直接启用。
- 全局HANDOFF/status/current保留原样；集成时请记录：测试T14迁移不能提交空0014stub；Agent启动使用model-input，要同步needs_conversation_title契约；不能把静默摘要/标题绕过账本。

## 最终验收记录

验证代码基于a8843a4加本次完整实现；迁移验收仅临时使用0016→0013。正式提交已恢复0016→0014。

- 全套SQLite+Redis：742 passed / 43 skipped / 180.47s；PG16+Redis：770 passed / 15 skipped / 201.12s。跳过项为真实Docker/OSB/Keycloak/clamd等未配置外部gate，以及SQLite中的PG专用用例；没有把这些记为真实组件验收。
- 全套完成后新增bootstrap断言及显式摘要迁移升降级用例，再补验模型+迁移专项：SQLite16 passed / 1 skipped；PG17 passed。服务代码与全套通过版本一致。
- Ruff check、format check、git diff --check通过；Portal11 tests和production build通过。
- 原基线红证据6 failed / 1 passed / 8 deselected；失败包含429/慢上游fallback、首字deadline、正常标题及标题失败/空输出隔离的调用链要求。
- 无开发库/.env操作，无远端写入。临时Redis16584、PG15584停止，工作树保留供集成。

### T16 span接入与标题结算补充

隔离基线没有telemetry模块，按主Agent指令不添加兼容stub或修改metrics/obs。集成补hook：

- `AgentLoop._model_attempt`：reserve成功、model_call.set后，在try里用`with span("model.call", model=metrics.model_label(model)):`包裹`await self._interruptible(...llm.chat_stream(...))`（当前loop.py约404行）。这里model就是实际尝试别名；不要在外层_call_model包整个fallback链，避免混淆别名。
- `_run_tool`：try内约554行`await self._interruptible(run_id, execute_tool(...))`用`with span("agent.tool", tool=metrics.tool_label(call.name)):`包裹；个人技能_read_personal分支也可同样覆盖。
- 标题结算重试耗尽转RuntimeError，策略不会fallback/重试，标题层只跳过元数据并保存成功主回答。额外回归覆盖title settlement失败仍成功且总共仅两个调用。

最终迁移依赖恢复之后，纯Agent标题专项再验：5 passed / 11 deselected，含结算失败隔离；未启动API迁移，不掩盖缺0014的集成条件。

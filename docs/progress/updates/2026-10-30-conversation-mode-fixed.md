# 对话模式固定 API 合同（2026-10-30）

基线：`feat/frontend-genui`，HEAD及本地`origin/main`为`97793e601d90d0e9db11d60621c4f1352450166b`，叠加已有及并行未提交改动。本轮不提交、stash、reset、revert、fetch或部署；Portal、models与0038不修改；routes_inbox只改模式准入调用。

## 实现与合同

- `api/conversation_mode.py`：纯函数`fixed_conversation_mode(first_own_run, forked_from)`取首个自有Run的`options.mode`（旧空options按既有work默认），否则fork选择，否则None。DB解析/409在同模块适配函数；不写历史。
- `routes_runs.validate_run_input`统一解析及拒绝冲突（409 detail `{code: conversation_mode_fixed, message: 对话模式在开始后不能切换}`）；`start_run`覆盖会话新消息、队列dispatch/resume、编辑/重试/重新生成。版本以原Run模式为准，保留历史混合模式合同；技能/模板要求Work。
- `routes_conversations`消息入口以`model_fields_set`区分省略mode与显式work；`routes_inbox.enqueue`同样处理省略，steer继续继承叶节点模式；不改身份digest、lookup与写入逻辑。
- `routes_schedules.execute_task`每次创建新Conversation后调用`start_run`，故任务修改模式只影响下次新会话，已建会话仍固定。fork复制保留旧options，选择mode在新Conversation的forked_from中；Chat→Work仍允许。

## 查询成本

准入在现有用户锁内增加至多一个返回一行的只读查询（版本/steer直接用已加载轮次，无新增查询）：

```sql
SELECT * FROM runs
WHERE conversation_id = :id
  AND JSON_EXTRACT(result, '$.forked_from_run_id') IS NULL
ORDER BY created_at, id LIMIT 1;
```

SQLAlchemy使用JSON表达式适配SQLite/PostgreSQL。SQLite `EXPLAIN QUERY PLAN`回归确认使用`ix_runs_conversation_created_id`，无临时排序；最坏跳过fork复制的最多500轮，结果只加载一个Run。无新索引、迁移、后台任务或依赖。PostgreSQL实机EXPLAIN本轮未跑。

## 验证

- `tests/test_conversation_mode_fixed.py`：专项16项通过，含双向切换409、省略继承、技能/模板、队列入队/出队、fork Chat→Work与随后固定、edit/retry双模式、首个自有Run优先、SQLite索引计划、定时任务新会话配置模式。
- 禁用新增解析校验的临时pytest monkeypatch反证：13项行为回归全部失败（首次14项版本；另1项为纯函数/索引辅助检查）。临时插件/日志在仓库外，不提交。
- Ruff check/format全部通过（446 Python文件）。完整`.venv/bin/pytest tests -q -p no:cacheprovider`已运行一次：进度计数1359通过、158失败、144跳过、34错误；失败/错误共192个参数化用例。明确根因包括沙箱禁止本地端口与`ps`，及会话树测试的一处附带切换（已修正）。解除限制后仅选择失败函数/参数组和模式专项复核，**228 passed，81.02s**，包含全部192个原失败/错误用例对应函数及其参数组，退出码0；中间两次选择因带空格/动态PPTX字节参数ID不稳定而收集退出，后改为函数选择。未重复全套，因此没有声称最终基线已在无沙箱环境完整单次全绿。
- 三处旧测试附带切换已改：`test_modes_skills_attachments.test_mode_and_skill_are_stored_and_checked`在新会话选择技能，仍验证skill隐含work及存储；`test_chat_fork_retry.test_fork_copies_history_artifacts_usage_summary_and_independent_objects`第二轮由Chat改Work，保留原分叉/存储验收目标；`test_conversation_tree.test_edit_earlier_path_versions_switch_restore_and_fork`三轮统一Work，保留路径、版本、摘要与workspace/fork验收。

## E2E需调整（未修改、未运行）

- `platform/e2e/test_integration_features.py:109`，`test_fork_first_turn_and_work_only_menu`：先分支为Chat，再在分支内切换Work；应改为分支时选择Work。
- `platform/e2e/test_fork_mode_restore.py:37`，`test_fork_restores_its_mode_before_sending`：明确断言已开始分支仍可切换，需改为固定模式合同（chat/work × desktop/mobile）。

其余grep命中的模式选择发生在新会话或定时任务配置，不是既有会话内切换；页面移除控件后的定位器更新由Portal工程师处理。本轮不做Portal build/浏览器、外部Redis/PG、真模型或部署验收；不把API测试当作前端验收。最终Ruff check/format与`git diff --check`通过，HEAD保持不变。下一步用户审阅后端改动，并由前端工程师更新以上E2E。

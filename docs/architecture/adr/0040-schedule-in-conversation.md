# ADR 0040：定时任务绑定一个会话，到点在原会话接着回复
状态：已采纳（2026-11-11）。

相关：迁移 `0071_schedule_conversation`、[ADR 0030 前缀稳定](0030-prefix-stable-model-requests.md)。

## 背景

原定时任务每次到点都新建一个会话（标题带日期）。结果：每次结果散在不同会话，助手看不到上一次做了什么，用户也没法在同一处追问；模式（Chat/Work）由页面下拉框选，和任务从哪里来无关；“一次性提醒”做不到；对话里也不能让助手直接定时。

## 决定

1. **一个任务 = 一个会话。** `scheduled_tasks.conversation_id` 记录它的会话。对话里创建的任务绑定当前会话；页面新建的任务在第一次执行时创建会话（标题 = 任务名），之后每次都在这个会话的最新叶子后追加一轮。删除会话或其项目会删除绑定的任务（同事务、写审计）。
2. **模式取决于来源。** 对话里创建的任务沿用该会话的模式（会话模式由首轮固定）；页面新建一律 Work。`TaskInput` 不再接受 `mode`。
3. **到点那一轮是平台在说话。** Run 的 `options.schedule = {title, rule, at, manual?}`；Agent 构造请求时把 prompt 包成 `<scheduled_task>` 头（`contracts/schedules.py::scheduled_prompt`），说明是到点触发、用户可能不在、直接完成不要等确认；“立即运行”用另一句，避免模型误判为重复触发。历史中的旧轮次用同一函数渲染，保持前缀一致。Portal 用居中时间标记代替用户气泡。
4. **会话忙时顺延，不并发、不丢失。** 到点时会话有进行中的回答：记 `due_at`（原定时间），每分钟重试，空闲后立即执行，这一轮仍代表原定时间；超过 2 小时或到下一次时间仍忙则跳过。会话在等用户回复（waiting_user/interrupted）时直接跳过，不插话。
5. **保持前缀稳定。** 定时轮继承叶子轮的 `client`（新会话为 `portal`），模型/思考强度沿用会话，工具列表与会话自己的轮次一致。`schedule` 工具对 Chat 与 Work 都固定在列表里，不按是否有任务增减。
6. **对话内工具 `schedule`**（list/create/update/delete），经 `/internal/runs/{run_id}/schedules`、scope `schedules:write_for_run`、attempt 围栏和 `X-Delivery-Id` 幂等。只能操作本会话的任务；定时触发的那一轮不能再新建任务（防自繁殖）。list 返回 `now` 和时区，模型据此换算“20 分钟后”。
7. **一次性任务**（kind `once` + `date`）：执行后删除，释放名额（上限仍 5 条），结果留在会话里；启动失败则暂停待修，重新启用必须选未来时间。DST 空档向后顺延到第一个有效分钟。

## 后果

- 迁移 0071 加两列一索引，无外键（与 `last_conversation_id` 一致），旧任务 `conversation_id` 为 NULL，下次执行时建会话后绑定。
- 任务卡片、会话条（输入框上方）、页面列表三处展示同一份状态（`lib/schedules.ts`）。
- 未改：每用户 5 条上限、无人查看/连续失败自动暂停、ends_on、用户锁 + `next_run_at` CAS 的派发方式。

# 队列发送持久幂等修复

基线：`.worktrees/frontend-genui`，`feat/frontend-genui`，HEAD及本地`origin/main`均为`97793e601d90d0e9db11d60621c4f1352450166b`，在既有未提交集成改动之上验证。未fetch、提交、stash、reset、部署或修改Portal；其他工程师的并行Portal改动不属于本次验证。

## 行为与成本

- 0038新增`queued_messages.client_send_id`及`(user_id, client_send_id)`唯一索引。没有回填旧options JSON：旧值可能重复，且无原始请求摘要，历史旧发送不纳入新保证；新请求持久保存Pydantic验证后的请求摘要（含默认值、模型/模式、附件顺序、steer、技能与模板），不依赖随后变化的活动Run、目录默认值或可编辑队列正文。
- 同用户/会话/原请求在queued、started、steered、canceled状态均返回原行当前输出，沿用直接发送的**201**；检查先于空闲409、容量422、文件就绪/模型/额度准入。取消不会复活；PATCH后原请求仍重放当前行，新的请求正文复用同ID冲突。
- 不同请求或会话返回**409**。直接Run发送与队列发送双向互斥同用户ID，返回**409**，因为两个端点的输出合同不同；两入口共用现有用户锁。队列派发不把ID复制到Run（身份由原队列行持有）。唯一索引竞争发生IntegrityError时回滚，查出胜出行并按同一规则重放/冲突。
- fork显式不复制发送ID；取消保留身份，reaper随已删除会话清理行，无新增外键或清理顺序。会话删除后POST仍404，不能复活。`QueuedMessageOut`形状完全不变，Portal无需新增字段。
- 每次带ID的新发送增加至多两次唯一索引点查，重放一次队列点查；索引与最多64字符ID/64字符摘要有线性存储开销，无后台任务或新依赖。不扫描完整历史，不增加外部调用或长事务。

## 本轮验证

命令均在`platform`、Python3.12虚拟环境执行；先将Homebrew coreutils/findutils的gnubin加入PATH。原始输出仅保留在仓库外临时目录。

- `.venv/bin/ruff check .`和`.venv/bin/ruff format --check .`：通过，444文件格式正常。
- `.venv/bin/pytest tests/test_client_send_id.py tests/test_client_send_migration.py tests/test_migrations.py tests/test_inbox_queue.py -q`（最终调用另加`-o addopts= --junitxml=/private/tmp/queue-id-targeted.xml`以显示数量）：**68 passed / 23 skipped**。涵盖派发且Run结束后响应丢失重放、已应用插话及fork身份、各字段/会话冲突、满队列重放、取消不复活、6并发重复POST、强制模拟首查漏掉胜出行后真实唯一约束失败的回滚恢复、跨入口复用与reaper。
- 追加`tests/test_integration_migrations.py`后目标组合**69 passed / 24 skipped**；迁移链head及父版本检查从0037扩展至0038，保留完整升级/降级/再升级验证。
- SQLite0037→0038→0037→0038、旧JSON重复兼容、唯一约束、原有数据保留及模型漂移通过；EXPLAIN QUERY PLAN确认点查使用`uq_queued_messages_user_client_send`。
- 完整`.venv/bin/pytest tests -q -x -p no:cacheprovider`：首次沙箱内运行因`tests/contracts/test_queue_health.py::test_redis_health_ping_has_a_short_deadline`绑定localhost临时端口被拒绝而中止；解除沙箱限制后发现集成迁移测试硬编码head=0037，已按新增迁移更新严格链断言并通过目标组合；最终完整套件**1535 passed / 144 skipped，224.41s**，退出码0（最终运行另加仓库外`--junitxml`统计数量）。

未运行PostgreSQL（未设置测试URL、未启动实例），因此PostgreSQL迁移执行/EXPLAIN和多进程竞争未实测；迁移使用通用add_column/create_index/drop_index/drop_column，PG测试路径已保留。未运行Portal build/浏览器/真实模型或部署；本轮不改UI、不宣称端到端模型计费验收。下一步由用户审阅本地差异，不自动提交或推进其他GenUI工作。

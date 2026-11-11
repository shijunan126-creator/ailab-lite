# 搜索正确性复核（2026-11-11）

- 授权：保留现有会话级 FTS5 / tsvector + GIN 方案，修复 bug、更正存储记录；已追加授权提 PR、CI 通过后合并并增量部署。
- 代码基线：`fc7d540b` 加 `fix/search-correctness` 的本报告所述代码差异。PR #181 已合并，其历史验收不覆盖本轮；本轮最终提交与 CI 见 [PR #182](https://github.com/shijunan126-creator/ailab-lite/pull/182)。

## 根因与修复

- 索引使用 NFKC/casefold，正文 SQL 原来只 lower：全角 SERVICE、Straße、组合重音会漏搜。SQLite 预筛选使用相同归一化；PG16 对非 ASCII 内容保留候选，由 Python 作最终校验，无新扩展。高亮映射覆盖组合重音/Hangul 的原始范围。
- 原文预筛选命中隐藏 OpenUI 源码后，兜底分支仍会返回一条没有可见命中的结果。删除该兜底，只接受可见内容的精确命中；拉丁词边界也在最终校验和高亮时检查，防止另一轮的词项让 searching 冒充 search。
- 词项并集直接读取 ORM 缓存后覆盖，两个事务会丢掉对方新增的词。先幂等建行，再写锁内刷新并合并；SQLite 在读取前通过 INSERT 获取单写锁，PG 使用行锁；每事务只锁一次并在事务结束释放缓存。新会话尚未 flush 时从 session.new 查找，避免漏掉同事务 Run。
- 固定 2000 会话 / 200 Run 截断会跳过排在误候选或隐藏源码之后的真实命中。保留 50 会话 / 20 Run 分页，找到前 limit 个真实命中或候选耗尽才成功；5 秒协作式期限超时返回 503，禁止以空列表冒充完成。混合标题/正文凑足 limit 后即停止扫描。
- 基准脚本原来将物理主库与 WAL/SHM 相加，还把回答字符数称作 MB。现在分别输出 UTF-8 字节、逻辑页、搜索表/索引页、WAL/SHM 和前后增量；默认临时库/数据目录在退出时清理。
- ADR、原搜索记录、HANDOFF 和 PR #181 描述已更正；不保留关闭词位置的实施计划。GitHub 连接器写描述返回 403，已通过本机 GitHub CLI 完成更新，未发送讨论消息。

## 验证

- 修改前新增的 7 个复现用例均失败；修复后搜索回归涵盖上述路径、已存在/首次创建索引行的双线程竞争，以及超时错误。
- `ruff check .`、`ruff format --check .` 通过。
- 最终 `pytest tests/test_search.py -o addopts='' -q`：SQLite 24 passed；临时 PG16（UTF8、C locale）24 passed。
- 关联 `test_conversations.py test_chat_fork_retry.py test_run_state.py test_conversation_tree_migration.py`：SQLite 37 passed / 2 skipped（未设 PG URL），PG 配置下 39 passed。包含双库升级/降级、FTS/GIN 和 Run 索引的 EXPLAIN。
- 基准：`PYTHONPATH=. python scripts/bench_search.py --conversations 5000 --runs-per-conversation 20 --iterations 3`（SQLite）；临时 PG 为 2000 会话 / 10 Run，3 次查询取中位数。SQLite 回填 77.24s，稀有词 6.8ms、常用词 9.0ms、多词 14.1ms、deploy 19.9ms、标题 5.8ms；PG 回填 15.42s，对应 6.7 / 7.4 / 13.0 / 15.4 / 5.6ms。没有将新旧多关键词语义不同的结果当作召回一致。
- 默认小基准另验 UTF-8/索引专用统计与临时目录清理。存储数字仅维护在[搜索记录](2026-11-11-search-index.md)。

## 成本与未测边界

- 无迁移、依赖、配置、索引布局或 Portal 改动。写事务首次合并每个会话多一次幂等 INSERT 与锁定读取，后续合并复用事务缓存；锁会序列化同会话写入。查询仍分页读取，contains 不再为整个文本建立高亮偏移数组。
- PG16 的非 ASCII 预筛选较宽，中文为主的长会话可能多读 Run；极端旧词项积累/隐藏源码可触发明确超时。协作式期限不能中断单条正在执行的 SQL，未测百万 Run、真实库副本、持续写入或整栈 CPU/RSS。
- 无可索引词项的输入仍只检查最近 200 会话；升级回填未完成时仍可能漏旧会话，均为原方案已知边界。未新增重建任务。
- 按仓库约定不重复本地全套；本地记录不代替最终 PR 的 CI；Portal/浏览器未重跑（未改 UI）。下一步等待最终 head CI，通过后合并并按用户授权增量部署；部署不额外备份或重复功能验收。

# 搜索正确性复核（2026-11-11）

- 授权：保留现有会话级 FTS5 / tsvector + GIN 方案，修复 bug、更正存储记录；已追加授权提 PR、CI 通过后合并并增量部署。
- 代码基线：`fc7d540b` 加本报告所述代码差异，最终代码提交 `cf4567a5880ca1f7da4d1d657e94d43cccd1c9f3`；[PR #182](https://github.com/shijunan126-creator/ailab-lite/pull/182) 已 squash 合并为 `b8fe90b2ee5ad3f7f1afca6cde3ae0de48860f32`，合并树与测试树一致。PR #181 的历史验收不覆盖本轮。

## 根因与修复

- 索引使用 NFKC/casefold，正文 SQL 原来只 lower：全角 SERVICE、Straße、组合重音会漏搜。SQLite 预筛选使用相同归一化；PG16 对非 ASCII 内容保留候选，由 Python 作最终校验，无新扩展。高亮映射覆盖组合重音/Hangul 的原始范围。
- 原文预筛选命中隐藏 OpenUI 源码后，兜底分支仍会返回一条没有可见命中的结果。删除该兜底，只接受可见内容的精确命中；拉丁词边界也在最终校验和高亮时检查，防止另一轮的词项让 searching 冒充 search。
- 词项并集直接读取 ORM 缓存后覆盖，两个事务会丢掉对方新增的词。先幂等建行，再写锁内刷新并合并；SQLite 在读取前通过 INSERT 获取单写锁，PG 使用行锁；每事务只锁一次并在事务结束释放缓存。新会话尚未 flush 时从 session.new 查找，避免漏掉同事务 Run。
- 固定 2000 会话 / 200 Run 截断会跳过排在误候选或隐藏源码之后的真实命中。保留 50 会话 / 20 Run 分页，找到前 limit 个真实命中或候选耗尽才成功；5 秒协作式期限超时返回 503，禁止以空列表冒充完成。混合标题/正文凑足 limit 后即停止扫描。
- 基准脚本原来将物理主库与 WAL/SHM 相加，还把回答字符数称作 MB。现在分别输出 UTF-8 字节、逻辑页、搜索表/索引页、WAL/SHM 和前后增量；默认临时库/数据目录在退出时清理。
- CI 暴露空会话候选的 N+1：2001 个只有标题的误候选原来逐个查询 Run，在并行负载下耗尽期限。现在每页先用相关 EXISTS 和 Run 索引批量探测，跳过空会话的正文查询；标题已命中时免探测。该场景涉及 Run 的 SQL 不超过 42 次（41 页探测 + 1 个真实会话），代替两千多次往返。
- ADR、原搜索记录、HANDOFF 和 PR #181 描述已更正；不保留关闭词位置的实施计划。GitHub 连接器写描述返回 403，已通过本机 GitHub CLI 完成更新，未发送讨论消息。

## 验证

- 修改前新增的 7 个复现用例均失败；修复后搜索回归涵盖上述路径、已存在/首次创建索引行的双线程竞争，以及超时错误。
- `ruff check .`、`ruff format --check .` 通过。
- 最终 `pytest tests/test_search.py -o addopts='' -q`：SQLite 24 passed；临时 PG16（UTF8、C locale）24 passed。
- 关联 `test_conversations.py test_chat_fork_retry.py test_run_state.py test_conversation_tree_migration.py`：SQLite 37 passed / 2 skipped（未设 PG URL），PG 配置下 39 passed。包含双库升级/降级、FTS/GIN 和 Run 索引的 EXPLAIN。
- 初轮代码基准（CI 后批量 EXISTS 修复前，不当作最终代码时延）：`PYTHONPATH=. python scripts/bench_search.py --conversations 5000 --runs-per-conversation 20 --iterations 3`（SQLite）；临时 PG 为 2000 会话 / 10 Run，3 次查询取中位数。SQLite 回填 77.24s，稀有词 6.8ms、常用词 9.0ms、多词 14.1ms、deploy 19.9ms、标题 5.8ms；PG 回填 15.42s，对应 6.7 / 7.4 / 13.0 / 15.4 / 5.6ms。没有将新旧多关键词语义不同的结果当作召回一致。
- 默认小基准另验 UTF-8/索引专用统计与临时目录清理。存储数字仅维护在[搜索记录](2026-11-11-search-index.md)。

### 首轮 CI 与性能回归修复

- `83af582` 的 [CI run](https://github.com/shijunan126-creator/ailab-lite/actions/runs/42954188041)：PG 1 failed / 2938 passed / 86 skipped，唯一失败为上述 2001 空会话回归，搜索返回 503（5031ms）；汇总 CI 因该失败失败。SQLite、quality、3 个 E2E 分片及其他作业通过，不将这一轮记为全绿。
- 查询次数回归额外模拟每次数据库往返消耗 5ms，以独立单调时钟推进，不实际 sleep，也不修改产品的 5 秒期限。对旧 `_indexed_search` 实现确定性复现 503；修复后仍要求找回最旧真实命中且 Run SQL ≤42 次。
- 新增生产 EXISTS 查询的双库 EXPLAIN 断言，要求使用 Run 的 conversation 索引。最终检查结果以本 PR 最新 head 为准，未重跑本地全套。
- 修复后 `pytest tests/test_search.py tests/test_conversation_tree_migration.py -o addopts='' -q`：SQLite 26 passed / 2 skipped，临时 PG16 配置下 28 passed；Ruff check/format 通过。包含不放宽期限的确定性往返回归及新增 EXISTS 查询计划检查。
- 第二轮 `727cc9e` 的 [PG job](https://github.com/shijunan126-creator/ailab-lite/actions/runs/42955812595/job/130310561122)：搜索实际返回 200 / 275ms，但引擎级计数器把后台任务查询也计入，断言为 44 >42。修正测试采样范围：仅计带请求专用 execution option 的连接，在请求外连接主动查 Run 并断言不会污染计数；保留 ≤42 上限、真实命中和原期限断言，不改产品代码。
- 采样修复后搜索回归 SQLite / PG 各 24 passed；补上计数必须非零的约束后，该用例双库各 1 passed，避免未采样导致虚假通过。Ruff check/format 通过；未重复产品压测或全套。
- 第三轮 `023953d` 的 [SQLite job](https://github.com/shijunan126-creator/ailab-lite/actions/runs/42956938030/job/130314206520) 在既有 mock 沙箱取消合同失败：子进程已创建但尚未登记时，cancel 可返回成功而漏杀。新增真实进程门控复现，在 Popen 返回前暂停，旧实现确定性失败；修复为同锁内创建并登记，communicate 期间释放锁。该修复只涉及 dev/test mock provider，无迁移或运行配置变化。
- 为复核第三轮 CI 失败，本轮例外执行本地完整套件（4 个 xdist worker、真实临时 Redis/PG、构建固定 Office reader、macOS GNU 工具路径）。SQLite 2822 passed / 204 skipped，126.89s；跳过数与 CI 环境不同，不替代最终 CI。最初缺 GNU 工具路径和 reader 的本地启动已中断并补齐环境，不将该次记为产品验收。
- 同一最终运行代码基线（`023953df` 加本段 mock 取消原子化修复），PostgreSQL 全套 2956 passed / 70 skipped，185.86s；SQLite 和 PG 均覆盖真实进程的新增门控回归及原取消合同。完整套件命令为 `pytest tests -n 4 --dist worksteal -o addopts='' -q --tb=short`，PG 使用专用可清空测试库，Redis 专用端口；临时数据与本轮 reader 构建产物在推送前清理。
- 最终 [CI run 42959458024](https://github.com/shijunan126-creator/ailab-lite/actions/runs/42959458024) 在 `cf4567a` 的 11 个作业全部通过，包括完整 SQLite/PostgreSQL、quality、三片 E2E、存储合同等；无评审或评论待处理后才合并。合并后 `git diff --quiet cf4567a origin/main` 通过；本段之后只更新连续性交接，不修改运行代码。

## 成本与未测边界

- 无迁移、依赖、配置、索引布局或 Portal 改动。写事务首次合并每个会话多一次幂等 INSERT 与锁定读取，后续合并复用事务缓存；锁会序列化同会话写入。查询仍分页读取，contains 不再为整个文本建立高亮偏移数组。
- PG16 的非 ASCII 预筛选较宽，中文为主的长会话可能多读 Run；极端旧词项积累/隐藏源码可触发明确超时。协作式期限不能中断单条正在执行的 SQL，未测百万 Run、真实库副本、持续写入或整栈 CPU/RSS。
- 无可索引词项的输入仍只检查最近 200 会话；升级回填未完成时仍可能漏旧会话，均为原方案已知边界。未新增重建任务。
- 本地完整套件仅为复核 CI 故障而例外执行；最终 PR 的 CI 已通过，三片 E2E 也覆盖浏览器。本机部署按用户授权增量执行，不额外备份或重复功能验收；结果仅在对话汇报。后续按实际使用反馈推进。

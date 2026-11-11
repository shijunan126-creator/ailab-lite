# 全量对话搜索：去掉继续搜索，会话级索引与性能对比（2026-11-11）

- 分支 `feat/search-index`，base `origin/main@01c1d16a`；工作树 `.worktrees/search-optimization`；[PR #181](https://github.com/shijunan126-creator/ailab-lite/pull/181)。未部署。
- 用户要求：去掉“继续搜索”的 UI 和逻辑，一次搜完；提升精准度和性能，在对话很多时也快；做性能对比测试；UI/UX 做到最好。
- 决定与边界见 [ADR 0039](../../architecture/adr/0039-global-search-index.md)。

## 做了什么

- `/v1/search` 不再有 `next_cursor`，一次返回整个账号的前 `limit` 条；Portal 删掉续查循环和“继续搜索”按钮，输入即搜（120ms 防抖、旧请求中止、10s 超时）。
- 新增会话级 `conversation_search` 词项并集：SQLite FTS5、PostgreSQL 存储 `terms_tsv` + GIN。查询先用索引选候选会话，再只扫这些会话的 Run；标题命中直接返回，不再扫正文。
- 查询语义：多个空白分隔的关键词全部命中（可跨问题与回答），中文 bigram 保持字面子串，拉丁文按词匹配；标题命中优先于正文，同一会话内 prompt 优先于 answer。
- 索引在同一事务内由 ORM 钩子维护；迁移 0070 不回填，启动后的水位任务分批补齐旧库。单字查询走最多 200 个最近会话的有界回退。
- UI：标题命中直接高亮标题；结果区显示多关键词高亮；加载中保留固定大小的 spinner；移动端打开搜索时自动收起侧栏（浏览器实测截图见下）。

## 性能对比

脚本：`platform/scripts/bench_search.py`。旧路径是索引前实现的冻结副本（含游标循环），两列使用同一批数据、同一 `limit=20`。每项取 3 次的中位数；旧路径会一直续查到没有游标或凑满 20 条，而旧 UI 实际最多只自动续 6 页。

### SQLite

撤回原先“索引为原文 9 倍”的结论。合并基线 `fc7d540b` 上的[本轮搜索修复](2026-11-11-search-correctness.md)复测同一种子、5,000 会话 / 100,000 Run：prompt 1,600 万字符 / 44.75 MiB UTF-8，answer 约 1.2 亿字符 / 335.95 MiB UTF-8，合计 380.70 MiB 原文（未含标题）。搜索回填前逻辑主库 517.44 MiB，回填后 553.09 MiB，增加 35.65 MiB（6.89%）。`dbstat` 搜索相关表/索引合计 35.68 MiB（含回填前已有的空表页）。

WAL 前后均为 520.71 MiB，SHM 均为 1.03 MiB。最初只量物理主库文件得到的 552.57 MiB / +6.8%，少计 WAL 中未 checkpoint 的约 0.52 MiB；最终口径采用 `page_count × page_size` 的逻辑主库页，WAL/SHM 分列。下表保留历史性能，体积列是旧的主库+WAL/SHM 合计，不是搜索索引；“120MB 文本”实际是回答字符数，不能用作字节分母。

临时库原型：在 100,000 Run 上，合并后的 FTS 数据页由默认位置索引的 12.23 MiB 降至 `detail=none,columnsize=0` 的 3.21 MiB，普通查询时延相近；默认 tokenizer 对含下划线的查询报错。另用 500 会话验证将下划线纳入词字符后，标识符、中文、多词 AND、稀有词和无命中查询的候选一致。未改运行代码；真实语料、持续写入和升级迁移未验证。

| 数据 | 索引构建 | 主库+WAL/SHM（历史 MiB） | 查询 | 新 | 旧 | 命中（新/旧） |
|---|---:|---:|---|---:|---:|---|
| 2000 会话 / 20k Run / 2400万 answer 字符 | 12.5s | 224.6 | 最旧会话的稀有词 | 3.0ms | 2100.9ms | 1 / 1 |
| | | | 两字常用词 `模型` | 8.7ms | 12.8ms | 20 / 20 |
| | | | 两关键词 `部署 评估` | 11.1ms | 2106.5ms | 20 / 0 |
| | | | `deploy` | 13.7ms | 13.7ms | 20 / 20 |
| | | | 标题标记 | 2.3ms | 2144.2ms | 1 / 1 |
| 5000 会话 / 100k Run / 1.2亿 answer 字符 | 77.2s | 1074.3 | 最旧会话的稀有词 | 7.0ms | 6362.2ms | 1 / 1 |
| | | | `模型` | 12.9ms | 12.6ms | 20 / 20 |
| | | | `部署 评估` | 16.9ms | 6445.6ms | 20 / 0 |
| | | | `deploy` | 18.7ms | 13.7ms | 20 / 20 |
| | | | 标题标记 | 5.9ms | 6485.4ms | 1 / 1 |

- 单关键词查询的新结果与旧全量扫描**顺序完全一致**（抽查 `模型`、稀有词、标题标记、`deploy`）。
- 两关键词查询旧路径为 0 命中，是因为旧语义要求整段 query 作为一个字面子串；新语义要求每个关键词都出现，这是精准度/召回的有意变化。
- 常用词和 `deploy` 旧路径本来就能在第一页凑满 20 条，所以耗时接近；稀有/较早命中旧路径要扫完整库，差距 300–1100 倍。

### PostgreSQL 16（本机 throwaway 实例）

2000 会话 / 20k Run / 2400万 answer 字符：索引构建 17.2s，整库 97.7 MiB（未单独量搜索增量）。

| 查询 | 新 | 旧 |
|---|---:|---:|
| 最旧会话的稀有词 | 7.3ms | 2828.1ms |
| `模型` | 12.0ms | 14.9ms |
| `部署 评估` | 14.6ms | 2869.2ms（旧 0 命中） |
| `deploy` | 30.7ms | 18.7ms |
| 标题标记 | 10.9ms | 2797.1ms |

PostgreSQL 最初直接用表达式 `to_tsvector('simple', terms)`，规划器在小表上选了 Seq Scan，稀有词要 480ms；改成触发器维护的 `terms_tsv` 存储列 + GIN 后降到 7.3ms。

本轮修复工作树在相同 PG 合成规模另测：UTF-8 prompt+answer 76.14 MiB；整库由 64.18 MiB 增至 97.39 MiB，增加 33.21 MiB（51.75%）；搜索相关关系含索引/TOAST 合计 33.26 MiB。这是 PostgreSQL 的 20k Run 数据，不能套用 SQLite 100k Run 的 6.89%。本轮按用户决定保留两库索引结构。

## 原 PR #181 验证（历史）

下列结果与边界属于原 PR 合并版本；本轮修复的当前验收见[正确性复核](2026-11-11-search-correctness.md)。

- `ruff check .`、`ruff format --check .` 通过（689 files）。
- 完整 SQLite 套件：3013 项收集，`pytest tests -q -n auto` exit 0。
- 完整 PostgreSQL + Redis 套件：`AILAB_TEST_DATABASE_URL`/`AILAB_TEST_POSTGRES_URL`/`AILAB_TEST_REDIS_URL` 指向本机 throwaway 实例，`pytest tests -q -n auto` exit 0。
- 迁移：`tests/test_migrations.py`、`test_integration_migrations.py`、`test_shares_migration.py`、`test_templates_migration.py`、`test_conversation_tree_migration.py` 在 SQLite 与 PostgreSQL 均通过；`test_migrations_and_models_do_not_drift` 过滤 FTS5 虚表和 GIN 表达式索引（索引实现，不是 ORM 表）。
- 查询计划：SQLite `EXPLAIN QUERY PLAN` 使用 `conversation_search_fts` 的 `MATERIALIZED` CTE；PostgreSQL 使用 `ix_conversation_search_terms`（GIN）和 `ix_runs_conversation_created_id`。
- Portal：`npm test` 282 项通过；`npm run build` 通过。
- 浏览器：`AILAB_E2E=1 pytest e2e/test_single_node_review.py -k search` 3 项通过（桌面/手机标题高亮、单次请求、输入新查询清空旧结果）。手机截图确认打开搜索后侧栏已收起，无横向溢出。

## 没覆盖到 / 风险

- 单字查询只覆盖最近 200 个会话；两字及以上才走全量索引。
- 拉丁文按词匹配，`search` 不命中 `searching`；中文 bigram 仍保持字面子串。
- 升级已有大库后，后台回填完成前旧会话可能暂时搜不到；新写入立即生效。回填速度约 2000 会话/17s（PG）、5000 会话/77s（SQLite），未在真实生产库副本上计时。
- SQLite 搜索在 100k Run 合成语料上增加约 35.65 MiB 逻辑主库页（6.89%）；未测真实生产语料和 100 万 Run。磁盘峰值还取决于 WAL 和导入事务。
- 旧词项只增不减；频繁编辑后候选会变多。原实现的 2000 会话截断及其漏搜已在[本轮修复](2026-11-11-search-correctness.md)；未做长期重建任务。
- 未在本机部署；PR #181 最终 head CI 全绿，合并后清理工作树和本地基准库。

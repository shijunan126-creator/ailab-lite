# 全量对话搜索：去掉继续搜索，会话级索引与性能对比（2026-11-11）

- 分支 `feat/search-index`，base `origin/main@323184b1`；工作树 `.worktrees/search-optimization`。未部署。
- 用户要求：去掉“继续搜索”的 UI 和逻辑，一次搜完；提升精准度和性能，在对话很多时也快；做性能对比测试；UI/UX 做到最好。
- 决定与边界见 [ADR 0039](../architecture/adr/0039-global-search-index.md)。

## 做了什么

- `/v1/search` 不再有 `next_cursor`，一次返回整个账号的前 `limit` 条；Portal 删掉续查循环和“继续搜索”按钮，输入即搜（120ms 防抖、旧请求中止、10s 超时）。
- 新增会话级 `conversation_search` 词项并集：SQLite FTS5、PostgreSQL 存储 `terms_tsv` + GIN。查询先用索引选候选会话，再只扫这些会话的 Run；标题命中直接返回，不再扫正文。
- 查询语义：多个空白分隔的关键词全部命中（可跨问题与回答），中文 bigram 保持字面子串，拉丁文按词匹配；标题命中优先于正文，同一会话内 prompt 优先于 answer。
- 索引在同一事务内由 ORM 钩子维护；迁移 0070 不回填，启动后的水位任务分批补齐旧库。单字查询走最多 200 个最近会话的有界回退。
- UI：标题命中直接高亮标题；结果区显示多关键词高亮；加载中保留固定大小的 spinner；移动端打开搜索时自动收起侧栏（浏览器实测截图见下）。

## 性能对比

脚本：`platform/scripts/bench_search.py`。旧路径是索引前实现的冻结副本（含游标循环），两列使用同一批数据、同一 `limit=20`。每项取 3 次的中位数；旧路径会一直续查到没有游标或凑满 20 条，而旧 UI 实际最多只自动续 6 页。

### SQLite

| 数据 | 索引构建 | 数据库 | 查询 | 新 | 旧 | 命中（新/旧） |
|---|---:|---:|---|---:|---:|---|
| 2000 会话 / 20k Run / 24MB 文本 | 12.5s | 224.6MB | 最旧会话的稀有词 | 3.0ms | 2100.9ms | 1 / 1 |
| | | | 两字常用词 `模型` | 8.7ms | 12.8ms | 20 / 20 |
| | | | 两关键词 `部署 评估` | 11.1ms | 2106.5ms | 20 / 0 |
| | | | `deploy` | 13.7ms | 13.7ms | 20 / 20 |
| | | | 标题标记 | 2.3ms | 2144.2ms | 1 / 1 |
| 5000 会话 / 100k Run / 120MB 文本 | 77.2s | 1074.3MB | 最旧会话的稀有词 | 7.0ms | 6362.2ms | 1 / 1 |
| | | | `模型` | 12.9ms | 12.6ms | 20 / 20 |
| | | | `部署 评估` | 16.9ms | 6445.6ms | 20 / 0 |
| | | | `deploy` | 18.7ms | 13.7ms | 20 / 20 |
| | | | 标题标记 | 5.9ms | 6485.4ms | 1 / 1 |

- 单关键词查询的新结果与旧全量扫描**顺序完全一致**（抽查 `模型`、稀有词、标题标记、`deploy`）。
- 两关键词查询旧路径为 0 命中，是因为旧语义要求整段 query 作为一个字面子串；新语义要求每个关键词都出现，这是精准度/召回的有意变化。
- 常用词和 `deploy` 旧路径本来就能在第一页凑满 20 条，所以耗时接近；稀有/较早命中旧路径要扫完整库，差距 300–1100 倍。

### PostgreSQL 16（本机 throwaway 实例）

2000 会话 / 20k Run / 24MB 文本：索引构建 17.2s，数据库 97.7MB。

| 查询 | 新 | 旧 |
|---|---:|---:|
| 最旧会话的稀有词 | 7.3ms | 2828.1ms |
| `模型` | 12.0ms | 14.9ms |
| `部署 评估` | 14.6ms | 2869.2ms（旧 0 命中） |
| `deploy` | 30.7ms | 18.7ms |
| 标题标记 | 10.9ms | 2797.1ms |

PostgreSQL 最初直接用表达式 `to_tsvector('simple', terms)`，规划器在小表上选了 Seq Scan，稀有词要 480ms；改成触发器维护的 `terms_tsv` 存储列 + GIN 后降到 7.3ms。

## 验证

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
- 索引体积约为 SQLite 原文的 9 倍；40GiB lite 卷可容纳 100k Run 量级，但这是明确成本。未测 100 万 Run。
- 旧词项只增不减；频繁编辑后候选会变多，当前用 2000 会话预算兜底，未做长期重建任务。
- 未在本机部署；PR CI 结果才是最终验收。

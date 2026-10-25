# main CI 索引计划回归修复（2026-10-25）

基线 `d7894797cf5c6e13fd26c477bb3dc7da4544a605`；分支 `fix/template-query-plan-ci`。用户授权修复、验证、PR 和合并；仅测试/文档，不改产品查询、索引、迁移或个人部署。

## 问题与修复

- [main CI 40191406645](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40191406645)：PostgreSQL `test_template_and_slides_queries_use_indexes` 要求 `ix_files_slides`，实际为 `ix_files_user_id` + Sort，1失败/1334通过/52跳过；其他执行套件成功。PR #72 当时只有范围门禁通过，产品套件跳过；不能混称全套通过。
- 原测试在共享库查询不存在的用户，数据量/分布依赖测试顺序，并设置 `enable_seqscan=off`。这种计划不是性能退化的充分证据。
- 检查移到迁移套件的独立 SQLite/PG 引擎，通过真实迁移建表；固定10000文件（500幻灯片）及5000模板（200未删除），更新两表统计，移除优化器开关。四条查询都必须有结果、专用索引必须存在，执行计划仍须命中预期索引；文件夹查询保留对既有 `ix_files_folder_listing` 的合理兼容。
- 生产代码/依赖/后台任务无变化，生产 CPU/RSS/磁盘/延迟无新增开销；测试临时写入15000行并随测试数据库清理，不做墙钟耗时阈值断言。

## 验证

- 双库专项已通过。PostgreSQL 四条查询均为专用 index-only scan，文件列表 backward scan，文件夹 GroupAggregate；SQLite 使用模板/幻灯片索引及兼容的文件夹索引。计划不代表所有生产数据分布的容量证明。
- 本地完整 SQLite 与 PostgreSQL16+Redis 套件均退出0，外部 OpenSandbox/Docker/Office/OTEL 等未配置项按既有条件跳过。两套完整运行使用固定数据/正常优化器的修复版本；随后去掉索引反射告警并增加非空结果检查，最终双库专项再通过。Ruff check/format365文件、Portal production build成功。
- 反向验证：临时 `DROP INDEX ix_files_slides` 后最终专项在两种数据库都失败，恢复代码后两种数据库都通过。没有通过删断言、放开全部索引或重跑旧失败冒充修复。
- 未调用付费模型、未改生产数据库；手机交互和文件摘要竞态不属于本轮修复。PR及合并后main检查待远端结果。

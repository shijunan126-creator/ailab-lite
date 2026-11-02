# 生命周期测试隔离（2026-11-02）

## 范围与基线

仅修复 `test_render_standby.py` 与 `test_lifecycle_assets.py` 的两个顺序相关测试；不改产品、阈值、索引、查询或依赖，不加重试/跳过。分支 `t3/fix-suite-test-flakes`，进入代码基线 `c033664c4d9d5837283193f44fbd9ce2ede306e9`，远端 `origin` 未 fetch。实际基线不包含办公整合分支的文档；从其工作树只读核对原失败记录，未切分支或修改该工作树。本轮只本地提交，不 push、PR、部署。

## 根因与复现

- **内存**：两个文件初次运行均通过；带前序完整套件的诊断运行也没有再次触发目标失败。随后在同一 CPython 3.12.11、未修改的旧测试前保留 137200 个动态 intern 字符串，重现约 7.38 MiB 的全局增长与原 6 MiB 断言失败。快照把其中 **7509 KiB、1 笔分配** 定位到 `pathlib.py:404` 的 `sys.intern`，而不是租约记录。旧表在跟踪开始前分配，扩容后的新表全额计入，释放旧表不会抵扣；前序测试改变表容量/扩容时机。该路径没有发现记录/句柄/回调留存证据。
- **计划**：原 PostgreSQL 文件独立运行和连续两次运行通过。对空表执行 ANALYZE 后，同一原 SQL 加原 `enable_seqscan=off`，规划器选择 `files_pkey`，原精确名称断言立即失败。原整合日志则是反关联索引名称断言失败，但没有保存完整计划，不能臆测其具体替代索引。共享数据、统计信息和 autoanalyze 时机使原断言不成立；没有证据要求新增产品索引。

## 修复与反例检查

- 内存先执行 10 次完整 create/adopt/finish/release，再 GC 并比较分配位置在 Broker/adapters/domain/sandbox 和测试替身的快照。保留 6 MiB 总量、5 MiB 后半程门限；不用 `all_frames=True`，避免把被调用的全局 intern 表重新算入。新增 1000 个句柄和准备回调的弱引用失效断言，补查 `_renewed`、`_inflight`、provider 进程表，并关闭 manager。相同 intern 压力下修复测试通过：相关净增长 **185112 bytes**，后半程 **88788 bytes**（含测试弱引用记账）。
- 计划使用已有隔离迁移 fixture，SQLite/PostgreSQL 各新建测试库；种入 10000 个背景文件及对应模板/引用、40 个候选文件。ANALYZE 后检查带实际 ORDER BY/LIMIT 的查询。SQLite 要求相应列的 SEARCH，PostgreSQL 用 JSON EXPLAIN ANALYZE 检查带 purpose/file_id 条件的索引访问和返回数；允许等价索引，删除强制规划器选项。
- 仓库外删除索引对照：分别删除三种索引，PostgreSQL **3/3** 被新断言检出；SQLite 文件/模板索引 **2/2** 被检出，引用索引删除后仍有唯一索引的 file_id skip-scan，因此合理通过。不是仅把固定名称换成“计划非空”。
- 产品运行时开销为零。测试新增约 30000 条背景行，分批 1000 条插入；无墙钟性能门限、常驻任务或新依赖。

## 验证

最终测试代码提交为 `ff3804ba073eec504a4d5528426e9adb893ac965`，后续仅更新交接文档。Python 使用用户 cache 中既有 Python 3.12 测试 venv，GNU coreutils/findutils PATH；先构建本地 Office reader。最初两次完整诊断运行各有 16 项 reader 前置构建缺失相关失败，目标两项通过；补齐构建后进行最终验收，不把这些诊断运行写成全绿。

检查命令：`python -m pytest tests -o addopts='' -q`；双库运行增加下列环境配置。Ruff 为 `ruff check .` / `ruff format --check .`，Portal 为 `npm ci --no-audit --no-fund && npm run build`。重复文件验证在每种后端各执行三轮：先分别运行两个受影响文件，再按下列顺序整组运行三轮（均为明确的稳定性验证，不是失败后自动重试）：

```text
tests/test_library_render.py tests/test_library_summaries.py
tests/test_lifecycle_assets.py tests/test_reaper.py
tests/test_render_sandbox_health.py tests/test_render_standby.py
```

| 检查 | 结果 |
| --- | --- |
| SQLite：两个文件各自独立三轮 | 75 passed / 3 skipped |
| SQLite：带前序六文件、按套件顺序三轮 | 162 passed / 3 skipped |
| PostgreSQL/Redis：两个文件各自独立三轮 | 78 passed / 0 skipped |
| PostgreSQL/Redis：带前序六文件、按套件顺序三轮 | 165 passed / 0 skipped |
| 最终 SQLite 全套 | 1985 passed / 165 skipped / 0 failed，561.92s |
| 最终 PostgreSQL/Redis 全套 | 2094 passed / 56 skipped / 0 failed，627.29s |
| Ruff 全平台 | check / format --check 通过，513 files |
| Portal build | 通过 |

重复验证合计 **480 passed / 6 skipped / 0 failed**。6 skip 均为默认 SQLite 配置未提供 PostgreSQL URL 时，既有跨库 migration fixture 的 PostgreSQL 参数；配置完整的 PostgreSQL 轮次没有跳过受影响测试。没有新增 skip 条件。

临时 PostgreSQL 16 用独立数据目录、`initdb -A trust -U ailab -E UTF8 --no-locale`，监听 127.0.0.1:15583；Redis 监听 16583、关闭持久化。各并发验证组分别用独立 database，未使用开发库或操作既有服务。PostgreSQL 验证环境变量为 `AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15583/ailab_test`、`AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15583/ailab_migrations`、`AILAB_TEST_REDIS_URL=redis://127.0.0.1:16583/15`；重复文件组使用另外的 `ailab_verify` / `ailab_verify_migrations`。PG/Redis 已停止，15583/16583 连接检查无监听；临时数据、原始诊断/JUnit 和复现脚本在结论提取后清理，未提交运行输出。

## 边界与下一步

本任务不改变产品能力状态，因此不更新能力矩阵或推进路线图。未跑 E2E、真实模型、浏览器或部署验收；没有 UI/请求构造改动。交回主代理审查并选择集成；办公分支的最终合并树仍需其集成验证，不能把本分支基线当成办公整合 HEAD。

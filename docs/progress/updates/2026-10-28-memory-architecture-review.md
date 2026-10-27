# 记忆架构审查交接

用户本轮授权：拉取最新 main，对照上传的 ChatGPT-like 记忆架构审查覆盖度与逻辑 bug。仅审查，不自动修复或发布。

- 基线 `origin/main@0659101142d972e7b28b6b282ec4a2d21aee37fc`；独立工作树 `/private/tmp/ailab-memory-review-20261028`，分支 `review/memory-architecture-20261028`。原目录 `fix/full-thinking-rendering@ab0e8d9` 的脏文档和 `.DS_Store` 保留。
- 结论：主要层次已有，附件 DoD 未满足；9 类已复现问题（7 类 P1、2 类 P2），集中在模型可见性/自动写入开关、进度/冲突原子性、来源/删除谱系和 SQLite 长写事务。完整范围、源码定位与修复方向见[审查报告](../../reviews/2026-10-28-memory-architecture-review.md)。
- 当前检查：既有 65 项记忆测试通过；13 个审查探针中 12 失败、1 对照通过，对应上述 9 类缺陷，不代表修复完成。模型返回与 embedding 使用替身，SQLite/API/纯函数是真实执行；提交竞态只复现 apply 接缝状态交错，未验真实 PostgreSQL 并发。证据见[最终输出](../../reviews/memory-architecture-20261028/probes.txt)。
- Ruff check/format覆盖platform及审查探针通过（387文件）；`git diff --check`通过。文档链接与原主目录保护状态在交付前复核。
- 未跑全 Python、PG/Redis、Portal build/E2E、真实模型和目标机性能；无产品源码/UI变化。测试夹具服务已退出，无本轮部署、模型进程或专用数据库服务；保留 review 证据、隔离 venv 和夹具临时文件。
- 旧 current 中“PR96 待评审”已经被实际 main 的 `7ad25a1` 取代，`0659101` 还包含 PR97。只据此确认合并代码，不推断个人部署状态；其他历史暂停保持。

下一步：用户审阅报告后决定修复范围；若授权，先统一可见性/资格/短事务，再处理进度和来源，不自动启动附件所有缺失功能。此工作树仅本地文档和复现材料，无提交、push、PR、merge、deploy。

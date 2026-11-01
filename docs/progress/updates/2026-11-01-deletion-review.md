# 后端删除正确性审查

基线：`fix/deletion-review`，base `origin/main@17aa2ef`。仅本地后端修复/提交，不 push、部署或修改 Portal。

第一组修复：来源删除取消 waiting_user/interrupted；资料库工具在取得用户锁后重新检查文件/会话；来源 GC 使用用户锁；purged 文件的迟到 file 任务 claim 不再访问 None；孤儿 GC 不再把 purged tombstone 当活文件。第一组无迁移、依赖或新开关。新增回归覆盖这些边界，原基线红测确认。

第二组：历史输入在 purge 后继续返回“文件已删除”；Artifact FK 删除前以 INSERT SELECT 保存无正文身份到现有 MessageFileRef，查询/分享/分叉均保留占位。分享回答读取统一来源围栏；失效分叉清除旧摘要/result/事件正文且不复制旧预览。无迁移。完整 a/b/c、代码证据、Portal 交接和产品决定只维护在用户指定 `/private/tmp/del-review/report.md`。

验证边界：PG/Redis、E2E、Portal、真实模型和真实运行资源未运行。缺 Office runner 会令 PDF 原生索引测试失败，已按 HANDOFF 在 vendor runtime 安装/构建后分组复验；macOS Worker harness 需要 ps，沙箱内失败不计作产品结果。

## 收尾修复与验证

- 第一组 `49990c9`；历史/分享/分叉 `3459066`。随后补 purged 版本预览 410、原件孤儿扫描（以 FileObject 而非 Run 存活保护资料库产物，独立扫描游标）、来源 GC 清 plan/question/file_dependency；修正 result.artifacts 仅覆盖已删除身份，保留额度暂停的原交付状态。
- 迁移 0044 仅增加 artifacts.file_id 反向索引；既有 files.parent_id 索引复用。必要性是 file purge 的反向关联清理和 INSERT SELECT 不扫描整张 artifacts 表；SQLite upgrade/downgrade、drift、EXPLAIN 均验证。PG 未运行。新增索引带来每产物一次索引写入及 O(产物数) 磁盘成本；原件 GC 每轮新增六个前缀各至多 gc_batch_size 个对象，PK 查文件，沿用宽限期与删除预算；未测目标机开销。
- 完整 SQLite **只运行一次**，代码基线 `3459066`：1835 passed / 152 skipped / 2 failed。两项为新增 serializer 替换 result.artifacts 影响 quota pause；已在后续修正。失败与受影响删除回归 84 passed / 16 skipped；最终迁移/删除契约 56 passed / 42 skipped。这些补测覆盖后续修改，不冒充最终 HEAD 全套重跑。
- 最终 Ruff check 通过、501 files formatted。原基线隔离红测两组：11 failed / 6 controls passed / 1 skipped，以及 8 failed / 6 skipped。初期环境/迁移开发失败不作为产品验收；缺 Office runner 需先 npm ci/build，已处理。详细 a/b/c、12 类根因、Portal 文件行号、产品决定和大历史性能边界见仓库外指定报告。

结果：本地修复完成，未 push/部署。下一步由用户/Claude 审阅 Portal 交接；任何发布、PG/真实资源验收或大历史优化均不自动执行。保留 owner-only 回收站内容读取、软删模板冻结引用、独立定时发生轮次、显式保存事实和不可变用量账本的既有产品合同。

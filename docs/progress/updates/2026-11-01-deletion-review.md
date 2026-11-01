# 后端删除正确性审查

基线：`fix/deletion-review`，base `origin/main@17aa2ef`。仅本地后端修复/提交，不 push、部署或修改 Portal。

第一组修复：来源删除取消 waiting_user/interrupted；资料库工具在取得用户锁后重新检查文件/会话；来源 GC 使用用户锁；purged 文件的迟到 file 任务 claim 不再访问 None；孤儿 GC 不再把 purged tombstone 当活文件。无迁移、依赖或新开关。新增回归覆盖这些边界，原基线红测确认；最终分组和完整 SQLite 结果收尾时更新。

第二组：历史附件/产物的字节无关身份、分享读取来源围栏和失效分叉副本处理正在收尾。完整 a/b/c、代码证据、Portal 交接和产品决定只维护在用户指定 `/private/tmp/del-review/report.md`。

验证边界：PG/Redis、E2E、Portal、真实模型和真实运行资源未运行。缺 Office runner 会令 PDF 原生索引测试失败，已按 HANDOFF 在 vendor runtime 安装/构建后分组复验；macOS Worker harness 需要 ps，沙箱内失败不计作产品结果。

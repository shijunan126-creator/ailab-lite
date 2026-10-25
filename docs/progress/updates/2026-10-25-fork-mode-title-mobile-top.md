# 分支模式、标题与手机顶部修复（2026-10-25）

基线 `origin/main@a99701b`，实现 `045da0b`，分支 `fix/fork-mode-title-mobile-top`。用户授权测试、PR、CI绿后squash合并、保留数据部署；不做付费模型验收。

- forked_from JSON记录mode，无迁移；纯chat历史转work分支后，所有旧轮次只能再分支为work，后端同样强制422。旧数据无mode保留逐轮推断。
- 标题优先原会话、其次首轮，加“ · 分支”后截断200；二者空时保留空标题供自动命名。
- 手机fixed chat-layout构成层叠上下文；预留48px顶部行，全屏预览期间隐藏外部浮动按钮，覆盖collapsed面包屑padding。
- 本轮测试新增/更新：工作模式元数据持久返回、纯chat历史work分支再fork chat拒绝、每个旧轮次仍可fork work、标题优先/回退/双空/200截断和已命名分支不重复自动命名。
- 当前检查（045da0b产品代码及本次测试）：Ruff check/format357文件、Python test_chat_fork_retry/test_conversation_tree/test_conversations共40通过，Portal38通过及production build通过。未跑本地完整Python、PG/Redis/E2E、手机浏览器及付费模型；完整套件交CI。当前未合并/部署，下一步CI全绿后合并并按个人记录备份部署五应用。
- 性能：无新依赖、后台作业、查询或文件拷贝；模式检查沿用已有至多500轮遍历，新增JSON标量和至多200字符标题。CSS无新增运行时轮询；不声称长期容量实测。

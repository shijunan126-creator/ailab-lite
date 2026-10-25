# 分支模式、标题与手机顶部修复（2026-10-25）

基线 `origin/main@a99701b`，实现 `045da0b`，分支 `fix/fork-mode-title-mobile-top`。用户授权测试、PR、CI绿后squash合并、保留数据部署；不做付费模型验收。

- forked_from JSON记录mode，无迁移；纯chat历史转work分支后，所有旧轮次只能再分支为work，后端同样强制422。旧数据无mode保留逐轮推断。
- 标题优先原会话、其次首轮，加“ · 分支”后截断200；二者空时保留空标题供自动命名。
- 手机fixed chat-layout构成层叠上下文；预留48px顶部行，全屏预览期间隐藏外部浮动按钮，覆盖collapsed面包屑padding。
- 本轮测试新增/更新：工作模式元数据持久返回、纯chat历史work分支再fork chat拒绝、每个旧轮次仍可fork work、标题优先/回退/双空/200截断和已命名分支不重复自动命名。
- 当前检查（045da0b产品代码及本次测试）：Ruff check/format357文件、Python test_chat_fork_retry/test_conversation_tree/test_conversations共40通过，Portal38通过及production build通过。未跑本地完整Python、PG/Redis/E2E、手机浏览器及付费模型；完整套件交CI。此为合并前历史检查；发布结果见下节。
- 性能：无新依赖、后台作业、查询或文件拷贝；模式检查沿用已有至多500轮遍历，新增JSON标量和至多200字符标题。CSS无新增运行时轮询；不声称长期容量实测。

## 合并与部署结果

- PR #70 最终8项远端检查成功，squash合入 `b961bc2876a8fb544429f656d127ac1be1c6fe21`；本轮部署与源码检查基线即此固定提交。首轮CI E2E `test_library_summary_file_menu_and_switch` 失败：摘要API已done，但文件页15秒内未显示`.lib-summary`；重跑通过。疑似文件页在摘要完成前停止刷新的既有竞态，未修，不把重跑成功当作风险消除。
- 已停写入者完整备份业务卷（29,235,200字节），实际解包并检查SQLite；原配置/旧镜像保留。部署后五应用healthy，迁移0030不变、quick_check正常/FK0，原六表ID及11份文件哈希保留。账号、模型密钥及个人配置保留；个人路径、配置与原始证据仅留本机运维记录。
- 固定源码审计177平台/97 Portal文件匹配。单次空闲内存采样API92.1、Agent77.71、Broker86.27、Worker75.29、Portal37.75 MiB，均低于容器限制；无新后台任务或依赖，不代表峰值/长期容量验收。
- 按用户授权减少重复验证：未跑本地完整Python/双库/E2E套件，无付费模型调用；可选390×844浏览器检查未做。保留摘要显示竞态、真机交互、长期容量及既有USB物理链路风险。本轮仅文档后续合并，不再部署文档提交，不自动启动其他路线。

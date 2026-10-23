# 2026-10-23 · 剩余工作恢复实施

用户恢复原先暂停的剩余功能，优先F8。分支 `feat/remaining-work`，基线 `main@6410fe9`。本轮后续新增：会话自动标题、生成中预览反复打开的根因修复，重点移动全屏与桌面交互。

F8实现提交 `9b74813`，主Agent负责。端到端有界磁盘spool、流式provider、实际字节限额、取消/失败清理、owner重启回收均已实现。SQLite737通过+新增取消分项通过，PG16/Redis766通过；真实Docker/OSB各1项契约通过，Portal11项及build、Ruff通过。2CPU/8GiB、Broker384MiB的双路50MiB完整I/O及双路100MiB扩限压力无OOM，RSS约66–69MiB。详见 [F8完整证据](../../reviews/2026-10-23-broker-streaming.md)。未声称完整k3s混合饱和、长期负载或真实S3联合性能。

原三项Gemini任务已按用户要求停止，未集成其半成品；新建三个GPT 6.1 Sol、reasoningEffort=medium隔离子任务：T09/T08队列与并发、T14模型策略与上下文、UI预览/自动标题/T15分页。各项必须经主Agent代码审核与集成验收后才更新能力状态。T07依赖队列契约，T19依赖模型能力，T16随集成完善，T20公司模板/golden样本仍有外部输入条件。

没有生产部署或远端合并。本次原有 `.DS_Store` 未纳入提交；一次性服务/VM在整个集成验收结束时清理，实际运行状态必须接续时再核对。

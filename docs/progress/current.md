# 文件读取与预览优化：实施与集中验收（2026-11-06）

- 授权：完整实现且达标；仅本地实现/迁移/验证，不push/PR/合并/部署，不操作既有栈或业务数据。按用户最新要求成组实现、最后集中测试。
- 工作区 `.worktrees/document-input-lifecycle`，分支 `feat/document-input-lifecycle`；最新fetch确认 `origin/main@fdc0a05f`，当前产品基线`4a9139e`（续接`f319613`），本组代码已本地提交。指定四份资料已带入，其他工作区与dirty修改未触碰。
- 此前安全ready/按需native、Work原件与OCR、新Chat三工具/显式OCR/分档/持久预算已接；本组补浏览器优先view、generation后备、规范截图引用/容量、viewer/Work缓存记账、TTL业务失效、迁移0060、浏览器准入Worker及主机/沙箱临时资源补偿。新Chat默认V2已在本地接通，尚未整体验收。
- 代码说明、前序阶段测量与最新边界统一见[实施记录](updates/2026-11-05-document-input-lifecycle.md#浏览器查看规范截图及回收接线2026-11-06续作集中验收前)。Ruff/生成是静态检查，不能冒充真实验收；完整Python/Portal/双库/Redis/E2E/浏览器/模型与50轮资源稳定性仍待本组最终基线实测。
- 最新集中结果见[本轮共因修复](updates/2026-11-05-document-input-lifecycle.md#集中验收与共因修复2026-11-06本地4a9139e)：生命周期两数据库21通过、迁移94通过、Ruff/Portal build通过。完整Python首轮两库失败，集中失败复核最后为56失败/70通过（另有后续定点修复），尚未全绿；真实浏览器/模型/50轮未完成，整体未达标。
- 下一步：从仓库外`/private/tmp/ailab-document-final-dVcZZC/remaining-fixed-check.log`的56项失败按共因收敛，核对legacy parser transfer grant/claim、OCR及fork预览复制容量与历史引用；只复核失败组。之后做独立真实栈、录制前缀/模型、浏览器与50轮资源稳定性验收。Docker专用VM停止，本轮未动既有栈；未测设备/后端如实列出。
- 合成样本与前序原始记录仍在仓库外 `/private/tmp/ailab-document-validation-oghcidq0`。只清本任务创建的临时资源，不动既有Docker栈。

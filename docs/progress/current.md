# 文件输入：真实模型workflow复核完成（2026-11-07）

- 最新授权：真实模型检查整个读/搜/看、等待恢复和追问编辑过程，必要时调整逻辑/提示词；仅本地实现、验证与提交，未push/PR/合并/部署。分支`feat/document-input-lifecycle`，本地base `origin/main@fdc0a05f`；本轮未fetch。此前完整实现/迁移至0062及真实资源验收见[原收尾](updates/2026-11-06-document-input-final-acceptance.md)。
- 本轮基线`b19ee388`，主体修复`47d9c75`，定向修复`15b97e8`。修复页图消费者先完成导致执行槽滞留；明确字面搜索、视觉版本/对象归属；修正V2 Excel模板导入；Work恢复事实追加到新消息，保持前缀。V1资源包/Portal未改，ADR0034当前状态已与默认V2实现统一。
- 实际DeepSeek Flash共38轮/191次settled调用（9类场景，含前后复测），真实ClamAV/Docker Office/CPU OCR。所有Run终态成功，但不能算38项流程质量全部通过；多数公开API驱动，另确证一轮Portal发送并浏览器viewport capture。Word准备122.206→8.891秒；定向Work先恢复原件再执行，0失败，下载成品公式/值及同名版本1/2/3已核验。
- 238项影响组通过/6环境跳过，最后录制前缀10项通过，Ruff646文件通过；11次追问首请求暖缓存97.45%–99.43%。未重跑最新SHA完整双库/E2E、50轮资源稳定性或NPU/目标移动/S3/OpenSandbox/整栈容量，不移用此前基线冒充本轮验证。
- 尚有体验边界：PPT区域参数仍两次试错后恢复，Excel曾误用OCR selector，回答仍可能偏长/主动延伸；不能称完全顺畅或回答绝对无误。后续优先定向完善类型错误恢复反馈及非模板/多模型评估，不重复无变化全套或靠挑选成功样本消除问题。现阶段可以继续代码审阅，PR须保留这些实际边界，发布仍需另行授权。
- 本轮专属五服务/Portal/Redis/Clam/OCR已退出，8端口关闭，内置tab关闭；`document-workflow` VM/context及35GiB独立数据盘已删除，Docker context仍default，用户旧`ailab-verify`仍Stopped。删除本轮DB/对象/会话/复制权重，不动用户原配置/权重/业务数据。必要外部证据与完整结果见[真实模型workflow记录](updates/2026-11-07-real-model-document-workflows.md)。

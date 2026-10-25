# 真实沙箱可运行性验证

状态：本地验收通过，PR远端检查与合并待完成。起点 `9cfadfa0deb6ab3d7c8ea1cb748f1b8b9165ad2f`，代码/测试提交 `6d5c305`、`1ca7c6d`、`6933c72`，分支 `verify/real-sandbox-acceptance`。详细证据见[审查](../../reviews/2026-10-25-real-sandbox-acceptance.md)。

目标：确认真实沙箱的创建、执行、文件传输、取消、恢复和清理，并区分已部署Docker路径与新增临时OpenSandbox路径。使用隔离数据库/队列/身份；不更换原部署或执行个人业务库迁移。个人配置与原始运行证据仅留仓库外。

开始时原部署五应用健康。数据库只读完整性正常；本轮不沿用前次mock E2E结果作为真实OpenSandbox证据。

- 真实Docker/runsc契约8项＋超时/取消1项通过；真实OpenSandbox/runc在`dns+nft`下契约8项＋网络1项通过，全部0跳过。
- 五服务浏览器：SQLite+Redis+OpenSandbox 7项通过；修正网络配置后PG16+Redis+OpenSandbox 7项通过，均0跳过。覆盖登录/多轮历史/真实命令/Excel生成与修订/PPT生成/上传扫描下载删除/DOCX真实Office页面预览；模型与扫描器为测试替身。
- SQLite+真实Redis全套1178通过/81跳过；PG16+Redis全套1224通过/35跳过。跳过的真实外部组件没有据此声称通过。Portal35项与生产build、Ruff339文件、ShellCheck和bash语法通过。
- 确认并修复Docker自动到期的inspect/exec与并发删除竞态；真实网络测试证实OpenSandbox默认DNS-only会放行直接IP，启动脚本改为新配置默认`dns+nft`、旧配置明确拒绝，健康进程也须匹配启动配置。真实启动与再次复用通过。
- 所有专用五服务/浏览器、PG/Redis、OpenSandbox服务/沙箱/回声探针已结束；只删除本任务测试镜像标签。原部署九容器保留、五应用健康；原有ID全部保留，验证期间存在新增用户活动，没有要求计数相等或清理用户数据。三份`.DS_Store`未动。
- 未部署修复、未迁移现有0028数据库、未更新个人配置。付费模型、ClamAV、S3与这些真实组件的联合验收、Kubernetes/gVisor OpenSandbox组合、IPv6及长期容量未在本轮重验。下一步核对PR精确head的远端检查后按既有合并授权集成。

[PR #54](https://github.com/shijunan126-creator/ailab-lite/pull/54)已创建并登记，首个远端head`5dc3678`；随后本链接/状态提交只修改文档。精确最新head的远端门禁仍需独立确认，不能沿用旧head的绿结果。

# 当前工作

## Work 标配 Office Runtime（2026-10-29）

用户授权基于最新 main 实施共存方案，不包含部署或远端发布。独立工作树 `.worktrees/work-office-runtime`，分支 `feat/work-office-runtime`，基线 `origin/main@09a28e8`；主目录的设计、证据清理及其他工作树修改保留。

已预装与系统 reader 同版本的 Node/解析核心，增加按需 `office-read` 命令、内部 skill 指令、四格式/修改后重读镜像合同，更新构建上下文与缓存失效输入。平台系统读取通道不变。设计与性能边界见 [ADR 0025](../architecture/adr/0025-work-office-reader.md)，检查结果见[本轮记录](updates/2026-10-29-work-office-runtime.md)。

下一步在隔离 Docker 环境完成真实镜像合同；Docker 专用 daemon 已确认正常；此前只检查默认 socket 的阻塞判断已纠正，真实镜像构建/容器合同仍待执行。未授权启动个人部署或部署新镜像。既有 NPU、多代满窗和数百人容量边界不变；前端 HLD 仍仅设计。

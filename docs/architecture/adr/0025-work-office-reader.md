# ADR 0025：Work 标配 Office 读取核心

2026-10-29，用户授权实施；不含部署。补充 ADR 0023：系统 reader 保留，允许同版本读取核心预装于用户 Work 镜像。

Chat/上传/资料库索引不能依赖 Work 租约；它们继续由 Broker 系统任务产生经过校验的派生结果。Work 则需要读取尚未交付或刚修改的工作区文件，因此镜像预装 Node24 与固定 ZCode parser，通过 `office-read SOURCE NEW_OUTPUT_DIRECTORY` 按需启动，不保留后台 Node 服务。它只读取原生文本和结构；编辑生成仍用现有办公库，转换渲染用 LibreOffice/Poppler，OCR 用平台执行器。

Work 结果不是可信平台索引，不能替代 file_id/授权/删除/版本合同。修改后用新目录检查；原子创建输出目录，已存在的目录拒绝复用，避免旧块或 manifest 混入新结果。manifest 的 coverage/status 和编号文本块供按需检查，原生提取不能证明布局/图表正确。

成本：镜像增加 Node 与生产解析依赖，单机镜像层共享；无新增常驻进程。执行按 60 秒截止、160 MiB V8 heap ceiling；heap 不等于 RSS，解析器仍可能全量加载，CPU/RSS/磁盘最终由 Work 容器边界限制。同一 Work 内并行命令仍共享该资源限额。镜像体积、真实容器峰值和数百人吞吐未实测，不作容量承诺。

构建上下文改为仓库根；部署缓存哈希覆盖 reader 源码与锁文件，排除本地 node_modules 和生成 runner。四格式与修改后重新读取合同加入现有镜像 smoke，CI 在 vendor 变化时触发。验证结果见[实施记录](../../progress/updates/2026-10-29-work-office-runtime.md)。

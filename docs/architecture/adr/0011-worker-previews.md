# ADR 0011：单机文件扫描隔离与资料库按需预览

日期：2026-10-23。状态：按本次产品任务实现。

目标：上传只做扫描、受限文本提取与既有图片缩略图；首次打开资料库预览时生成重型派生数据，批量上传不触发 Office/PDF 转换。

Worker 通过 Scanner 端口流式扫描临时文件。不可用与命中分开：前者保持隔离并有界重试，后者 rejected。解析和图片解码使用短命子进程；Linux 强制地址空间/CPU/文件大小限制，父进程负责墙钟与总输出，macOS 另用 RSS watchdog。此进程边界是资源隔离，不宣称提供 Kata 内核隔离。

Office/PDF 预览进入 Broker 的 office 镜像沙箱，复用 render_preview.py；Worker 不执行 LibreOffice/poppler。xlsx/xlsm/csv/tsv 调同一个 sheets 函数，禁用重算并流式读取。图片解码同时生成 display.webp，SVG 由 CSP sandbox 限制；文本只取已提取文本，不解析 HTML。

资料库预览没有会话 Run。Broker 的 RenderContexts 仅为已验证 file_id/job_id 注册内存上下文；调用 LeaseManager 公开 create/release 和文件/exec 方法，仍共享容量限制。它向 create 提供该文件的输入列表，屏蔽该临时上下文的 Run 事件，finally 清理；没有写入伪 Run，也没有修改 LeaseManager 内部实现。D 合并租约重构时必须保留此公开协议，或提供正式文件任务租约入口。

与冻结目标的区别：目标文档描述上传时转换、Kata Worker、双副本与独立路径；本次按已决定的单机产品契约改为按需转换、derived/{id}/preview/ 永久缓存、一个 Worker 渲染槽。OCR 不在范围内。

预览请求必须与队列副作用可靠关联。基线无 outbox，因此采用已预留的 0011（down_revision=0008）新增最小 outbox 表；manifest 仍在 files.meta，没有改变文件表。预览状态和 outbox 同事务，发布成功才删除记录，崩溃最多重复派发。后续 T09 可扩展共享表，不应再建第二个预览队列事实源。

# W1 文件扫描、隔离与资料库预览交接

## 合并重点

- 分支 `arch/w1-worker-preview`，起点 `66665739b4efc0c9d3e95fae05e29afc024ec98d`，仅本地提交。
- 公共预览字段、状态、资源名称与请求路径遵循任务约定。`FileOut.preview` 未请求时为 null；扫描/解析不会提前写 preview。
- **新增迁移**：`0011_preview.py`（revision `0011`，down_revision `0008`）只建 `outbox`，不改 files。原因是仓库要求事务 outbox，而基线没有该表。A/B/D/E 合并时应统一 Alembic heads 和共享 outbox 定义。
- B 的 `scanner_backend`/secure profile 定义优先；保留本分支所需 clamd_address、scan_timeout_seconds、parse_memory_mb、parse_cpu_seconds、parse_timeout_seconds、scan_retry_backoff_seconds。继续使用原 service_auth，B 需给 Worker 文件完成接口 `files:preview_write`、Broker `/internal/render` 的 `sandbox:render` 权限并绑定 file/job。
- D 无 LeaseManager 内部 diff；注意 LibraryRenderer 在公开 platform 客户端上安装 RenderContexts 适配器，为已验证预览作业提供短期输入上下文。所有租约仍通过 create/release，共享容量。
- E 需合并新增 Broker Service、Worker Broker URL、ClamAV Deployment/Service/PVC 与 Worker 384 MiB 内存限制；保留 derived/{file_id}/ 整前缀删除逻辑。

## 扫描、解析与限制

Scanner.scan(BinaryIO) 返回 verdict=clean/infected/unavailable、engine、signature。clamd 使用 TCP 或 Unix 的 zINSTREAM，64 KiB 块，默认总期限 30 秒、最多 4 KiB 响应；断连、超时、ERROR 与无终止符均 unavailable。开发 stub 只在允许的开发环境生效，secure profile 拒绝。

Worker 流下载到临时文件，扫描 clean 后才解析。不可用写 processing=scan_unavailable，默认 3 次尝试、5/10 秒退避；用尽仍 pending，scan_exhausted=true，可在资料元数据中看到，不提供下载/预览。命中 rejected。失败扫描不会被视作 clean。

所有文档解析、ZIP 检查、个人 skill 检查、图片解码都在子进程：默认 Linux RLIMIT_AS=192 MiB、CPU=45 秒（硬 46 秒）、墙钟=60 秒、文件/总输出=16 MiB；预览 sheets 墙钟 120 秒。macOS 不设置无效的 RLIMIT_AS，50 ms 轮询 RSS 并保留 CPU/文件/墙钟限制。进程组超时 SIGKILL 并 wait 回收；不继承服务密钥环境。

ZIP 上限：2048 项、128 MiB 总解压尺寸、任一项 200 倍压缩比，先检查后调用解析器；沙箱 renderer 也使用同一个检查函数。损坏/超时/内存/CPU/输出限制为受控 extract.error；文件扫描 clean 后保持 READY。大于 max_parse_bytes 仅跳过提取，kind=too_large。图片失败不改变 READY；输入 20 MiB，JPEG 128 MP、其他 16 MP；首帧/EXIF/透明度保留，缩略图≤480，display.webp 长边≤2048。

sheets.json 保持既有键和单元格格式。受内存/输出约束，最多 20 张表、200×40 单元格/表、每格 2000 字、工作簿总显示文本 100 万字；原始公式不在 Worker 重算，显示缓存值或公式。流式模式暂不提取列宽、合并与图表统计（对应字段仍保留为空/0）；这是渲染保真度边界。

## Broker 与 API

`POST /internal/render`，服务认证，JSON `{file_id,job_id}`。仅接受 API 元数据中对应 pending preview 且 READY 的文件；不接受用户给出的路径/key/image/sandbox_id。租约 TTL 180 秒，渲染命令 120 秒内（含转换工具子进程），最多 40 页，保留已知总 page_count；单资产读取沿用 Broker 上限，总输出≤32 MiB。返回 `{preview, assets}`，assets 为名称→{key,content_type,sha256}，写入 derived/{id}/preview/；finally release。不存在业务文件的 Run 伪记录。

`POST /internal/files/{id}/preview` 完成作业，只接受当前 job_id、白名单 manifest、匹配资产集合/前缀/MIME/SHA256；重复完成幂等。它是 Worker→API 的写边界。

GET manifest 只读 DB meta；首次请求与 outbox 同事务，便捷派发失败不丢请求，后台每秒最多派发 20 个。pending 超 300 秒才新建 job；failed 永不自动重新渲染。Worker 一个渲染槽、每文件队列锁，重复消息不会并发转换。

资产路由只接受 manifest 中列出的名称。全部 private 一年 immutable、强 ETag/If-None-Match 304、nosniff、指定 CSP sandbox；文本固定 text/plain。使用 _ClosingStreamingResponse，关闭 DB 后流式读取，断开会关闭存储流。删除清理整个 derived/{id}/；删除与完成竞态时 Worker 清理随后产生的对象。

## 性能与资源

本机 macOS 15.8.2 arm64，Python 3.12；合成固定样本，不代表 2 vCPU Linux 容器验收。历史测量摘要见下列数值；复现脚本为 platform/tests/measure_worker_preview.py（用 `python -m tests.measure_worker_preview`，并明确设置不存在的 AILAB_ENV_FILE）。

- 20 MiB PPTX 子进程峰值93.9 MiB、父80.3 MiB（和174.3 MiB为保守预算），耗时1.122秒、子CPU0.122秒。含一张约 19 MiB 不可压缩 PNG，解析子进程峰值与父进程峰值分开记录；二者峰值和为保守预算，并不宣称同时发生。完整启动/解析时延包含 Worker import。
- 10 MiB XLSX 子进程峰值34.6 MiB、父79.5 MiB（和114.2 MiB），耗时4.096秒、子CPU2.736秒。为 12,000×10 随机字符串，read_only 读取；覆盖缺失维度信息的文件，避免把 max_row=None 当损坏。样本含少量补齐尺寸的 ZIP_STORED padding。
- 单页 DOCX 2.625秒、输出23,437字节、结束后0租约。mock provider 运行真实 LibreOffice→PDF→pdftoppm，包括租约/注入/存储/释放；输出与释放状态见本节摘要。
- Worker 限额提升至 384 MiB，覆盖父进程加 192 MiB 子进程与临时缓冲；本地样本并未触及上限。临时磁盘最多一个受上传上限约束的输入加 16 MiB 子进程输出；Broker 输出最多32 MiB，缓存永久直到删除。
- ClamAV 单 Deployment，共享一份签名，预算约 1 GiB 常驻；request 1.5 GiB、limit 2 GiB，100m request/500m limit CPU，签名 PVC 1 GiB。此 RSS 是部署预算，未在本机启动真实 clamd 实测。

## 验收边界

最终 SQLite+Redis：435 passed / 10 skipped；PG16+Redis：444 passed / 1 skipped。PG 仅跳过真实 clamd；SQLite 另跳过9项PG专用。Ruff check/format、依赖锁、diff check、Portal 4测试与生产build通过。涵盖扫描三态、TCP/Unix/超时协议、ZIP 数量/尺寸/比率、真实超时/OOM、损坏文档、像素/缩略图失败、预览各类型、并发打开/消费、失败不重试、越权/路径/非法完成、304/流关闭、删除、outbox 故障恢复、双库迁移/EXPLAIN、57页 PDF 只渲染40页。

真实 clamd、Linux RLIMIT_AS、Docker/OpenSandbox、k3s 部署、镜像拉取与签名更新未验收。现有租约注入仍沿用 get_file_bytes 整文件接口；本次 Worker 下载已流式，但不将整个 Broker 存储链路标为流式完成。渲染的120秒执行预算不包含 provider 无响应时自身的创建/释放网络等待；继承 provider 超时，D 需在适配器中落实统一 deadline。未改 Portal UI，未跑浏览器 E2E；Portal 测试与 build 已通过。

## 责任范围外的增量文件

config.py；clients.py；contracts/file_preview.py；models.py；migrations/versions/0011_preview.py；api/main.py；api/preview_dispatch.py；api/routes_internal.py；api/serializers.py；回归测试/测量脚本；HANDOFF 与架构/审查文档。其余产品实现位于任务列出的所有权范围。

# 单机实现状态（2026-10-23）

本表仅记录已在当前实现验证的能力；总体目标以冻结架构文档为准。

- 文件扫描：实现 Scanner 端口、开发 EICAR stub、ClamAV TCP/Unix INSTREAM。不可用保持 pending，指数退避后标记 scan_exhausted；真实 clamd 验收待设置 AILAB_TEST_CLAMD。
- 上传解析与图片解码：受限子进程，内存/CPU/墙钟/输出/ZIP 膨胀限制。macOS 故障测试已验证；Linux RLIMIT_AS、生产容器压测待验收。
- 资料库预览：按需 pages/sheets/image/text、不可变派生缓存、资产白名单、ETag/304/CSP、删除清理；Office/PDF 经 Broker 临时沙箱复用既有渲染器。mock + 真实本机 Office/PDF 工具已验证。
- 预览可靠入队：0011 outbox 与 files.meta 同事务；API 每秒最多派发 20 个任务，重复投递由 file/job 状态与锁去重。其他既有上传/Run 入队路径尚未迁入 outbox，不据此宣称 T09 全部完成。
- OCR、Kata Worker、多机转换池、真实 OpenSandbox/ClamAV/k3s 的组合验收：未完成；不在本次交付范围。

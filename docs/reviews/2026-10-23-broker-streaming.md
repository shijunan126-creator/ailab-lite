# F8 · Broker 有界流式传输验收

分支 `feat/remaining-work`，实现提交 `9b74813`，基线 main `6410fe9`。本轮仅本地提交；没有生产部署。

## 根因与实现

原路径同时使用 PlatformClient `.content`、provider bytes 上传/下载和 Storage `put_bytes/get_bytes`。运行附件、资料库渲染输入、产物交付、工作区快照/恢复均会保留整文件副本。交付只在读取前 stat，文件随后增长可越过限额，已用回归复现。

现在 API HTTP流 → data_dir 磁盘临时文件 → `put_file_from_path`。反向通过 `get_file_to_path` 限额落盘，再流写对象存储；实际 size/SHA256 从这份冻结文件获得。每块64KiB。保持 run_id / preview_job 服务授权绑定。

Docker 继续使用原有 exec 通路，stdin/stdout 直接连接本地文件描述符，不额外构造 tar；输入写随机临时目标、成功后原子替换，输出最多 limit+1 字节以检测增长。OpenSandbox 1.1.1 公开 SDK 的 binary IOBase multipart 与 `read_bytes_stream` 已支持分块，不另造 HTTP 协议；直接与 server-proxy 两种上传均可用同一 SDK 接口。下载生成器在 finally 关闭，上传失败清除部分目标，清理失败不掩盖原错误。

本地 spool 在磁盘卷，按 owner 隔离；成功、超限、存储失败、取消均退出临时目录。新 Broker 取得单实例所有权后回收其前任遗留。取消准备的 `CancelledError` 属于 BaseException，已修复只捕获 Exception 导致的 creating 沙箱泄漏。预览输出也迁入有界路径，并限制页名和总输出。

## 红绿与功能验证

- 新增最初5项回归在旧实现全部失败：禁止 bulk bytes接口、授权绑定与限额、stat后增长、快照/恢复、失败清理；修复后通过。
- 取消准备回归先失败（遗留 creating 租约），补充 BaseException 清理后通过；另覆盖 owner重启回收和对象存储失败。
- HTTP下载回归涵盖错误体限额、超限及时停止及流关闭。
- 真实 Docker 与真实 OpenSandbox 各1项共用传输契约通过：2MiB分块往返、大小上限、中文/引号路径、符号链接拒绝。Mock同一契约通过。
- SQLite + 临时Redis：737 passed / 45 skipped / 83.41s；随后新增的取消用例单独通过。PostgreSQL16 + Redis全套（含取消用例）：766 passed / 17 skipped / 108.79s。
- Ruff check/format、Portal Node 11项、生产build通过。没有修改Portal UI，本项没有重复浏览器E2E。

双库均为一次性测试数据：PostgreSQL15511、Redis16511；未读取开发.env或开发数据库。真实provider契约独立开启 gate；全套内仍跳过未配置的 S3、ClamAV、Keycloak和真实provider gate，不将跳过算作已测。

## 目标机资源

专用 Colima `ailab-verify`：原生ARM64 Linux，2vCPU/8GiB。Broker探针容器限1CPU/384MiB；真实OpenSandbox 1.1.1、sandbox每个1536MiB/0.5CPU。数据HTTP源和Local对象存储是真实流，metadata/产物登记是夹具；这是Broker I/O验收，不是五服务整栈延迟。50MiB内同时跑注入、交付、不可压缩快照与恢复；100MiB场景只做注入/交付，测试显式把上传上限从默认50MiB提高到100MiB，生产配置未变。

| 输入/输出 MiB × 并发 | 空闲RSS MiB | 峰值RSS MiB | 临时spool峰值 MiB | cgroup峰值 MiB | 总wall s | Broker CPU s |
|---|---:|---:|---:|---:|---:|---:|
| 1 × 1 | 62.34 | 65.66 | 1.00 | 56.84 | 18.72 | 0.290 |
| 50 × 1 | 62.30 | 65.62 | 50.01 | 261.12 | 23.19 | 0.997 |
| 50 × 2 | 62.36 | 68.57 | 100.00 | 367.14 | 28.80 | 1.573 |
| 100 × 2（扩限） | 62.35 | 67.95 | 200.00 | 384.00 | 13.30 | 1.973 |

峰值RSS不随1→50MiB文件大小线性增加；双路压力无Broker OOM。100MiB双路的memory.max事件322次、oom/oom_kill均0，说明文件页缓存已触发cgroup回收；低RSS不表示384MiB还有同量余量。共享页统计口径不同，cgroup与进程RSS不可直接相减。

采样20ms；CPU数字包括本进程HTTP源与采样器，未扣除。吞吐/耗时包含SDK控制调用、创建、注入、copy、存储和释放；不能与旧整栈3.57s注入直接比较。磁盘spool最多每并发请求一份文件，Local/S3写入还可能有原子提交/multipart磁盘暂存；按部署PVC容量另计持久对象和沙箱文件。无新运行时依赖、无新增后台线程（采样仅测试）。

历史指标保留在上表，逐点采样与派生曲线已清理。可复跑脚本：[broker_transfer.py](../../platform/tests/load/broker_transfer.py)。容器安装依赖沿用 `platform/deploy/Dockerfile`；通过只读源码挂载验证上述提交，不使用开发.env。

## 仍需区分的边界

未在本轮跑真实S3+OSB联合性能、完整k3s五服务混合饱和、长期负载或生产公网验收。已有Storage真实S3验收属于历史证据。极端磁盘不足会作为传输失败，不能保证任务成功；临时文件退出清理已测试。同步provider调用受120s传输及SDK超时约束，客户端断开不会立即中断所有同步线程。

早期夹具误把sandbox预算缩为256MiB，不可压缩50MiB快照上传触发的是sandbox OOM，非Broker OOM；恢复真实1536MiB预算后验收通过。并发夹具须先执行与HTTP lifespan相同的recover；未准备就直接并发create会被正确拒绝。这两次诊断失败没有混入成功资源曲线。

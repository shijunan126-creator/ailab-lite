# ADR 0030：单次使用的预热渲染沙箱

状态：部分被取代（2026-10-31）。用户确认隔离边界为用户，新的决定见[渲染沙箱生命周期指南](../design/2026-10-31-render-sandbox-lifecycle.md)；实现完成，2026-11-01 测速见[记录](../../progress/updates/2026-10-31-prefix-cache-render.md)。历史结果和未测边界见 [C3d](../../progress/updates/2026-10-31-prefix-cache-render.md#c3d-预热渲染)。

C3f/C3g 之后的现状以“新决定”“槽位”“边界”三节为准；C3d–C3f 的过程见 [C3f 进展](../../progress/updates/2026-10-31-prefix-cache-render.md#c3f-实现进展)。下方“历史决定”保留作为历史描述。

## 新决定

渲染沙箱绑定一个用户直至销毁，同一用户可在清理和健康检查后复用；调度、故障处理和资源限额以[指南](../design/2026-10-31-render-sandbox-lifecycle.md)为准。槽位保持 **1 槽 / 768 MiB**（2026-11-01 runsc 实测，见下文“槽位”）。

## 历史决定（已部分取代）

Broker 在预留 render 容量内按 `AILAB_RENDER_PREWARM`（默认 1，0 禁用）创建无用户输入的 standby 沙箱，运行一次 headless LibreOffice 转换，保留 profile 与系统缓存，不保留常驻 LibreOffice 进程。standby 计入容量、不可经普通租约 I/O 使用；预览任务通过单次认领绑定新的系统任务身份，注入文件后激活，完成或失败时销毁。未命中时走原有短租约。预热失败不阻止按需冷启动。

空闲 standby 按 `AILAB_RENDER_PREWARM_IDLE_SECONDS`（默认 600）回收、Broker 重启或存储丢失时不认领旧 standby、等待任务不被补池插队——这三条仍有效。“一次性使用、完成即销毁”已被取代：standby 现在保留常驻 LibreOffice listener，认领后绑定一个用户，任务结束清理并通过健康检查后以 `idle(U)` 保留 `AILAB_RENDER_USER_IDLE_SECONDS`（默认 180）供同一用户复用。

Work 的视觉查看（`view_workspace_file`）在 Work 租约内懒启动独立 profile/pipe 的 LibreOffice listener，120 秒闲置或 RSS 超限退出，不跨租约复用。默认 Docker supervisor 的最长生存期在预热资源创建时覆盖空闲期和一次任务，认领后仍受短任务租约限制。

## 槽位

runsc 实测（2026-11-01，`c3-render-lifecycle` 镜像，VM cgroup，含页缓存）：warm standby 102 MiB，连续处理 15 页 PPTX、7 页 DOCX、XLSX 后 158 MiB，峰值 192 MiB；C3f 曾测得 listener 常驻约 274 MiB。两槽需把每槽限额降到 384 MiB，只剩样本峰值约 2 倍、C3f 常驻约 110 MiB 的余量，含图片的真实大文档有 OOM 风险，因此保持 1 槽 / 768 MiB。代价：用户交替（A、B、A）每次切换冷启动，首页约 4.2–4.6 s；同用户热复用首页约 1.0–1.3 s。

## 边界

同一用户的预览与系统解析复用同一沙箱；首页（或请求页）先转换并发布，其余页一次光栅化、一次打包下载后按“等待页优先、其余顺序”逐页发布，批次开始后的新请求不插队。整机同一时刻最多一个 LibreOffice 转换。没有 listener 的宿主机型 provider（mock）每次转换启动 LibreOffice，健康检查只做清理。Broker 进程内协调依赖现有单 Broker 所有权合同；异常退出由租约重建和回收机制处理。

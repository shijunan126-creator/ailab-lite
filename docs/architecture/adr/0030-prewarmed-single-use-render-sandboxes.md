# ADR 0030：单次使用的预热渲染沙箱

状态：部分被取代（2026-10-31）。用户确认隔离边界为用户，新的决定见[渲染沙箱生命周期指南](../design/2026-10-31-render-sandbox-lifecycle.md)；实现及性能验收仍在进行。历史结果和未测边界见 [C3d](../../progress/updates/2026-10-31-prefix-cache-render.md#c3d-预热渲染)。

C3f 已改为在 standby 保留单次使用的 UNO listener，转换失败用新 profile 冷回退；同沙箱解析、逐页发布、工作区 listener 和响应后释放仍未实现，详见 [C3f 进展](../../progress/updates/2026-10-31-prefix-cache-render.md#c3f-实现进展)。下方 C3d 决定保留作为历史描述。

C3e 目前仅补充租约来源及非重叠注入计时，以下设计仍未改变；新字节基线及待完成事项见 [C3e](../../progress/updates/2026-10-31-prefix-cache-render.md#c3e-后续)。

## 新决定

渲染沙箱绑定一个用户直至销毁，同一用户可在清理和健康检查后复用；调度、故障处理和资源限额以[指南](../design/2026-10-31-render-sandbox-lifecycle.md)为准。槽位数量待 runsc 实测常驻与峰值 RSS 后决定，不预先声称两槽可放入 768 MiB。

## 历史决定（已部分取代）

Broker 在预留 render 容量内按 `AILAB_RENDER_PREWARM`（默认 1，0 禁用）创建无用户输入的 standby 沙箱，运行一次 headless LibreOffice 转换，保留 profile 与系统缓存，不保留常驻 LibreOffice 进程。standby 计入容量、不可经普通租约 I/O 使用；预览任务通过单次认领绑定新的系统任务身份，注入文件后激活，完成或失败时销毁。未命中时走原有短租约。预热失败不阻止按需冷启动。

空闲 standby 按 `AILAB_RENDER_PREWARM_IDLE_SECONDS`（默认 600）回收；Broker 重启或存储丢失时不认领旧 standby。用户文件处理和 Work 租约启动提示 Broker 补池；预览、系统文档解析、工作区视觉及自动预览受同一 Broker 内活动协调，转换或等待转换时不启动新预热。系统解析需要 render 槽时可以释放空闲 standby。等待任务不被补池插队。

Work 的视觉查看在同一租约内保留 `/tmp/.ailab-lo-profile`；这只是复用 profile，不是驻留 listener，也不跨租约复用。默认 Docker supervisor 的最长生存期在预热资源创建时覆盖空闲期和一次任务，认领后仍受短任务租约限制。

## 边界

系统解析和预览仍是两个沙箱；页面仍整批完成后发布，无法提前唤醒等待页面。预热会占一个预留槽，启动和解析可能互相等待；一次性预热没有消除 LibreOffice 的冷进程开销。观测到上传和工作区速度均未达到目标，不应将该实现视为性能验收通过。Broker 进程内协调依赖现有单 Broker 所有权合同；异常退出由租约重建和回收机制处理。

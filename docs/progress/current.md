# 当前工作

只写进行中、暂停、阻塞和下一步。做完的事从这里删掉，结论写进模块文档、HANDOFF、ADR 或 status-matrix（见 [AGENTS.md §8](../../AGENTS.md#8-文档怎么维护)）。

## 进行中

- `fix/genui-tooltip-hy4`（base `main`，PR #207）：图表读数定位与单位格式、加入 hy4-flash-free。下一步：等 CI 绿后合并；本机栈的 VM 上的 `models.local.toml` 需要和 Mac 上的副本同步后才会出现 hy4。

## 等用户决定

- Compare/Highlights 工具是否改成按需加载：要先拿真实模型做对比。

## 已知未覆盖

- 子任务经 `call_tool` 调用、压缩后重读工具说明：还没有真实模型覆盖。
- OpenSandbox 在 Kubernetes + gVisor 下的耗时没测过。
- Portal 待发送队列只存在浏览器内存里，整页刷新会丢掉还没提交成功的失败提示。
- 旧 PPTX 的页图缓存：要处理的话，先给 `RenderedPage` 和文件预览元数据加按格式区分的渲染器版本。

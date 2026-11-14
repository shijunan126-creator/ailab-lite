# 当前工作

只写进行中、暂停、阻塞和下一步。做完的事从这里删掉，结论写进模块文档、HANDOFF、ADR 或 status-matrix（见 [AGENTS.md §8](../../AGENTS.md#8-文档怎么维护)）。

## 进行中

无。

## 等用户决定

- Compare/Highlights 工具是否改成按需加载：要先拿真实模型做对比。

## 已知问题（未排期）

- `AILAB_PROFILE=secure` 不拒绝 Docker 后端配 `runc` 运行时（`platform/ailab_platform/config.py::Settings.validate_secrets`）。线上走 OpenSandbox + gVisor，不受影响。
- `deploy.sh --help` 只打印脚本头注释，漏了 `--skip-build`、`--listen`、`--dry-run` 等参数。
- `platform/evals/genui/run.py` 会回退去读开发者本机的模型凭据文件；`platform/scripts/bench_office.py` 把结果写进 `docs/reviews/`。

## 已知未覆盖

- 子任务经 `call_tool` 调用、压缩后重读工具说明：还没有真实模型覆盖。
- OpenSandbox 在 Kubernetes + gVisor 下的耗时没测过。
- Portal 待发送队列只存在浏览器内存里，整页刷新会丢掉还没提交成功的失败提示。
- 旧 PPTX 的页图缓存：要处理的话，先给 `RenderedPage` 和文件预览元数据加按格式区分的渲染器版本。

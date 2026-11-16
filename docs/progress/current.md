# 当前工作

只写进行中、暂停、阻塞和下一步。做完的事从这里删掉，结论写进模块文档、HANDOFF、ADR 或 status-matrix（见 [AGENTS.md §8](../../AGENTS.md#8-文档怎么维护)）。

## 进行中

- 模型 provider 拆分（分支 `main`，base `origin/main`，未提交）：新增 `opencode-go`/`deepseek` provider，请求形状默认值由 `platform/ailab_platform/model_providers.py` 提供；Hy4 与 DeepSeek 的 `reasoning.toml` 加 `none` 档（官方 `thinking.type=disabled`），Hy4 加 `high` 档。已改仓库目录和本机私有 `models.local.toml`；232 项相关测试、ruff、真模型 reasoning_tokens 验证通过。下一步：决定提交/PR 与是否部署。

## 等用户决定

- Compare/Highlights 工具是否改成按需加载：要先拿真实模型做对比。

## 已知未覆盖

- 子任务经 `call_tool` 调用、压缩后重读工具说明：还没有真实模型覆盖。
- OpenSandbox 在 Kubernetes + gVisor 下的耗时没测过。
- Portal 待发送队列只存在浏览器内存里，整页刷新会丢掉还没提交成功的失败提示。
- 旧 PPTX 的页图缓存：要处理的话，先给 `RenderedPage` 和文件预览元数据加按格式区分的渲染器版本。

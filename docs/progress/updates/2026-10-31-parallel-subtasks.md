# Work 并行子任务（2026-10-31）

基线 `origin/main@f316b85`。决定见 [ADR 0031](../../architecture/adr/0031-parallel-subtasks.md)。

## 实现

- `agent/subtasks.py`：工具 schema、允许工具集、子任务说明模板、参数校验（2–4 个，含 title/instructions）。
- `AgentLoop._subtasks/_subtask/_subtask_tool`：从主循环最近一次请求 fork，`asyncio` 并发执行，任一子任务被取消/租约失效/预算暂停时取消其余并上抛；不允许的工具返回错误。
- `turn.offered` 记录主循环最近一次的工具列表，子任务原样复用。`run_subtasks` 计入 `FILE_TOOLS/PREVIEW_AFTER`，结束后照常快照工作区和刷新预览。
- Work 系统提示新增一条适用条件；配置 `AILAB_SUBTASK_MAX_STEPS`（默认 20）。

## 验证

`tests/test_subtasks.py`：子任务请求 = 主循环请求 + 说明（工具与前缀一致）、两子任务实际并发、被禁工具返回错误、写入沙箱、报告按顺序回传、单个任务被拒绝。完整套件交 CI。

## 未验

真实模型是否在合适时机调用、报告质量、并发子任务在真实沙箱/模型限流下的耗时与成本。

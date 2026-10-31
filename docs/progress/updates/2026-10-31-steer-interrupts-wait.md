# 用户插话打断后台等待（2026-10-31）

基线 `origin/main@f316b85`。来源：DeepSeek《Super-agent 设计探索》4.1 节“全双工交互”。用户授权实现、分别提 PR、合并并部署。

## 问题

`exec` 未结束时返回句柄，模型随后用 `exec_wait(seconds≤300)` 等待。等待期间 `_interruptible` 只在取消时中断，不看 steer；用户中途补充“别等了，改用方案 B”最长要等 5 分钟才进入上下文。

## 修复

- `_interruptible(..., steerable=True)`：心跳（每 `CANCEL_POLL_SECONDS`）看到 `steer_pending` 时抛 `SteerArrived`。只有 `exec_wait` 可被打断。
- 被打断的 `exec_wait` 返回 `{"running": true, "steered": true, "handle": …}` 与提示，命令照常在后台运行；未读输出仍在原 offset 之后。下一步开始时现有 `_apply_steers` 把新消息放入上下文。
- 不打断 `exec`（启动请求被放弃时无法确定命令是否已启动，会触发 `UNCERTAIN_EXEC`）、模型调用和其他工具。

沙箱内被放弃的等待循环只读状态，最迟到原 `seconds` 截止自行退出；不增加 CPU/内存常驻开销。

## 验证

`tests/test_long_runs.py::test_user_steer_ends_a_background_wait_but_not_the_command`：撤掉修复时失败（等满 3 秒、无 `steered`），恢复后通过；相关 `test_long_runs/test_stuck/test_background_exec/test_inbox_queue` 74 项通过。完整套件交 CI。

未验：真实沙箱下的打断时延（预期 ≤ 一个心跳周期 2 秒）。后续可选：命令结束时把结果作为事件主动投递，模型不必轮询。

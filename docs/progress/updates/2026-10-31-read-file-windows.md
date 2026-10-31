# read_file 分段读取（2026-10-31）

基线 `origin/main@f316b85`。来源：DeepSeek《Super-agent 设计探索》3.3 节 Inline & Spill——超出窗口的内容留在上下文之外，但保留续读入口。

## 问题

`read_file` 只有 `path`，超过 30,000 字符直接截断并附 `[truncated N chars]`；模型要看后文只能自己拼 `sed`/`head`，而资料阅读工具早已用 `offset` 分段。

## 修复

- 新参数 `offset`（Unicode 字符，默认 0）、`limit`（1–30,000，默认 30,000）。
- 文件未读完时返回 `next_offset` 与 `total_chars`；非首段也返回 `offset`。短文件返回形状不变（只有 `content`）。
- 参数越界（负数、超过长度、limit 越界、非整数）返回工具错误，不再静默截断。删去只剩此处使用的 `_truncate`。

单次结果上限不变，不增加上下文或内存开销（Broker 仍整文件读回，与修复前一致）。

## 验证

`tests/test_agent_loop.py` 中 read_file 用例：分段读回拼接与原文一致、窗口参数、越界拒绝；旧实现下失败。完整套件交 CI。

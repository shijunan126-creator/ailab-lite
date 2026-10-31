# 历史召回附带下一跳（2026-10-31）

基线 `origin/main@f316b85`。来源：DeepSeek《Super-agent 设计探索》3.3 节“链式召回”——每次召回的对象内联下一跳关联对象的 ID，模型沿引用逐跳展开。

## 问题

`recall_history` 命中只给对话 ID、标题和摘录。用户说“把上周那份 PPT 第三页改一下”时，模型找到那一轮后仍要按文件名猜着搜资料库；读到某轮原文后也无法继续看它前后的轮次。

## 修复

- 检索命中与 `run_id` 原文读取都附 `files`：该轮交付、当前仍可被 `read_library_file` 打开的文件（同一组过滤：本人、ready、library、未删除/清除），每轮最多 5 个，一次 `IN` 查询（`artifacts.run_id` 有索引）。
- `run_id` 读取另附 `previous_run_id`（父轮）与 `next_run_ids`（最多 3 个子轮，走 `ix_runs_conversation_parent`），每个相邻轮次复用原文读取的同一可见性检查（本人、成功、未被替代、对话未删除、记忆读权限、同项目）。
- 工具说明补一句如何沿 `previous_run_id/next_run_ids/files` 继续读。

不新增存储或后台任务；每次召回多一到三条带索引的小查询。

## 验证

`tests/test_memory_recall.py::test_recall_names_the_next_hops_delivered_files_and_adjacent_turns`：已删除文件不出现、被替代分支不作为下一跳、父子双向可达；旧实现下失败。`test_memory_recall` 30 项通过。完整套件交 CI。

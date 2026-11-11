# 定时任务绑定会话（2026-11-11）

分支 `feat/schedule-in-conversation`，base `origin/main@81d24699`。决定见 [ADR 0040](../../architecture/adr/0040-schedule-in-conversation.md)。

## 做了什么

- 每个定时任务绑定一个会话，到点在该会话后接着回复；Chat/Work 由创建来源决定（对话里创建沿用会话模式，页面新建为 Work），列表打「对话/工作」标签。
- 对话里可直接让助手定时（新工具 `schedule`），会话输入框上方显示本会话的任务，可立即运行、暂停、编辑、删除；到点的一轮显示为时间标记，点开看任务内容。
- 新增「一次」：选日期和时间，执行后自动删除。
- 会话忙时顺延到空闲后立即回复；在等用户回复时跳过。

## 修掉的逻辑问题

- 每次新建会话、看不到上次结果 → 同一会话追加。
- 定时轮缺 `client: portal` 导致 `run_js` 从工具列表消失，前缀缓存只有 46–49% → 继承叶子轮 client 后 93–95%。
- 完成的一次性任务占着 5 条名额 → 执行后删除；上限报错附“可在定时任务页删除”。
- “立即运行”被模型理解为重复触发 → `manual` 单独措辞。
- 删除会话/项目后任务仍指向已删会话 → 同事务删除。

## 验证

代码基线：`f8032c33` + 本次未提交修正（同一 PR）。

- `ruff check` / `ruff format --check` 通过；定向 `pytest -n auto`（schedule_conversation、schedules_citations、conversation_mode_fixed、net_tools、web、migrations、prefix_cache）310 passed / 50 skipped（PG/后端未设置的用例）。
- Portal：`tsc --noEmit` 通过，vitest 48 文件 286 passed。
- CI 首轮（`fb1e042`）3 项失败：迁移链测试仍断言 head 0070、Chat 工具列表断言缺 `schedule`，均为测试未随改动更新，已修正。随后 E2E 仍点已删除的「模式」下拉（改为断言「工作」标签）；`test_lifecycle_assets` 资产清扫用例依赖 worker 内前序测试的残留（与本改动无关的既有不稳定），改为循环清扫到 0。PG 一轮又遇两个既有实时钟用例：job heartbeat（0.3s 租约下事件循环停顿 146ms 越界）改为虚拟时钟事件循环，去掉 margin 时三项失败、恢复后通过，用时约 1.5s；broker Redis flush 重建的轮询期限由固定 2s 改为按心跳周期推导（ttl×2）。
- 真实模型（DeepSeek Flash 经 OpenCode，临时 host 栈 + mock 沙箱）：
  - Chat 里创建、到点触发、修改为一次性、删除并 list：均正确；多步轮次缓存 95–97%。
  - “20 分钟后提醒”：模型先 list 取 `now`，再建 once 当日对应时间。
  - 会话忙：到点顺延，用户那轮结束后立即回复（parent 为用户轮）。
  - 页面新建任务为 Work，第二次触发落在同一会话，缓存 95%。
  - Work 会话里创建的任务触发后在沙箱写出并交付 markdown 文件，缓存 95%。
- 浏览器（内置预览，桌面与 iPhone 12 Pro 宽度、浅色/深色）：会话条和菜单、时间标记展开、立即运行后流式显示、调度器无人值守触发后已打开的页面自动接上流、定时任务页标签与状态链接、编辑器「一次」日期行。无横向溢出。

## 没覆盖到 / 风险

- 未在 PostgreSQL 上本地跑（CI 双库覆盖）；未部署。
- 真实沙箱（OpenSandbox）未测，Work 交付只用 mock 沙箱。
- 顺延按 1 分钟重试，最长 2 小时；多实例 API 下仍依赖用户锁 + CAS，未做多机压测。
- 旧任务的会话在下一次执行时才创建，之前的历史结果仍散在旧会话。

## 成本

- 迁移 0071：两列一索引，无回填。派发每分钟多一次绑定会话的忙碌查询（按主键/索引）；新增的 `schedule` 工具描述固定在工具列表里，随前缀缓存。

# Work 运行进展监督（2026-10-31）

基线 `origin/main@f316b85`。来源：DeepSeek《Super-agent 设计探索》第 6 节 Watchdog——长期运行的 Agent 不仅要监控进程存活，还要监控任务本身是否有实质进展、是否偏离目标。

## 现状与缺口

已有：重复操作检测（`stuck.py`，相同调用/结果）、每小时自查提示、长时间无进展暂停、reaper 处理基础设施故障。缺：一直在做“新的、看似合理但方向错”的事，机械规则看不出来。

## 实现

- Work 运行每累计 `AILAB_WATCHDOG_INTERVAL_SECONDS`（默认 600，60–86400）活跃工作时间，在步骤边界用同一模型做一次判定：重放本运行的全部消息与同一组工具，末尾追加一条平台检查指令，要求只输出 `{"verdict":"progressing|stalled|drifting","reason":…}`。参考 dsh 压缩调用复用前缀的做法，这次调用除指令和答案外都是缓存命中。
- `AILAB_WATCHDOG_MODE`：`off`；`observe`（默认）只记 `agent.watchdog` 事件与 `ailab_watchdog_verdicts_total{verdict}`，不改对话；`nudge` 首次非进展追加一条平台提示（并发 `agent.budget kind=nudge`，Portal 显示“提醒换个方法”），下次仍非进展则 `BudgetPause`（可继续）。
- 判定调用以 quiet 方式计费入账；模型失败、返回工具调用或非 JSON 记为 `unknown`，不影响运行。检查时间点存在本工作窗口的预算数据中，继续任务开启新窗口时重置。

## 成本

每 10 分钟活跃工作一次模型调用：输入几乎全部命中缓存（新增约 120 token 指令），输出几十 token。无常驻进程、无新存储。

## 验证

`tests/test_watchdog.py` 8 项：解析、observe 不改对话且检查请求是下一次真实请求的前缀、nudge 一次后暂停、off 不调用。完整套件交 CI。

## 未验 / 下一步

真实模型判定质量与误报率未测，因此默认 `observe`；观察 `agent.watchdog` 分布后再决定是否切到 `nudge`。

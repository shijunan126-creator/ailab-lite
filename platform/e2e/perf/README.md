# Portal performance benchmark

从仓库根目录运行（需要已有 `platform/.venv`、Portal `node_modules`、Redis 和 Playwright Chromium）：

```bash
platform/.venv/bin/python platform/scripts/bench_portal.py
```

脚本冻结 `97793e6` 归档和当前 dirty 源码；借用相同已安装依赖（Next/React 版本应与两份 lock 一致），不写原 Portal。复用各版本 `e2e/conftest.py` 的生产构建、四服务、浏览器和 SQLite 隔离。Redis 16450/13、四服务 18450–18453、Portal 13450；发现占用即拒绝。结束删除 `/tmp/ailab-perf`，不保留原始采样/日志/trace；不要把重定向的 stdout 提交到仓库。默认 `pytest e2e` 跳过全部 perf 用例，必须同时设置 `AILAB_E2E=1` 和 `AILAB_PERF=1`；推荐使用上述脚本统一设置临时日志/输出位置。

每版本 1×/4× CDP CPU 节流各三次，顺序 `(repeat 0: 1×,4×), …`。浏览器 1280×900，正常动效，无 tracing。每次新 context；账号只有两条固定会话，500 轮输入会话全部分页载入；流式会话每次加载最近 20 条历史，新答案追加后恢复相同的分页上限。

- 输入：70 次独立 `keydown`，每次等待页面 rAF + MessageChannel tick；丢弃前 5 次，65 次有效样本。时钟从捕获阶段监听器开始，排除 Playwright 往返，不是操作系统键盘队列延迟；MessageChannel 是绘制机会后的代理，不是物理显示测量。
- 流式：仅测试入口替换 `e2e.scripted_agent` 的模型查找，保留真实队列、Agent、事件落库及 Portal SSE；2000 字，每 50ms 两字（约 40 字/秒，非 tokenizer 实测速率），Agent 原有约 250ms delta 合批保持。记录实际到达率。
- 流窗口：首个 `agent.delta` 到新 turn 全文出现后 250ms。校验新 turn ID、至少 100 个 delta、至少 45 秒，防止重复答案误命中上一轮。长任务 buffered observer、连续 rAF 帧间隔、对提交前全部 `.turn` 的 subtree MutationObserver。
- 可见延迟：在应用实际使用的 SSE reader 上观察字节返回，不 tee；每个 delta 累积的纯文本在新 turn `.assistant-body` 中完整出现后，经 rAF/MessageChannel 记录。它衡量 reader 到 DOM/绘制机会，不含网络在途，也不证明物理像素；仅用于这个无 Markdown 标记的纯文字负载。
- MutationObserver 非 React Profiler：非零可来自 viewport 挂载、属性、全局 locked/last 或结束状态；零也不能证明 React 未执行 render。报告代理值，不将其冒充组件执行次数。
- JS：Next conversation route `page_client-reference-manifest.js` 中 `entryJSFiles` 去重、每文件 gzip 后求和；GenUI 检索生产 JS 中 `createLibrary`，报告所在 chunk、gzip 和 entry 成员关系。使用十进制 KB（80,000/60,000 B）。
- 可选解析：Node 单次首解析约 16,000 字符，使用 Portal 同款 `createParser` 和实际 schema；不含模块加载、React 或布局，也不受 CDP 节流。

表中输入/帧 p95 为三次各自 p95 的中位数；DOM 取三次中位数；输入最大值、长任务数、mutation 数、可见最大延迟取三次最坏值，避免硬门槛被中位数隐藏。16.7ms 的浮点比较保留到 0.001ms，避免 `16.700000000004` 被误判超标。40 万字全文/滚动、store 每帧更新数、GenUI 骨架和服务端 RSS 不在这套新增基准中，不能因此宣称完整 HLD 已验收。

# Portal 性能基准（2026-10-30）

仅新增 [perf harness](../../../platform/e2e/perf/README.md) 与 [runner](../../../platform/scripts/bench_portal.py)，测量并报告，不修 Portal/后端，不提交、push 或部署。分支 `feat/frontend-genui`，HEAD 与本地 `origin/main` 均为 `97793e601d90d0e9db11d60621c4f1352450166b`。基线由该提交 `git archive`；current 为启动时复制的 dirty 树，Portal 相对路径及文件内容排序 SHA-256：`93a85a57d20034847dde974ecdeda5352cc891b7adf6de08789209bd0dfe221f`。后续并行修改不在本次结论内；不修改共享 `current.md`/`HANDOFF.MD`，遵守本次文件归属约束。

## 方法与环境

MacBook Air / Apple M1（8 核、16 GB），macOS 15.8.2 arm64；Python 3.12.11、Node 26.11.1、Playwright Chromium **156.0.8078.4**，headless 1280×900、正常动效。共用相同已安装工具链：Next 16.4.2、React/React DOM 19.3.1、TypeScript 5.9.3，与两份 lock 一致。不是目标 2vCPU 机器容量结论，未隔离同机其他工程师的负载。

复用各副本 `e2e/conftest.py` 的生产构建、四服务、浏览器 fixture 和全新 SQLite。每构建 1×/4× CDP `Emulation.setCPUThrottlingRate` 各三次、顺序交替；账号仅两条固定会话，避免侧栏规模污染。500 轮沿用既有 seed，全部分页载入并滚底；70 次独立 ASCII keydown，捕获监听器时钟→rAF→MessageChannel，丢弃前五次，各 65 个有效样本。计时不含 Playwright 协议往返，也不覆盖 IME/系统输入队列。

流式会话每轮载入最近 20 个旧 turn；测试专用 Agent 入口仅替换现有 E2E 模型查找，不改生产 Python。输出 2000 字、每 50ms 两字，保留真实队列/事件落库/约 250ms delta 合批/SSE。既有 mock 无定速参数；此测试接缝避免将无等待的 mock burst 当作 40 tok/s。实测字符率：基线 38.16–39.42、current 38.68–39.77 字/秒；每轮 199–201 个 SSE delta，**不是 tokenizer 实测速率**。

首个 delta 至新 turn 全文出现后 250ms 内，记录 buffered longtask、rAF 间隔及提交前所有 `.turn` 的 subtree mutations。读取应用实际 SSE reader，不 tee；累积文本在新回答 `.assistant-body` 完整出现后，以 rAF/MessageChannel 记录可见代理。全文检查绑定新 turn ID，并要求 ≥100 delta、≥45s，防止相同回答误命中旧 turn。试跑曾发现此陷阱，已作废并重新完成全部正式测量。

下表 p50/p95 为三次各自分位数的中位数；max、长任务、mutation 为三次最坏值；DOM 为中位数。输入 p95 范围：基线 1× 16.7–17.6 / 4× 19.8–20.1ms；current 1× 16.1–17.1 / 4× 18.7–19.4ms。预算来源：[HLD §10](../../architecture/design/2026-10-28-frontend-genui-hld.md)，KB 按十进制；16.7ms 浮点比较保留到 0.001ms。

## 结果

| 指标 | CPU | 基线 | current | 预算 | 结果 |
|---|---:|---:|---:|---|---|
| 输入 p50/p95/max（ms） | 1× | 15.2 / 17.1 / 18.3 | 15.6 / 16.4 / 17.4 | p95 ≤18.81 | PASS |
| 输入 p50/p95/max（ms） | 4× | 17.7 / 20.1 / 20.6 | 17.9 / 18.8 / 19.8 | p95 ≤22.11 | PASS |
| DOM 节点 | 1× | 740 | 736 | ≤888 | PASS |
| DOM 节点 | 4× | 740 | 736 | ≤888 | PASS |
| >50ms 长任务 | 1× | 0 | 0 | 0 | PASS |
| >50ms 长任务 | 4× | 0 | 0 | 0 | PASS |
| 帧 p95（ms） | 1× | 16.7 | 16.7 | ≤16.7 | PASS |
| 帧 p95（ms） | 4× | 16.7 | 16.7 | ≤33 | PASS |
| 历史 turn mutations（代理） | 1× | 11 | 15 | 0 | 代理 FAIL* |
| 历史 turn mutations（代理） | 4× | 1 | 5 | 0 | 代理 FAIL* |
| delta→可见 p95/max（ms） | 1× | 17.6 / 19.7 | 150.7 / 164.4 | max ≤150 | FAIL |
| delta→可见 p95/max（ms） | 4× | 18.2 / 20.2 | 158.9 / 168.7 | max ≤150 | FAIL |
| chat entry JS gzip（B） | — | 61,731 | 73,164 | ≤141,731 | PASS |
| GenUI lazy chunk gzip（B） | — | 无 | 83,271 | ≤60,000 | FAIL |
| 16,000 字符单次解析（ms） | Node、无节流 | 无 | 3.585 | ≤8 | PASS |

Next conversation route `page_client-reference-manifest.js` 的 `entryJSFiles` 去重：基线 5 个文件，current 6 个，逐文件 gzip 后求和；增加 11,433 B。含 `createLibrary` 的 current chunk 为 `static/chunks/0__qyqqggsnhh.js`，**不在 entry 清单**；渲染器、目录、SVG 图表未拆成独立图表块，不能额外宣称图表拆包预算通过。解析使用 Portal 同款 `createParser` 与真实 schema，单次首解析，无预热；不含模块装载、React、布局或浏览器 CPU 节流。

*MutationObserver 不是 React Profiler：基线也非零，变化可能来自 viewport 挂载、属性、`last`/`locked` 变化及完成状态。代理未达到零，**“非流式轮次 React render 0 次”仍未证实**，不能把 15/5 当成 React 执行次数。可见延迟是 reader→DOM/绘制机会代理，不测网络在途或物理像素；测量与观察器开销未扣除。

## 失败路径的代码推断（未作因果对照实验）

- 文字延迟：`platform/portal/components/chat/turn.tsx:251` 接入 `useSmoothText`；`components/motion/smooth-text.ts:6` 固定 120ms drain，effect 后还需逐帧释放；`lib/stream/batcher.ts:25` 先等待 rAF 合批。多次帧边界再加渲染，符合基线约 18ms→current 约 151–159ms p95 的变化。需要 Portal 工程师做隔离对照后确认，不在本次调整动画。
- 包体：`components/genui/openui/block.tsx:5` 一起导入 Renderer/createParser 与整个目录；`components/genui/catalog/components.tsx:7` 引入 react-lang，`:11` 静态引入全部图表、`:12` 引入 schema。该依赖图是超 60KB 的主要嫌疑；实际 83,271 B 与懒加载成员关系已经生产构建验证，具体模块占比未做 bundle analyzer。
- mutation 代理：`components/chat/chat.tsx:898` 的 `last`、`:906` 的 `onRetry`、`:909` 的全局 `locked` 会在新轮开始/结束时变化；Turn 内 viewport 也会更新 DOM。这是候选解释，不是持续流式重渲染的证明。

## 复现与验证边界

从此工作树根目录：

```bash
platform/.venv/bin/python platform/scripts/bench_portal.py
```

端口仅 Redis 16450（DB13）、服务 18450–18453、Portal 13450；端口占用即拒绝。脚本退出已停止所启动服务并删除 `/tmp/ailab-perf`，最终监听检查为空。仓库不保存原始采样、JSON/JSONL、日志、trace 或生成 build；harness 文档说明测量边界与后续复现。runner 后处理已采用硬门槛三轮最坏值，本表按同一批有效采样重算，不为汇总调整重跑浏览器。

正式 perf：基线 **6 passed / 376.66s**、current **6 passed / 384.53s**；这表示采样完整，**不是全部预算通过**。fixture 同时执行两份 Portal 测试及生产 build：基线 45 项、current 72 项通过/build 成功。新增文件 Ruff check/format、默认关闭性能开关时 6 skipped 通过。未跑后端全套（无后端改动且有并行工作）；未新增或重跑 40 万字全文/滚动检查，未测 store 每帧更新次数、GenUI 骨架、服务端 RSS、真实模型/目标机容量。沿用[前次记录](2026-10-29-frontend-genui-integration.md)的历史证据，不把它们冒充本次检查。

下一步由 Portal 工程师处理可见延迟与 GenUI 包体，并用相同入口重新比较；若要验收 React render 零次，需要另行合适的组件执行计数。本次不推进其他 HLD 阶段。

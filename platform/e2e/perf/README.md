# Portal 性能基准

`platform/scripts/bench_portal.py` 用同一套已安装的 Next/React 依赖，比较内部基线归档与当前源码副本的 Portal。它复用 E2E 的 Portal 生产构建、四服务、Redis 和 SQLite 隔离，不改原工作树。

从仓库根运行：

```bash
platform/.venv/bin/python platform/scripts/bench_portal.py
```

前置条件：`platform/.venv`、`platform/portal/node_modules`、Redis 和 Playwright Chromium 已安装。脚本检查两份 lock 与本机 `node_modules` 的 Next/React/TypeScript 版本一致；版本漂移时拒绝比较。

脚本用 `/tmp/ailab-perf`，占用 Redis 16450/13、四服务 18450–18453 和 Portal 13450；端口已被占用时拒绝启动，不终止其他服务。结束时停止自有 Redis 并删除整个工作目录，不把原始采样、日志或 trace 写入仓库。

单独运行 perf 用例时必须同时设置两个开关：

```bash
cd platform
AILAB_E2E=1 AILAB_PERF=1 \
AILAB_PERF_OUTPUT=/tmp/<owned>/perf.jsonl \
AILAB_E2E_LOG_DIR=/tmp/<owned>/e2e-logs \
.venv/bin/pytest e2e/perf -x -s --basetemp /tmp/<owned>/pytest
```

`platform/e2e/perf/conftest.py::paced_model_entrypoint` 要求设置 `AILAB_PERF_OUTPUT` 与 `AILAB_E2E_LOG_DIR`，并把 E2E 的脚本模型入口换成按固定节奏发送 delta 的实现。基准脚本会把两者都放在仓库外。

## 测什么

每个版本在 1× 与 4× CDP CPU 节流下各运行三次，报告输入、DOM、长任务、帧间隔、流式期间 mutation 和 delta 到 DOM/绘制机会的延迟。输入延迟从捕获阶段监听器开始计时，不含 Playwright 往返；可见延迟在应用实际使用的 SSE reader 上观察，不含网络在途。报告还比较 conversation route 的 entry JS gzip、GenUI chunk gzip 和单次解析时间。

`table()` 在 `platform/scripts/bench_portal.py::table` 中定义硬阈值。输入/DOM 相对基线、帧 p95、流式 mutation、可见延迟、JS/GenUI 字节和解析时间分别判定；信息项不参与 PASS/FAIL。

MutationObserver 不是 React Profiler，零值不能证明组件没有 render。这里没有覆盖 40 万字全文滚动、每帧 store 更新、完整 GenUI 骨架、服务端 RSS 或目标机 2 vCPU/8 GiB 联合负载，不能据此声称完整 HLD 或生产容量已验收。

# T16 · 观测基础与负载验证

分支 `feat/remaining-work`，OTLP/指标提交 `4bae361`，生命周期补充 `2c5000e`，模型/工具span接入 `aa5eced`，队列指标集成 `f6cc3a3`；后续完整集成结果以进度记录为准。

已实现 TTFT（实际尝试别名）、Chat/Work排队、双流队列深度、持久outbox积压/最老年龄、未对账账本、扫描耗尽，以及后续runtime可调用的resume/心跳失败指标。所有标签固定或由catalog工具白名单规范化，无user/run/file维度。队列重构后旧单流深度会错误显示0，已改为两条流总量并新增回归。

W3C traceparent经过HTTP中间件、内部client与durable job信封，Agent/Worker退出时恢复ContextVar。默认不加载OTel SDK；安装 `.[otel]` 并配置endpoint才启用有界OTLP protobuf导出，普通span默认5%、错误span保留，队列饱和可丢弃。HTTP、内部HTTP、model/tool/provider有span；不记录敏感正文/路径/URL query或异常消息。生命周期验证optional依赖并关闭exporter。采样和完整错误trace的边界见 [ADR0006](../architecture/adr/0006-optional-bounded-tracing.md)。

本轮新增traceparent/积压7项在原实现隔离副本全部失败，修复后观测专项32项通过；额外真实localhost OTLP HTTP collector接收并解析protobuf，验证零普通采样时错误span仍导出、父trace保持、异常内容不泄漏、禁用路径无SDK导入和非法endpoint拒绝。合计观测/OTLP专项37项通过，后续双流指标另增1项，待最终全套一起验收。

看板与7条告警位于 [deploy/observability](../../platform/deploy/observability/README.md)。真实arm64 `promtool 3.5.0 check rules`通过7条规则；未启动生产Grafana/Prometheus Operator。告警依赖kube-state-metrics/kubelet，ClamAV副本可用不等同其内容扫描端到端可用，readme已说明。lite不默认增加这些组件。

## 已有目标机负载

F8资源数据见 [Broker报告](2026-10-23-broker-streaming.md)，覆盖50MiB输入/100MiB输出和双路压力。

本轮新增专用2CPU/8GiB VM内Agent调度探针，进程容器限1CPU/256MiB；两条60秒慢Work替身和100条连续Chat，真实调度器与HTTPclient pool、memory队列，runtime替身不发付费模型/工具请求。最大活动任务3，Chat排队P95 **74.77ms**，RSS **76.85→84.10MiB（+7.25MiB）**，wall60.14s、CPU4.15s。这是调度/client开销，不能当作真实三个模型上下文峰值。[原始结果](broker-streaming-20261023/agent-scheduler-linux.json)，脚本 [agent_scheduler.py](../../platform/tests/load/agent_scheduler.py)。

修复该负载脚本的一个陷阱：必须先导入隔离的tests.conftest再导入service/config；否则配置会先读取开发.env。通过isort分段保留顺序。

## 成本与未完成验收

关闭OTel时只有ContextVar透传和no-op分支，无exporter线程。启用后每进程1条有界后台导出线程；所有结束span先本地记录再选择导出，未对启用时目标机CPU/RSS做完整模型负载测量。API scrape多3条DB查询，COUNT随未ACK任务数增长，最老年龄使用0014的created_at索引；按建议30秒采集，不遍历对象存储或全files JSON。

完整五服务混合SSE+上传+下载、20MiB Office的新版本联合负载、真实监控部署与错误全trace保留尚未验收。现有F8和调度器数据仅覆盖各自声明场景，T16保持部分完成直至覆盖剩余场景。

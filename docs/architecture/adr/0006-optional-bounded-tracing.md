# ADR 0006 · 精简部署的可选有界追踪

状态：采用（2026-10-23）。

单机默认依靠Prometheus低基数指标和请求相关ID，不默认运行追踪Collector或加载OpenTelemetry SDK。设置OTLP HTTP traces endpoint并安装otel extra后才启用追踪；错误配置在服务启动失败，不静默回退。等待导出的span队列256、每批32、导出超时2秒，不把外部Collector阻塞加入业务请求链。

为了保留普通采样外的错误span，SDK本地记录span，在导出前按trace ID选默认5%的普通span，额外保留所有已记录错误span。它增加启用时的本地记录开销，且无法保证导出错误的完整trace；队列饱和也可丢span。需要完整错误trace时，应在资源足够的Collector配置tail sampling，不能声称head sampling会回溯恢复未记录的数据。lite不增加这种常驻组件。

仅记录HTTP方法/路由模板/状态、固定工具名与provider operation；异常只记类型，不记录消息、请求/回答正文、文件路径、查询串、用户/run/file ID和凭据。W3C上下文跨内部HTTP和队列传递，关闭OTel仍可传递合法上下文。观测故障不改变授权、计量或任务恢复合同。

数据库积压gauge只读API-owned表，不在scrape中遍历对象存储或扫描files JSON。容量告警采用kubelet卷用量；待确认任务COUNT成本随积压增长，按默认30秒采集而非高频轮询。看板/PrometheusRule与lite部署解耦，依赖说明见[观测组件](../../../platform/deploy/observability/README.md)。

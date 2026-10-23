# 可选观测组件

lite 默认只暴露带服务凭据保护的 `/metrics`，不安装 Prometheus、Grafana 或追踪服务。Prometheus 每个服务只采集一个进程端点（建议 30s），各自使用受限 metrics 凭据；不要把任一服务的完整身份凭据写进公开 ConfigMap。

`dashboard.json` 可导入 Grafana；`alerts.yaml` 需要 Prometheus Operator CRD、kube-state-metrics 和 kubelet PVC 指标，不属于 base kustomization，不要在精简部署中直接 apply。扫描副本告警只判断 Pod 可用性；活着但扫描无响应的路径靠扫描错误/耗尽信号，不能将其解释为签名服务端到端健康检测。

可选追踪：安装 `.[otel]`，设置 `AILAB_OTEL_ENDPOINT=http://collector:4318/v1/traces`。默认不加载 SDK/exporter。启用后进程仅保留256个待导出span、每批32个、导出超时2秒；普通span按trace ID选5%，错误span始终保留（队列饱和仍可丢弃），不是完整错误trace全量保留。所有span先在本地记录，结束时决定是否导出，以保留采样外错误；开销应在目标负载另测。若需要完整错误trace，在集群Collector设置tail sampling，此处不伪称head sampling能回溯丢弃的span。

HTTP span仅含方法、路由模板、状态，provider span仅含固定operation/provider；不记录URL query、请求/回复内容、文件路径、用户标识、令牌或异常消息。W3C traceparent 经内部HTTP与持久job信封传播；禁用OTel仍可转发合法上下文，不导出数据。

积压、最老任务与未对账gauge在API采集时读数据库：每scrape三条查询，COUNT成本随未确认任务数变化；最老任务使用created_at索引。不要通过高频采集全对象存储来推算实时容量。磁盘指标使用kubelet实际卷用量，包含可回收临时数据。

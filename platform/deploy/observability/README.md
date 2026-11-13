# 可选观测组件

lite 默认只暴露带服务凭据保护的 `/metrics`，不安装 Prometheus、Grafana 或 tracing collector。每个 API、Agent、Broker、Worker 进程各有一个 metrics 端点；用 `platform-ops-identity` 的 `AILAB_SERVICE_CREDENTIAL` 认证，只有 `ops` 身份有 `metrics:read`。不要把完整服务凭据写进公开 ConfigMap。

建议每 30 秒抓取一次：

```yaml
scrape_configs:
  - job_name: ailab
    authorization:
      credentials_file: /etc/prometheus/ailab-ops-token
    metrics_path: /metrics
    scrape_interval: 30s
    static_configs:
      - targets:
          - platform-api:8080
          - platform-agent:8082
          - platform-worker:8083
          - NODE_IP:8081        # Broker 在 lite 里是 hostNetwork
```

`dashboard.json` 可导入 Grafana。`alerts.yaml` 需要 Prometheus Operator CRD、kube-state-metrics 和 kubelet 卷指标，不属于 `base` kustomization，不要在精简部署里直接 apply。扫描器告警只判断 ClamAV Pod 是否可用；活着的进程仍可能扫描无响应，另用 `ailab_scan_exhausted_total` 和文件处理错误观察端到端扫描路径。卷容量用 kubelet 的实际卷用量，不要靠高频遍历对象存储估算。

指标和标签基数由 `platform/ailab_platform/metrics.py` 定义。HTTP 的 `route` 是路由模板，工具名和模型名会归并到已知集合，run/user/file ID 不做标签。API 的 scrape-time gauge 会做少量数据库查询；最老 outbox 使用 `(created_at, id)` 索引，积压 COUNT 的成本随未确认任务增长，因此保持低采集频率。

可选追踪：安装 `platform[otel]`，设置 `AILAB_OTEL_ENDPOINT=http://collector:4318/v1/traces`。默认不加载 SDK/exporter。启用后待导出队列 256 个 span、每批 32 个、导出超时 2 秒；普通 span 按 trace ID 选 5%，错误 span 始终保留，但队列饱和仍可丢弃，不是完整错误 trace 的全量保留。span 只带方法、路由模板、状态和固定 operation/provider；不记录 URL query、请求/回复内容、文件路径、用户标识、令牌或异常消息。需要完整错误 trace 时在 Collector 做 tail sampling。边界见 [ADR 0006](../../../docs/architecture/adr/0006-optional-bounded-tracing.md)。

部署和 Prometheus 接入的整体说明见 [部署模块](../../../docs/modules/deploy.md#观测)。

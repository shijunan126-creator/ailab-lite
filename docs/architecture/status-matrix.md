# 实现状态（增量）

2026-10-23，基于 `6666573` 的 T03/T05 更新；其余能力仍以 [差距审查](../reviews/2026-10-23-single-node-architecture-gap.md) 为基线。

| 能力 | 当前状态 | 验证边界 |
|---|---|---|
| T03 严格配置与 secure profile | 已实现 | backend/range、默认秘密、mock/stub、注册、外部模型、可信代理、日志脱敏有回归；scanner 实现由 T04 交付 |
| T05 服务身份和 scope | 已实现（静态凭据适配器） | 所有现有 `/internal` 路由巡检，含 FastAPI 延迟 include；新业务端点仍必须声明 scope |
| RunContext / Worker 文件状态授权 | 已实现 | 跨服务、跨用户、未附加文件、READY 扫描回写拒绝；会话附件与产物双库覆盖 |
| K8s Secret 最小分发 | 已实现配置 | 独立令牌 + verifier、幂等升级经 fake kubectl 和 manifest 测试；未操作真实集群 |
| SA TokenReview / TLS 自动轮换 | 未实现 | 保留 ServiceAuthenticator port，见 [ADR 0002](adr/0002-service-identity.md) |
| 真实扫描与隔离解析 | 并行实现中 | 本分支只交付 scanner_backend 配置与 secure 门禁 |

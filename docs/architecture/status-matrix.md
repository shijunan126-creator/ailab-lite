# 当前实现状态（增量）

基线为 `feat/arch-hardening@609c1d3`，目标与其他能力差距见 [2026-10-23 审查](../reviews/2026-10-23-single-node-architecture-gap.md)。此文件在基线中不存在；这里只登记本分支实际改变的能力。

- **T13 身份 / RBAC / 部门 / 审计：已实现后端。** 稳定 user_id、外部身份唯一键、部门、角色、配额策略、事务审计、管理元数据 API；无跨用户内容读取权限。管理 Portal UI 由 lead 并行实现。
- **T17 OIDC：已实现适配器及 Portal 服务端路由，本地 issuer 已验收。** 授权码 / PKCE / state / nonce / JWKS 验签、组映射、停用和平台注销；真实 Keycloak gate 待配置后验收。
- **部门用量快照 / 配额全入口：等待合并 A。** 本分支只接入 token 准入的策略解析；账本基线没有 dept_id 或冻结金额，不能将当前部门查询冒充历史部门报表。

配置、权限矩阵、性能证据及合并点见 [身份实现交接](../reviews/2026-10-23-identity-implementation.md)。

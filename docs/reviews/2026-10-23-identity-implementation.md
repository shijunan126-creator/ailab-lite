# 2026-10-23 · T13 / T17 身份与管理 API 交接

基线 `609c1d312a1a546035da0f0c2000377b064fcbf8`，分支 `arch/w2-identity`。仅本地提交，不推送。Project 保持个人资料容器；没有新增管理员访问其他用户内容的接口。

权限矩阵（多角色取能力并集）：

- `user`：本人业务接口；所有 `/v1/admin` 拒绝。
- `dept_admin`：仅本部门用户元数据、创建、修改、停用/启用、密码重置、配额分配；可授予 `user/dept_admin`，不能修改平台管理员/审计员，不能移动用户跨部门。用户列表的部门条件直接进入 SQL。可读本部门部门条目及策略目录（人数限本部门），用量查询也限定本部门。
- `platform_admin`：全部管理元数据接口、审计、用量；仍没有其他用户内容访问权。
- `auditor`：只读审计及全局用量；不能管理用户、部门、策略。

所有管理操作（包括读取和拒绝）记审计；成功写入与审计同事务，失败审计另起事务，不提交失败业务变更。登录成功/失败、注册、注销、密码修改和 CLI 管理也留审计。detail 采用字段白名单，密码、Token、原始请求体与 IdP 响应均不写入。审计尚不负责外部 WORM 归档或保留清理。

`GET /v1/me` 和已有 `/v1/auth/me` 均增加 roles/dept/can_admin。用户和审计列表返回 items/next_cursor；部门、策略保持要求的数组响应，下一页在 `X-Next-Cursor`，Portal 同源代理已透传。全部列表 `limit <= 100`、默认 50。用量报表默认本月，`to` 为排他边界；最多 366 天、1000 个分组，超出返回 422。

## OIDC 配置

均以 `AILAB_` 为前缀：

- `AUTH_BACKEND=local|oidc|both`，默认 local；`OIDC_LABEL` 默认「统一身份登录」。oidc 模式关闭密码登录和注册。
- `OIDC_ISSUER`、`OIDC_CLIENT_ID`、`OIDC_REDIRECT_URI`（Portal 的 `/api/auth/oidc/callback` 完整地址）启用 OIDC 时必填。
- `OIDC_CLIENT_SECRET` 可选，SecretStr；有值时使用 client_secret_basic，无值时为 public client + PKCE。
- `OIDC_GROUPS_CLAIM=groups`，支持点分路径。
- `OIDC_ROLE_MAPPING` 为 JSON，如 `{"/pilot-admins":["dept_admin"]}`。
- `OIDC_DEPARTMENT_MAPPING` 为 JSON，如 `{"/pilot":"dept_xxx"}`；映射目标必须已存在，多部门命中拒绝登录。
- `OIDC_JWKS_TTL_SECONDS=300`（10–3600），`OIDC_FLOW_TTL_SECONDS=600`（60–900），`OIDC_HTTP_TIMEOUT_SECONDS=15`（1–60）。

配置强制 HTTPS；dev/test/smoke/k3s 可使用 loopback HTTP。实现授权码 + S256 PKCE，state 与浏览器独立绑定 Cookie，nonce、iss/aud/azp/exp/iat、RS256/JWKS 验签；未知 kid 刷新一次 JWKS，metadata/keys 缓存有 TTL。服务端流程单次原子消费，重启不丢有效流程。每次 IdP 响应最多 128 KiB，ID token 最多 32 KiB，总时限及连接/读取时限受配置控制。

账号只按 `(iss, sub)` 链接，绝不按 email 或 preferred_username 自动合并。JIT 用户使用内部唯一 email，角色/部门在每次登录映射；移除 IdP 组会移除旧映射。平台停用始终优先于有效 IdP 会话。上游 access/id/refresh token 不持久化，不给浏览器。注销撤销平台会话并清 Cookie；可选 IdP end-session 本轮未接。没有修改登录页或管理页 UI。

验证标准依据 [OIDC Core ID Token Validation](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation) 和 [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636)。

## 迁移与合并点

`0013_identity.py`：revision=0013，down_revision=0008，由 lead 重接链。新增四张要求的表及短期 `oidc_flows`；User 新字段全可空。SQLite 直接 ADD COLUMN REFERENCES，绝不重建 users。现有用户保留 ID，回填 user 角色，名为 admin 的本地用户迁移获得 platform_admin；配置的引导账号在启动时幂等授权。SQLite 开启外键、现存 Run/Token、部门父子、策略、审计的 upgrade → downgrade → upgrade 已验证，双库 metadata drift 为空。

**必须在合并 A 的账本分支时处理：**

- 本基线 ModelUsage 没有 dept_id，按要求未越权改它；不能声称已存历史部门快照。当前报表暂用用户当前部门，A 的 dept_id 存在时自动选择该列；写入快照仍由 A/lead 接到 usage 入口。
- `_cost()` 优先读 A 的 `weighted_amount`，本基线暂用旧公式。合并后必须使用冻结金额；旧公式仍依赖当前缓存折扣。
- `quota.py` 只改 enforce_token_quota 的一个限额解析点。`limits_for(user)` 已返回日/月/库容量/并发完整策略；usage_summary、资料库准入和并发准入留给 A/lead 统一接入，未重写 reservations。
- 用量全局时间范围聚合需要 A 的 ledger 时间索引/汇总；本分支未改 ModelUsage 或其索引，现有 run/created 复合索引可用于用户范围查询，不能当作全局报表的大规模验收。

所有权外的改动：config.py 增量独立 OIDC 配置块；deps.py 一行将 Principal 附到 request.state；security.py 认证加载角色的 joinedload（避免普通会话列表 SQL 从 3 增至 4）；api/main.py 注册路由和 /v1/me；quota.py 上述单一接入点；pyproject.toml/constraints.txt 增加 PyJWT[crypto] 及其依赖；Portal 通用代理只透传 X-Next-Cursor；回归测试与交接文档。未改其他工程师实现。

## 性能及验证边界

没有新增后台循环。流程过期清理仅登录开始时按 expires_at 索引执行；审计每次操作新增一行。用户全局/部门 keyset 与审计全局/actor/action/target/request keyset 都有索引。1 万用户、100 部门、1 万角色、2 万审计的 SQLite/PostgreSQL EXPLAIN 和 30 次热查询均值见下表；这些是合成索引探针，不是整栈或 2 vCPU 生产容量承诺。含 q 的任意子串搜索仍需扫描候选用户。

本机独立进程 import API 三次：基线 RSS 93.27–95.67 MiB / CPU 0.520–0.525 s；身份分支 103.94–104.27 MiB / CPU 0.584–0.668 s。新增约 10 MiB 常驻导入开销（主要为 OIDC/crypto 依赖和路由）；不等同服务运行峰值。原有会话列表 3 次 SQL 的回归门槛保持通过。

真实 Keycloak 测试位于 `tests/contracts/test_keycloak_identity.py`，由 `AILAB_TEST_OIDC_ISSUER` 启用；另需测试 realm 的 `AILAB_TEST_OIDC_USERNAME/PASSWORD`，可选 CLIENT_ID/CLIENT_SECRET/REDIRECT_URI。当前环境未配置，明确跳过。普通单元测试则实际启动本地 HTTP issuer、生成 RSA 密钥并服务 JWKS，覆盖 state/binding/nonce/过期/azp/错误 audience/issuer/重放/未知 kid 轮换/停用/组映射。

浏览器真实回归使用独立临时 API+SQLite、生产 Portal、本地 fake IdP；IdP 用 localhost，Portal 用 127.0.0.1，验证跨站回跳、Strict httpOnly 平台 Cookie、Lax 流程 Cookie 清理、document.cookie 为空、注销后 401。修复前 Next 将回跳主机改成 localhost 而丢会话，修复后相对 Location 保持访问者主机；Portal Node 回归固定这个契约。内置 preview snapshot 连续两次超时后才改用仓库 Playwright 流程。

最终验证（本分支同一实现，macOS Python 3.12，GNU 工具在 PATH；真实服务仅 Redis 16470、PG16 15470）：SQLite + Redis **463 passed / 10 skipped**；PG + Redis **472 passed / 1 skipped**；其中 Keycloak 未配置占 1 skip，其余 SQLite 轮次为 PG 专用项。完整浏览器 E2E **34 passed**；Portal Node **9 passed**，生产构建通过；Ruff check / format、diff check、依赖锁一致性通过。迁移与双库各含 identity 往返，额外开启 SQLite 外键及部门父子关系的定向往返 **2 passed**。既存 Starlette/httpx 弃用警告保留。临时服务已停止；没有连接开发者 .env / var 数据库。

## 历史索引探针摘要（2026-10-23，非本轮重测）

| 查询 | SQLite 均值 ms | PostgreSQL 均值 ms |
|---|---:|---:|
| `users_global` | 0.107 | 0.168 |
| `users_dept` | 0.152 | 0.230 |
| `audit_global` | 0.128 | 0.195 |
| `audit_actor` | 0.036 | 0.119 |
| `audit_action` | 0.138 | 0.231 |
| `audit_target` | 0.048 | 0.164 |
| `audit_failure` | 0.039 | 0.107 |
| `user_roles` | 0.034 | 0.084 |

SQLite 均使用对应覆盖索引（request 查询为普通索引）；PostgreSQL 部门页使用复合 Bitmap Index Scan 后排序，action 页使用时间索引后过滤，其余使用对应普通/覆盖索引。这些是历史合成结果，原始 JSON 已按精简约定移除。

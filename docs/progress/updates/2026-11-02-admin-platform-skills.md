# 管理员平台技能：后端实施（2026-11-02）

## 范围与基线

用户授权 API、数据模型、一次双库迁移、Agent/Broker 文件输入授权、回归测试、提交并推送 `origin/feat/admin-platform-skills`；不改 Portal，不建 PR、不合并、不部署。设计与最终字段见[设计接口节](../../architecture/design/2026-11-02-admin-platform-skills.md#接口后端最终契约)。`current.md` 按分工留给 Claude。

基线 `origin/feat/admin-platform-skills@f5b1c1a`。指定本地分支已由 Claude 的 `.worktrees/platform-skills` 占用，Git 拒绝重复 checkout；当前隔离 worktree 使用 `impl/admin-platform-skills`，向同名远端 feature 分支推送，不移动 Claude 的本地分支或文件。

## 已实施

- 迁移 **0047**：`platform_skills`、`user_platform_skills`，双库升级/降级与 ORM 漂移核对；用户主键与反向技能/副本索引。
- 管理元数据、版本上传/扫描自动发布、历史与回退、在线草稿；系统所有者 `sys_platform_skills` 默认停用且无登录凭证，平台文件不计个人配额。新增管理员写操作进入审计。
- 用户订阅、自定义草稿确认与 lineage/base 原子绑定、恢复默认、取消添加；管理员软删除脱钩个人副本。目录有分页，订阅最多 100 项。
- 已添加的平台技能并入 `/v1/skills`，保留按名称的用户开关与全局/部门策略。自定义副本保留个人审批边界。旧 `market.py`、`routes_market.py`、市场测试与注册移除。
- 创建 Run 时冻结具体技能文件 ID；bootstrap 和内部读文件检查同一快照、当前订阅与策略，禁止未添加用户和未发布版本读取。`run_asset_refs` 保留引用中的历史文件。Agent/Broker 继续复用已有文件注入协议，无新增沙箱执行权限。

## 验证

最终产品代码基线 **`d85da1b74945a0da1c238003f3bd9c7274e1842c`**（schema 提交 `fb5d3a2` + API 提交 `d85da1b`）；后续提交只加入回归用例和记录。原始日志在仓库外 `/tmp/ailab-platform-skills-*.log`，不提交。

- 最终 Ruff check / format check、`git diff --check`：通过。
- 技能相关定向初检：61 passed（个人技能、草稿、设置、治理与新平台链路，集中修正前）。
- **最终迁移/平台技能/reaper 组：87 passed**，包含 SQLite + PostgreSQL 全部迁移、0047 升降级/漂移/Run 查询 EXPLAIN、15 项新平台回归和 6 项 reaper。0047 新增当前版本、自定义 base、草稿 base 的反查索引，避免保留检查扫描全平台引用；审批批量读取。
- **最终原失败相关文档/渲染/OCR/模型回退组：65 passed**。
- **最终 PostgreSQL 平台技能 + 集成迁移：17 passed**（15 + SQLite/PG 集成迁移各 1）。
- 全量 Python `pytest tests` **仅执行一次**：1935 passed、162 skipped、20 failed、6 errors（367.52 s）。这是集中修正前的实现，首次创建环境误用最新 Python 依赖且未构建 Office reader，不能称为最终全套全绿。20 个失败中，18 个文档解析相关用例在补齐 vendor reader 构建后通过；模型回退时序用例复核通过；迁移 head 断言更新到 0047。6 个夹具错误是新测试留下的软删平台记录仍引用文件，已在本测试 teardown 清除 registry 行，随后平台测试与 reaper 连跑通过。全套失败项全部包含在上面最终复核组中，按用户要求未再重复整套。

首次环境问题已纠正：按 `platform/constraints.txt` 安装 Python 依赖，并在 `vendor/zcode-office-runtime` 执行 `npm ci && npm run build`；这些是忽略目录中的本地测试依赖/构建产物，无产品依赖变更。最终复核没有已知失败。

PostgreSQL 使用 `/tmp/ailab-platform-skills-pg`、非默认端口 **15487**、专用 `ailab_migrations` / `ailab_platform_test` 库；未访问开发或部署数据库，**已停止**（`pg_ctl status` 确认 no server running）。未跑 Portal build/浏览器、真实模型、真实服务多容器验收（本轮仅后端，按用户指定验证范围）。

## 成本与剩余边界

无新依赖、常驻进程或轮询；扫描与 ZIP 解包复用 Worker 资源限制。平台内容共享存储，添加只写订阅；自定义才复制并计用户配额。目录有界，运行输入按订阅主键/文件主键及 lineage 索引查询。保留中的 Run、自定义 base 与活动草稿会使平台版本数超过普通历史上限，管理员软删除保留系统版本历史；不把软删除当作物理回收。

Portal 对接、UI 验证、首批技能内容和质量评测由对应实施方完成；未声称整体功能外部验收或已部署。最终整套未重复运行，因此最终证据是首次全量结果 + 失败项/变更相关测试复核，不是同一基线的一次整套全绿。下一步：Claude 拉取本分支、对接设计中的最终接口并完成 Portal 验证；无后端已知未修问题，未开放任何部署授权。

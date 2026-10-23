# T20 · 办公 Skills 治理（可落地部分）验收

基线 `61be178`（已集成 T09/T14 的 `feat/remaining-work`）。实现在分支 `t3/skill-manifest-policy-admin` 上的单个提交 `feat(skills): verify sandbox skill index and add admin skill policies`。本轮仅本地提交；没有推送、PR、部署或合并。

## 已实现

- **manifest**：每个技能目录一个 `skill.toml`，字段为 `name, version, description, license, requires, roles`。`skill_index.py` 严格校验字段集合、目录名、语义化版本和角色名。
- **确定性内容索引**：`skills/index.json` 对目录内每个文件记录相对路径、大小和 SHA256，并输出每个技能的整体哈希。符号链接直接拒绝。`images/sbx-office/skill_index.py` 与平台副本必须逐字节相同，由 `platform/scripts/check_skills.py` 在 CI lint 中强制；镜像构建时执行 `--check --requirements`。
- **沙箱证明**：Broker 的 `GET /internal/skills/index?lease_id=` 在租约实际所在的容器内执行校验器，并与 Broker 自身源目录的索引比较。结果按 `(lease_id, provider_ref)` 缓存在进程内，最多 512 条。退出码 `3` 表示漂移（409 `skill_index_mismatch`），其他失败为 503 `skill_verification_failed`。mock 沙箱永远为 503 `skill_index_unverified`。
- **Agent 加载**：每次 `load_skill` 都向 Broker 取证明，校验租约号、provider_ref、image_ref 非空，并与 Agent 本地索引逐项比较。任何不一致或不可证明都转为 ToolError，模型可见，运行不崩溃。
- **策略**（`skill_policies`，迁移 0017）：
  - 全平台或部门对内置技能设为 `allowed|disabled`，全平台禁用不能被部门覆盖（`skill_policy.effective_policy`）。
  - 技能 `roles` 非空时，角色外的用户视为禁用。
  - 个人技能策略 `allowed|review|disabled`，取全局与部门中最严格的一条。`review` 进入审核队列，管理员通过/拒绝写入 `skill_approvals`。
  - 个人策略为 `disabled` 时，用户无法开启个人技能。
- **策略作用面**：旧 `GET /internal/runs/{id}`、T14 `GET /internal/runs/{id}/model-input`（共用 `skill_input_for`）、`POST /v1/runs` 的显式技能选择，以及 Agent 的 `load_skill`（循环已拒绝 `disabled_skills` 中的名称，见 `agent/loop.py` 的 `_run_tool`）。
- **管理台**：平台管理员可见「技能」页签，含全平台/部门范围选择、内置与个人策略下拉、待审核列表和加载更多。
- **golden 入口**：`platform/tests/golden/run.py --self-test` 用仓库自有 fixture 生成 pptx/xlsx，做结构断言；已接入 CI lint。

## 明确未做 / 未验

- **公司模板、业务样本、真实付费模型输出**：外部输入缺失（ADR 0001）。golden 仅覆盖仓库自有 fixture 的结构性断言，不证明业务正确、渲染溢出、公式重算或模型质量，也没有夜间真实模型运行与费用上限。
- **真实沙箱**：本机没有 Docker daemon，也没有 OpenSandbox 环境。证明路径仅经 fake manager 单测和 mock 验证；真实容器内 `exec`、镜像构建与 `--requirements` 在目标机未跑。
- **镜像身份不是 digest 绑定**：租约记录的 `image` 是可变标签（默认 `harbor.ailab.local/sandbox/sbx-office:lite`）。证明的是**该容器内容**与仓库索引一致，不证明镜像 digest 可信。要绑定 digest，需要 provider 暴露容器的镜像 ID（Docker `inspect .Image`），本轮未做，属于后续项。
- **mock 与 E2E**：mock 沙箱下 `load_skill` 被拒（503 `skill_index_unverified`）。mock 运行的是宿主 checkout，与自身比较不能证明镜像。
- **Broker 重启**：证明缓存在内存中，重启后对活动租约重新执行一次校验。
- **`skills()` 进程缓存**：API/Agent 进程内只计算一次，源目录漂移需重启；`index.json` 与目录不一致时 API 启动即失败（CI 应先拦截）。
- **个人技能列表无上限**：`personal_skills` 读取用户全部就绪的个人技能文件，这是既有行为，未在本轮限界。

## 集成点（给主 Agent）

1. **循环**：未修改 `agent/loop.py`。策略经 `disabled_skills` 进入 `turn.disabled_skills`，循环已据此拒绝 `load_skill`；验证在 `agent/tools.py` 的 `load_skill` 分支完成。
2. **Broker**：`broker/main.py` 注册 `broker/routes_skills.py` 的路由；未修改 `broker/service.py`，不需要 prepare hook。验证在第一次 `load_skill` 时按租约进行。
3. **策略读取**：`ailab_platform/skill_policy.py` 的 `effective_policy(db, user)` 返回 `(被禁内置技能集合, 个人策略)`，`run_disabled_skills(db, user)` 返回并入用户个人开关的禁用列表。`api/routes_skills.py` 的 `skill_input_for(db, owner)` 是 bootstrap 字段的唯一来源。
4. **T14 patch**：`docs/reviews/2026-10-23-t20-model-input.patch` 已删除，改动直接落在 `api/routes_model_context.py`（`out.update(skill_input_for(db, owner))`）。
5. **迁移**：`0017_skill_policies` 的 `down_revision = "0016"`，单头已验证；未使用 stub。
6. **模型**：`SkillPolicy`、`SkillApproval`、`REVIEW_PREDICATES` 和稀疏索引定义在 `models.py`；原 `skill_models.py` 已删除。
7. **CI**：`ci.yml` 的 lint 任务新增 `scripts/check_skills.py` 和 `tests/golden/run.py --self-test`。
8. **Chat 与成本**：Chat 不提供 `load_skill` 工具，不启动沙箱；Work 中第一次 `load_skill` 若尚无沙箱，会先启动沙箱。

## 验证

环境：macOS arm64，Python 3.12.11（uv 管理），独立 venv `platform/.venv`，GNU coreutils/findutils 在 PATH 前（AGENTS §9）。临时 Redis 16379、临时 PostgreSQL 16 on 15432（`ailab_test`、`ailab_migrations`）；测试结束后已关闭。

- `ruff check .`、`ruff format --check .`：通过。
- `scripts/check_imports.py`：`IMPORTS_OK`。
- `scripts/check_skills.py`：`Skill manifests, directory hashes and image verifier agree`。
- `tests/golden/run.py --self-test`：通过（2 个 fixture）。
- `tests/test_skill_governance.py`、`tests/test_skill_policy_api.py`、`tests/test_prompts.py`、`tests/test_agent_loop.py`：全部通过。
- SQLite + Redis 全套 `pytest tests`：818 通过，49 跳过，0 失败。跳过项为真实外部组件或 PostgreSQL 专用项。
- PostgreSQL 16 + Redis 全套 `pytest tests`：848 通过，19 跳过，0 失败。迁移与漂移测试在 PostgreSQL 上真实运行；真实 Docker/OSB/S3/ClamAV/Keycloak 契约仍跳过（外部组件不可用）。
- Portal：`npm test` 14/14，`npm run build` 通过。
- E2E：`e2e/test_skill_admin.py`（真实 Chromium，Portal + API + Broker + Agent 全栈，mock 沙箱）通过。
- 全量 `pytest e2e`：47 通过，2 失败。两个失败在基线 `61be178` 上以相同断言复现，见下。

### 全量 E2E

本轮全量 `AILAB_E2E=1 pytest e2e` 为 47 通过、2 失败：

- `test_operations::test_one_run_can_be_followed_through_the_logs_of_three_services`：API 日志中找不到该 run 的 `[run_id]` 行。
- `test_portal::test_a_deck_that_cannot_be_previewed_says_so`：期待“预览生成失败”或预览图，实际显示“预览已更新”且无图。

同一提交的基线 `61be178`（`git archive` 导出到临时目录，同样在 GNU PATH 下运行）复现完全相同的两个断言失败。因此它们是集成基线已有的问题，不是本轮引入。本轮未修改这两个测试或其被测代码，需由主 Agent 单独排查。

E2E 中的行为变化：mock 沙箱下 `load_skill` 现在被拒绝。`agent.log` 记录 `skill attestation unavailable: broker answered 503`，模型收到 ToolError。deck 的后续流程仍可完成，因为 mock 模型不依赖技能正文。这是 mock 无法证明镜像的预期结果，不是回归。

## 红绿与关键回归

- 旧草稿中的以下测试在修正前即不成立，已改写：
  - `test_roles_are_enforced_in_shared_model_input` 修改的是每次重新计算的字典副本，因此从未生效。现改为临时技能目录与重建索引。
  - `test_agent_rejects_unbound_or_different_attestation` 因缺少 provider_ref 而失败，并非测试名所称的绑定检查。现逐项覆盖 lease_id、verified、provider_ref、image_ref、index。
  - `test_0017_direct_upgrade_downgrade` 手工建表且绕过真实迁移链；已替换为 `tests/test_migrations.py` 中的 0016→0017→0016→0017 往返测试，SQLite 与 PostgreSQL 均运行。
- 新增回归：证明按租约与资源缓存、漂移与 mock 永不缓存、退出码映射、Agent 对不绑定或漂移的证明拒绝、load_skill 工具错误映射、个人策略 disabled 时禁止开启、旧 GET 与 model-input 同一策略、审核查询的生产 SQL 在 SQLite 与 PostgreSQL 上命中 `ix_files_skill_review`。

## 成本

- 镜像构建：`skill_index.py --check --requirements` 一次。本机 `build_index` 对 752 KiB 技能目录约 9.6 ms（M 系列 Mac，未在目标机测）。
- 证明：每个租约一次容器 `exec`，包含一次 Python 启动和全目录哈希。目标机耗时未测。
- Agent：每次 `load_skill` 一次 Broker 请求，并在本地重算一次索引（约 10 ms）。
- 进程内：`skills()` 只计算一次，被 `functools.lru_cache` 缓存。

## 交接给 HANDOFF 的陷阱（请主 Agent 并入）

- 迁移号必须接在真实 head 之后；不要用 stub 或改号绕过。
- 修改 `skill_index.py` 后必须同步复制到 `images/sbx-office/`，`check_skills.py` 会检查。
- 技能 manifest 与 `SKILL.md` 的 `name` 必须与目录名一致；`index.json` 需与目录同次提交。
- macOS 上跑测试前先把 GNU coreutils/findutils 放到 PATH 前面，否则 `stat -c` 等失败。
- E2E 中登录会丢失 `?t=` 查询参数，需要先点击标签页。
- 普通 `pytest -q`：`pyproject` 的 `addopts = "-q"`，再加 `-q` 会抑制汇总行。
- 从其他 checkout 复用 `.venv` 会导入错误的 `ailab_platform`（可编辑安装指向主 checkout）。每个 worktree 需要自己的 venv。

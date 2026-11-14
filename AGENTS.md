# AGENTS.md

所有改动这个仓库的人和 Agent 都按本文工作。产品暂名 AILab，正式名称未定；文档里用“平台”指代，代码标识符（`ailab_platform`、`AILAB_*`）不变。

## 1. 开始之前

1. 读 [docs/progress/current.md](docs/progress/current.md)：有没有进行中、暂停或等用户决定的工作。
2. 核对实际状态：`git status --short --branch`、当前分支、HEAD、与 `origin/main` 的关系。保留已有改动；记录和代码对不上时，以代码和用户最新指示为准，并修正记录。
3. 按下面的路由表只读与任务相关的模块文档和 HANDOFF 分节。不要为了一个改动通读全仓或全部文档。
4. 计划不等于授权。用户没让做的路线图条目不要自动开始；暂停和待决定的事项跨会话保持。

## 2. 读取路由

| 要改的东西 | 先读 | 坑 |
|---|---|---|
| Agent 循环、工具、提示词、上下文与压缩、模型调用、联网工具 | [modules/agent.md](docs/modules/agent.md) | [HANDOFF#agent](HANDOFF.MD#agent) |
| API 路由、数据模型与迁移、Run 状态、outbox、配额与账本、会话/分叉/定时/搜索 | [modules/api.md](docs/modules/api.md) | [HANDOFF#api](HANDOFF.MD#api) |
| 上传、扫描解析、预览/OCR/页图、文档输入、交付件、资料库 | [modules/files.md](docs/modules/files.md) | [HANDOFF#文件](HANDOFF.MD#文件) |
| Broker、租约、沙箱镜像与办公技能、渲染沙箱、受控联网 | [modules/sandbox.md](docs/modules/sandbox.md) | [HANDOFF#沙箱](HANDOFF.MD#沙箱) |
| 记忆、技能 | [modules/memory-skills.md](docs/modules/memory-skills.md) | [HANDOFF#记忆](HANDOFF.MD#记忆)、[#技能](HANDOFF.MD#技能) |
| Portal 界面、流式渲染、GenUI、预览查看器 | [modules/portal.md](docs/modules/portal.md)、[platform/portal/AGENTS.md](platform/portal/AGENTS.md) | [HANDOFF#portal](HANDOFF.MD#portal) |
| 部署、配置、模型目录、镜像构建、观测、备份 | [modules/deploy.md](docs/modules/deploy.md) | [HANDOFF#部署](HANDOFF.MD#部署) |
| 测试、CI、E2E、真模型场景 | [modules/testing.md](docs/modules/testing.md) | [HANDOFF#测试](HANDOFF.MD#测试) |
| 为什么这样设计 | [ADR 索引](docs/architecture/adr/README.md) | |
| 某个能力做到哪一步 | [status-matrix.md](docs/architecture/status-matrix.md) | |
| 多机目标架构（参考，默认不读） | [docs/architecture/README.md](docs/architecture/README.md) | |

跨模块的坑在 [HANDOFF#通用](HANDOFF.MD#通用)。全部文档的索引和术语表见 [docs/README.md](docs/README.md)。

## 3. 目标

这是平台的单机底座。成功的标准：在一台机器上，Chat、Work、文件上传与解析、交付件、多轮修改和资料库都可靠；重启、重复投递、超额和依赖故障都有明确的合同；将来扩到多机，只换适配器和部署，不改业务授权、用量账本、文件交付和 Portal 事件协议。

用第一性原理：写代码前先确认真实目标、约束和验收标准。在根因处修 bug。没有具体行为需要，就不加开关、抽象或依赖。

## 4. 服务边界

| 服务 | 负责 | 不能做 |
|---|---|---|
| Portal（Next.js，`platform/portal`） | 界面、httpOnly 会话 cookie、同源代理 `/api/v1 → /v1` | 持有模型密钥或沙箱凭据，调用内部端点 |
| API（`api/`） | 业务数据库（唯一写入方）、授权、配额、账本、文件记录 | 执行用户代码，解析不可信文件 |
| Agent（`agent/`） | Agent 循环、模型调用 | 直接写业务库，持有容器运行时 socket，用模型输出决定 `sandbox_id` |
| Broker（`broker/`） | 沙箱租约、exec 代理、文件注入与交付 | 信任 Agent 给的路径和大小 |
| Worker（`worker/`） | 扫描、解析、缩略图、预览 | 在进程内无时间/内存/输出限制地解析 |

服务之间经 `ailab_platform/clients.py` 走 HTTP，各用自己的凭据和 scope（`ailab_platform/service_auth.py`）。内部路由在 `/internal` 下，Portal 代理永远到不了。

代码结构（`platform/ailab_platform/`）：五个服务入口 `api/ agent/ broker/ worker/`；`contracts/` 是跨服务共享的版本化枚举、DTO 和事件（不导入 `models.py`，不含 ORM 和 HTTP）；`domain/` 是纯状态机与策略；`ports/` 只为真实存在或已计划的第二实现定义接口；`adapters/` 放 local/redis/s3/opensandbox 实现。只在本来就要改的代码上做搬迁，不做全仓重排。API 之外的服务不导入 `models.py`。工厂遇到未知后端直接拒绝，绝不静默回退到 mock 或本地目录。

## 5. 必须守住的合同

**可靠性**
- 幂等按真实的工作单元做键：模型调用（`call_id`）、交付（`delivery_id`）、上传（`upload_id`）。不要把整个 Run 的重试当一次调用去重。
- 业务变更和它触发的副作用（队列消息、事件）通过 `outbox` 表在同一事务里写入，由 dispatcher 投递。不要提交后再入队。
- Run 状态只经 `run_state.transition_run`（compare-and-set）改变，终态不可再变。恢复 Run 不重复已确认的交付，也不重放标记为不可重放的工具。
- 用量记录在写入时冻结全部计价输入（权重、缓存折扣、策略版本），历史从不按当前配置重算。
- 每个外部调用都有连接/读取/总超时，每个等待循环都有截止时间。流（S3 body、HTTP 响应、浏览器 reader）在 `finally` 里关闭。

**前缀缓存**（目标：多轮 Chat/Work 的缓存命中 ≥95%；依据见 [ADR 0030](docs/architecture/adr/0030-prefix-stable-model-requests.md)）
- 下一次请求等于上一次请求加上追加的消息。Run 内不改写、不重排已有消息，不改工具列表、`tool_choice`、思考设置或 system 消息。
- 会变的东西（记忆、技能、附件、模板、工作区状态，包括删除）都作为消息或工具结果追加，不插进 system 提示或工具 schema。system 内容只在有记录的边界（`agent.context_boundary`：压缩、换模型、权限撤销、重建）刷新。
- 旁路调用（摘要、收尾、检查、子任务）复用主请求的前缀和设置，在后面追加指令。
- 改动请求构造的代码，要在 `platform/tests/test_prefix_cache.py` 加录制请求测试，并在宣布完成前用真实模型检查 `cached_tokens / prompt_tokens`。

**安全**
- 资源归属由 API 根据已认证主体决定，不接受 Agent 或模型给出的 `user_id`。
- 内部端点检查调用方的服务 scope，相关时把请求绑定到 `run_id`/`job_id`。
- `AILAB_PROFILE=secure` 拒绝 mock 沙箱、Docker 配 `runc`、未知后端、默认凭据、桩扫描器和不在白名单的模型。新增任何不安全的默认值，都要在这里加检查。
- 不记录密钥、token、预签名 URL 和数据库密码；沙箱凭据不进消息和事件。不可信文件只在 Worker 的受限子进程或沙箱里解析。

**性能**（目标机器 2 vCPU / 约 8 GB 跑整套；pod 限额见 `platform/deploy/base/apps/services.yaml`）
- 大文件走流式，内存里不保留超过一份完整文件；遵守 `max_upload_bytes` / `max_parse_bytes`。
- 列表接口都分页或有上限；大表上的新查询要有索引，并在 SQLite 和 PostgreSQL 上看过 `EXPLAIN`。
- Prometheus 标签保持低基数，不按用户、Run、文件或会话 ID 打标签。
- 昂贵的派生资源（预览、缩略图、OCR）只生成一次，缓存在 `derived/{file_id}/`，以 immutable 缓存和 ETag 提供。
- 新的后台任务或依赖，在 PR 描述里写清 CPU、RSS、磁盘、延迟的代价。

## 6. 代码约定

**Python**：3.12，行宽 140，`ruff check` 和 `ruff format` 干净。注释解释为什么，不解释做了什么，密度与周围一致。配置是 `AILAB_*`，统一在 `config.py` 里校验枚举和范围。时间一律 UTC，API 输出用 `UTCModel`。数据库每次改动一个 Alembic 迁移（`migrations/versions/NNNN_name.py`），带 upgrade 和 downgrade，SQLite 与 PostgreSQL 都要能跑；SQLite 加列用普通 `ALTER TABLE`，不要批量重建被其他表引用的表。新行为要有一个没有这次改动就会失败的测试。

**Portal**：先读 [platform/portal/AGENTS.md](platform/portal/AGENTS.md)（Next.js 版本差异和 Portal 规则）。界面文字用中文，让布局说话，不加多余的副标题和说明文字。样式 token 只在 `styles/tokens.css`；覆盖样式要写明被覆盖的元素，不依赖文件顺序；重做的部分删掉被它取代的旧规则。图标只从 `components/icons.tsx` 取；只有 `components/genui/**` 能导入 `@openuidev/*`。从侧栏打开的弹窗和菜单用 `createPortal` 挂到 `document.body`。可被取代的请求（详情加载、流、轮询）过期时要取消或忽略。与 API 共享的类型放在 `lib/api.ts`，与 `ailab_platform/contracts` 保持一致。新的 UI 依赖要在 ADR 0027 里写一行理由。

## 7. 验证与验收

怎么跑、跑什么见 [modules/testing.md](docs/modules/testing.md)。原则：

- 本地只跑 ruff、改动模块的测试，以及 CI 跑不了的东西（真模型场景、改过的界面在真实浏览器里看一眼、在真实数据副本上跑迁移）。全量套件由 CI 跑，CI 在要合并的那个 commit 上的结果就是验收记录。只有复现 CI 失败或改动暂时推不上去时，才在本地跑全量。
- 改提示词、工具、技能、请求构造或沙箱镜像，要跑真模型场景（`platform/scenarios/`）。
- CI 时限也是验收的一部分。拖慢测试的改动是 bug，修它，不要放宽时限；测试里的真实等待（退避、轮询、sleep）要在测试里缩短。
- 测试从不指向开发数据库。PostgreSQL/Redis 用非默认端口的一次性实例，用完停掉。
- 说“完成”之前：lint 和改动模块的测试本地通过，最终 commit 的 CI 全绿，界面改动在真实浏览器里看过。“已实现”“本地验证”“已合并”是三个不同的说法，不要混用。

## 8. 文档怎么维护

每类信息只放一处，其他地方链接过去：

| 信息 | 位置 | 什么时候改 |
|---|---|---|
| 模块的代码地图、流程、不变量、扩展步骤 | `docs/modules/<模块>.md` | 改了结构、入口、不变量或扩展方式，在同一个变更里改 |
| 踩过的坑 | `HANDOFF.MD` 对应分节 | 发现新坑时；能并进已有条目就合并；坑不再成立时直接删 |
| 设计决策与取舍 | `docs/architecture/adr/NNNN-*.md` + [ADR 索引](docs/architecture/adr/README.md) | 做出或推翻决策时；被取代的旧 ADR 只改状态行 |
| 能力状态 | `docs/architecture/status-matrix.md` | 能力状态变化时，改对应那一行 |
| 未完成的目标与验收标准 | `docs/progress/roadmap.md` | 目标增加、完成或调整时 |
| 进行中的工作与下一步 | `docs/progress/current.md` | 每次停下或交接前覆盖写 |
| 一次改动的验证细节 | PR 描述 | 不另写会话记录 |

写法：
- 写现在的样子，不写历史和过程。不写“本轮/之前/已修复/改为”，不写日期标题，不贴测试计数、日志、原始 JSON。历史在 git 里。
- 代码引用写 `路径::符号`，不写行号；写之前用 rg 确认它存在。
- 一句话能说清就不写一段。删掉不影响读者行动的句子。
- `current.md` 不超过 40 行，只写进行中、暂停、阻塞和下一步具体动作（含分支和 base）；做完的事从里面删掉，结论落到上表其他位置。
- HANDOFF 每条不超过 4 行：先写规则（祈使句），再写原因，附代码位置。
- 不在仓库保存运行输出（日志、trace、采样、生成文件、重复截图）。最小回归 fixture、独有复现脚本和能证明 UI 问题的代表截图可以保留，但要有文档引用它。
- 永远不写 token、cookie、签名 URL、个人路径或个人数据。
- 纯文档改动只检查差异和链接，不跑测试和构建。

## 9. 工作流程与仓库卫生

- 在分支上工作；不碰无关的用户改动（如 `.DS_Store`）。小而可审的 commit，conventional message（`feat(ledger): …`、`fix(broker): …`）。
- 本次会话产生的杂物，本次会话清掉：
  - PR 合并或关闭后删本地分支；只有默认分支和有开放 PR 的分支留在远端；之后 `git fetch --prune`。有独有提交但不提 PR 的分支，记下 SHA 后删除。
  - `git worktree prune`；交接前 `git worktree list` 只剩正在用的 worktree。
  - 删除 `build/`、`.next/`、`__pycache__/`、`*.egg-info/`、`.pytest_cache/`、临时 venv、解压的上游副本和 `.local/` 草稿目录。
  - 存过凭据（token 文件、`.env`、kubeconfig）的临时目录，调查结束就删。
  - Docker 空间紧张时按时间或镜像回收，不要 prune 测试环境数据库或工作区所在的 volume。
- 交接时 `git status`、`git branch -vv`、`git worktree list` 只显示进行中的工作，`current.md` 写清下一步。

## 10. Pull Request

读者是没跟过这次工作的审查者，要能在五分钟内判断改了什么、为什么、怎么证明的、要注意什么。

- 标题：conventional 前缀加一句平实的中文结果，例如 `feat(office): Chat/Work 原生查看并引用 Office 文档`。不用内部代号或上游产品名。
- 正文依次是：
  1. `## 做了什么`：1–3 句，用户能看到的结果和原因。
  2. `## 主要改动`：按 Agent/API/Broker/Worker/Portal/镜像/文档分组，每行一项改动及理由。写明新依赖、迁移、配置或环境变量变化；生成或 vendored 的目录要注明来源，方便审查者跳过。
  3. `## 怎么验证的`：实际跑过的命令和结果数（如 `2093 passed / 169 skipped`），真模型或浏览器检查的关键数字，出过的失败及其根因。没看到的绿不能写。
  4. `## 没覆盖到 / 风险`：没测到的边界、已知限制、后续事项。没有就写“无”。
  5. `## 成本与部署`：CPU/RSS/磁盘/延迟/镜像大小影响，迁移与上线顺序，是否需要部署。用户没要求就不部署。
  6. `## 参考`：设计、ADR、相关模块文档。
- 只在前后对比时用表格。正文控制在约 60 行内。不贴日志、堆栈、原始 JSON 或完整测试输出，不写密钥、token、签名 URL、个人路径。
- 后续推送改变了范围或结果，就同步改描述。合并前确认最终 head 的 `CI` 检查是绿的，描述与 diff 一致。

## 11. 用户测试环境

- 只在用户当次明确要求时部署。之前给过的部署授权不延续到后续修复。
- 没有明确要求时：不备份；部署阶段不另跑测试、浏览器或真模型验收，不重复 PR 已做过的检查。只更新必要组件并启动，结果在对话里汇报，不写进仓库文档（本机部署不代表能力状态）。
- 本机是可重建的测试环境，允许失败；不要套用生产的备份、回滚和全面验收流程。部署优先复用现有镜像和没变的依赖，只增量构建变化的部分。

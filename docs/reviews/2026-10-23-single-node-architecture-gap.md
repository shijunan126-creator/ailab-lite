# AILab 单机实现与目标架构差距审查

审查日期：2026-10-23。目标：`/Users/michael/Developer/AILab/architecture` v1.8；实现：`/Users/michael/Developer/ailab-lite`。

## 1. 结论与基线

**当前平台已具备完整的单机业务主链路，但距离“只增加服务器和资源实现即可扩展”的底座还有业务契约、恢复机制和基础设施解耦三类工作。现在最有价值的是把单机做可靠、把边界定清楚，而不是搭建多机控制面。**

按本轮追加指示，已执行 `git fetch origin main` 和 `git merge --ff-only origin/main`。实现基线最终锁定在 `main@ac4b2cb52a6085c7157fd0cf9c598da923260862`，与本次获取的 `origin/main` 一致。没有提交、推送或修改产品代码；既有 `.DS_Store` 与 `platform/portal/AGENTS.md` 未跟踪文件保留。

`AILab` 目前只有 architecture、图源和 references，没有平台业务实现，Git 尚无提交；因此未来直接 clone 当前 AILab 不会得到可运行平台。需要在后续建设阶段把 **ailab-lite 的实现、目标文档和受控的部署配置纳入一个已版本化的底座仓库**，再从该底座派生多机仓库。本轮没有执行此整合。

已读取更新后的 [HANDOFF.MD](/Users/michael/Developer/ailab-lite/HANDOFF.MD:1)。远端 main 已包含近期正确性修复、项目/资料库改版、缩略图和工作区 TTL，以下报告只讨论这个新基线。旧版本的 Chat 意外工具执行、产物配额绕过、预签名对象覆写、未完成模型流误成功、Agent readiness 错误状态码，均不再列为当前缺陷。

建议本阶段的成功标准：

1. 单机上完整支持 Chat、Work、文件上传解析、产物交付、多轮修改和项目资料库；重启、重复投递、超额与依赖故障的行为有明确契约。
2. 权限、计量、文件版本、Run 状态由稳定领域模型表达，不依赖进程内偶然顺序。
3. 本地替身与真实单实例 PostgreSQL、Redis/Valkey、S3、OpenSandbox 共享契约测试。
4. 未来多机改动主要落在适配器和部署层；不再改写业务授权、账本、文件交付与前端事件协议。

“已实现”表示代码中存在对应能力，并不代表完整生产验收。下文 P0/P1/P2 是本轮任务优先级，与目标文档中的实施阶段命名独立。

## 2. 对照目标的实现盘点

目标依据：[Agent 与沙箱](/Users/michael/Developer/AILab/architecture/03-Agent与沙箱设计.md:5)、[平台服务与数据](/Users/michael/Developer/AILab/architecture/04-平台服务与数据设计.md:5)、[安全与运维](/Users/michael/Developer/AILab/architecture/06-安全-高可用-运维.md:5)、[Skills](/Users/michael/Developer/AILab/architecture/08-Skills与沙箱环境设计.md:101)、[请求链路](/Users/michael/Developer/AILab/architecture/09-请求链路与网关设计.md:24)。

| 能力及目标章节 | 当前状态 | 代码证据与剩余差距 |
|---|---|---|
| 五服务分工，03 §1 / 04 §0 | 已实现 | `api/agent/broker/worker` 与 Portal 已分开；业务 DB 由 API 写，其他服务经 [clients.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/clients.py:1) 调用。应保留此边界。 |
| 本地账号与会话，04 §1 | 已实现（本地范围） | [accounts.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/accounts.py:1) 有密码哈希、会话过期/撤销、停用、登录限速；Portal 用 httpOnly Cookie 和同源代理。OIDC、SSO、MFA 未实现。 |
| Chat / Work、附件与懒创建沙箱，03 §3 | 已实现（基础链路） | [AgentLoop.process](/Users/michael/Developer/ailab-lite/platform/ailab_platform/agent/loop.py:362) 支持模式、工具、附件文本、个人技能、流式回答；Chat 的意外工具调用已被丢弃。 |
| 多轮会话与消息，03 §2 / 04 §0 | 部分实现 | [routes_conversations.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/routes_conversations.py:93) 以一个 Run 表达一轮，summary/result/events 保存回复；没有目标中的独立 messages 模型。历史采用最近 12 轮，尚无摘要记忆。删除会话是隐藏，并未清理消息/事件等数据。 |
| 项目与资料库组织，04 §4 | 已实现（当前范围） | [routes_projects.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/routes_projects.py:57)、[list_files](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/routes_files.py:141) 已有项目、文件名搜索、收藏、图片分类、移动、批量删除/ZIP、缩略图、引用资料库文件对话。项目是当前扩展能力，不等于目标中的部门、目录树或标签。 |
| Run 状态、取消与恢复，03 §2 | 部分实现 | [run_state.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/run_state.py:1) 有 CAS 转移、终态保护；队列可重投、SIGTERM 可交还。**恢复是从头重跑，不是从步骤 checkpoint 续跑**；waiting_user/interrupted 未实现，waiting_sandbox 主要用事件表达。 |
| Agent 框架与工具，01 / 03 §3 | 部分实现 | [agent/loop.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/agent/loop.py:104) 为自研循环，依赖未使用 LangGraph/deepagents。缺 ask_user、持久化计划/子任务、library_search/fetch 工具、edit/glob/grep、视觉自检等目标能力。不要把自研循环描述为已完成目标 runtime。 |
| Redis/Valkey 任务队列，03 §2 | 已实现（基础抽象） | [JobQueue / RedisQueue](/Users/michael/Developer/ailab-lite/platform/ailab_platform/queue.py:40) 有消费组、XAUTOCLAIM、ACK、锁及内存替身；尚无续可见期、延迟重试、死信诊断和稳定 attempt 契约。 |
| 调度与准入，03 §4 | 部分实现 | [consume_forever](/Users/michael/Developer/ailab-lite/platform/ailab_platform/agent/main.py:108) 串行等待每个 Run 完成；[LeaseManager.create](/Users/michael/Developer/ailab-lite/platform/ailab_platform/broker/service.py:118) 只有全局固定沙箱上限和轮询等待。没有 Chat/Work 隔离、公平队列、每用户/部门沙箱上限、规格准入。 |
| 沙箱 Provider 边界，03 §4 / §7 | 已实现（接口）；隔离部分实现 | [SandboxProvider](/Users/michael/Developer/ailab-lite/platform/ailab_platform/sandbox/base.py:32) 有 mock、Docker、OpenSandbox。Agent 不持有 runtime socket，路径和字节上限在 Broker 执法。当前主部署是 OpenSandbox Docker runtime；Kata、k8s Pool、Secure Access ingress 链路未完成验收。 |
| 租约生命周期，03 §4 | 实现但需重构 | [LeaseManager](/Users/michael/Developer/ailab-lite/platform/ailab_platform/broker/service.py:71) 用 Redis 记录和进程锁，固定 TTL、每轮销毁；无会话 idle 复用、renew、心跳、持久会话记录。`recover()` 启动时清理 orphan 并删除所有租约记录，不满足目标中的可接管恢复。 |
| 多轮工作区，03 §5 | 已实现（单机替代策略） | Broker 打包/恢复 `/workspace`，[workspace_alive](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/routes_internal.py:105) 和 [purge_expired_workspaces](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/reaper.py:90) 默认三天过期。它保存文件连续性，不保存 Agent 执行状态；与目标“闲置 15 分钟保留活沙箱”不同，需文档化此差异。 |
| 上传与产物交付，03 §5 / 04 §4 | 部分实现且基础已加固 | [complete_upload](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/routes_files.py:383) 已冻结独立对象后哈希；[internal_commit_artifact](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/routes_internal.py:267) 有去重、大小/哈希校验和配额。目标的独立 prepare/commit、持久化交付记录及失败恢复闭环尚不完整。 |
| 文件版本、删除与保留，04 §4 | 部分实现 | [FileObject](/Users/michael/Developer/ailab-lite/platform/ailab_platform/models.py:163) 有 version/parent_id；同名变化产物仍各建 FileObject，缺统一版本根、版本列表/回退、最多十版、30 天回收站、目录/标签。当前文件/项目删除为硬删除；conversation_files 关系表缺失。 |
| 文档 Worker，04 §4.3 | 部分实现 | [pipeline.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/worker/pipeline.py:68) 有 PDF/Word/PPT/Excel 文本抽取、受限缩略图、pending/ready/rejected；**扫描仅 EICAR 字符串测试桩**，无真实 ClamAV、单任务强制时限和隔离解析执行器。上传 Office 的 PDF 预览未实现。 |
| 配额策略，04 §2 | 部分实现 | [quota.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/quota.py:16) 有日/月加权 Token、存储与并发 Run 限额，三条文件入口已覆盖配额。缺运行中用户额度检查、部门池、角色档、80% 提醒/超额降级、按模式并发约束及请求/SSE 限流。 |
| 用量账本，04 §3 | 实现但需重构 | [ModelUsage](/Users/michael/Developer/ailab-lite/platform/ailab_platform/models.py:213) 冻结模型 weight，支持缓存 Token；但 usage 写入无 call_id 幂等，记录失败只日志，无可靠补交；缓存折扣未冻结，历史费用会随配置变化。缺部门/模式/实际 backend/reasoning 等计量维度和对账。 |
| 模型目录、路由、降级，02 §5 / 09 | 部分实现 | [catalog.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/catalog.py:38)、[llm.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/llm.py:154) 已有别名、隐藏 Key、工具能力、OpenAI 兼容流式和同上游重试；无跨模型 fallback、独立 TTFT 策略、上下文 token 预算、多模态消息/OCR 路径。只有 x-user-id，没有模型调用级 run_id/call_id 契约。 |
| 内部认证与授权，04 §0.3 / 06 §1.4 | 实现但需重构 | [deps.service_auth](/Users/michael/Developer/ailab-lite/platform/ailab_platform/deps.py:62) 所有服务共用令牌；[internal_get_file](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/routes_internal.py:385) 等按 file_id 读写，不要求关联 run_id。尚无服务身份与端点权限矩阵。外部文件归属检查已有，不能把此差距直接当成已证明的浏览器越权。 |
| 组织、RBAC、审计与管理台，04 §1 / §6 | 未实现 | 当前 User 无 ext_id/issuer/dept/role/quota_policy，未见 departments、user_roles、audit_log 或相应管理 API/UI。名为 admin 的引导账号只是普通 User，不具备完整平台管理员模型。 |
| Portal，04 §5 | 部分实现 | 已有 Next.js/React、Chat、资料库、项目、技能、用量和实时预览；没有目标 assistant-ui 适配、双语、部门/运维管理台、完整计划面板和断线自动续流。不建议为库选型本身立即重写可用界面。 |
| 可观测性，06 §3 | 部分实现 | [obs.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/obs.py:1)、[metrics.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/metrics.py:1) 已有 JSON 日志、请求关联、低基数 Prometheus 指标、readiness/degraded。缺 OTel spans、TTFT、队列等待、工作区/预览字节指标、看板、告警和账本对账。 |
| 测试与供应链，06 §5 / 08 §7 | 部分实现 | CI 有 Python 双库/Redis、Portal build、浏览器 E2E、Ruff、ShellCheck 和依赖审计；近期已有查询计划证据。缺真实 S3/沙箱统一契约、故障恢复/压力门槛、技能业务评测、双架构镜像验收、完整镜像 digest/SBOM 策略。 |
| 持久化与部署，05 / 06 | 实现但需解耦 | [platform.yaml](/Users/michael/Developer/ailab-lite/platform/deploy/k8s/platform.yaml:1) 使用 SQLite、共享 local-path PVC、hostNetwork Broker/宿主 OpenSandbox、NodePort、宿主 Portal；已有 PG/S3 代码，但部署不是可直接横向扩展形态。PVC 仅 2 GiB，而默认每用户资料库 20 GiB。 |
| 备份与恢复，06 §4 | 未实现（可执行运维闭环） | 未见受控的一键备份、校验、异机/异盘恢复及恢复演练。Redis 清单关闭持久化，必须实测队列丢失与租约丢失的不同后果。 |
| 集群与本地模型资源，02 / 05 / 06 §2 | 未实现，按阶段跳过 | 多 Atlas 推理、PD/RoCE、三控制面、PG/Valkey HA、反亲和、Pacific/CSI 等不属于本轮单机实施范围。服务接口与真实单实例验证仍需先做。 |

## 3. 当前仍需处理的根因与风险

以下只基于 `ac4b2cb`。复现脚本为 [probe-main.py](/Users/michael/Documents/Codex/2026-10-23/ailab-review/probe-main.py)，结果为 [probe-results-main.json](/Users/michael/Documents/Codex/2026-10-23/ailab-review/probe-results-main.json)。探针使用隔离数据库和替身，不连接开发数据或付费模型。

**F1 · P0：usage 无幂等与可靠提交，配额账本不能作为可靠事实源。** 同一 usage 请求提交两次，均返回 200，生成两行；`internal_usage()` 直接 INSERT，schema 无稳定调用标识。另一方面 `AgentLoop.process()` 的 record 捕获写账异常后继续执行，会产生漏计。根因是模型调用没有“意图—调用—结果—记账”的持久状态与幂等边界。应增加 call_id、attempt_id、实际模型、计费快照和唯一约束，并设计补交/对账；不要只给 HTTP 重试加次数。参见 [internal_usage](/Users/michael/Developer/ailab-lite/platform/ailab_platform/api/routes_internal.py:228)、[record](/Users/michael/Developer/ailab-lite/platform/ailab_platform/agent/loop.py:412)。

**F2 · P1：历史缓存折扣随当前配置变化。** 已入账后，把 cached_token_weight 从 0.1 改成 0.8，探针同一历史区间的使用量从 230 变成 300。模型 weight 已冻结，但 [tokens_used_since](/Users/michael/Developer/ailab-lite/platform/ailab_platform/quota.py:16) 仍读取全局缓存折扣。应将折扣或最终 weighted_amount 与策略版本一起写到账本；保留原始 Token，历史不重算。配额精确性建议用定点数或整数最小单位，避免 Float 边界漂移。

**F3 · P0（面向多人/非信任文件时）：扫描、解析与生产配置的可信边界尚未闭合。** [scan_bytes](/Users/michael/Developer/ailab-lite/platform/ailab_platform/worker/pipeline.py:68) 只识别 EICAR，不是真实病毒扫描；普通文件在 Worker 线程里解析，压缩文件大小限制不能约束解压后内存或最坏解析时长。应提供 scanner/parser 接口，真实扫描不可用时明确隔离或降级状态，解析进程有 wall-time/内存/输出上限。配置层目前未知 sandbox_provider 会回退 MockSandbox，mock 可在宿主执行命令，必须在安全 profile 禁止；未知 storage_backend 也不应静默回退本地目录。证据：[get_provider](/Users/michael/Developer/ailab-lite/platform/ailab_platform/sandbox/__init__.py:20)、[get_storage](/Users/michael/Developer/ailab-lite/platform/ailab_platform/storage.py:247)、[Settings](/Users/michael/Developer/ailab-lite/platform/ailab_platform/config.py:25)。这些是静态确认，未在本轮执行恶意解析/逃逸测试。

**F4 · P1：Broker 重启恢复策略会抹掉资源所有权。** [recover](/Users/michael/Developer/ailab-lite/platform/ailab_platform/broker/service.py:389) 清理 provider orphan 并删除所有 lease，release 即使 provider 删除失败仍 drop 记录。单机升级也会碰到，不是多机才出现。应把“期望租约状态”和“provider 实际资源”做对账，删除失败保留待清理记录；资源用稳定 owner/lease_id 标记，启动可重建映射。当前 `threading.Lock` 只保证单 Broker 进程，不得通过简单增加副本扩容。

**F5 · P1：重跑、状态与事件不在同一个可靠执行协议中。** [agent/main.py](/Users/michael/Developer/ailab-lite/platform/ailab_platform/agent/main.py:52) 支持重投但从头执行；工具副作用不能自动安全重放。状态提交和相关事件还存在分开的提交点。先确立执行 attempt、步骤结果、取消截止点和 checkpoint，再用事务 outbox 连接业务提交与队列/事件。全局替换成“exactly once 队列”不能解决这些问题。

**F6 · P1：长 Work 阻塞短 Chat，且资源控制混在执行循环里。** 消费器逐条 await 完整 Run；用户并发配置为 2，不等于 Agent 实际并发 2。应先加有界并发与 Chat 保留槽位，保持同一会话串行，沙箱容量仍单独控制；在 2 vCPU/约 8 GB 的目标小机上测 RSS、CPU 和排队时延后再调参数。不要先引入新的分布式调度平台。

**F7 · P1：Token 上限语义需要明确，而非继续列作旧 bug。** 当前 main 已在新调用前和工具执行前检查 Run 总用量，但**允许最后一个已完成回答跨过预算后成功**；探针 limit=1、usage=110 得到 succeeded，与当前代码注释一致。它是“阻止下一步”的软预算。目标若要求硬额度，必须在调用前预留预算、限制模型最大输出，并计入并发在途调用；单靠收到 usage 后判断不可能阻止已发生消费。日/月用户配额目前只在创建 Run 时检查，缺运行中检查与并发预留。参见 [AgentLoop.process](/Users/michael/Developer/ailab-lite/platform/ailab_platform/agent/loop.py:362)、[enforce_token_quota](/Users/michael/Developer/ailab-lite/platform/ailab_platform/quota.py:50)。

**F8 · P1：本地数据位置、流量代理和资源上限需要从业务中脱离。** Broker 注入/交付、Worker 解析仍有整文件 bytes 路径；100 MB 产物、50 MB 输入与 256/384 MiB Pod 内存组合需实测。SSE 以数据库轮询驱动，当前有退避和短连接保护，但浏览器未自动用游标恢复。工作区 TTL 已有，预览、上传临时对象、事件与软删对话仍缺统一保留/清理策略。不能把“工作区已清理”扩大成“全平台存储生命周期已完成”。

## 4. 单机现在做什么，哪些应跳过

**现在应补齐的基础能力**：可靠计量/配额、单机重启恢复、真实扫描与受限解析、服务级权限、持久执行状态、备份恢复、配置严格校验、内部/外部 API 边界、真实单实例适配器契约测试、队列公平性和受控并发。上述能力即使只有一台服务器也成立，其中前六项直接影响未来扩展能否保持业务语义。

**单机高价值产品能力**：Word/PDF/转换技能、公司模板、文件版本/回收站、长上下文摘要、OCR/图片理解、ask_user 与持久计划、跨模型降级、Office 上传预览、前端续流与失败后继续。先按真实办公任务评测排序，避免单纯扩大工具和依赖清单。

| 类别 | 当前阶段处理 | 不能误判的点 |
|---|---|---|
| 三控制面/etcd、VIP、跨节点副本/反亲和、自动故障转移 | 跳过实际实现；保留部署分层与服务端点配置 | 它们是真正需要多故障域的能力，单机无法验收其 HA。 |
| 多 Atlas/GPUStack Worker、PD 分离、RoCE、KV 传输、NPU 调度/监控 | 跳过硬件实现与容量承诺 | 现在只冻结 ModelGateway 契约与别名；本轮不重新论证目标文档的型号、版本和容量时效。 |
| CNPG 主备、Valkey Sentinel、Pacific/CSI、跨机存储复制 | 跳过 HA/设备适配；做单实例协议验证 | PostgreSQL、Redis/Valkey、S3 的单实例测试不是多机功能。 |
| OpenSandbox k8s Pool、Kata 跨节点运行、预热池节点调度 | 预留 SandboxSpec/CapacityProvider；有资源再接 | 无 KVM 可以暂不做 Kata 真机验收，但不能宣称 runc、gVisor 与 Kata 隔离等价。 |
| OIDC/Keycloak、部门/RBAC、审计、账号停用同步 | 现在建身份/策略模型；是否立刻接真实 SSO由用户范围决定 | 属于企业身份和权限依赖，不依赖多机；不能统一推迟到“多机阶段”。 |
| Higress TPM 与服务 Key、单实例 S3、真实扫描器 | 轻量环境做接口/契约；受资源限制时单独运行测试组 | 是外部组件依赖，不是多机依赖。业务计量不应挪给网关。 |
| 开发者 API Key 门户、RAG/pgvector、定时任务、高级子 Agent | 可推迟产品实现；保留命名/扩展点即可 | 这些是目标的后期能力，不是多机前提，也不应挤占可靠性工作。 |
| Koordinator、自研集群调度器、服务网格、全套 GitOps/Harbor 平台 | 当前不建设大型平台 | 先完成镜像版本、接口、配置与单机性能预算；不要为了未来预留引入持续资源成本。 |

## 5. 为低改动多机扩展预留的设计

**总体原则：共享同一套业务核心，只替换基础设施实现和部署组合。当前保持五个服务入口；逐步从路由和 AgentLoop 中抽出用例与端口，不做全仓目录搬迁。**

| 边界 | 现在应固定的契约与单机实现 | 未来增加的实现 |
|---|---|---|
| IdentityProvider / AuthorizationPolicy | `Principal(user_id, external_subject, issuer, dept_id, roles)`；本地账号适配器；API 唯一归属判定。角色能力与登录方式分开。 | OIDC/Keycloak、公司组映射、停用同步；业务接口继续接收同一 Principal。 |
| ServiceAuthenticator / RunContext | 分离 Agent/Broker/Worker/运维身份、端点 scopes；请求绑定 run_id/file_job_id；API 推导归属，不接受模型任意指定 user_id。 | projected SA Token + TokenReview / TLS 证书；不改变授权策略。 |
| AgentRuntime / CheckpointStore | `start/resume/cancel/snapshot`；保存 message/tool 游标、已完成步骤、usage、计划、runtime_version。先封装当前循环，再用适配器落实目标 LangGraph/deepagents。 | PostgreSQL checkpoint 存储、更多执行器；避免业务代码依赖框架私有状态。 |
| RunDispatcher / JobQueue | 保留已有 JobQueue；任务 envelope 包含 job_id、schema_version、run_id、attempt、deadline、request_id。明确 ACK、重投、续期、毒消息和取消契约。单机实现有界并发、Chat 槽位及同会话串行。 | 多 Worker 领取、持久所有权/fencing、按资源路由；单机本轮不实现跨节点调度。 |
| SandboxProvider / LeaseStore | 保留已有 Provider；新增 `SandboxSpec(image_digest, profile, cpu, memory, disk, ttl, idle_timeout)`、`LeaseHandle(provider_ref, lease_id)`；renew/inspect/list_owned/exec-cancel；租约持久记录和启动对账。 | OpenSandbox k8s/Kata/预热池适配；凭证只在可信服务内，禁止进入模型工具参数。 |
| CapacityProvider / AdmissionPolicy | `capacity()` 返回资源预算，`admit(principal,spec)` 返回准入/排队/拒绝；现在使用静态单机容量，公平 FIFO 与每用户限制。 | 读取节点 allocatable/池状态；实现部门份额和动态容量。业务只依赖准入结果。 |
| ModelGateway / ModelPolicy | `ModelRequest` 带 alias、run_id/call_id、能力、deadline、token_budget；输出内容、usage、actual_backend、完成标志。降级只发生在输出前，已输出禁止重播；策略与 HTTP transport 分离。 | Higress/GPUStack 路由、本地模型池；模型目录保留同一业务别名。 |
| ObjectStore / FileTransfer | 复用现有 Storage；补流式写/读、ObjectInfo/版本、分页列举、幂等复制、关闭流契约；内部 endpoint 与浏览器签名 endpoint 分离。prepare/commit 有持久交付 ID。 | Pacific/S3 或目标允许的 NFS 适配器。调用方只用对象 key，不能使用本地绝对路径。 |
| FileProcessor / Scanner / OCR | 解析器支持输入定位符与受控输出、强制 timeout/memory/page/expanded-size；scanner 不可用与命中分开；OCR 不可用显式标记。 | Kata Worker、远端 OCR、独立转换池；保持 FileProcessingResult schema。 |
| EventStore / EventPublisher | 定义事件 version、run_id、seq、type、timestamp、payload；DB 保持可恢复事实，接口支持 `after` 和 snapshot；前端按序号去重。现在低并发可以保留 DB SSE。 | 事务 outbox 到目标 Valkey 事件 Stream，24h 保留和 replay-gap snapshot；不改 UI 事件语义。 |
| QuotaPolicy / UsageRecorder | 先落实调用级幂等、计费快照和检查/预留/结算/释放契约；按用户、模式、模型检查，预留部门扩展维度。 | 部门池、多执行器并发预算；无需改 usage 和 quota API。 |

现有 Broker 代理全部 exec，与目标图中 Agent 持单沙箱令牌直连 execd 不同。当前代理边界更集中，单机可保留；应通过 RunContext/工具执行接口隐藏传输细节，记录 ADR，未来若按目标改为直连，只替换该工具执行适配器。不要让模型选择 sandbox_id，也不要为追求图上箭头一致而扩大当前权限。

### 5.1 数据模型应先稳定

- **身份与组织**：新增可空 external_subject/issuer/dept_id 和角色关联，保证 `(issuer, subject)` 唯一；保留内部 user_id。`Project` 继续是用户资料组织容器，不要复用为 Department 或租户。
- **Run / attempt / checkpoint**：Run 是用户任务，RunAttempt 是执行尝试，step_id/tool_call_id 是可重放边界；加入状态版本、owner/lease/deadline 字段的契约，明确 waiting_user/waiting_sandbox/interrupted。沙箱临时凭证不持久进消息和事件。
- **计量**：usage_call_id 唯一；记录用户/部门快照、模式、业务 alias、实际 backend/revision、input/cached/output/reasoning、模型与缓存权重、weighted_amount、policy_version。实际重跑产生的新模型调用必须新 call_id，不能按 run_id 简单去重。
- **文件**：逻辑文件与版本分开，版本不可变；unique(file_id, version)，会话引用具体版本；prepare_id/commit_id 标识交付；trash 的 deleted_at/purge_at 与保留上限明确。现有 FileObject/parent_id 通过迁移映射，不丢旧链接。
- **可靠副作用**：OutboxMessage 与业务变更同事务；保存重试状态和唯一事件 ID。AuditLog 记录 actor/action/target/outcome/request_id，内容脱敏，不以 run_events 代替审计。
- **写入权**：业务表仍归 Platform API；Agent checkpoint 与 Broker sandbox_sessions 如按目标直接写入，只使用各自独立 schema/凭据和接口，不开放全业务 DB 写权限。

### 5.2 配置与目录

现有 `AILAB_`、models.toml、API DTO 是可复用基础。应新增严格枚举和范围校验；dev/test/lite-secure 的 profile 明确禁止项。建议预留 `auth_backend`、`service_auth_backend`、`runtime_backend`、`checkpoint_backend`、`lease_store_backend`、`capacity_backend`、`agent_concurrency`、`chat_reserved_slots`、`sandbox_profiles`、`scanner_backend`、`ocr_alias`、`object_public_endpoint`、`event_retention`、`backup_target`、OTel endpoint，以及独立 connect/read/total/TTFT timeout。只为有明确切换行为的参数建配置，不创建空实现开关。

```text
platform/ailab_platform/
  api/ agent/ broker/ worker/       保留服务入口
  domain/                          状态、策略、领域数据；无 HTTP/ORM/runtime 依赖
  application/                     start_run、deliver_file、record_usage 等用例
  contracts/                       版本化请求、响应、事件和公共枚举
  ports/                           runtime、lease、capacity、identity、model、storage 接口
  adapters/                        逐步收纳现有 local/redis/s3/opensandbox 实现
platform/tests/
  contracts/ faults/ load/          在既有测试旁增补；不把所有测试搬家
  golden/                          办公产物与实际业务评测样本
platform/deploy/
  base/ overlays/dev/ overlays/lite/
  # 未来 overlays/cluster：目前只写约定，不部署多机清单
architecture/ 或 docs/architecture/  纳入版本化底座，与原目标有来源/版本关系
  adr/ runbooks/ status-matrix.md
deploy/                             硬件和资源部署；未来机型实现只进入这一层
HANDOFF.MD                          保留已知坑、测试隔离与运行约束
```

共享 contracts 必须避免依赖 ORM；Agent 当前为 RunStatus 导入 models.py，应逐步改为公共枚举。端口按实际耦合点增设，不给每个函数套 Repository。API 与前端共享事件/状态可由 OpenAPI/契约生成，避免手写类型长期分叉。外部路径 `/v1`、Portal `/api/v1`、内部 `/internal` 的映射与目标差异要明确记录，不在同一变更里无收益更名。

### 5.3 clone 前的“扩展就绪”门槛

1. 从干净 clone 能按文档启动 dev/lite，目标文档有确定版本，依赖/镜像与 schema 版本可追溯。
2. 同一核心测试在 SQLite + LocalStore + mock 和单实例 PG + S3 + Redis 组合通过；真实 OpenSandbox 契约独立跑通。
3. 杀 Agent、重启 Broker/Redis、队列重复投递、交付超时/重复、对象复制或 DB 提交失败后，结果与账本符合既定契约。
4. 一个慢 Work 不饿死 Chat；流断线按游标补齐，暂停/恢复不会重复不可重放工具；全部入口保留同用户/同部门授权语义。
5. 业务模块无宿主 IP、容器 runtime socket、S3 物理路径或集群节点选择逻辑；两类 profile 只替换 ports/adapters 和部署配置。

达到这些门槛后，派生仓库才有机会主要新增节点发现、资源容量、Kata/k8s/Pacific/模型池/HA 部署。此时仍需硬件、网络和故障域验收，不能承诺“clone 后只加服务器就天然完成生产多机”。

## 6. 可执行任务清单

工作量为单人有效开发日区间，含本任务必要测试，不含硬件等待或企业接入审批；任务存在交集，不能直接相加当排期承诺。“扩展”指对未来低改动多机的影响。

| ID / 优先级 | 任务与代码落点 | 工作量 | 风险 | 扩展影响 | 依赖与完成标准 |
|---|---|---:|---|---|---|
| T01 / P0 | 可靠调用账本：`internal_usage`、UsageIn、ModelUsage、Agent record | 3–5 日 | 高 | 直接阻塞 | 引入 call_id/attempt_id 唯一约束，冻结全部计费参数；相同调用重试只记一条，真实重跑另记一条；API 暂时断开后能补交；上游断流缺 usage 标记待对账，不伪造精确值。先完成 F1/F2 回归。 |
| T02 / P0 | 配额完整语义：`quota.py`、`AgentLoop.process`、`start_run` | 2–4 日 | 高 | 直接阻塞 | 依赖 T01；明确软/硬 Token 边界、运行中检查及并发预留/结算；覆盖两 Run 同时接近余额、最终回复、取消/失败释放额度；保留文件三入口现有配额回归。 |
| T03 / P0 | 安全 profile 与配置校验：`config.py`、provider/storage factories、`models.toml` | 1–2 日 | 中 | 直接影响 | 未知 backend/负容量/无效 timeout 拒绝启动；安全 profile 禁止 mock/公开默认凭据/未授权外部模型；启动日志隐藏 DB 密码和 Key，明确 trusted proxy。 |
| T04 / P0（共享试点前） | 真实扫描与隔离解析：`worker/pipeline.py`、`worker/main.py` | 3–5 日 | 高 | 直接阻塞 | scanner/parser 端口；ClamAV 单实例测试；超时、压缩炸弹、损坏 Office/PDF、超像素图、扫描器故障均有受控结果；缩略图失败不得回退 READY 文件。 |
| T05 / P1 | 服务认证与授权端口：`deps.py`、`security.py`、`routes_internal.py`、Broker routes | 2–4 日 | 高 | 直接阻塞 | 先用独立服务凭据和 scope 表；Agent 不能调用扫描回写/任意文件接口；Broker 文件解析绑定 run_id，Worker 绑定 job/file；跨用户负向测试。不实现 SA TokenReview。 |
| T06 / P1 | Broker 持久租约与重启对账：`LeaseManager.recover/release/create`、`SandboxProvider` | 3–5 日 | 高 | 直接阻塞 | 稳定 owner 标记、LeaseStore、inspect/list_owned；重启不清空有效映射；删除失败可重试；续租/最长时限与可恢复失败模型明确；不实现跨节点选主。 |
| T07 / P1 | Runtime/checkpoint 与人工继续：`AgentLoop`、`RunStatus`、`run_state.py` | 5–8 日 | 高 | 直接阻塞 | 依赖 T01/T05/T06；runtime 接口后落实目标框架；持久化计划/步骤/usage，ask_user → waiting_user → resume；杀进程后恢复不会重复已确认交付或不安全工具。 |
| T08 / P1 | 单机调度隔离：`agent/main.py`、`queue.py`、Broker admission | 2–4 日 | 中 | 直接影响 | 先限流/有界并发，后按负载决定分队列；同会话串行，Chat 有槽位，Work 等沙箱不占满所有执行能力；不用自研分布式 scheduler。 |
| T09 / P1 | 事务副作用与重试：`start_run`、`transition_run`、`append_event`、文件 complete/commit | 3–5 日 | 高 | 直接阻塞 | 持久 outbox/交付 ID；DB 提交后入队前退出、对象复制后 DB 失败、重复提交、事件缺失均能收敛；对账任务有批次与幂等边界。 |
| T10 / P1 | 单实例 PG/S3/OpenSandbox 契约测试：`Storage`、`JobQueue`、`SandboxProvider`、CI | 2–4 日 | 中 | 直接阻塞 | 保留内存替身；真实单实例覆盖签名 URL、freeze、copy、流关闭、Redis 重投、sandbox expiry/exec；支持按测试组启停，适应小内存机器。 |
| T11 / P1 | 持久化/部署配置解耦：`platform.yaml`、`deploy.sh`、`clients.py`、settings | 2–4 日 | 中 | 直接阻塞 | base/lite 组合、Broker Service/独立端点、hostNetwork 限定 lite 兼容层；内部 API 不暴露用户入口；数据库/对象存储/Portal 生命周期独立；不新增集群部署。 |
| T12 / P1 | 备份恢复与全量存储生命周期：`api/reaper.py`、`Storage.list_prefix`、运维脚本 | 3–5 日 | 高 | 直接影响 | SQLite 在线备份或 PG 单实例备份 + 对象/配置清单；恢复到空目录校验引用和哈希；设置 staging/preview/events/软删数据保留与 GC 重试；保留工作区三天 TTL。 |
| T13 / P1 | 身份/RBAC/部门/审计最小模型：`models.py`、migrations、API policy | 3–6 日 | 高 | 直接阻塞 | 稳定内部 user_id；新增 issuer/subject、dept、roles、quota_policy、audit；默认不允许 admin 读取用户内容；先支持本地身份，完整部门运营 UI 可后做。 |
| T14 / P1 | 模型策略与上下文：`catalog.py`、`llm.py`、`agent/prompts.py` | 3–5 日 | 中 | 直接影响 | 独立 ModelGateway/Policy；输出前 fallback、能力检查、TTFT/总时限、tokenized 上下文压缩；已输出不重播，账本按实际模型；用可控慢/坏上游测试。 |
| T15 / P1 | Portal 恢复与长会话性能：`portal/lib/api.ts`、`chat.tsx`、会话 API | 2–3 日 | 中 | 协议边界 | SSE 游标续读/退避/去重、切换会话取消旧流；详情按页/按需返回；减少每轮详情触发的关联查询；用真实浏览器断线和乱序测试。 |
| T16 / P1 | 可观测性和单机负载基线：`obs.py`、`metrics.py`、tests/load | 2–4 日 | 中 | 直接影响 | TTFT/队列等待/恢复/计量失败/存储占用；最小抓取、看板和告警；受控 Trace 采样；见第 7 节性能门槛，不高基数打用户/run 标签。 |
| T17 / P2（身份共享时升 P1） | OIDC 单实例接入：`accounts/security/deps`、Portal auth routes | 2–4 日 | 中 | 适配器替换 | 依赖 T13；Keycloak 测试 realm 验证登录、回调、注销、停用和组映射；浏览器不拿上游 Token；企业 AD/SSO 审批另计。 |
| T18 / P2 | 文件逻辑版本与回收站：`FileObject`、`routes_files`、`library.py`、Portal library | 4–6 日 | 高 | 数据模型边界 | 依赖 T09/T13；旧文件迁移不破下载链接，版本不可变/可回退/受保留策略，删除/恢复影响会话引用可解释。目录/标签按需求再补，不重做已存在的搜索、项目、批量操作。 |
| T19 / P2 | OCR、多模态和上传 Office 预览：`worker/pipeline.py`、ModelGateway、FileOut | 3–5 日 | 中 | 适配器边界 | 依赖 T04/T14；扫描 PDF/照片有可读内容，超长分块/摘要；Office PDF 预览在受控执行器中生成；OCR 失败不伪装解析成功。 |
| T20 / P2 | 办公 Skills 与治理：`images/sbx-office/skills`、`routes_skills`、镜像 CI | 3–6 日 | 中 | 能力与发布边界 | 先 Word/PDF/转换和公司模板；Skill manifest 有版本/哈希/依赖/许可/适用角色，索引与沙箱镜像一致；管理员禁用与个人技能策略可测试；视觉质量 golden 集。 |
| T21 / P1 起持续 | 文档与扩展交付：AILab architecture、README、HANDOFF、contracts/ADR | 1–2 日起 | 低 | 直接阻塞 clone | 固定目标与实现版本、状态表、未验收项；写清本地 BFF/exec 代理/工作区策略差异；记录恢复/配额/扫描限制和测试命令；底座源码与 architecture 均纳入版本控制后再派生 repo。 |

推荐执行顺序：T01–T04 → T05/T06/T09/T10 → T07/T08/T11/T12 → T13–T16；T21 随每项更新。T17–T20 根据共享用户范围和真实任务需求插入。先修可靠性与契约，再扩大能力；**本清单没有多机部署任务**。

## 7. 验收、性能与证据边界

本次在临时 Python 3.12 环境、仓库 constraints、独立 SQLite/LocalStore/mock 下运行当前 main 的 Python 全套：**376 passed，14 skipped，24.98 秒**。14 项跳过为 6 项真实 Redis 分支和 8 项 PostgreSQL 分支；当前未启动这两类真实服务。结果：[tests-main.xml](/Users/michael/Documents/Codex/2026-10-23/ailab-review/tests-main.xml)。现有 Starlette/httpx 弃用警告 1 项，未因警告改依赖。

本轮额外探针确认：产物零配额返回 413、冻结上传不受旧 key 覆写、Chat 不执行意外工具、缺结束标记流抛错、Agent 不就绪返回 503；当前仍能复现重复 usage 双记和历史缓存折扣变化。Token 最终回复跨预算按当前“软预算”契约说明，不列作旧缺陷。

本轮未执行浏览器/E2E、Portal 构建、真实 Docker/OpenSandbox/k3s/Kata、S3/Pacific、Higress、付费或本地 NPU 模型、双架构镜像、部署与恢复演练。已有 HANDOFF/CI 的旧证据仅作索引，不冒充本轮验收。目标文档视为规范输入，不据此宣称其中组件版本、硬件容量估算已经重新核验。

建议的单机性能验收先固定硬件、模型替身、数据规模和冷/热状态，再记录变更前后：

- **混合队列**：1 个慢 Work + 持续短 Chat，验证 Chat 不等待 Work 完成；分别量模型首字、平台额外排队、端到端耗时。可先以受控模型下 Chat 排队 P95 < 1 秒作为讨论起点，不直接承诺真实模型 TTFT。
- **文件与内存**：50 MB 上传、100 MB 交付、20 MB 压缩 Office、超像素照片、大 ZIP，测 API/Broker/Worker 峰值 RSS、临时盘和失败清理；验收上限与 Pod memory/disk limit 对齐，避免多份全量 bytes 共存。
- **连接与事件**：多 SSE、上传和下载同时进行，记录 DB pool、线程数、事件写频率、p95 API 时延；先覆盖预期单机并发，再决定是否引入事件总线缓存。读历史/详情必须有分页或规模上限。
- **存储增长**：按预计用户数分配资料库、历史版本、缩略图、预览、工作区、临时对象、日志和备份预算；默认 20 GiB/用户不能部署在总计 2 GiB PVC 上。保留宿主机与 OSB 的资源余量。
- **故障与恢复**：kill Agent、Broker 重启、Redis 清空、S3 断开、磁盘满、DB 提交失败；量任务恢复时延、重复工具/文件/账本数量与孤儿对象数。新测试应在撤掉修复时失败、修复后通过。
- **观测成本**：控制事件体积、思考文本保留与 Trace 采样；Prometheus 用有限标签集合；不把日志/追踪写入关键调用的无界同步等待。采样比例和 scrape 周期根据小机 CPU/RSS 实测定值。

### 7.1 需要确认的问题

1. 单机的使用性质：个人开发、少数可信试用，还是部门多人生产？决定真实扫描、OIDC/RBAC 和内部网络隔离的上线门槛。
2. 单机正式资源上限、允许同时启用的测试组件及预期 Chat/Work 并发是多少？现有小机 profile 不应沿用集群数值。
3. Token 配额是软预算还是严格硬限？超额时 Chat 是否仍允许，降级消耗是否计入同一池，日界线按 UTC 还是业务时区？
4. 是否继续采用“每轮销毁 + 三天工作区快照”，还是在单机也实现目标的 15 分钟 idle 沙箱？两者不是同一个恢复保证。
5. 用户文件与会话的删除/保留、备份 RPO/RTO、管理员访问审批、个人技能审核和互联网模型/图片搜索政策如何确定？
6. 哪个仓库作为长期底座与未来 clone 来源？AILab 当前未提交的 architecture 如何版本化，ailab-lite 实现如何纳入底座？
7. 公司 PPT/Word 模板、首批真实办公任务和可验收质量样本何时提供？缺这些输入时无法评定 Skills 的业务完成度。

### 7.2 后续 HANDOFF 应记录的事项（本轮未修改仓库文档）

审查必须锁定目标文档版本及实现 SHA；远端 main 更新后重新核验缺陷，不能把旧分支结果照搬。区分 workspace snapshot 与 runtime checkpoint、单实例协议验证与多机 HA、文件组织 Project 与组织 Department；usage 幂等键必须是一次真实模型调用，不能是整个 Run；完整回复超预算的当前软语义要与产品约定一致。现有测试隔离、GNU 工具、SQLite UTC、S3 流关闭及预签名冻结规则继续保留。

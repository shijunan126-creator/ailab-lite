# 单机能力状态

本表回答“这个能力实现到了哪里、代码在哪”，不代表部署、CI 或外部验收状态。进行中的工作见 [current](../progress/current.md)，未完成目标与验收标准见 [roadmap](../progress/roadmap.md)。

状态只有四种：已实现、部分实现（写明缺什么）、未实现、与目标架构有意不同（链接 ADR）。代码位置写 `路径::符号` 或目录；找不到确定符号时写目录。`待核实` 表示本文没有从代码确认。

## Agent

| 能力 | 状态 | 代码位置（路径::符号或目录） | 决策/文档 |
|---|---|---|---|
| 模型调用、降级与思考字段 | 已实现 | `platform/ailab_platform/agent/loop.py::AgentLoop`、`platform/ailab_platform/thinking.py` | [agent](../modules/agent.md)、[ADR 0038](adr/0038-reasoning-effort.md) |
| Run 循环、checkpoint、续跑与 ask_user | 已实现 | `platform/ailab_platform/agent/loop.py::AgentLoop`、`platform/ailab_platform/adapters/runtime.py` | [agent](../modules/agent.md)、[ADR 0004](adr/0004-self-developed-runtime.md) |
| 上下文组装、压缩与运行过程快照 | 已实现 | `platform/ailab_platform/agent/context.py`、`platform/ailab_platform/agent/context_band.py`、`platform/ailab_platform/api/process_snapshots.py` | [agent](../modules/agent.md)、[ADR 0033](adr/0033-context-epoch-band-process-snapshot.md) |
| 前缀稳定请求与同形侧调用 | 已实现 | `platform/ailab_platform/agent/context.py`、`platform/ailab_platform/agent/context_usage.py` | [前缀缓存设计](design/2026-10-31-prefix-cache.md)、[ADR 0030](adr/0030-prefix-stable-model-requests.md) |
| 工具目录与按需工具 | 已实现 | `platform/ailab_platform/agent/tools.py::RETIRED_TOOLS`、`platform/ailab_platform/agent/deferred.py::DEFERRED` | [agent](../modules/agent.md)、[ADR 0042](adr/0042-single-tool-catalog.md)、[ADR 0043](adr/0043-on-demand-tools.md) |
| 提示词与模式规则 | 已实现 | `platform/ailab_platform/agent/prompts.py` | [agent](../modules/agent.md) |
| 并行子任务 | 已实现 | `platform/ailab_platform/agent/subtasks.py`、`platform/ailab_platform/agent/loop.py` | [ADR 0031](adr/0031-parallel-subtasks.md) |
| 联网读取、下载与依赖获取 | 已实现 | `platform/ailab_platform/agent/net_tools.py`、`platform/ailab_platform/api/routes_reader.py`、`images/crawler` | [ADR 0007](adr/0007-controlled-public-fetch.md)、[ADR 0021](adr/0021-isolated-browser-rendering-for-read-url.md) |

## API

| 能力 | 状态 | 代码位置（路径::符号或目录） | 决策/文档 |
|---|---|---|---|
| 会话、消息、编辑、重试与分叉 | 已实现 | `platform/ailab_platform/api/routes_conversations.py`、`platform/ailab_platform/api/fork_context.py`、`platform/ailab_platform/conversation_tree.py` | [api](../modules/api.md)、[ADR 0041](adr/0041-fork-reuses-checkpoint.md) |
| Run 状态机与 attempt 栅栏 | 已实现 | `platform/ailab_platform/run_state.py::transition_run`、`platform/ailab_platform/api/runtime_fence.py` | [api](../modules/api.md)、[ADR 0004](adr/0004-self-developed-runtime.md) |
| 事务 outbox 与持久任务 | 已实现 | `platform/ailab_platform/api/jobs.py::stage_job`、`platform/ailab_platform/models.py::OutboxMessage` | [api](../modules/api.md) |
| 配额、账本与额度暂停 | 已实现 | `platform/ailab_platform/quota.py::reserve_usage`、`platform/ailab_platform/quota.py::settle_usage` | [使用账本](usage-ledger.md)、[ADR 0008](adr/0008-long-run-budgets-and-leases.md) |
| 身份、RBAC、部门、OIDC 与审计 | 已实现 | `platform/ailab_platform/api/routes_auth.py`、`platform/ailab_platform/adapters/oidc.py`、`platform/ailab_platform/audit.py` | [api](../modules/api.md)、[ADR 0002](adr/0002-service-identity.md) |
| 管理接口与用量视图 | 已实现 | `platform/ailab_platform/api/routes_admin.py` | [api](../modules/api.md) |
| 定时任务绑定会话 | 已实现 | `platform/ailab_platform/api/routes_schedules.py`、`platform/ailab_platform/contracts/schedules.py` | [ADR 0040](adr/0040-schedule-in-conversation.md) |
| 全量对话搜索 | 已实现 | `platform/ailab_platform/search_index.py`、`platform/ailab_platform/api/routes_search.py` | [ADR 0039](adr/0039-global-search-index.md) |
| 项目、内部只读分享、收件箱与排队插话 | 已实现 | `platform/ailab_platform/api/routes_projects.py`、`platform/ailab_platform/api/routes_shares.py`、`platform/ailab_platform/api/inbox.py` | [api](../modules/api.md) |
| 文档模板与品牌套件 | 已实现 | `platform/ailab_platform/api/templates.py`、`platform/ailab_platform/api/brand.py`、`platform/ailab_platform/api/routes_templates.py` | [文件](../modules/files.md) |
| 部门额度池与软硬水位 | 未实现 | — | [ADR 0020](adr/0020-elastic-sandbox-allowance-and-idle-reclaim.md) 明确不含 |

## 文件

| 能力 | 状态 | 代码位置（路径::符号或目录） | 决策/文档 |
|---|---|---|---|
| 上传、幂等与安全就绪 | 已实现 | `platform/ailab_platform/api/upload_identity.py`、`platform/ailab_platform/api/routes_files.py`、`platform/ailab_platform/worker/safety.py` | [files](../modules/files.md)、[ADR 0034](adr/0034-safety-ready-and-lazy-native-input.md) |
| 扫描器与隔离解析 | 已实现 | `platform/ailab_platform/worker/isolate.py`、`platform/ailab_platform/worker/parsers.py`、`platform/ailab_platform/adapters/clamav.py` | [files](../modules/files.md)、[ADR 0011](adr/0011-worker-previews.md) |
| 原生正文按需准备与范围读取 | 已实现 | `platform/ailab_platform/api/native_preparation.py`、`platform/ailab_platform/api/native_ranges.py` | [ADR 0034](adr/0034-safety-ready-and-lazy-native-input.md) |
| OCR 执行与页结果复用 | 已实现 | `platform/ailab_platform/api/document_ocr.py`、`platform/ailab_platform/ocr_backend.py`、`platform/ailab_platform/worker/document_ocr.py` | [files](../modules/files.md)、[ADR 0023](adr/0023-system-document-reader-and-concurrent-capabilities.md) |
| 预览、缩略图与派生资源 | 已实现 | `platform/ailab_platform/api/derived_assets.py`、`platform/ailab_platform/api/preview_dispatch.py`、`platform/ailab_platform/broker/render.py` | [ADR 0011](adr/0011-worker-previews.md)、[ADR 0032](adr/0032-prewarmed-single-use-render-sandboxes.md) |
| 文档查看、视觉覆盖与文件等待 | 已实现 | `platform/ailab_platform/api/document_view.py`、`platform/ailab_platform/api/document_visual.py`、`platform/ailab_platform/api/file_dependencies.py` | [ADR 0026](adr/0026-document-visual-coverage-and-file-waits.md) |
| 交付件身份、版本与回收站 | 已实现 | `platform/ailab_platform/api/artifact_identity.py`、`platform/ailab_platform/api/owned_assets.py`、`platform/ailab_platform/models.py::FileObject` | [files](../modules/files.md) |
| Chat/Work 文档工具 | 已实现 | `platform/ailab_platform/agent/document_tools.py`、`platform/ailab_platform/domain/document_reading.py` | [ADR 0042](adr/0042-single-tool-catalog.md) |
| 资料库摘要、分享与文件提问 | 已实现 | `platform/ailab_platform/api/routes_library_summary.py`、`platform/ailab_platform/api/routes_library_tools.py`、`platform/ailab_platform/api/routes_shares.py` | [files](../modules/files.md) |

## 沙箱

| 能力 | 状态 | 代码位置（路径::符号或目录） | 决策/文档 |
|---|---|---|---|
| Broker 租约、所有权与对账 | 已实现 | `platform/ailab_platform/broker/service.py::LeaseManager`、`platform/ailab_platform/adapters/lease_store_redis.py` | [sandbox](../modules/sandbox.md)、[ADR 0003](adr/0003-broker-exec-proxy-and-leases.md) |
| 准入、弹性额度与闲置回收 | 已实现 | `platform/ailab_platform/domain/admission.py`、`platform/ailab_platform/broker/service.py::LeaseManager` | [ADR 0020](adr/0020-elastic-sandbox-allowance-and-idle-reclaim.md) |
| exec 代理、文件注入与交付 | 已实现 | `platform/ailab_platform/broker/service.py::LeaseManager.exec`、`platform/ailab_platform/broker/service.py::LeaseManager.put_file`、`platform/ailab_platform/sandbox/transfer.py` | [sandbox](../modules/sandbox.md)、[ADR 0003](adr/0003-broker-exec-proxy-and-leases.md) |
| Docker 与 OpenSandbox 适配 | 已实现 | `platform/ailab_platform/sandbox/docker.py`、`platform/ailab_platform/sandbox/opensandbox.py` | [sandbox](../modules/sandbox.md) |
| 渲染沙箱与按用户复用 | 已实现 | `platform/ailab_platform/broker/render.py` | [渲染沙箱设计](design/2026-10-31-render-sandbox-lifecycle.md)、[ADR 0032](adr/0032-prewarmed-single-use-render-sandboxes.md) |
| Work 镜像内 Office 读取 | 已实现 | `images/sbx-office`、`vendor/office-suite`、`platform/ailab_platform/agent/office_guides_v2.json` | [ADR 0025](adr/0025-work-office-reader.md) |
| 受控公网内容注入 | 已实现 | `platform/ailab_platform/egress.py`、`platform/deploy/base/network.yaml`、`platform/tests/contracts/test_sandbox_network.py` | [ADR 0007](adr/0007-controlled-public-fetch.md) |
| 后台命令、等待与中断 | 已实现 | `platform/ailab_platform/agent/background_exec.py`、`platform/ailab_platform/agent/background_helper.py` | [ADR 0008](adr/0008-long-run-budgets-and-leases.md) |
| Agent 直连 execd / Secure Access | 与目标架构有意不同（ADR 0003） | — | [ADR 0003](adr/0003-broker-exec-proxy-and-leases.md) |
| 跨轮空闲沙箱复用与预热池 | 与目标架构有意不同（ADR 0001） | — | [ADR 0001](adr/0001-single-node-scope.md) |
| 多节点 Broker 路由与分布式调度 | 未实现 | — | [ADR 0023](adr/0023-system-document-reader-and-concurrent-capabilities.md) |

## 记忆与技能

| 能力 | 状态 | 代码位置（路径::符号或目录） | 决策/文档 |
|---|---|---|---|
| 记忆内容、作用域与有界文档 | 已实现 | `platform/ailab_platform/memory_recall/store.py`、`platform/ailab_platform/models.py::MemoryItem` | [memory-skills](../modules/memory-skills.md)、[ADR 0035](adr/0035-memory-v2.md) |
| 记忆读取、召回与历史检索 | 已实现 | `platform/ailab_platform/memory_recall/search.py`、`platform/ailab_platform/memory_recall/retrieval.py` | [ADR 0019](adr/0019-memory-read-path-and-governance.md)、[ADR 0022](adr/0022-memory-consistency-and-scoped-recall.md) |
| 夜间整理、质量标准和来源标签 | 已实现 | `platform/ailab_platform/memory_dream/service.py`、`platform/ailab_platform/memory_dream/scheduling.py`、`platform/ailab_platform/memory_dream/prompts.py` | [ADR 0036](adr/0036-memory-quality.md) |
| 记忆页、记忆 API 与对话工具 | 已实现 | `platform/ailab_platform/api/routes_memory.py`、`platform/ailab_platform/agent/tools.py` | [ADR 0037](adr/0037-memory-single-origin.md) |
| 平台技能与权限策略 | 已实现 | `platform/ailab_platform/api/platform_skills.py`、`platform/ailab_platform/skill_policy.py`、`platform/ailab_platform/skill_registry.py` | [memory-skills](../modules/memory-skills.md) |
| 个人技能、草稿与整包替换 | 已实现 | `platform/ailab_platform/skill_drafts`、`platform/ailab_platform/api/routes_skills.py` | [memory-skills](../modules/memory-skills.md) |
| 基础技能与办公技能包 | 已实现 | `images/sbx-office/skills`、`platform/ailab_platform/agent/skill_modules.py` | [ADR 0025](adr/0025-work-office-reader.md) |

## Portal

| 能力 | 状态 | 代码位置（路径::符号或目录） | 决策/文档 |
|---|---|---|---|
| 流式接收、非阻塞发送与上传 | 已实现 | `platform/portal/lib/stream`、`platform/portal/lib/outbox.ts`、`platform/portal/lib/uploads.ts` | [portal](../modules/portal.md)、[ADR 0027](adr/0027-portal-ui-stack-and-send-flow.md) |
| 会话树、分叉、重试与历史版本 | 已实现 | `platform/portal/components/chat`、`platform/portal/lib/draft-tree.ts`、`platform/portal/lib/conversation-pages.ts` | [ADR 0041](adr/0041-fork-reuses-checkpoint.md) |
| GenUI 渲染、控件状态与 HtmlArtifact | 已实现 | `platform/portal/components/genui`、`platform/portal/lib/answer-parts.ts` | [ADR 0028](adr/0028-generative-ui-openui.md)、[ADR 0029](adr/0029-genui-registry-v2.md) |
| 样式 token、图标门面与 CSP | 已实现 | `platform/portal/styles/tokens.css`、`platform/portal/app/globals.css`、`platform/portal/components/icons.tsx`、`platform/portal/proxy.ts` | [portal](../modules/portal.md) |
| 原生文档查看器与预览 | 已实现 | `platform/portal/app/frame/[kind]`、`platform/portal/office/host.ts`、`platform/portal/components/preview` | [ADR 0027](adr/0027-portal-ui-stack-and-send-flow.md) |
| 文件库、记忆页、技能页、管理与分享 | 已实现 | `platform/portal/components/library`、`platform/portal/components/memory`、`platform/portal/components/skills`、`platform/portal/components/admin` | [portal](../modules/portal.md) |
| 移动端布局与触控交互 | 已实现 | `platform/portal/components/app-shell.tsx`、`platform/portal/lib/use-touch-reveal.ts` | [portal](../modules/portal.md) |
| E2E 钩子与脚本化 Agent | 已实现 | `platform/e2e/conftest.py`、`platform/e2e/scripted_agent.py` | [testing](../modules/testing.md) |
| assistant-ui / LangGraph 运行时 | 与目标架构有意不同（ADR 0004、ADR 0027） | — | [ADR 0004](adr/0004-self-developed-runtime.md)、[ADR 0027](adr/0027-portal-ui-stack-and-send-flow.md) |

## 部署

| 能力 | 状态 | 代码位置（路径::符号或目录） | 决策/文档 |
|---|---|---|---|
| 本机测试环境与增量部署 | 已实现 | `deploy.sh` | [deploy](../modules/deploy.md) |
| k3s base 与 dev/lite overlays | 部分实现（单机已验证；多节点调度与跨节点路由未实现） | `platform/deploy/base`、`platform/deploy/overlays` | [deploy](../modules/deploy.md)、[ADR 0023](adr/0023-system-document-reader-and-concurrent-capabilities.md) |
| 服务镜像与 Portal 构建 | 已实现 | `platform/deploy/Dockerfile`、`platform/deploy/portal.Dockerfile`、`images` | [deploy](../modules/deploy.md) |
| 模型目录与思考档位配置 | 已实现 | `platform/models.toml`、`platform/reasoning.toml`、`platform/ailab_platform/config.py` | [ADR 0038](adr/0038-reasoning-effort.md) |
| 指标、追踪与告警 | 已实现 | `platform/ailab_platform/telemetry.py`、`platform/ailab_platform/metrics.py`、`platform/deploy/observability` | [ADR 0006](adr/0006-optional-bounded-tracing.md) |
| 备份、校验与恢复 | 已实现 | `platform/ailab_platform/ops/backup.py` | [deploy](../modules/deploy.md) |
| 存储生命周期与删除重试 | 已实现 | `platform/ailab_platform/api/reaper.py`、`platform/ailab_platform/api/source_lifecycle.py` | [deploy](../modules/deploy.md) |
| 持续归档、异地备份与生产 RPO/RTO | 未实现 | — | 需另定目标与验收 |

## 测试

| 能力 | 状态 | 代码位置（路径::符号或目录） | 决策/文档 |
|---|---|---|---|
| 单元与集成测试 | 已实现 | `platform/tests` | [testing](../modules/testing.md) |
| 故障注入测试 | 已实现 | `platform/tests/faults` | [testing](../modules/testing.md) |
| 真实后端契约测试 | 已实现 | `platform/tests/contracts` | [testing](../modules/testing.md) |
| 浏览器端到端测试 | 已实现 | `platform/e2e` | [testing](../modules/testing.md) |
| 真实模型场景测试 | 已实现 | `platform/scenarios` | [testing](../modules/testing.md) |
| CI 与真实组件定期任务 | 已实现 | `.github/workflows/ci.yml`、`.github/workflows/real-components.yml`、`.github/workflows/audit.yml` | [testing](../modules/testing.md) |
| 测试夹具与 golden | 已实现 | `platform/tests/fixtures`、`platform/tests/golden` | [testing](../modules/testing.md) |
| 负载与容量基线 | 部分实现（脚本与短时基线存在；目标机长期容量与 2 vCPU / 8 GiB 联合上限未验证） | `platform/tests/load`、`platform/tests/acceptance` | [testing](../modules/testing.md) |

# AGENTS.md

Rules for anyone (human or agent) changing this repository. Start with this file, [current work](docs/progress/current.md), and [HANDOFF.MD](HANDOFF.MD). The [progress index](docs/progress/README.md) links goals, plans, capability status and evidence; do not infer current authorization from an old session or plan.

## 0. Resume and maintain continuity

- Before editing, read `docs/progress/current.md` for the active goal, authorized scope, paused work, blockers and next action; then read the relevant roadmap, status-matrix rows and linked design/review. Read `HANDOFF.MD` for known traps. For Portal work also read `platform/portal/AGENTS.md`.
- Verify the actual worktree, branch, HEAD, `git status --short --branch` and remote/base before relying on recorded state. Preserve existing edits. Report stale or conflicting records and reconcile them with code, evidence and the latest user instruction.
- Keep one source for each kind of information: `current.md` for the latest handoff; `roadmap.md` for goals/order/acceptance; `docs/architecture/status-matrix.md` for capability state; dated files in `docs/progress/updates/` for session outcomes and validation; `docs/reviews/` for detailed evidence; `HANDOFF.MD` for reusable traps; architecture ADRs for decisions. Link rather than copy long tables or logs.
- Update these records in the same change when scope, status, a blocker or a decision changes, and before handing off or stopping. `current.md` must state what was done, what remains, the next concrete action, branch/base and validation links. Dated updates must distinguish current checks from historical evidence and name the tested commit or code baseline, skipped checks and residual risk. Never record tokens, cookies, signed URLs or personal data.
- A plan is not permission to implement it. Preserve explicit pauses and pending product decisions across sessions; do not start the next roadmap item automatically. “Implemented”, “locally verified”, “externally accepted” and “merged to main” are separate claims. Portal-only support does not complete the backend contract.
- Do not mark a task done from test counts alone. Check its acceptance criteria, record untested boundaries, and leave the next session a usable next step. No timestamp-only updates or duplicate status files.

## 文档与测试证据精简

- 只保留有决策或复现价值的设计、结论、必要测试和关键证据；每个事实只维护一处，其他文档链接引用。`current.md` 覆盖当前交接，历史结论留在原 dated update，不逐轮累积整份旧状态。
- 默认不向仓库保存原始 JSON/JSONL、stdout/stderr、完整测试日志、逐帧/逐点采样、trace、中间值、生成 output 或多份相同截图。放入仓库外临时目录或 CI artifact；调试结束即清理，不复制到另一目录或改扩展名留存。
- 验收记录只写基线、必要命令、结果摘要、关键指标、失败根因、跳过/未测边界与下一步。已有摘要足够时直接引用；纯文档小改不新增一整套重复验收报告。
- 必要的最小回归 fixture、独有复现脚本和能证明 UI 问题的代表截图可以保留；新增时说明用途并删去重复材料。机器可读契约/配置不属于运行输出，不能按 JSON 扩展名误删。不要为证明测试通过提交几万或几十万行输出。
- 纯文档/证据清理只检查差异、引用和留存内容；未改产品/测试/配置时，不重跑 Python 全套、Portal build 或浏览器。代码变更仍遵守下方验证要求。

## 1. What we are building

AILab lite is the single-machine base of the AILab platform. Target architecture: `docs/architecture/` (frozen copy, see its README for the source version). Current state versus target: `docs/architecture/status-matrix.md`.

Success means: on one machine, Chat, Work, file upload/parse, artifact delivery, multi-turn edits and the project library are reliable; restart, redelivery, over-quota and dependency failure each have a defined contract; and moving to many machines later changes only adapters and deployment, never business authorization, the usage ledger, file delivery or the Portal event protocol.

Think from first principles: confirm the real goal, the constraints and the acceptance test before writing code. Fix bugs at their cause. Do not add a switch, abstraction or dependency without a concrete behaviour that needs it.

## 2. Service boundaries (do not blur them)

| Service | Owns | Must not |
|---|---|---|
| Portal (Next.js, `platform/portal`) | UI, httpOnly session cookie, same-origin proxy `/api/v1 → /v1` | hold model keys, sandbox tokens or call internal endpoints |
| Platform API (`api/`) | the business database (only writer), authorization, quota, ledger, file records | execute user code, parse untrusted files |
| Agent (`agent/`) | the agent loop, model calls | write the business DB directly, hold a container runtime socket, choose a `sandbox_id` from model output |
| Broker (`broker/`) | sandbox leases, exec proxy, file injection/delivery | trust paths or sizes from the agent without enforcing limits |
| Worker (`worker/`) | scanning, parsing, thumbnails, previews | parse in-process without time/memory/output limits |

Services talk over HTTP through `ailab_platform/clients.py` with per-service credentials and scopes (`ailab_platform/service_auth.py`). Internal routes live under `/internal` and are never reachable from the Portal proxy.

## 3. Code layout

```
platform/ailab_platform/
  api/ agent/ broker/ worker/   service entry points (keep five services)
  contracts/                    versioned enums, DTOs and events shared across services; no ORM, no HTTP
  domain/                       pure state machines and policies (run state, quota, versions)
  ports/                        Protocol interfaces where a second implementation is real or planned
  adapters/                     local/redis/s3/opensandbox implementations of ports
```

- Move code into `domain/ports/adapters` only when you are already changing it. No repo-wide reshuffles.
- `contracts` must not import `models.py`. Services other than the API must not import `models.py` either.
- One port per real coupling point. Do not wrap every function in a repository class.
- Factories reject unknown backends. Nothing silently falls back to a mock or local directory.

## 4. Reliability contracts

- **Idempotency** is keyed by the real unit of work: a model call (`call_id`), a delivery (`delivery_id`), an upload (`upload_id`). Never deduplicate a retry of a whole Run as one call.
- A business change and the side effects it triggers (queue message, event) are written in one transaction through the outbox (`outbox` table); a dispatcher delivers them. Do not enqueue after commit "and hope".
- Run state changes go through `run_state.transition_run` (compare-and-set). Terminal states are final.
- Resuming a Run never repeats a confirmed delivery or a tool marked not replayable.
- Usage records freeze every pricing input (weights, cached-token discount, policy version) at write time. History is never recomputed from current config.
- Every external call has connect/read/total timeouts. Every loop that waits has a deadline.
- Streams (S3 bodies, HTTP responses, browser readers) are closed in `finally`.

## 5. Security rules

- Ownership is decided in the API from the authenticated principal; never accept a `user_id` from the agent or the model.
- Internal endpoints check the caller's service scope and, where relevant, bind the request to a `run_id`/`job_id`.
- `AILAB_PROFILE=secure` refuses mock sandboxes, unknown backends, default credentials, stub scanners and models not on the allow-list. Add a check there for any new unsafe default.
- Never log secrets, tokens, presigned URLs or database passwords. Never put sandbox credentials into messages or events.
- Untrusted files are parsed only in the Worker's limited subprocess or inside a sandbox.

## 6. Performance budget

Target machine: 2 vCPU / ~8 GB for the whole stack, with pod limits in `platform/deploy/base/apps/services.yaml` (API/Broker/Worker 256–384 MiB). Performance is part of acceptance:

- Stream large files; never hold more than one full copy of a file in memory. Respect `max_upload_bytes` / `max_parse_bytes`.
- Every list endpoint is paginated or bounded. Every new query on a large table has an index and an `EXPLAIN` check on SQLite and PostgreSQL.
- Prometheus labels stay low-cardinality: never label by user, run, file or conversation id.
- Expensive derived data (previews, thumbnails, OCR) is produced once, cached under `derived/{file_id}/`, and served with immutable caching and ETags.
- State the cost (CPU, RSS, disk, latency) of any background job or new dependency in the PR description.

## 7. Python conventions

- Python 3.12, line length 140, `ruff check` and `ruff format` clean. Match the surrounding comment density: comments explain *why*, not *what*.
- Settings are `AILAB_*` via `config.py`; validate enums and ranges there.
- Database changes: one Alembic revision per change under `ailab_platform/migrations/versions`, numbered `NNNN_name.py`, upgrade **and** downgrade, works on SQLite and PostgreSQL. On SQLite add columns with plain `ALTER TABLE` (no batch rebuild of tables other tables reference). `tests/test_migrations.py` checks model/migration drift.
- Time is UTC; API output uses the `UTCModel` schemas.
- New behaviour needs a test that fails without the change. Fault tests (killed process, duplicate delivery, dependency down) go in `platform/tests/faults/`; real-backend contract tests in `platform/tests/contracts/` and skip cleanly when the backend URL is not set.

## 8. Portal conventions

- Read `platform/portal/AGENTS.md` (Next.js version notes) before touching the Portal.
- UI text is Chinese. Let the layout explain; avoid secondary labels and helper descriptions.
- Styles: tokens live only in `styles/tokens.css`. Every Portal stylesheet is in the components layer above Tailwind's preflight (`app/globals.css`); specificity and load order decide, so an override names the element it overrides (`.modal.search-dialog`), never relies on file order. Rebuilt UI uses Tailwind utilities or `styles/app.css`/`styles/genui.css`; when a part is rebuilt, delete the rules it replaces from `styles/base|shell|chat|library|pages.css`. Dependency choices are in ADR 0027; a new UI dependency needs a concrete use and a one-line reason there.
- Icons come from `components/icons.tsx` (lucide facade). Only `components/genui/**` imports `@openuidev/*` (ADR 0028).
- Modals and menus opened from the sticky sidebar must `createPortal` to `document.body`.
- Every fetch that can be superseded (detail loads, streams, polling) must be cancelled or ignored when stale.
- Types shared with the API live in `lib/api.ts` and mirror `ailab_platform/contracts`.

## 9. Testing

```bash
cd platform
export PATH="$(brew --prefix coreutils)/libexec/gnubin:$(brew --prefix findutils)/libexec/gnubin:$PATH"  # macOS
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest tests -q
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/15 \
AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_test \
AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_migrations .venv/bin/pytest tests -q
AILAB_E2E=1 .venv/bin/pytest e2e -q
cd portal && npm test && npm run build
```

Tests build their own isolated settings (`tests/conftest.py`). Never point a test at a developer database; the Postgres test URLs are wiped. Start throwaway Redis/PostgreSQL on non-default ports and stop them afterwards.

## 10. Workflow

- Work on a branch; keep unrelated user changes (e.g. `.DS_Store`) untouched.
- Small, reviewable commits with conventional messages (`feat(ledger): …`, `fix(broker): …`).
- Maintain the continuity records in section 0 with every meaningful change. Add reusable traps to `HANDOFF.MD`, update `docs/architecture/status-matrix.md` when a capability changes state, and record decisions that diverge from the target architecture as ADRs in `docs/architecture/adr/`. A handoff is incomplete if only the chat response contains its outcome or next step.
- Before saying "done": lint, full Python suite, Portal build, and for UI changes a real browser check (see HANDOFF for the Playwright route).

## 用户测试环境约定（2026-10-29）

- 仅在用户当次明确要求时部署，不把之前的部署授权延续到后续代码修复。
- 未明确要求，不备份，不在部署阶段另跑测试、浏览器或真模型验收，不重复 PR 已完成的检查。默认只更新必要组件、启动并记录命令结果；CI 按仓库既定流程执行。
- 本机是可重建的测试环境，允许失败；不得擅自套用生产备份、回滚和全面验收流程。部署优先复用当前镜像和未变化的依赖，只对变化部分增量构建。

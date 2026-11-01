# 记忆摘要、项目记忆模式与设置页 UI（2026-11-01，已合并并部署）

worktree `/Users/michael/Developer/ailab-lite/.worktrees/memory-summary`，分支 `feat/memory-summary`，base `origin/main@4c6f092`，已合并为 `main@21f3300`（见下“合并与部署”）。设计合同：[2026-11-01-memory-summary-project-scope.md](../../architecture/design/2026-11-01-memory-summary-project-scope.md)。

## 用户原始指令（2026-11-01，要点照录）

1. “项目仅使用本项目记忆”不应在记忆页设置，应在单独项目里点进去设置；并且可迁移：从“仅限项目”迁移并**融合**到“所有记忆”（像 ChatGPT），且两个方向可互相迁移。要考虑记忆怎么设计：融合时是重新整理成记忆，还是作为新记忆。
2. UI 太丑：技能页平台技能为适配开关把 ⋯ 移到开关左边，没开关时左侧空一块；记忆页“整理”是单独一块、不对齐；里面有很多 “undefined”。**其他页签也都检查并优化**。
3. 不需要向用户展示每天整理了什么（那是我们排障用的）。像 ChatGPT/Claude 一样设一个可点进去的“你的记忆” summary：是对全部记忆的总结，不展示背后所有记忆，背后用别的结构存。
   - 展示的可能只是 summary 的一部分；点“立即整理”，summary 与后端真实记忆同时更新。
   - 用户觉得 summary 不对时，可以**通过对话**直接修改，由 AI 更新后端记忆并重新生成 summary。
   - 既然是 summary 页，就不需要再分“个人偏好”和“长期记忆”。
4. 分工：**后端实现、测试和最终部署交给 Codex（gpt-6.1-sol，reasoning medium）**，质量由 Claude 最终把控，确保美观、没有 bug。最后**提 PR 并 merge**（部署已授权，按 AGENTS“用户测试环境约定”只更新必要组件、不备份、不额外验收）。
5. 全局习惯：第一性原理；坑写进 HANDOFF.MD；一组问题改完再整体 review，不逐条冒烟；找共同根因；优先内置浏览器（本轮内置 preview 截图超时，改用 Playwright 无头，见下）。

## 已完成

**后端（Codex 实现，Claude 审查并修正）**
- 迁移 0046：`projects.memory_mode`(shared/isolated)、`memory_merge_requested_at`；`memory_summaries` 表（账号/项目各一行，级联删除）；`memory_dream_runs.project_id/instruction`；旧 `project_only=true` → 该用户所有项目 isolated。
- 可见性/历史池统一在 `memory_recall/policy.py`（`visible(..., mode)`、`history_pool`、`same_history_pool`）。共享项目读账号+本项目，隔离只读本项目；历史：隔离只看本项目，其余共享池互通。
- 共享项目抽取按 fact `scope` 分流账号/项目；推断出的敏感事实不再以 review 入库。
- 共享→隔离：证据全来自本项目的账号事实移入项目（确定性、同事务）；隔离→共享：标记待融合并立即排整理，模型逐条决定 to_account/duplicate_of/contradicts/keep_project（`memory_dream/maintenance.py::merge_projects`）。
- 摘要 `memory_dream/summary.py`：digest 判过期，每次整理末尾最多重写 5 个作用域，不进模型上下文。
- 接口：`GET /v1/memory/summary[?project_id]`、`POST /v1/memory/summary/edit`、`DELETE /v1/memory/entries[?project_id]`、`PATCH /v1/projects/{id} {memory_mode}`、`POST /v1/memory/dream/run`（没新对话也会做融合/摘要）、撤销对 edit 恢复原摘要。完整响应形状见设计文档与 `routes_memory.py`。
- Claude 追加修正：三段提示词收进 `prompts.py` 常量并重写措辞；编辑的畸形 op 跳过而非整单失败、容量满给“记忆已满”；`report.changes` 计数（有删除则前端不提供撤销——删除谱系不可逆）；单个项目融合失败不拖垮整次整理（`report.merge_errors`，保留标记）；摘要与“使用记忆”开关解耦（关闭后不显示为空）；新增 `PATCH /v1/memory/settings`（开关不再整份重发事实）；`MockLLM` 对三段提示词给确定性回答；修 `scripts/evaluate_memory_context.py` 并恢复原 golden 测试；`tests/test_memory_summary.py` 新增 5 个用例。

**Portal（Claude）**
- 记忆页 `app/(app)/memory/page.tsx` 重写：上方“你的记忆”卡片（摘要前两行预览，点开全文），下方四个开关（使用记忆/自动记忆/引用历史对话/夜间自动整理），去掉逐条列表、搜索筛选、字数配额、整理日志；删除 `components/memory/dream-panel.tsx` 及 `.dream-*`、`.memory-row/...` 等旧样式。
- `components/memory/memory-summary.tsx`：`useMemorySummary`、`MemoryCard`、`MemoryDialog`（立即整理/“已是最新”、⋯清空、对话式修改带回复气泡与撤销、活动整理轮询、打开时过期自动整理一次）。`components/memory/project-memory.tsx`：项目记忆弹窗，“仅使用本项目记忆”开关 + 迁移确认文案。入口：项目页头部 ⋯ → 记忆；侧栏项目 ⋯ → 记忆（`lib/projects.ts` 把原 brief 请求泛化为 `requestProjectPanel/takeProjectPanel`）。
- `Modal` 增加 `actions`；`lib/api.ts` 增加 `MemoryMode/MemorySummary`、`Project.memory_mode`。
- 其他页签：技能行与定时任务行统一为“开关在前、⋯ 在最右”，删 `.switch-slot`；技能扫描中显示转圈+“安全扫描中…”而非红色“无法使用”；ZIP 文件数为空时不显示；账号页改为与设置页一致的 `mpage/mlist` 布局、去掉标题副说明；管理-技能“待审核”标题间距（`.admin-section > h2`）；输入框占位符原先贴在右侧（`left: inherit` 缺陷）已修。
- “undefined”：用本机部署数据只读核对了整理报告/条目字段结构（未读内容），未能复现具体位置；出现它的整理日志面板已整体移除，其余页签（含管理 9 个标签）文本扫描无 undefined/NaN/null。

## 验证（审查修复后的未提交工作树）

基线：`feat/memory-summary`，HEAD/base `4c6f092`；全部产品修复未提交，Python 3.12.11。命令从 `platform/` 执行（E2E 从根目录使用 `platform/` 路径），导出 GNU coreutils/findutils PATH；pytest 加 `-o addopts='--strict-config --strict-markers'` 显示统计。

| 检查 | 命令 | 本轮最终结果 |
|---|---|---|
| lint/格式 | `.venv/bin/ruff check .`、`.venv/bin/ruff format --check .` | 通过，510 files formatted |
| SQLite 全量 | `.venv/bin/pytest tests -q` | 1930 passed / 163 skipped |
| PostgreSQL 16.15 + Redis 全量 | 同上，设置下述测试 URL | 2037 passed / 56 skipped |
| Portal | `cd portal && npm test && npm run build` | 28 files / 131 tests passed；生产构建通过 |
| 指定浏览器 E2E | `AILAB_E2E=1 .venv/bin/pytest e2e/test_memory_skills.py e2e/test_portal.py -q` | 40 passed |

- 本轮回归：批次终态/游标先提交，租约覆盖摘要阶段并在模型调用前续租、finally 释放；`active_job()` 统一摘要 GET、编辑 409、手动整理复用、夜间 enqueue 与整理链判断，claim/recover 仍不领取终态。阻塞摘要 mock 验证旧文本期间 active/409/复用，结束后 active=null 且文本更新；摘要异常或租约过期仍保留终态/游标、重投不重提取。Portal 编辑等摘要 active 为空才显示完成回复/撤销并 load；等待仍受 FOLLOW_MS 限制。
- 双库 EXPLAIN 回归通过：8,000 条整理记录、64 用户，ANALYZE 后检查新活动谓词的实际索引路径，复用现有用户索引，无新迁移。新增开销为摘要模型调用前一次租约更新、每任务末尾一次主键释放更新；不增加模型调用、常驻任务或依赖。
- 首轮全量均加载了旧的“终态摘要模型调用时租约为空”断言，已改为有效租约；SQLite 摘要专项 51 passed，最终双库全量均通过。首轮 PG 另有 `test_pre_output_fallback_has_distinct_calls_and_real_ledger[429]` 因额外超时重试得到 3 条而非预期 2 条账本；未改无关网关逻辑，保留首次失败，最终重跑结果见表，不能据一次重跑宣称时限用例稳定。
- 临时测试 URL：`AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:25432/ailab_test`、`AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:25432/ailab_migrations`；Redis `AILAB_TEST_REDIS_URL=redis://127.0.0.1:26379/14`，E2E 使用 `/15`。E2E 服务端口 18480–18483、Portal 13400；仅使用专用临时实例，PG/Redis 均已停止。
- 原始日志仅在仓库外 `/tmp/ms-summary-regression-*.log` 和 `/tmp/ms-summary-regression-e2e/`。本轮未跑完整 E2E 或视觉走查（此前 154 passed / 6 skipped 与 1280/390/深色走查是历史证据）；本轮指定 E2E 为真实浏览器 + mock 模型，未测真实模型质量或生产隔离。

## 合并与部署

- [PR #137](https://github.com/shijunan126-creator/ailab-lite/pull/137) head `3af6da3`（最终工作树：SQLite 1930 passed/163 skipped，PG16+Redis 2037 passed/56 skipped，Portal 131 passed + build，E2E 记忆/Portal 40 passed）CI 9 项全绿，squash 为 `main@21f3300`。
- Codex 部署 `main@21f3300` 到 AILab-local：后端共用镜像与 Portal `release-21f3300`，其余复用；迁移 0045→0046；12 服务 running、10 项健康检查 healthy、Portal 200。未备份、未做部署后验收。

## 原剩余工作（已完成，保留作历史）

1–3 **已完成并修复审查 1–9**：批次终态/游标与摘要解耦，失败路径仍刷新摘要；请求作用域优先；空作用域与禁用不自动运行；去除摘要 GET 历史扫描和重复轮询；编辑仅围栏本作用域事实；删除与召回查询开销修正。本轮补充修复摘要阶段活动租约与 Portal 编辑等待；验证与首次失败/复跑结果见上。本次实现子任务未 commit/stash/reset、推送、提 PR、merge 或部署。

4. 整体 review（可用 `/code-review`），然后更新 `docs/progress/current.md`、`docs/architecture/status-matrix.md` 记忆行，提交（conventional commits）、推 `origin`、提 PR（描述写成本：摘要每变化作用域一次短调用、每次整理≤5；融合每项目一次；编辑每条指令一次编辑+一次摘要；无新常驻任务/依赖），等 CI 9 项全绿后 squash merge。
5. 部署（已授权）：交给 Codex（gpt-6.1-sol medium）按 AILab-local 惯例部署 merge 后的 main（后端共用镜像 + Portal，迁移 0045→0046），不备份、不额外验收，个人细节只记在 `~/Documents/AILab-local/HANDOFF.MD`；回填本记录与 current.md。

## 临时验收栈（仓库外，可重建）

- 脚本在 `/tmp/ms-stack/`：`api.sh`（独立 SQLite `/tmp/ms-stack/data/ui.db`、memory 队列、mock 模型，端口 18180，账号 `ui-user`/`ui-password-1234`）、`seed.sh`、`reset.sh`、`shot.py`（页面截图）、`flow.py`（记忆完整走查，`W=390`/`DARK=1`）、`tabs.py`（管理页逐标签扫文本）。Portal：`cd platform/portal && npm run build && AILAB_API_BASE=http://127.0.0.1:18180 node node_modules/next/dist/bin/next start -H 127.0.0.1 -p 13100`。改 Portal 后必须先停服务再 build 再启动。
- 内置 preview 对该栈截图超时，Playwright 无头可用（cookie `ailab_token` 取 `/tmp/ms-stack/token`）。
- 本机部署 Portal 在 `localhost:23000`（需登录，未用）。

## 风险与未测边界

- 真实模型下的摘要质量、编辑指令理解、融合判定未验（只有 mock 与脚本化单测）。
- 多批整理链每批都会刷新过期摘要（每批≤5 次调用，请求作用域优先），可接受但非最优。批次先发布终态，活动租约保留至摘要结束；进程崩溃最多等剩余 10 分钟租约过期，摘要保持过期并在下次整理重试，不会重提取已完成批次。
- 迁移 0046 按设计保留：既有非 `project_only` 用户的项目升级为 shared，其账号外对话与所有共享项目历史将互相可召回；原 `project_only` 用户的项目迁为 isolated。
- 既有 review/suppressed 条目无界面入口，仍不进模型与摘要，“清空”时删除。

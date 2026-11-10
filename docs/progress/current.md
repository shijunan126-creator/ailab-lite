# 记忆质量与整理节奏（2026-11-10）

- 用户授权按[计划](../architecture/design/2026-11-10-memory-quality.md)全部实现，含迁移 0067、ADR 0036、真实模型评估、推送、开 PR 并修到 CI 全绿；不授权合并、部署、改动本机运行中的服务或数据库。分支 `feat/memory-quality`，base `main@36b17590`；[PR #177](https://github.com/shijunan126-creator/ailab-lite/pull/177)。
- 已完成 A–F 六步（每步一个提交）：每晚每用户一条整理链；删除来源保留记忆；中文标准、转变与理由代号（ABI 6）；来源项目标签与独立项目文件隔离；提示词版本与旧文档整理一次；记忆页分类卡片。决定见 [ADR 0036](../architecture/adr/0036-memory-quality.md)，验证、评估数字和偏差见[记录](updates/2026-11-10-memory-quality.md)，坑见 HANDOFF 2026-11-10。
- 第二轮（用户要求修正并补覆盖）：0068 把旧夜间 explicit 行改回 inferred，一次性整理可删低价值推断行；真实库副本新旧对比、一次性整理、真实服务链路缓存（Chat 97.3% / Work 98.4%）已做；修复调度循环被慢链阻塞。整夜压测按用户要求取消。数字见[记录](updates/2026-11-10-memory-quality.md)。
- PR #177 已合并（`80f62af6`）并部署本机。之后修复：「立即整理」不做按新标准的一次性整理（分支 `fix/manual-memory-cleanup`）；下一步 CI 绿后由用户决定合并与部署。
---

# CI 与测试提速（2026-11-10）

- 用户要求排查 tests/CI 的低效和无用部分并修复。分支 `ci/faster-tests`，base `origin/main@37850623`，[PR #176](https://github.com/shijunan126-creator/ailab-lite/pull/176)；未改产品代码，未部署。
- 已完成：Python 双库套件改为 pytest-xdist 并行，每个 worker 独立库与 Redis db；E2E 分 3 片，Portal 单测只跑一次；去掉测试里的固定等待（服务器轮询、LLM 退避、本机代理）；修复 `lock_deps.sh --check` 恒通过的问题。根因、数字和边界见[记录](updates/2026-11-10-ci-speed.md)，坑见 HANDOFF 2026-11-10。
- 本机：SQLite 11:21 → 91 秒，PG+Redis `-n 4` 185 秒，全部通过。CI `533ae0cd` 11 项全绿：整轮 12.2 分钟（原约 20 分钟），双库各约 7 分钟，E2E 三片 6.5–11.3 分钟（最慢一片含 3.8 分钟 apt 波动）。之后 CI 暴露了并行下的时序依赖和 apt 下载慢，已一次性排查修复（见记录“并行暴露的时序依赖”）。下一步：确认 PR #176 最终 head 的 CI 全绿；合并由用户决定。

---

# 分支与工作区清理、仓库卫生规则（2026-11-10）

- 用户要求工作区切到最新 main 并清掉过期分支。已 fetch：`main = origin/main @ e9d0056d`（含 #172/#174 记忆 v2）。上一轮工作区停在 `fix/source-pill-alignment`（落后 30 个提交），据此得出的记忆页结论已在对话中按最新 main 更正。
- 已删除全部 226 个本地分支（只留 `main`）、origin 上 134 个 PR 已合并/已关闭的分支（保留 `main` 和唯一 open PR 的 dependabot 分支）、`review-fork` 的 7 个工作分支。删除前唯一未推送到别处的提交是切 main 前的文档快照 `12de4a0d`，仅存于 reflog。
- 清理 296MB 的 `.local/file-context-ocr` 调查暂存（含 `ocr-token` 凭据文件）、2.4MB `platform/build`、散落的 `.DS_Store` 与 `vendor/zcode-office-runtime/runner.mjs` 残留；`platform/e2e-logs/` 保留，2026-10-24 审查仍引用其中的截图。
- AGENTS.md §10 新增 `Repository hygiene`：分支不留档、远端只留默认分支与 open PR、worktree 与 prune、构建/暂存输出不入仓库、凭据目录及时删除、Docker 只回收构建缓存不动业务卷。未改产品代码，未跑测试，未部署。

---



- 分支 `feat/memory-doc`，base `origin/main@c4e88613`，本轮全系统审查基线 `02f0e066`（保留 `a79c712a` Portal提交）（已拉取 Portal 移动布局/新到旧排序）；[PR #174](https://github.com/shijunan126-creator/ailab-lite/pull/174)。授权实现、真模型、只读部署库副本、前端 merge、推送与 CI；不合并、不部署。
- 已完成有界文档/ABI5/单调用 Dreaming、程序融合/保留撤销、历史行索引回收、会话失效/条件空闲刷新、前序 turn context 与脱敏；76项旧失败已按新路径迁移。前端 `4bf62947` 经 merge commit 合入并保留菜单修复。
- 测量、检查、最小 Portal 修正与边界统一见[本轮记录](updates/2026-11-09-memory-document.md)，设计见[ADR0035](../architecture/adr/0035-memory-v2.md)。PR 已 ready；本轮修复暂停写入、原因拆分、无变化/行围栏、无效维护、closed隐私、空闲压缩；最新决定撤销用户删除重建，删除仅推送handle通知、保持前缀，list为当前事实。本轮修复shared作用域/重定向/保护/review/逐op并发/维护无进展/轻量设置，补两个revision旧断言；下一步核对最终head双库/E2E CI并修复失败；不得把历史通过当作最终验收。

# Memory v2 后端质量与评测（2026-11-09）

- 分支 `feat/memory-v2-b`，实施基线 `feat/memory-v2-a@c6fe1d46`，base `origin/main@df83d8e`；已含前轮Portal。最终产品/测试冻结`72efcf3153576ee6533f9ec8ef140ff6210f1326`。仅本地提交，无部署、推送或PR。
- 已完成质量修复、0064持久队列/恢复/计量、测试来源排除、无时钟常驻块与确定增量。检索/时效、ABI2真实触发及六场景真实缓存达到数值门槛；真实历史副本已删除。审查修复、§3–§15完成/偏差、提示词各轮数字和逐调用缓存只维护在[实施记录](updates/2026-11-09-memory-v2.md)。
- 最终验证：SQLite2672/247跳过，PG/Redis2849/70跳过，前缀132/0跳过，全部0失败；隔离E2E194/15跳过，Portal274测试/build通过（各自基线见记录）。一次性PG/Redis及E2E服务已停止，临时数据/调试输出已清理。
- 剩余边界：模型仍偶发无效JSON并被拒绝；提取质量未独立人工标注，2条兴趣价值偏弱；未测300真实用户整夜负载及真实服务链路缓存。下一步交主代理审阅本地提交，不自动推进部署或其他路线图。

---

# 首条消息与会话原子创建（2026-11-09）

- 用户报告 `/c/cnv_bf66cafddac34d6a8b4a` 空白，原有 PRO 正文和失败记录消失。证据：Conversation 已创建，但 `runs=0`、`run_events=0`、`queued_messages=0`；首条消息返回 `429 too many active runs for this user`。工作树 `.worktrees/first-message-admission`，分支 `fix/first-message-admission`，base `origin/main@e47d22d8`；主工作区既有修改保留。
- 已修复：新增 `POST /v1/conversations/start`，在一个用户锁和事务内完成 Run 准入、Conversation 与首条 Run 创建，失败回滚；同一 `client_send_id` 重放幂等，内容变化 409。Portal 新会话首条发送改走原子接口，并让已发送项保留到导航完成。根因、验证和边界见[记录](updates/2026-11-09-first-message-admission.md)。
- 合并与部署：PR [#170](https://github.com/shijunan126-creator/ailab-lite/pull/170) 的 head `a3d49234` 九项 CI 全绿，squash merge 为 `main@ad756f92`；已增量部署 `release-ad756f9`。Portal `/login`、API `/readyz` 返回 200，OpenAPI 已注册新接口；12 running / 10 healthy，数据库 revision `0063`。
- 验证：定向 SQLite 回归、Portal 272 项测试与生产 build、完整 E2E 通过。完整本机 Python 套件仍有 16 项 Office/document 运行时环境失败，不在本改动路径。
- 边界：历史空会话不会自动恢复；待发送 outbox 仍只在浏览器内存，完整刷新会丢失未提交的失败提示。下一步等待用户实际使用反馈，不推进其他路线图项。

---

# GenUI 主动使用与分档组件（2026-11-08）

- 用户要求：模型更主动但不滥用 UI 组件，GenUI 初始提示放宽到约 3000 token；新增截图式“色块 + 说明”分档组件。工作树 `.worktrees/genui-proactive`，分支 `feat/genui-proactive`，base `origin/main@34b1e58e`；主工作区既有修改保留。
- 已实现：索引按内容形状触发并写明不用界面的边界；13 个常用组件说明直接进系统提示（其余仍按需 `ui_components`）；语法与对错规则移入索引，工具指南只剩交互；新增 `Segments`。根因、token 与评测见[记录](updates/2026-11-08-genui-proactive.md)。
- 追加：模型目录支持可选 `session_header`，可接入要求会话头的 OpenAI 兼容端点（OpenCode Go）；“为什么”类问题按内容形状判断（因果链可用 Flow），不再整类列为纯文字。最终提示词经 OpenCode 复测：主动 9/9、多余 UI 0/14、闭合块 15/15 合法。
- 验证：真实模型主动用例 5/8 → 9/9（最终版），未缓存输入 113,912 → 32,425。Portal 270/build、Ruff、全套 SQLite 通过（唯一时序抖动已修），详见记录。
- 下一步：用户已授权提 PR、CI 通过后合并，并一次性部署本机。
---

# 网页图片短 ID 与无落盘代理（2026-11-08）

- 用户授权按前序讨论实施；工作树 `.worktrees/web-image-refs`，分支 `feat/web-image-refs`，base `origin/main@e3122ae3`。主工作区修改保留；用户随后追加授权提 PR、CI 通过后合并并增量部署。
- 已实现：16 字符图片 ID、API 地址去重/attempt 围栏、候选 24h 与已采用消息引用、分享路径权限、分叉保留与引用回收；列表缩略图/大图按需流式转发，不再持久化图片。新链路取消签名 key 要求，保留旧路径兼容。
- 检查与边界统一见[实现及验收](updates/2026-11-08-web-image-refs.md)。真实模型已搜索配图并在后续一轮复用 ID，浏览器桌面/手机浅深色与大图通过；最终图片/索引回归 SQLite 22/1 跳过、PG 23/0；Portal 267/build、Ruff 通过，全套首轮失败与复核边界见记录。
- 下一步：提交 PR，CI 通过后合并并增量部署最新 main；本轮临时验收环境已清理，不重复部署验收，不推进其他路线图项。
- 前序 UI/技能 PR #165 的范围与历史验收见[原记录](updates/2026-11-08-ui-polish-media.md)。

## 上一轮交接（2026-11-08 文件输入 PR #161 已合并）

- 最新授权：用户在 PR #161 上问预览开关是否会让页码错位；同意按“每种格式一套编号”修复后，要求提 PR、合并并部署最新版。PR head `4338d69d` 的 13 项 CI 全绿后 squash 合并为 `main@2bf86144`。
- 本轮完成：Word 页图只走服务端；PPT 服务端导出包含隐藏页；修复 CI 的两个根因（Portal `jszip` 锁文件不一致、`get_storage` 关闭后并发首次创建的竞争）。根因、验证和风险见[页码一致性记录](updates/2026-11-07-document-page-numbering.md)。
- 验证：Ruff 通过；PostgreSQL/Redis 全套 2739 通过 / 69 跳过；E2E 193 通过 / 15 跳过，唯一失败是旧的 Word 浏览器截图期望，已改并重跑通过。
- 剩余：已渲染过、并且含隐藏页的 PPTX 不会自动重渲（页图缓存没有渲染器版本）；本机现有 PPTX 都没有隐藏页，不受影响。listener 转换路径已在合并后的 sbx-office 镜像里实测；Portal 浏览器路径没有真实跑过。
- 下一步：等用户使用反馈。若要处理旧 PPTX 页图缓存，先给 `RenderedPage` 和文件预览元数据加按格式划分的渲染器版本。

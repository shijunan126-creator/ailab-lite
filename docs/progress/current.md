# 当前工作交接：memory / JS / 管理页集成（2026-10-31）

工作树 `.worktrees/polish-integ`，分支 `fix/memory-js-polish`，基于 `origin/main@995a5f6`；部署产品基线 `dd25c82`、平台标签 `sm-p3-dd25c82`，未 push、未 PR。

**完成**：P3/P4 检查与 P5 指定部署和复验已记录，个人技能与 xlsx Run 最终成功；手动整理和有行页面菜单已检查。详见 [P3–P5 记录](updates/2026-10-31-memory-js-polish.md)。

**遗留**：整理新增语义重复的偏好；xlsx Run 最后一次附件查看超时，虽成功交付并最终 succeeded，原因尚未完全确认。定时任务无行，菜单未测。本包按要求只提交文档，未改产品代码。

**下一步**：Claude 打开 PR 并合并；审阅上述缺陷与未测边界。未 push 或创建 PR。

---

# 当前工作

## 记忆质量与 run_js（2026-10-31）

`fix/memory-js-backend`，base `origin/main@995a5f6`；后端提交 `b7480ed`、`b57331e`，未 push。本轮收紧偏好规则，真实 episode 对照及三条指定记忆删除见[记录](updates/2026-10-31-memory-js-backend.md#p2a真实来源回放与删除)。

**下一步**：等待单独的集成包；本轮明确不集成、不部署、不做真实模型产品验收。完整检查由后续集成包负责。

## 技能、办公、记忆与轻量计算（2026-10-31）

`feat/skills-memory`，base `main@b35ef6e`，已通过 [PR #110](https://github.com/shijunan126-creator/ailab-lite/pull/110) 合并至 main（merge commit `8cd9792`，保留各独立提交）。PR CI 13/13 通过；[main CI](https://github.com/shijunan126-creator/ailab-lite/actions/runs/41104326303) 通过。

**已完成**：需求全部范围已实现（基础办公技能、句中多技能且不强制 Work、后台技能草稿与目录树编辑、回到底部、浏览器 QuickJS、增量记忆整理与报告、稳定提示前缀，迁移 0040–0043），见[汇总记录](updates/2026-10-31-skills-memory.md)。最终 `ca1ee78` 全套通过：SQLite 1713/111 跳过、PG+Redis 1768/56 跳过、Portal 113 + build、E2E 145/6 跳过（[I3](updates/2026-10-31-skills-memory-integration-i3.md)）。本机已部署 `sm-i3-ca1ee78`，12 服务 healthy，迁移 0043，入口 http://127.0.0.1:23000。

**未验**：多技能、办公产物、草稿提取与记忆决策的真实模型验收；供应商缓存命中率、QuickJS 设备开销与浏览器截图、真实沙箱下的办公产物。本机部署仍为 `sm-i3-ca1ee78`，未变更。

**下一步**：本范围无必须执行的后续工作；上述未验事项等待用户决定。

## 菜单、收起侧栏与手机字号修复（2026-10-30）

`fix/sidebar-menu-motion`，base `origin/main@e586ff6`，[PR #109](https://github.com/shijunan126-creator/ailab-lite/pull/109)。用户本轮明确授权提交、push、仅 Portal 增量部署，以及最终 head CI 全绿后 squash 合并。Claude 完成 UI 修复、验证并担任最终审阅；Codex 维护记录与发布。

范围：菜单按压跳动/锚定、收起侧栏归还列宽、手机统一字号及字段防缩放、记忆页共享 Select 与控件高度。根因、检查与未测边界见[本轮记录](updates/2026-10-30-menu-motion.md)。Codex Ruff check/format 与 Portal 96 项通过；完整检查交给 PR CI。

**部署**：`ui-fix-1865bd458cbb`，源 `be68280` 的固定产品树；只重建 Portal、复用其他镜像。sync/build-portal/up/health/portal-http/migration 均 exit 0；12 服务 running、配置健康检查均 healthy，Portal HTTP 200，Alembic head/database 0039。无备份、部署后浏览器/模型或其他测试。

**下一步**：部署记录已提交后等待最终 head CI 全绿并 squash 合并。P6 与组件目录扩展仍待用户决定，多块穿插尚未付费评测，不自动推进。

## GenUI 组件目录 v2 与按需披露（2026-10-30）

`feat/frontend-genui` 已经 [PR #108](https://github.com/shijunan126-creator/ailab-lite/pull/108) squash 合并至 main，合并提交 `e586ff6`。结果、修复与检查见[本轮记录](updates/2026-10-30-genui-registry-v2.md)，决定见 [ADR 0029](../architecture/adr/0029-genui-registry-v2.md)。

**已完成**
- 目录由 21 个组件扩到 57 个，派生数字由组件计算。
- `ui_components` 实现按需披露。Chat 系统提示由 5.6k 降到 1.4k token，Work 由 6.3k 降到 2.2k；决定回答对错的规则留在索引里。
- 全部组件在浅色/深色、桌面/手机下做了浏览器检查，发现的问题已修复。
- Codex 后端改动已审核，Ruff 与 Python 全套通过。
- 先部署了 `genui-v2-5bf1a1e460a6`，真模型 9 例全部正常渲染；之后又部署了 `genui-v2-011828392781`，见下文。

**P6（门槛：无错误陈述）**
- 用户已把门槛改为“整份回答没有错误陈述”，块外复述只作诊断。授权的付费提示词迭代共两轮：
  - v6：两种模式的基础提示加了一条通用数据陈述规则，严格项 20/23（v5 为 15/23），错误陈述 33→12。
  - v7：再加“只描述自己确实实现的功能”，严格项 16/23。新增失败属于计数和排名笔误，与这条规则无关，单次运行波动约 ±4/23。
- 按用户“多次无提升即停”的要求停止迭代：采用 v6 提示词（系统提示 sha256 前缀 `400cbdfec9008dd62`），撤回 v7 那条。
- P6 仍未达到 100%。遗留问题：
  - 缓存解释在 v6 和 v7 中都无条件给出延迟数量级（no_ui 9/10）。
  - 偶发笔误。
- 引文与判定见[评测报告](updates/2026-10-30-genui-real-model-eval.md#迭代-v7)。

**发布进展**
- PR #108 已 squash 合并为 `e586ff6`，9 个 CI job 全绿：SQLite 1655、PostgreSQL 1706、E2E 142。
- 已部署的 `genui-v2-695dbf2d51d0` 与该 main 基线的产品代码一致；后续 CI 修复只改测试与文档，无需为 #108 再次部署。历史部署结果见[发布记录](updates/2026-10-30-genui-registry-v2.md#多块穿插用户要求放宽)。菜单修复部署状态见上节。
- P6 为 20/23，多块穿插未付费评测；下一步由用户决定 P6 与目录扩展。

## GenUI 对话模式空白块与 HtmlArtifact 坏 JS（2026-10-30）

`feat/frontend-genui`，base `origin/main@97793e6`，未提交。根因、修复和检查见[本轮记录](updates/2026-10-30-genui-chat-render-follow.md)，修复覆盖五项：
- OpenUI 中文名字被词法器跳过；
- 单引号串吞反斜杠，导致页面 JS 损坏；
- 历史里缺来源；
- 工具发现误当权限；
- 滚动跟随。

部署：用户本轮授权，由 Codex 执行。后端和 Portal 先部署为 `genui-chat-5ea5a21487fc`，修复空代码块后 Portal 又增量部署为 `genui-chat-68d185733e9e`。服务 healthy，Portal 返回 200。

真模型验收共 6 例，结果见[本轮记录](updates/2026-10-30-genui-chat-render-follow.md#codex-本机部署与真模型验收)：
- 所有块均为 ok，没有空白块，没有工具不可用，也没有否认来源。
- 滚动跟随三阶段全部通过。
- 发现三项问题：
  - 空代码区域：已修复。
  - Work 追问用 CSS 柱状条替代图表：模型遵循问题，与 P6 同类。
  - 趋势结论写错：模型遵循问题，与 P6 同类。

**下一步**：用户决定 P6 的处理方式，以及是否扩充组件目录（偏差条、时间轴等）；多块穿插尚未付费评测。相关代码已随 PR #108 合并。

## P5 收尾、ui-state、HtmlArtifact 与 P6 根因修复（2026-10-30）

`feat/frontend-genui`，base `origin/main@97793e6`，全部未提交、未 push。用户要求做完全部剩余项（GenUI chunk 超预算除外），整体验证一次，最后由 Codex 部署。

**已完成**
- 控件状态跨刷新保留，分享页和打印页可见。
- 性能闸门通过。
- P5：清空 legacy、启用 preflight、加 CSP。
- P4-H HtmlArtifact。
- PostgreSQL 全套通过。
- P6 失败的根因修复：
  - 表达式结论、对象数组数据。
  - 形状错误的数据显示为“数据格式有误”。
  - 复制按求值后的树和读者当前状态生成。
  - delta 定义为变化率（%）。
- 整体验证：Ruff、SQLite/PG 全套、Portal、E2E 和性能基准，结果见[本轮记录](updates/2026-10-30-p5-ui-state-html-artifact.md)。失败均为测试与新行为不一致，已修正后定向复跑通过。

**未完成**
- P6 严格数据一致性仍未达标：第四轮 47.8%，见[评测报告](updates/2026-10-30-genui-real-model-eval.md)。规则和示例已写清楚，剩余的是模型遵循问题。是换模型、加运行时约束还是调整门槛，需要用户决定。
- GenUI chunk 85.7 KB，用户已接受。

**部署**：已按用户要求增量部署本机测试环境。重建了 Portal 和后端共用镜像，其余镜像复用。5 个更新服务 healthy，迁移到 0039，HtmlArtifact 用默认开启。未备份，部署后未另做验证。部署身份和命令结果只记在仓库外的 AILab-local。

**下一步**
- 用户体验本机部署，决定 P6 的处理方式。
- 相关代码已随 PR #108 合并；后续产品改动与部署需用户另行要求。

## GenUI P6 默认模型评测（2026-10-30）

本轮明确授权付费评测；`feat/frontend-genui`，HEAD/base `origin/main@97793e6`，保留全部并行未提交改动。新增 `platform/evals/genui/` 与唯一允许的 Portal 新文件 `scripts/genui-eval-check.ts`，未改产品提示词/后端/性能脚本，未提交、push或部署。首轮37场景43请求：闭合块100%、不应UI误出0%、两轮编辑83.3%，严格数据一致性人工23项仅43.5%（只看数值/上下文69.6%），P6未通过。仅失败15场景复测一次，系统性数据问题仍存在，已停止；总计385,252 tokens，原始临时输出已清理。方法、人工核对、成本和提示词建议见[唯一评测报告](updates/2026-10-30-genui-real-model-eval.md)；新增harness离线3项与Ruff通过，未重复产品全套/浏览器检查。下一步由提示词负责人审阅建议及图表精度目录缺口，修改与下一版评测另行授权；不自动开启灰度或部署。下方旧“P6未做”由本节更新为“已评测未达标”。

## 前端重设计与可交互 GenUI（2026-10-30）

`feat/frontend-genui`，base `origin/main@97793e6`，全部未提交/未 push/未部署。已完成：GenUI 页内交互（绑定、表达式、Select/Slider/Toggle/Input、图表读数/图例、表格排序）；溢出与字号根因修复；墨青强调色与全局样式/Composer 重做；模式开始后固定（Portal 已按下方 API 合同调整，改模式用分支），技能收进 + 菜单。审阅并接入 Codex 的队列幂等与固定模式后端。Ruff、Python 全套、Portal 72 项/typecheck/build 通过；E2E 134/138，4 项旧定位器修后受影响文件 10 项通过。详见[本轮记录](updates/2026-10-30-frontend-redesign-genui-interactive.md)。剩余：GenUI chunk 81 KiB 超 60 KB、性能闸门、P5 全站迁移、ui-state 持久、P4-H、P6 评测。下一步：用户审阅；提交/PR/部署需用户当次授权。

## 对话模式固定 API 合同（2026-10-30）

用户本轮授权在 `frontend-genui` 实施后端固定模式与测试；`feat/frontend-genui`，HEAD/base `origin/main@97793e6`，保留并行 dirty 改动，不提交/stash/reset/revert，不修改 Portal 或队列幂等/0038。已接通消息、入队/出队、编辑/重试；模式来自首个非复制 Run，否则 fork 选择；定时任务仍每次创建新会话。专项16项及Ruff通过；完整Python套件只跑一次（沙箱端口/ps限制及一处旧测试附带切换失败），已修正该测试；解除限制后全部失败函数/参数组与专项集中复核228项通过；结果、性能查询和旧测试/E2E调整清单见[本轮记录](updates/2026-10-30-conversation-mode-fixed.md)。Portal与E2E已在同日前端重设计中按新合同调整（见上节）。

## 队列发送持久幂等修复（2026-10-30）

当前用户授权仅在 `frontend-genui` 后端修复与验证；分支 `feat/frontend-genui`、HEAD及本地base `origin/main@97793e6`，保留全部原有dirty改动，不提交、stash、reset、部署或修改Portal（另有工程师并行编辑）。已补齐0038/模型、跨状态请求重放、双入口ID互斥、唯一冲突恢复、fork不复制身份及相应回归；指定检查通过，最终完整Python套件1535 passed / 144 skipped，224.41s。完整结果与未测边界见[本轮记录](updates/2026-10-30-queue-send-idempotency.md)。下一步仅由用户审阅这组本地改动；不自动推进下方历史GenUI待办或沿用旧发布授权。

## Portal / GenUI 集成收尾（2026-10-29）

当前工作树 `.worktrees/frontend-genui`、分支 `feat/frontend-genui`、基线 `origin/main@97793e6`，改动仍未提交或push；随后用户明确要求的本机增量部署已完成，未做部署后验证，实际状态仅记AILab-local。已有聊天/输入/流式/结构化 GenUI、首次预览与发送幂等的集成失败已修复；完整浏览器 134 项、Portal 68 项/build/typecheck、PG 全套 1610 项通过。SQLite 全套一项 Redis 恢复时序失败后单项通过；最终历史裁剪补丁另跑相关 87 项通过，详见[集成收尾与完整边界](updates/2026-10-29-frontend-genui-integration.md)。

完整 HLD **尚未全部实现或验收**：GenUI chunk 约 78.48 KiB gzip，超 60 KB 目标；500 轮 DOM 783→785，但严格输入/流式/4×CPU 性能闸门尚未完成。P0/全部 P2、P5 全站页面/组件/数据迁移/CSP、P4-H 自由 HTML、P6 付费模型评测仍有待办；用户随后已要求全局和所有现有模型默认启用结构化GenUI，代码及本机配置已同步；真实模型质量尚未评测。历史队列 pending 去重缺口已由本轮0038修复；outbox 刷新丢失、前端开关准入边界仍见上述记录。下一步处理结构化路径准入/包体/性能，再按 HLD 继续页面迁移；不把已有测试全绿视作全站完成。

## 办公内容、OCR与等待可靠性（2026-10-29）

用户现已授权部署最新版、push、PR及merge，覆盖此前仅本地修复的限制。独立工作树`.worktrees/document-reading-reliability`、分支`fix/document-reading-reliability`，基于本地origin/main@202e48a（包含已合入的#106），保留主目录、原文件方案工作树与全部已有修改；未fetch。

已集中修复原生占位/OCR读取选择、缓存及在途复用、持久等待/恢复/取消/准入、四格式视觉覆盖与Chat/Work输入边界、含图Excel兼容，并补自动等待的Portal/SSE合同。最终SQLite1502/103跳过、PG1550/55跳过、真实浏览器128、Portal45/build、Office15、真实离线四格式渲染与两并发系统reader、原指定PDF真实CPU OCR、Ruff425/导入均通过。唯一结果与未测边界见[本轮记录](updates/2026-10-29-document-reading-reliability.md)，决定见[ADR 0026](../architecture/adr/0026-document-visual-coverage-and-file-waits.md)。下一步：提交PR并按CI完成合并，增量部署最终版本；遵守测试环境约定，不备份、不另跑部署后测试。验证临时资源已清理，现有个人部署未更新。前端视觉重构HLD仍暂停，本轮只修文件处理状态。

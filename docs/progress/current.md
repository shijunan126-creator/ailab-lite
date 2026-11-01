# 前缀缓存矩阵与首批广场技能：交接（2026-11-02）

- 分支 `test/prefix-cache-matrix`，base `origin/main@c308ed7`。用户授权：测试子代理自行启动隔离临时栈、真实模型多轮矩阵、导入并试用六个 ZIP、修复缓存缺口、回归与完整 pytest、提交 PR；不部署、不合并。下方管理员技能旧交接的“待 PR/合并”已由 #143 取代。
- 已完成：文件技能按 file_id/模式跨轮复用正文，仍重新授权读取并安装沙箱文件；四项录制请求回归。29 组多轮与六个真实技能任务，逐调用计数、首处差异、边界和限制统一见[矩阵](updates/2026-11-02-prefix-cache-matrix.md)。广场稳定续轮 Chat/Work 98.14%/98.49%；不宣称全场景 ≥95%，短 Chat 供应商限制、新版与压缩冷段均保留实测。
- 六包扫描发布并以普通用户添加全部通过；五个任务通过，gongwen 因缺 zip/unzip、标准字体而受限失败（Noto 替代 DOCX 已交付，但不合规且步数耗尽）。未向部署环境导入。
- 验证：Ruff 与前缀专项 42 通过；完整 Python 1963 passed / 164 skipped / 2 failed，修复网关测试 500ms 期限后该组21通过，未重复整套；未改 Portal，不跑 build。具体基线、未测边界和开销见矩阵。
- 临时栈已停止，六端口空闲、沙箱0残留。下一步：提交 PR，等待审查/CI；未授权合并或部署。不要把短 Chat 相同正文复放仍不足95%的供应商计数当成消息改写继续修补。

---

# 管理员平台技能与技能广场：历史交接（已合并为 main@c308ed7）

- 分支 `feat/admin-platform-skills`（worktree `.worktrees/platform-skills`），base `origin/main@82ff828`。用户要求：删掉自写八个技能（#140 已关闭）；广场改放高 star、许可可再分发的办公技能；管理员可增删改平台技能（不含基础技能）并同步所有用户，未修改的随平台更新、修改过的不更新，可恢复默认；以后所有 agent 设计考虑前缀缓存（已写入 AGENTS.md，#142）。
- 已完成：设计 [design](../architecture/design/2026-11-02-admin-platform-skills.md)；后端（Codex，迁移 0047）与 Portal（Claude），记录见[本轮记录](updates/2026-11-02-admin-platform-skills.md)。
- 下一步：① 本分支全套检查后提 PR、CI 全绿后 merge；② 管理后台可用后按调研首批（gongwen 公文排版、公文写作、humanizer-zh、audit-xls、process-doc、financial-analyst，保留 LICENSE 与来源）导入并用真模型试用；③ 用户要求：完成后交 Codex 用真实模型测文件/图片/技能/压缩/多轮等场景的前缀缓存命中（目标 ≥95%），有问题修复。未授权部署。

---

# Portal 滚动锚定、页面宽度、技能广场：交接（2026-11-01）

- 分支 `fix/composer-scroll-anchor`，base `origin/main@1d3d8d5`。用户本轮：前端由 Claude 做；后端 view_workspace_file 反复失败、联网搜索为空、广场目录内容交给 Codex（gpt-6.1-sol medium，各自 worktree/PR，不部署）。
- 已完成（前端，[PR #139](https://github.com/shijunan126-creator/ailab-lite/pull/139)）：发送后跟随到回复开头即停、到底后贴底；管理页 840、资料库 1120；技能页“我的技能 / 技能广场”；基础技能不进 `/` 与 + 菜单。占位符靠右已由 #137 修复。结论与检查见[本轮记录](updates/2026-11-01-portal-follow-skills.md)。
- 后端两项已由 Codex 的 #141 合并（见下节）。广场目录 [PR #140](https://github.com/shijunan126-creator/ailab-lite/pull/140) CI 全绿但暂不合并：用户质疑八个技能包价值，待其选择（只做深公文/合同并经真模型对比后上架、改用团队自有技能、或作示例全部合并）。
- 下一步：#139 CI 全绿后 merge；#140 等用户决定。未授权部署。

---

## 工作文件查看与搜索错误语义（2026-11-01）

- 分支 `t3/workspace-search-errors`，base `origin/main@21f3300`；用户授权后端调查、修复、PR、CI 绿后 squash 合并，明确不部署。其他交接保持原范围。
- 已确认：新闻会话 6 次搜索被记为成功空结果，同期 SearXNG 多引擎网络超时；现已恢复（现场同参数探针 42 条、无失败引擎）。修复 HTTP 200 中的失败信号识别，保留真正空结果和部分成功结果。
- PPT：用户追加授权真模型后，从 checkpoint 恢复首次失败规格，复现并抓到 RSS ceiling 在导出中途终止 LO、冷回退复用中止导出目录导致 exit 0 却无 PDF。修复为全新输出命名空间恢复，保留内存上限；真模型八组查看与补充 12 次调用全通过，含实际 bridge disposed 后恢复成功。错误码安全贯穿模型与事件。
- 验证、局限与后续见[记录](updates/2026-11-01-workspace-search-errors.md)。[PR #141](https://github.com/shijunan126-creator/ailab-lite/pull/141) 已提交；最终本机全套与失败项复核完成。`gh pr merge --auto` 在无 required checks 的仓库意外立即 squash 为 `1d3d8d5`，未遵守绿后合并条件；当前 CI 6 项通过、SQLite/PG/E2E 3 项运行中，无已知失败。下一步：核对最终 CI，失败则修复；之后等待用户另行授权部署，其他范围不自动开展。未修改 Portal，未部署。

---

# 记忆摘要与项目记忆模式：交接（2026-11-01，已合并并部署）

- 分支 `feat/memory-summary`，base `origin/main@4c6f092`；[PR #137](https://github.com/shijunan126-creator/ailab-lite/pull/137) head `3af6da3` CI 9 项全绿，squash 为 `main@21f3300`。用户授权：设计实施、PR、merge、部署；后端实现、测试与部署由 Codex（gpt-6.1-sol medium），Claude 审查把关。指令、实现、验证与风险见[本轮记录](updates/2026-11-01-memory-summary.md)，设计见 [design](../architecture/design/2026-11-01-memory-summary-project-scope.md)。
- 已完成：项目记忆模式 shared/isolated 两向迁移与融合、记忆摘要与对话式修改/撤销/清空、`PATCH /v1/memory/settings`、记忆页/项目记忆弹窗与其他页签 UI 修整；审查后修复摘要阶段与批次终态解耦（终态/游标先落库，摘要阶段保留活动租约）、请求作用域优先、编辑本作用域围栏及若干查询开销。
- 已部署：Codex 把 `main@21f3300` 部署到本机 AILab-local：后端共用镜像与 Portal 为 `release-21f3300`，其余复用；迁移 0045→0046；12 服务 running、10 项健康检查 healthy、Portal 200。未备份、未做部署后验收，细节只在 AILab-local 记录。
- 下一步：本范围完成。未测边界：真实模型下摘要质量、编辑理解与融合判定；升级后既有共享项目历史互相可召回（设计决定）。待用户使用反馈。

---

# GenUI 存图与表格复制：交接（2026-11-01）

- 分支 `feat/genui-image`（worktree `.worktrees/genui-image`），base `origin/main@4104d5a`。用户本轮授权：设计实施、PR、merge；测试与最终部署可交给 Codex（gpt-6.1-sol medium），质量由 Claude 把关。
- 已完成：GenUI 块悬停复制为图片/下载图片；Markdown 与 GenUI 表格复制为表格（HTML + Markdown）；代码块单独复制沿用。设计、成本、检查与未测边界见[记录](updates/2026-11-01-genui-copy.md)，陷阱见 HANDOFF 顶部。
- 已合并：PR [#135](https://github.com/shijunan126-creator/ailab-lite/pull/135) CI 9 项全绿后 squash 为 `main@8890338`。
- 已部署：Codex 把完整 `main@8890338` 部署到本机 AILab-local（main 另含未部署的 #131/#133 后端与迁移，只换 Portal 会前后端错配）：后端共用镜像与 Portal 均为 `release-8890338`，其余镜像复用；迁移 0043→0045；12 服务 running、健康检查均 healthy、Portal 200。未备份、未做部署后验收，细节只在 AILab-local 记录。
- 下一步：本范围完成。未测边界（Safari/Firefox 图片剪贴板、iOS 下载、在 Office 内实际粘贴）见记录，待用户使用反馈。

---

# 生命周期收尾与定时任务规范（2026-11-01，已合并）

- 分支 `feat/lifecycle-limits`（worktree `.worktrees/deletion-review`），base `origin/main@ada5f60`；[PR #133](https://github.com/shijunan126-creator/ailab-lite/pull/133) 最终 head `27303a1` CI 9 项全绿，squash 为 `main@b42a3bd`。用户授权：按删除审查后续方案实现停用取消任务、模板/品牌素材回收、回收站预览，并设计实现定时任务规范；提 PR、测试后合并；未要求部署。
- 已完成：见[本轮记录](updates/2026-11-01-lifecycle-limits.md)（停用即取消任务、`run_asset_refs` 引用回收、回收站只读预览与恢复、定时任务结束日期/不重叠/无人查看与连续失败自动暂停，迁移 0045）。
- 用户决定：保存的记忆事实随来源对话删除时保留；删除定时任务不取消进行中的一轮；账号删除暂不做。
- 下一步：未部署（本轮未授权）；需要时由用户授权部署 `main@b42a3bd`（后端 + Portal，含迁移 0045）。账号删除（4b）暂缓，不自动推进。

---

## 上一交接：删除路径审查（已合并）

- 分支 `fix/deletion-review`（worktree `.worktrees/deletion-review`），base `origin/main@17aa2ef`。用户授权：审查并修复全部删除逻辑，提 PR、测试后合并；本轮未要求部署。
- 已完成：后端 12 项删除缺陷（暂停 Run 取消、锁内复验、来源 GC 串行、迟到任务、tombstone 与原件孤儿回收、历史/分享/分叉删除占位与来源围栏、purged 预览 410、0044 索引），回收站文件在本人历史中保留原名，Portal 把已删除附件/产物显示为划线“已删除”且无操作，E2E 端口占用即拒绝启动。
- 结论、缺陷表、检查与待决事项：[本轮记录](updates/2026-11-01-deletion-review.md)。
- 已合并：[PR #131](https://github.com/shijunan126-creator/ailab-lite/pull/131) 最终 head `764938f` CI 9 项全绿（含 PostgreSQL、全套 E2E），squash 为 `main@e5e78af`。首轮 PG 仅 `test_document_indexes_are_bounded_at_100000_blocks` 失败：批量插入后无统计信息，计划取决于 autoanalyze 时机；改为 EXPLAIN 前 ANALYZE，本机 PG16 连跑 3 次通过。
- 下一步：未部署（本轮未授权）；需要时由用户授权部署 `main@e5e78af`（后端 + Portal，含迁移 0044）。待产品决定的五项见本轮记录，不自动推进。

## 其他工作保持原边界

前缀缓存/渲染已合并部署的历史证据见[原记录](updates/2026-10-31-prefix-cache-render.md)。召回正文摘要约束、真实 Docker 多用户故障与 CPU/容量边界、DOCX 全页性能目标、Work watchdog 是否启用 nudge、GenUI P6 与组件目录扩展等仍待用户决定，不自动开展。

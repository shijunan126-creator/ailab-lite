# 记忆 / GenUI / 预览修复：PR 与部署（2026-11-02）

- 分支 `fix/genui-timeline-alignment`，实施基线 `main@82ff8285`，发布前已整合 `origin/main@d7e2058`。用户已追加授权：本线程全部改动提PR，CI通过后merge，并增量部署最新main；无工具模型不在优化范围。
- 已实现：按实际保留的配对工具结果识别完整当前说明；重复读取只返回短提示，新组件只补缺失依赖，压缩/恢复后按实际内容重判。保持旧消息与工具定义不变，不增加长期状态。前序记忆、时间线与预览间距修复保留。
- 验证与升级边界见[本轮记录](updates/2026-11-02-genui-doc-reuse.md)。Portal138项/build、Ruff、真实压缩2例、真实Chat/Work四轮检查通过；后续累计缓存命中分别96.20%/97.24%，具体冷启动排除范围与局限见记录。Python全套1964通过/163跳过，最后追加的两种压缩边界另行通过。
- 前序范围和验收只链接：[记忆审查](../reviews/2026-11-02-memory-lifecycle-audit.md)、[时间线/预览](updates/2026-11-02-genui-timeline.md)。发布整合 `4f5d2ea` 的记忆/GenUI/前缀专项173项通过；下一步：等待PR最终CI，绿后merge并部署。原有.DS_Store和.github本地文件保留，不入PR；部署仅更新平台/Portal，无备份、无额外测试。

---

# 本机部署与六个广场技能发布：交接（2026-11-02）

- 文档分支 `docs/deploy-41dc893`，base `origin/main@41dc893`；本轮明确授权本机部署、管理员 API 导入、文档 PR，不自动合并或追加验收。下方旧交接中的未部署/待合并状态为历史记录：#143/#144 已包含在本次基线。
- 已完成：后端共享、Portal、Office 均切到 `release-41dc893`；迁移 0046→0047；12 服务 running、10 项健康检查 healthy，Portal HTTP 200。六个技能全部经部署后的管理员 API 新建、上传并发布；结果、命令和边界见[部署记录](updates/2026-11-02-prefix-cache-matrix.md#部署2026-11-02)。其余镜像、VM 与服务资源限额复用。
- 本轮未备份，未跑部署后测试套件、浏览器或真模型。gongwen 尚未在新沙箱镜像复测；字体别名使用 Noto 替代，不代表标准公文字体或合规验收。此前矩阵为隔离栈历史证据。
- 剩余与下一步：审阅本次文档 PR，之后由用户使用反馈；仅另获授权才复测 gongwen 或继续实施。部署命令详情保留本机 AILab-local 的 `state/deploy-release-41dc893/report.md`，私有配置与凭据未入库。

---

# 前缀缓存矩阵与首批广场技能：历史实施交接（2026-11-02）

- 分支 `test/prefix-cache-matrix`，base `origin/main@c308ed7`。用户授权：测试子代理自行启动隔离临时栈、真实模型多轮矩阵、导入并试用六个 ZIP、修复缓存缺口、回归与完整 pytest、提交 PR；不部署、不合并。下方管理员技能旧交接的“待 PR/合并”已由 #143 取代。
- 已完成：文件技能按 file_id/模式跨轮复用正文，只登记到 loaded_skills；租沙箱时按 run 绑定读取并安装随包文件（读不到只警告），Chat 不再读文件；四项录制请求回归。审查时去掉了 run 开始的额外读取（Chat 每轮多一次读、读失败会整轮失败）。29 组多轮与六个真实技能任务，逐调用计数、首处差异、边界和限制统一见[矩阵](updates/2026-11-02-prefix-cache-matrix.md)。广场稳定续轮 Chat/Work 98.14%/98.49%；不宣称全场景 ≥95%，短 Chat 供应商限制、新版与压缩冷段均保留实测。
- 六包扫描发布并以普通用户添加全部通过；五个任务通过，gongwen 因缺 zip/unzip、标准字体而受限失败（Noto 替代 DOCX 已交付，但不合规且步数耗尽）。本 PR 给 sbx-office 镜像加 zip/unzip，并把仿宋/楷体/黑体/小标宋映射到 Noto（标准字体有商业许可不进镜像）；需重建镜像后复测 gongwen。六包在仓库外 `/private/tmp/skills-pkg/`，未向部署环境导入。
- 验证：Ruff 与前缀专项 42 通过；完整 Python 1963 passed / 164 skipped / 2 failed，修复网关测试 500ms 期限后该组21通过，未重复整套；未改 Portal，不跑 build。具体基线、未测边界和开销见矩阵。
- 临时栈已停止，六端口空闲、沙箱0残留。已提交 [PR #144](https://github.com/shijunan126-creator/ailab-lite/pull/144)。下一步：CI 全绿后 merge；部署需用户授权，部署后在管理后台「技能广场」导入六包（gongwen 待重建镜像后复测）。不要把短 Chat 相同正文复放仍不足95%的供应商计数当成消息改写继续修补。

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

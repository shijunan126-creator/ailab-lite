# 当前工作

## 2026-10-26 网页来源发布（已授权，进行中）

用户现已明确授权推送并部署；按既定发布流程推进PR、绿后合并、固定main部署与数据保留验证。独立分支 `fix/web-source-tabs`，原base `412168d`，已同步最新 `origin/main@93b94b3`（含主线既有0033迁移）。本轮不扩展实现范围；后续以PR和合并后CI为部署门禁，备份保留账号/配置/文件。下一步：推送/创建PR、准备候选与备份验证，CI绿后合并部署；个人细节仅记本机HANDOFF。

## 2026-10-26 网页来源多标签、渲染与提取修复（本地验证完成）

本轮授权仅为用户反馈的网页来源问题审核/修复/本地验证。原主目录停在旧分支且有存储交接文档与.DS_Store，已保留；独立工作树 `/private/tmp/ailab-web-source-tabs`，分支 `fix/web-source-tabs`，base `origin/main@412168d`，产品/回归提交 `eb506d9`，最后元数据容错提交 `e6cfe60`。按URL保留多网页与标题，最近4个文档保留模式/加载内容，修复hover、文件/来源关闭回退与普通链接标题；原版静态隔离跳转，正文修复header/hidden/异常闭合/字符限额与评分成本。

Portal42项与生产build/TypeScript通过，五服务浏览器4/4通过、网页Python专项25/25；SQLite+独立Redis全套在 `eb506d9` 通过1317项/95跳过，后续 `e6cfe60` 仅异常元数据/换行容错由25项专项覆盖。专用五服务/Redis已停，六测试端口无监听。下一步：本地修复交付完成，等待用户明确发布授权；不push/PR/merge/deploy，不改个人部署。JS动态站点、真实BBC URL与目标机资源容量边界见[本轮审核及验收](updates/2026-10-26-web-source-tabs.md)。

## 2026-10-26 管理文件去重、恢复思考折叠与增量部署（本地验证完成，待CI）

最新授权：删除重复管理文件入口，恢复Chat/Work点击才展开完整思考，保留此前性能优化并减少部署冗余。独立分支`fix/admin-thinking-deploy`，base`origin/main@7abee90`，产品`c0af4db`；主目录存储交接与.DS_Store未改。管理标签/面板源头去重；思考默认折叠、展开状态留在Turn、全文按需挂载；部署仅Portal使用稳定COPY/依赖层及standalone。SQLite1270通过/132跳过、Portal41/build/TypeScript、Ruff通过；目标机候选传输+冷构建34.16秒/同源码缓存1.83秒，镜像224→105MB，隔离候选9静态资源/登录/代理HTTP200。最终浏览器组15/15通过，旧手机长文链接测试改为等可见且实测高度稳定；详见[本轮记录](updates/2026-10-26-admin-thinking-deploy.md)。下一步：提PR，CI全绿再合并并按前端独立流程发布；现有部署未切换。不重启后端、不触及业务库、不做付费调用。

## 2026-10-26 完整思考 PR、合并与数据保留部署（已授权，进行中）

用户明确授权提PR、检查通过后合并并部署。本轮发布工作树 `/private/tmp/ailab-full-thinking-release`，分支 `fix/full-thinking-release`；起点 `ab0e8d9`，已合并最新 `origin/main@f878ce1`（合并 `7859825`），保留网页/HTML侧栏并将网页打开回调传入长Markdown分块；新增桌面/手机真实点击回归 `41c71a2`。原主工作区未提交存储文档与`.DS_Store`未动。

当前：[PR78](https://github.com/shijunan126-creator/ailab-lite/pull/78) head `8747dda`，Portal41项/build/TypeScript、Ruff361、SQLite1270通过/132跳过；106浏览器104通过，账号记忆两个场景遇SQLite登录审计写锁，独立复验记忆2＋网页2共4/4通过，相关后端代码对main未变，保留并发写锁风险。候选镜像已构建且179平台/102Portal源码逐项匹配，活动配置与原137d9a3部署未切换；预检原ID/11文件哈希、0030/quick_check/FK正常，active0。首轮远端e2e为104通过/1跳过/1失败：表格已显示实时侧栏，测试强等viewer超时；CI总门禁因此失败，其余全部成功。已修正表格用例接受实际唯一面板并保留内容/重开/修改断言；表格关闭重开/再修改＋桌面/手机实时侧栏3项定向全部通过（85.37秒），产品代码不变。下一步：自动等待PR全绿后按固定head合并；核对候选与合并运行源码等值，完整卷备份及实际解包/独立副本验证后保留数据部署，并做真实已登录页面、资源与完整性验收。不运行付费模型，不清理旧镜像/独立备份。详细前轮证据见[实现记录](updates/2026-10-26-full-thinking-rendering.md)。

## 2026-10-26 取消详情入口、完整思考与长会话渲染（本地验证完成）

最新用户决定取代下方“详情tabs”交付：移除两个模式回答下方的详情入口，展开过程直接展示完整思考，补齐大内容与长会话负载机制。分支 `fix/full-thinking-rendering`，base `3eae4ed`，产品提交 `4899eb2`、最终测试提交 `3014834`；原存储恢复文档与 `.DS_Store` 保留。历史独立Run URL仅保留兼容，普通会话不再提供入口。

已实现：过程按200事件连续分页、展开后无二次摘要折叠；聊天正文分块和屏幕外消息/步骤卸载，折叠过程不挂载正文；流式尾部更新保留旧对象身份；会话缓存只留最新20轮并受总量限制；打印不启用延迟挂载。SQLite1256通过/132跳过、Portal41项与build/TypeScript、Ruff358文件通过。浏览器104场景已覆盖：全量101通过/3旧断言或占位等待失败，修正后3/3补验通过；40万字四组合实际滚轮/500轮输入/长回答打印6/6最终复验通过。最后500轮仅挂载3轮正文、DOM826、输入到下一帧约14.5–23.3ms（本机测试，不冒充目标机/真机容量）。原有翻页锚定及切换取消已验。专用五服务由夹具停止、Redis核对PID关闭，六端口均无监听。下一步：本轮无待实现项；未push/PR/merge/deploy，发布须另行明确授权，不启动其他路线。保留事件30天清理、极端单块/无限历史与目标机容量边界。[完整验证与失败历史](updates/2026-10-26-full-thinking-rendering.md)。


## 2026-10-26 任务详情 tabs 与返回定位（本地验证完成）

用户授权修复：桌面右侧详情 tab、手机 tabs、返回原对话及位置、详情从任务开头展示。分支 `fix/run-details-tabs`，base `4358ead`；保留进入任务前的存储恢复文档修改和 `.DS_Store`，未据旧计划扩展授权。详情入口已改为会话内状态，复用预览侧栏与事件展示；不再跳独立页面或自动滚底。桌面关闭侧栏后恢复打开前滚动位置；手机返回只收起侧栏。历史 `/runs/:id` 链接仍可打开，返回指向所属会话。

代码提交 `fa1435e`：Portal 38项/build与TypeScript、Ruff、完整SQLite Python 1256通过/132跳过，以及五服务浏览器组41通过/0跳过。桌面及390×844覆盖详情初始顶部/实际滚动、文件tabs切换、关闭非当前tab、返回原位置、手机浏览器Back与Escape；历史失败与修正均保留在记录。临时五服务已由夹具停止，专用Redis经PID核对后关闭，六个本轮端口均无监听。下一步：本轮无待实现修复，代码仅本地；未push/PR/merge/deploy，后续发布须另行授权，不自动启动其他路线项。[本轮记录](updates/2026-10-26-run-details-tabs.md)。


## main 合并后 CI 索引回归（2026-10-25，已合并且main完整CI通过）

[PR #73](https://github.com/shijunan126-creator/ailab-lite/pull/73) head `80cb51c` 的8项检查全部通过，squash合入 `main@137d9a3274d89a32057300aa8c1f48a63699d0e7`，合并树与测试head相同；[合并后main完整CI](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40195893591) 8项全部成功，包含SQLite、PostgreSQL、存储和浏览器E2E。

修复共享稀疏表/不存在用户导致的索引计划误报：独立迁移库固定10000文件/5000模板并ANALYZE，默认优化器验证四条非空查询及专用索引存在。本地完整双库、最终专项、Ruff和Portal build通过，删除索引的双库反向验证均失败。仅改测试/文档，无产品查询/迁移/运行时变化，无需部署；保留既有摘要显示风险。临时PG/Redis已停止，原`.DS_Store`保留。

最终验收记录在本地分支 `docs/template-query-plan-accepted`，base `origin/main@137d9a3`，仅记录已完成结果，不再推送纯文档触发重复全套检查。下一步：本轮无待实现、待合并修复或待部署项，不自动开启其他任务。[验证记录](updates/2026-10-25-template-query-plan-ci.md)。

## 分支模式、标题与手机顶部（2026-10-25，已部署）

[PR #70](https://github.com/shijunan126-creator/ailab-lite/pull/70) 最终8项CI成功后 squash 合入 `main@b961bc2876a8fb544429f656d127ac1be1c6fe21`，已按个人运维流程完整备份、实际解包检查后部署该固定提交。五应用 healthy；迁移0030不变，原六表ID、11份文件哈希及账号/密钥/个人配置保留。按授权不跑本地完整套件、不做付费模型验收；可选390×844浏览器检查未做。

首轮CI `test_library_summary_file_menu_and_switch` 失败：摘要API已done，但文件页15秒内未出现`.lib-summary`；重跑通过。疑似文件页在摘要完成前停止刷新的既有竞态，未修，保留残余风险。当前文档分支 `docs/fork-mobile-deployed`，base `origin/main@b961bc2`；下一步：本小文档PR CI绿后合并，无待部署或自动开启的实现项。[本轮记录](updates/2026-10-25-fork-mode-title-mobile-top.md)。

## 来源抽屉与新对话返回修复（2026-10-25）

[PR #68](https://github.com/shijunan126-creator/ailab-lite/pull/68) 已在远端选中检查全部成功后 squash 合入 `main@c7c93e6`，并按个人运维流程备份、解包校验后部署该固定提交。五应用 healthy；原六表 ID、10份文件哈希保留，SQLite 0030/quick_check正常/FK0。Portal本地38项测试/build通过；按授权不跑本地Ruff/Python/E2E，CI Python/storage-contracts由范围选择器跳过。不做付费模型验收，可选手机浏览器抽屉/真机手势未验。当前文档分支 `docs/sources-swipe-deployed`，base `origin/main@c7c93e6`；本轮无待部署/待实现项，不启动其他路线。详见[本轮更新](updates/2026-10-25-sources-sheet-swipe.md)。旧PDF“部署前”小节已过时。


## PDF 分享入口部署前修复（2026-10-25）

用户授权重新部署。PR #64 已合并至 `main@99fd678`，但 E2E 仍点击已移除菜单项而失败；本轮 `fix/share-pdf-e2e` 从该 main 修复测试路径，并保留打印全部选定分支内容的断言。部署尚未切换，原应用运行；已构建并核对固定源码，不将失败版本上线。下一步：修复 PR 全绿后合并，再部署固定合并版本、备份/校验数据并验证分享/PDF。详见[本轮记录](updates/2026-10-25-share-pdf-deploy.md)。

## 本机部署来源与长任务（2026-10-25，有限真实验收完成）

用户已授权把固定 `main@72901e34dadee26f9fa8e5958b713a8e1a0bf4f6` 部署到现有本机环境，保留业务数据、账号、模型密钥与个人配置；本轮只提交本地文档，不 push。分支 `docs/deploy-long-runs`，base 为该 main。已按既有个人运维流程恢复存储/VM、完整备份业务卷并实际解包检查，迁移 `0028 → 0029 → 0030` 成功；切换前后的原有业务 ID 全部相同，文件哈希、SQLite quick_check 与外键检查通过。个人脚本、配置、原始证据与财务记录仅留本机独立 HANDOFF，不入 Git。

- 旧运行中断的当次内核证据先为 USB link change interrupt，随后 APFS 未完成事务并卸载；已有自动挂载器因旧 VM 存活而拒绝重挂。复用原恢复流程，未强制卸载/清库；硬件链路根因未消除，不能用恢复后健康声称稳定性已证明。
- 新版五应用健康，平台 177、Portal 82 份源码与固定提交匹配；VM 2 vCPU/8 GiB。Ruff、完整 SQLite Python 1251通过/131跳过、Portal 38项及部署构建通过。真实模型首轮曾因提供商402余额不足失败；用户随后充值并明确恢复验收，不把首轮失败隐去。
- 真实Chat10.14秒/10来源，来源按钮/桌面tab/文内pill/刷新恢复和390×844抽屉通过；模型DOCX52.64秒交付及浏览器Office预览通过，192.45秒长命令/3次exec_wait、取消、ask_user继续和受控日额度暂停/UI继续通过。强制重复检测路径未触发，明确保留未验。
- 真实工作1590.89秒（26分30.89秒），8阶段各180秒numpy计算、逐阶段分析，交付DOCX/CSV并完成最终预览；53次采样观察26次claim续期、投递始终1、workspace最多/最终1份、终态checkpoint122222字节，无stall误判/OOM/重启。九服务RSS合计中位1930.21MiB，VM可用内存最低5628.86MiB。83条调用、余额减少¥0.16，开始/每10分钟/结束均有本机记录，低于¥8授权。
- 最终0030/quick_check正常/FK0、原ID保留、10份文件SHA正确；仅禁用本轮两个合成账号，未删除数据，活动/等待/中断Run0，部署留运行。详细资源/测试、失败历史、探针修正及边界见[部署记录](updates/2026-10-25-deploy-long-runs.md)。
- 下一步：本地文档提交交父任务审阅，无待实现/待部署或需继续付费的本轮工作；保留完整1–4小时/真实额度重置、强制重复检测、真实手机、长期并发及硬件链路稳定性边界，不启动其他路线项。下方“未部署”为各历史阶段，不覆盖本节。

## 来源与长任务（2026-10-25，PR #59 已合入 main，未部署）

- [PR #59](https://github.com/shijunan126-creator/ailab-lite/pull/59) 最终 head `ae0d134` 的 8 项远端检查全部通过，squash 合入 main `dfa27d2`，合并树与测试 head 一致。合并前同步了 main 的 PR #56/#57/#58：`agent/loop.py` 删除旧死代码方法、`portal/lib/api.ts` 在 #58 的结构化错误解析上保留额度重置提示；CI 首轮 ask_user E2E 失败为测试自身的异步等待缺陷（见 HANDOFF），已改为 API 轮询。
- 下一步：部署需用户另行授权；部署后验收真实模型 1–4 小时长任务（含额度真实重置）、目标机 2vCPU/8GiB 整栈容量与真机交互。不自动启动其他路线项。

### 合并前最终验收记录

统一范围：Portal来源面板、Run来源持久化/恢复、长任务v2预算窗口/短claim续租/统一exec/重复检测、日/月额度暂停和各轮审查修复。集成分支 `feat/long-runs-sources@a677a5f` 已含 `490de63`/`a96cd84`；最终测试分支 `feat/long-runs-final`，工作树 `/Users/michael/.t3/worktrees/ailab-lite/t3-015c88c8`，起始干净。授权本地修复、真实沙箱和全套测试及提交，不改Portal，不push/PR/merge/deploy，不接触个人部署/DB/非测试容器。

- 修复 resume后的checkpoint删除旧快照却遗留result引用：checkpoint事务同步结果引用再提交删除；红绿覆盖崩溃后下一轮恢复、过期判断、Work fork独立副本。真实Docker又发现并修复跨tmpfs helper安装EXDEV和SIGKILL supervisor遗留活后代；macOS身份检查补齐Z拒绝。
- 已验证Docker/runc与runsc各400连续exec，分别209.262/259.915秒，四次采样各5 PID/0 Z；静默poll、9028字符头尾、20000字符CJK命令、4成员kill-tree、kill-all、supervisor失踪失败/槽位释放、tar与release正常。轮询分别6/5次exec_wait仅2快照、旧对象删除、最终1对象；OpenSandbox1.1.1/runc+dns+nft也完成400次/1012.291秒、每100次5 PID/0 Z，静默2次wait和相同控制生命周期通过，快照2次wait/2快照/最终1对象。三条真实路径9项通过、0失败/跳过。
- 最终产品/测试`38a71f8`：SQLite1248通过/132跳过，PG16+Redis1335通过/45跳过，Portal38项/build、Ruff355文件、五服务E2E98通过/0跳过，均0失败。包含未配置外部组件及可选OTEL跳过；真实沙箱另验不冒充全套外部组合。所有本轮结果、失败历史、性能成本和清理统一写入[最终记录](updates/2026-10-25-long-runs-sources.md#final-real-sandbox-validation)。历史子分支待接收/待cherry-pick均不代表当前集成缺失。
- 本轮PG/Redis/五服务/OSB均停止，测试容器/卷/四个镜像已删除，专用Docker计数全0；`ailab-verify=Stopped`，七应用/DB端口、OSB38090及43100–43220均无监听，默认context未改，无本轮测试/helper残留。已快进合入本地集成分支 `feat/long-runs-sources@7f94cba`（领先 `origin/main` 41个提交）。下一步：等待用户授权推送/PR/合并/部署，无待实现修复；继续保留真实模型1–4小时、目标2vCPU/8GiB整栈容量、真实手机未验。不自动开启其他路线项或发布；旧审查证据见[review3](updates/2026-10-25-long-runs-review3.md)。

## 真实沙箱可运行性验证（2026-10-25，PR #54已合并，未部署）

用户要求确认项目能否实际跑通，重点补足真实OpenSandbox验证。分支 `verify/real-sandbox-acceptance`，base `origin/main@9cfadfa`（已含PR #46/#49/#51）。允许隔离测试与必要修复；不替换现有部署、不迁移个人业务库、不清理现有服务/数据。

- 已读仓库与本机运行交接，现有五应用健康，实际provider为Docker；尚无OpenSandbox服务。本次部署只读基线SQLite0028/quick_check正常/FK0，尚无文件或Run数据。
- 修复Docker到期/删除竞态和OpenSandbox DNS-only字面IP绕过；新配置默认dns+nft、旧配置拒绝且绑定实际进程。代码/测试`6d5c305`、`1ca7c6d`、`6933c72`。真实Docker/runsc 9项、OpenSandbox/runc+dns+nft 9项、PG16+Redis+OpenSandbox五服务浏览器7项通过，均0跳过。SQLite/PG全套1178/1224通过（跳过81/35），Portal35/build、Ruff339、shell检查通过。
- 专属五服务/浏览器/PG/Redis/OpenSandbox及镜像已清理，原九容器保持运行、五应用健康；原0028业务库未迁移，原有ID保留并保留期间新增的用户数据。个人配置与三份.DS_Store未动。[PR #54](https://github.com/shijunan126-creator/ailab-lite/pull/54)最终head `16245a718e86ab61701441218b46c9360b67251f` 的8项远端检查全部通过，无阻塞审查/冲突，squash合入main `a646ba355dd35f6f65d300ee7b480338125b493e`；本PR的14个文件与测试head一致，同时保留main并行合入的账号UI变更。结果见[本轮记录](updates/2026-10-25-real-sandbox-acceptance.md)。
- 当前仅文档交接分支`docs/real-sandbox-merged`，base `origin/main@a646ba3`。本轮验证/修复及产品合并已完成，无待实现项；不自动部署或启动下一路线项，发布需后续授权。模型/扫描替身、外部组合及长期容量边界仍保留。

## 单机全量代码审查与修复（2026-10-25，PR #49已合入main，未部署）

用户授权全库审查、直接修复、完整验证、提 PR 并在检查通过后合并；不包含重新部署。目标是在单机上改善体验、调度、性能与可靠性，同时保留扩展边界。实现分支 `fix/single-node-review`，base `origin/main@df30dd5`，起点 `aadfa9a`（前轮合并交接文档）。当前仅文档交接分支 `docs/single-node-review-merged`，base `origin/main@d11f86e`。个人配置/运行数据和三份 `.DS_Store` 保留。

- 产品代码 `869e06b`，测试选择器修正 `e23cf4d`。修复九类问题：恢复预算、调用部门快照、详情批量查询、SSE 无进展重连、扫描轮询、分支分页/预览竞态、搜索续查、累计插话与历史文件引用；迁移 `0029 ← 0028`。
- 当前验证：SQLite+Redis 全套1170通过/79跳过、覆盖率87.04%；PG16+Redis全套1216通过/33跳过；Portal35通过/build、Ruff、导入/技能/依赖锁/golden、CI单测28项、ShellCheck/语法和三套部署静态渲染通过。20轮详情读取86→9次；500轮首屏本机SQLite/PG p95约20/21ms。
- 最终五服务E2E98通过/0失败/0跳过；首轮夹具与选择器失败及修正保留在报告。专用PG/Redis、五服务与浏览器均已停止，本轮端口无监听。
- 远端 `f601680`：PG、E2E、S3、quality/shell通过，SQLite仅500轮性能分项失败（exclusive插值p95=156ms、max209ms，另外24/25约25–33ms）。保留150ms门槛，将分项改为100次实测nearest-rank p95；双库性能各11项通过（SQLite覆盖率下18.31ms、PG16.40ms），SQLite覆盖率全套1170/79通过、87.04%。专用PG/Redis已再次停止。最新head `0dc288433f15916dcabc21ee6d987fd5a51b8bb8` 的八项检查全部通过，无阻塞审查或冲突，已squash合入main `d11f86ee2b40d3e9a0224358e4389f37b81fe357`；合并树与测试head一致。
- 下一步：代码审查与修复的合并授权已完成。本轮无待实现项；不自动启动其他路线项或部署，后续发布需用户新授权。[PR #49](https://github.com/shijunan126-creator/ailab-lite/pull/49)已合并并停止监控。详细发现/性能/边界见[审查报告](../reviews/2026-10-25-single-node-review.md)，本轮结果与PR将记录在[交付记录](updates/2026-10-25-single-node-review.md)。

## 已有发布状态与边界

PR #48 已合入主线 `df30dd5`，模板预览自动切图尚未重新部署。本机部署记录仍为 `b2c8394`；本轮不改变运行环境。旧交接中“工作区未部署”已被后续模板集成部署记录取代，不据历史小节判断当前状态。

历史证据：[模板修复/合并](updates/2026-10-24-template-preview-refresh.md)、[最近部署验收](updates/2026-10-24-ppt-templates-clean-acceptance.md)、[工作区集成](updates/2026-10-24-workspace-features-integration.md)、[早期发布](updates/2026-10-24-release-ui-features.md)。真实硬件链路/长期容量和未配置外部组件仍按报告保留，不自动启动下一路线项。

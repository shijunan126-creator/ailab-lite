# 当前工作

## 分支模式、标题与手机顶部（2026-10-25，待 CI）

用户授权补测试、PR、CI 绿后 squash 合并并保留数据部署；不做付费模型验收。分支 `fix/fork-mode-title-mobile-top`，base `origin/main@a99701b`；产品基线 `045da0b`。已复核工作分支继承模式、标题后缀与手机层叠上下文修复，并补针对性测试。本地 Ruff357文件、相关Python40项、Portal38项/build通过；完整Python/双库/E2E交CI。下一步：PR检查全绿后合并固定head，按个人运维流程备份/解包校验并部署合并main，确认五应用健康。当前尚未合并/部署。[本轮记录](updates/2026-10-25-fork-mode-title-mobile-top.md)。

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

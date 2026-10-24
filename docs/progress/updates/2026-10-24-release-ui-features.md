# 2026-10-24 UI 功能发布门禁

## 授权、基线与结论

本次用户明确授权依次执行：真实模型验收 → PR 到 main、CI 通过后 squash merge → 备份并部署合并提交。任一门禁失败或歧义即停止；不得先部署。此授权覆盖历史未授权发布及“先部署”记录。

实际工作树 `t3-1eabe4f5`，分支 `t3/real-model-release-gates`，测试 HEAD `ec5993283eac80df899c5552a7a470a62f1ea3c0`，开始时干净。集成分支为 `feat/ui-polish-fork-retry`；本地 merge-base `origin/main@94d3e22`，未 fetch，不声明远端最新状态。本次没有修改产品代码、Portal 或仓库测试。

**Gate 1 未通过：真实模型早标题首轮超时，相同请求复测成功。** 这是实际功能验收失败，不能用复测成功覆盖首轮。已确认后端执行了既有可选标题超时策略；尚不能确定上游偶发时延还是预算/提供商匹配问题。不通过任意延长超时或增加重试掩盖原因，按歧义停止条款交父 Agent 处理。没有确认 Portal 产品缺陷。

## 环境与证据

独立 SQLite、Redis 非默认端口、独立文件目录/队列/Broker owner，五服务真实进程；真实外部模型、ClamAV、Worker 解析路径和 Docker 沙箱配置。未向部署 API 写入测试业务数据，未使用开发 DB 或 .env。只调用 Chat，没有创建 Work 沙箱，不宣称本次验证了真实 Office 执行或 Worker PDF 解析。

Portal 使用 `/tmp/ailab-rerun-5750/portal` 的隔离副本，逐文件核对 app/components/lib 与本 HEAD 一致，重新 production build 通过。浏览器为既有 Playwright Chromium 路线；内置 preview 也打开了登录页，但功能证据来自 Playwright。

本机证据目录 `/tmp/ailab-release-1eab/evidence/`：`chat-1-persisted.json`、`chat-2-persisted.json` 为独立测试数据库导出的 Run/事件，`title-timeout.txt` 为首轮错误，`first-chat.json` 为复测逐帧观察，`first-chat.png` 与 `first-chat-trace.zip` 为复测浏览器证据，`portal-build.log`、`cleanup.json`。原始配置、个人运维状态只留仓库外本机 HANDOFF。Trace 已清除认证头、cookie 与已知凭据；临时凭据文件已删除。

首轮探针在模型完成后错误读取不存在的 `main` 标签，复测探针误用旧响应字段 `runs`（实际为 `turns`）。这是临时验收脚本错误，不是产品错误；逐帧 JSON/截图在复测读取字段前已保存，另从数据库导出两轮完整持久事件补齐判断。没有把探针非零退出当作产品失败。

## Gate 1 结果

- **早标题：失败 / 间歇性。** 首轮 Run succeeded，但 conversation.title 为空、无 conversation.titled 事件；Agent 记录 `conversation title skipped ... error=TimeoutError`。同 prompt 第二轮在正文未完成时出现纯文本标题，16.14 秒观察到正文终态。见两轮持久事件及逐帧 JSON。复现：新建 Chat，要求 web_search 查 Python 官方 csv.DictReader 文档、read_url 阅读并写约800字指南。根因路径为 `platform/ailab_platform/agent/loop.py:199` 的10秒预算、`:585` 的 gate+模型调用总截止时间及 `:595` 的失败后放弃。上游具体慢在哪一段尚未测定；没有根据猜测修改后端。
- **Chat reasoning / 框外流式：通过有限场景。** 两轮分别8/22个 agent.thinking 事件；复测浏览器2个大纲块，正文在 `.assistant-body > .md` 中于终态前流式增长。未覆盖 Work。
- **网页引用：通过有限场景。** 两轮均实际调用 web_search/read_url；复测答案3个链接、来源行8个链接。未执行外部页面跳转，不宣称每个引用均完成点击验收。
- **项目多页 PDF 引用、正确页码及查看器跳页：未执行。**
- **Chat 资料库读取：未执行。**
- **Retry：未执行。**
- **Edit、版本切换与废弃路径上下文隔离：未执行。**
- **Chat→Chat、Chat→Work、Work 轮次仅 Work 分叉：未执行。**
- **Work PPT/Excel 及修改完成后预览仅打开一次：未执行。**
- **Schedule 立即运行：未执行。**
- **上下文计量与低阈值真实压缩：未执行。**
- **Chat/Work memory 读写能力：未执行。**
- **390×844 横向滚动与 composer：未执行。**

历史 mock 全套证据仍见[集成验收](2026-10-24-integration-tests.md)，不能替代上述未验项。未重跑 Python 双库全套、Portal 单测或 mock E2E；本次只有隔离 Portal build 与真实模型浏览器探针，没有生产性能或长期容量结论。

## Gate 2/3、清理与下一步

Gate 2 未进入：未 push、建 PR、运行远端 CI 或 merge，无 PR URL、发布 head 或 merge commit。Gate 3 未进入：未部署、备份或执行部署迁移，未检查部署用户数据数量/哈希、服务健康、RSS 或线上 smoke；临时 SQLite 升至0024不是既有部署数据迁移验收。

五服务、独占 Redis、临时代理和浏览器均已结束；8个测试端口关闭，见 cleanup.json。未操作 .DS_Store。个人环境恢复经过及最终运行状态只记本机 HANDOFF，未删除部署数据。

下一步由父 Agent 确定真实提供商下标题延迟的验收要求并定位超时；若确认后端产品问题，补修复前失败回归、修根因并复跑受影响套件，再从 Gate 1 继续全部未验项。任何成功发布结论均须等 Gate 1 完整通过，之后按 CI/merge/deploy 顺序执行。


## Round 2：首次标题修复（进行中）

基线 `ca08623`（`ec59932` 加授权记录），交付分支 `t3/fix-agent-first-turn-titles`。上一轮四份未提交记录已逐份合并保存。最新授权允许后端根因修复及回归，不允许编辑 `platform/portal/`；发布顺序仍为真实验收、PR/CI/squash merge、最后备份部署。

### 测量与选择

临时 sitecustomize 包装标题、gate 和真实流回调；原始计时仅留 `/tmp/ailab-title-e1b/evidence/{timings,call-timings}.jsonl`。五次旧实现首次标题：gate 0.0122/0.0162/0.0158/0.0164/0.0170秒；TTFT 0.7810/0.5501/0.7031/0.7677/0.4816秒；reasoning 1.3512/0.8340/0.8090/0.8648/0.4989秒；content 0.0311/0.0146/0.0307/0.0092/0.0064秒；模型总时长2.1721/1.4223/1.5445/1.6478/0.9895秒。**本次未重现10秒超时，不能把上一轮超时归因于某个已测定分段**；确认了 gate 计入预算及标题存在无用reasoning两项机制问题。

真实提供商显式 `thinking.type=disabled` 三次探针支持且无reasoning。依据 [DeepSeek 官方协议](https://api-docs.deepseek.com/guides/thinking_mode/)，新增 catalog `disable_reasoning_for_quiet`（默认false，随仓库DeepSeek条目显式启用）；只在账本调用quiet标记为true时发送，普通主回答保持思考能力。同别名、同allow-list、同reserve/settle冻结权重与价格，无未计费HTTP旁路。个人目录没有改动；验收用临时catalog副本显式启用。此能力也使quiet摘要直接回答；其他不支持此参数的provider必须保持false或配置直接回答的别名。

标题仍为10秒推理预算，但从主额度gate通过后开始。gate受外层run截止时间和取消约束，直接调用另受run_timeout_seconds约束；主调用未预留便失败时取消gate任务，不等到其deadline。取消与attempt丢失不兜底写入。推理timeout/error/empty后以prompt第一条非空行规范化兜底，最终仍由API空标题CAS与attempt fence写入，不覆盖已命名会话。`ailab_conversation_titles_total{outcome,reason}`仅用固定枚举；fallback原因可区分timeout/error/empty，写入失败单独计数。

成本：无新增后台进程、依赖、数据库查询或表；每个需要标题的首次Run至多一条并发quiet调用（既有重试协议不变），最多2048输出token的原有预留；真实关闭思考后生成明显缩短，非联合容量结论。兜底只做有界文本清洗并复用既有写入。

五次修复后的真实浏览器标题显示延迟1.815/1.463/2.130/1.250/1.779秒（从发送至DOM观察），均早于正文终态；五次都有思考大纲和框外流式。计时与截图见 `browser-titles.json` / `browser-title-0..4.png`。另五次直接栈调用标题总时长见timings，reasoning均为0。390×844截图 `phone.png`，scrollWidth=390、composer底部834；未验真机键盘。

### 当前回归与新增发现

慢gate、timeout兜底在旧代码上均失败（`red.log`）；新实现针对性58项通过，覆盖真实API晚标题SSE顺序、取消清理、账本及既有标题CAS。补充reasoning上游在未关闭thinking时挂起、关闭后直接返回的回归。SQLite全套首跑1032通过/112跳过，mock五服务E2E78通过，fixture内Portal31项和production build通过。最终基线检查仍进行中。

PG+Redis首跑1110通过/1失败/33跳过：Unicode搜索依赖数据库默认locale，在本次UTF8但C locale实例中lower('ÉCOLE')='École'，漏匹配école。已保留修复前失败证据，按允许的后端修复改为有界搜索窗口内显式ICU root collation `und-x-icu`（PostgreSQL16镜像提供）；SQLite不变。相关9项通过（含双库查询计划检查），PG全套复跑1111通过/33跳过。PG部署要求包含ICU root collation，不能把仅ASCII的C locale测试失败改成skip。无迁移、无全表新索引；扫描/候选上限不变。

### 门禁状态

Gate 1进行中；尚未PR/merge/deploy，不将以上样本等同完整Gate 1。验收脚本token和浏览器账号属于不同临时用户，项目创建/上传/检查现统一走浏览器同源认证；文件引用由Portal渲染为button.md-file，不能用a标签计数断言其缺失。这些是探针问题，不是已确认Portal缺陷。


## Round 2 最终本地结果（代码提交 d865356）

实现提交 `bc5eddc`（PG Unicode搜索）和`d865356`（标题修复）；最终测试与这两份提交的产品/测试代码一致，后续仅文档记录。Ruff check/format通过；SQLite **1033通过/112跳过**（129.32s），PG+Redis **1112通过/33跳过**（168.44s），完整mock E2E **78通过/0失败/0跳过**（201.87s，含Portal31项及临时production build）。独立PG/SQLite EXPLAIN再次通过，日志分别为`pg-explain.log`、`sqlite-explain.log`。这些是本轮结果，不复用子分支旧计数；S3/Keycloak/真实OpenSandbox等未配置的契约仍跳过，真实组件边界沿用历史说明。

**Gate 1通过，有限样本，不保证模型普遍遵循率。** 场景结果逐项：

| 要求 | 当前结果与本机证据（目录 `/tmp/ailab-title-e1b/evidence/`） |
|---|---|
| Chat/Work思考大纲、正文框外流式 | Chat五次均通过；Work成功Run额外通过，`browser-titles.json`、`browser-work-stream.json`及截图。Work早标题1.270秒，context meter显示4k/11k（31%） |
| 首次纯文本早标题至少5次 | 五次全部通过，延迟见上文；均早于正文结束，无Markdown标题 |
| 网页引用链接及source row | 真实web_search/read_url；正文3链接、来源行8链接，`browser-title-0.png` |
| 项目多页PDF、Chat/Work读库、正确页码 | 真实Worker对三页PDF扫描/提取，Chat和Work均search_library/read_library_file，回答COBALT-729/17分钟，原始Markdown含正确`?page=2`；点击按钮查看器scrollTop1382，page2 top52.281/root top52；`gate-results.json`、`gate-work-library.json`、`pdf-viewer.png`。重复探针产生同名测试文件，模型有跨项目选择与一次自我更正；最终关键引用页码/内容正确，不据此承诺引用普遍准确 |
| 最后一轮retry | 2/2生成、切回1/2，`retry.png`、`gate-results.json` |
| edit版本和模型路径 | 同轮先retry再edit产生3个版本；切换3/3与1/3，真实追问分别只见GREEN-842与BLUE-317，废弃分支不泄漏；`edit-context.png`及Run事件JSON |
| Chat→Chat/Chat→Work、Work仅Work分叉 | UI创建两种fork，Work真实后续回答成功，Work轮次菜单无Chat项；`work-fork.png`、`gate-results.json` |
| Work办公产物/修订预览 | Excel交付并修订，文件哈希不同；MutationObserver记录open状态仅在终态计数1/2时各打开一次，无中途打开；`gate-remaining.json`、`artifact-initial.png`、`artifact-revision.png`。本轮选择Excel，未另做PPT |
| Schedule立即运行创建会话 | UI创建并立即运行，生成真实成功会话；`schedule.png`、`gate-remaining.json` |
| Context meter与真实压缩 | 真实调用摘要并发出agent.context_compacted，压缩后回忆AMBER-593；`gate-context.json`、`context-compaction.png`。为可控验收仅临时catalog声明16K context/4096输出，管理员阈值0.2，已恢复管理员原设置；同真实模型/计费链，未验证百万窗口/长期容量 |
| 双模式memory读写 | 两种模式分别list/add，偏好落库，新的Chat/Work会话从记忆召回青绿色；`memory-chat.png`、`memory-work.png`、`gate-remaining.json` |
| 手机390×844 | Chat和Work scrollWidth=390、composer bottom834；`phone.json`、`gate-work-library.json`、phone截图；非真机键盘验收 |

内置preview两次snapshot超时，原始错误为`Preview automation snapshot timed out after 15000ms.`，后续按HANDOFF使用Playwright。初版临时探针的身份、引用按钮选择器、JavaScript转义、发送按钮未ready、以及编号为“第0场景”导致模型合理ask_user，均已纠正/取消并完整复验；不把未发送或waiting_user写成产品失败，也不计入成功Work流式样本。原始证据只在本机，不提交raw logs或认证材料。没有确认Portal缺陷，`platform/portal/`无diff。

Gate 2下一步：按授权push至`feat/ui-polish-fork-retry`并开PR到main，等待实际远端CI；Gate 3在合并前禁止进入。本轮尚无部署提交或部署迁移/数据备份验收结果。


## Gate 2 移交：PR #42

已按授权`git push origin HEAD:feat/ui-polish-fork-retry`，创建并通过T3 `link_pull_request`登记 [PR #42](https://github.com/shijunan126-creator/ailab-lite/pull/42)，base为main。首个检查快照head `fafe1d827b906f0471986fe2cf8f48af08fb4837`：changes成功，quality/SQLite/PG/storage-contracts/shell/e2e进行中，OPEN/UNSTABLE。此后追加此移交文档提交；最终head以当前Git分支为准。尚不能声称CI通过、merge或部署完成。

本线程是T3委派的实现子任务；T3运行规则要求子任务不监控或轮询PR，将URL/head/待完成检查交父任务，因此在PR创建、单次状态快照与清理后移交。父任务下一步：对最新head监控CI，失败则根因修复并复验；全部green且head一致后squash merge；然后才按仓库外本机HANDOFF执行已授权备份、备份验证、部署合并main、0022→0024原数据ID/哈希/线性树核对、健康/RSS和真实浏览器smoke，失败从备份回滚。

Gate 3未进入：没有部署提交、备份路径、现存用户数据迁移验收、部署RSS或线上smoke结果。不得把本次临时数据库升级head0024与真实模型浏览器结果写成部署验收。

清理：五服务、Redis、临时PG、专属网络代理、浏览器均结束；14个临时端口关闭，`cleanup.json`。临时凭据配置已删除，repo Portal/.env/.DS_Store均未改。VM恢复本轮开始的停止状态，卷保留挂载，个人环境细节与检查只写本机HANDOFF。未重建/迁移/改写部署业务数据。


## Gate 3：部署合并后的 main（Round 3）

### 当前结论与固定基线

**Gate 2已合并，Gate 3已部署并通过有限外部验收。** 本节覆盖上文的历史“未合并/未部署”状态，不覆盖历史失败证据。[PR #42](https://github.com/shijunan126-creator/ailab-lite/pull/42) 的head `16a88220e8c1ee9b6363fbc6563b21e8e0234277` 全部8项检查成功，squash merge为 `b07548995ffbce3fc618f94955c5d66b54b380c7`；本轮fetch后确认 `origin/main` 为此提交，`git diff 16a8822 origin/main` 为空。Gate 3只发布此固定提交，文档分支 `docs/release-b075489-deploy` 以其为base；没有更改产品代码、Portal、开发.env或.DS_Store。

部署使用已存在的个人VM/容器路线，源码取自固定Git archive；运行镜像中的147份Python源文件和65份Portal app/components/lib文件逐一SHA256匹配快照。镜像：

- Platform：`sha256:899f1c80f77d1429f02b23dd01d644b17881975a4ee9e40203ce269adcfb6a64`
- Portal：`sha256:95495dcff8ab8c39df0c800c0a4ccf5911eafe25b616814c4b5726ea0db5c176`

个人模型catalog显式启用已实现的quiet关闭reasoning配置，与Gate 1一致，未改普通回答思考能力。历史本机Compose上限高于仓库预算，本次将API/Broker限制为384MiB/0.5CPU，Agent256MiB/0.3CPU，Worker384MiB/0.3CPU，Portal维持384MiB/0.75CPU。没有新增常驻服务或依赖。

### 备份、迁移和完整性

切换前无活动Run，先停止五个业务服务，再备份完整数据卷（60,702,720字节、1215个文件），保留旧镜像与配置。归档SHA256重复读取一致；实际解包恢复后SQLite integrity_check=ok、外键错误0、全部旧ID集合一致、24份ready文件SHA256与数据库记录一致。备份路径、manifest和原始日志只记仓库外本机HANDOFF。

API启动迁移0021→0022→0023→0024成功，实际alembic head=0024。切换前后用户4、项目1、会话61、Run63、产物18、文件24，ID集合和数量完全一致；24份文件hash、SQLite quick_check及外键通过。已有会话关联的61个Run全部成为按原顺序的parent_run_id链，active_run_id均为最后Run；另2个历史Run本来未关联会话，不纳入会话链断言。迁移后scheduled_tasks=0，无意外任务。

部署smoke后再次检查：全部原ID仍在，新增仅专用测试账号及测试数据；25份ready文件hash全部正确，quick_check=ok、外键0、head0024。删除会话使用公开软删除契约，保留审计Run；测试Excel产物保留在已禁用测试账号下，未改真实用户内容。最终活动Run=0、定时任务=0。

### 健康、资源与监控

API、Agent、Broker、Worker、Portal均healthy，且实际容器image ID与上述镜像一致。83次Docker资源采样及45次进程RSS采样覆盖浏览器smoke；以下是本次短样本，CPU百分比以单核100%计。smoke RSS上界保守包含临时采样进程，空闲RSS排除采样进程；不能推导长期峰值或2vCPU/8GiB持续容量。

| 服务 | 空闲RSS MiB | smoke RSS上界 MiB | 空闲CPU最大% | smoke CPU最大% | 限制MiB / CPU |
|---|---:|---:|---:|---:|---|
| API | 154.34 | 191.26 | 16.21 | 32.55 | 384 / 0.5 |
| Agent | 102.65 | 139.09 | 0.57 | 27.75 | 256 / 0.3 |
| Broker | 95.47 | 159.21 | 17.34 | 35.99 | 384 / 0.5 |
| Worker | 91.59 | 105.08 | 15.27 | 22.73 | 384 / 0.3 |
| Portal | 109.34 | 149.8 | 0.04 | 16.12 | 384 / 0.75 |

空闲短采样包含既有健康探针/维护轮询的CPU突发；没有活动Run不等于CPU恒为0。

`ailab_conversation_titles_total{outcome="ok",reason="none"}` 在Agent /metrics暴露，Prometheus实际抓取到序列。首次探针误用旧service token被403正确拒绝，随后改用现有ops凭据验证成功，未放宽认证。调度器enabled、间隔30秒；新建真实定时任务到期后10.771秒产生新会话并执行成功，证明后台循环运行。无任务时独立100次dispatch探针累计CPU0.053934秒（wall0.100867秒），折合每轮CPU约0.539ms、按30秒间隔估算单核0.0018%；这是空队列估算，执行模型/任务成本另由正常Run承担。

### 部署浏览器smoke逐项结果

| 项目 | 当前结果 |
|---|---|
| 新Chat早标题、网页引用 | 早标题2.260秒且早于正文终态；真实web_search/read_url，正文链接在新浏览器页打开官方文档HTTP200 |
| Work产物与预览 | 真实模型生成一份Excel、真实Worker处理，交付1份产物并显示sheet-grid预览 |
| ⌘K搜索 | 快捷键打开搜索，按专用测试标记检出当前会话 |
| 置顶/取消置顶 | 菜单操作后侧栏置顶区出现/消失 |
| 编辑与版本箭头 | 编辑后2/2生成成功，切回1/2显示原引用，切回2/2显示新回答 |
| 定时任务 | UI创建、立即执行成功，后台真实到期执行成功；随后UI删除，最终任务数0 |
| PDF导出 | 菜单打开新print页、渲染所选版本；观察到window.print被调用。为避免自动化被原生对话框阻塞，探针拦截print调用；未另验系统保存PDF文件 |

浏览器pageerror=0。内置preview两次snapshot均返回`Preview automation snapshot timed out after 15000ms.`，按HANDOFF回退Playwright Chromium。首个探针把标题命中误当成必须出现正文mark高亮，实际搜索结果已出现；其清理代码又误把list响应当items对象。纠正探针后完整smoke通过，首个账号的会话也已清理并禁用；未将探针错误归因为产品故障。无用户可见产品故障，无需回滚；已验证备份和旧镜像继续保留。

### 清理、验证范围与交接

测试会话均从公开列表删除，定时任务删除，两个专用账号禁用，临时凭据文件删除；Playwright、资源观察器已退出，无新建临时服务/代理或遗留临时端口。部署栈继续运行，数据卷按既有流程保持挂载，不对运行VM执行verifyVolume或卸载。个人配置、地址、账号、备份路径、状态JSON及原始日志只在本机HANDOFF。

本轮实际新检查为固定镜像构建、源文件比对、备份恢复、已有数据迁移/哈希/树、五服务健康、资源与监控、上述真实模型浏览器smoke、文档diff检查。未在文档分支重跑Python/Portal测试；产品树与Gate 2测试head一致，8项远端CI和Gate 1完整本地套件属于明确历史基线。未重新做整VM重启、真机键盘、PPT、百万窗口/长期容量或USB物理掉线；打印仅到print视图和调用。此次发布无待实现项；父任务下一步审核本docs PR实际CI并合入，子任务不merge/不监控。功能想法等待用户决定，不自动开始其他路线图。

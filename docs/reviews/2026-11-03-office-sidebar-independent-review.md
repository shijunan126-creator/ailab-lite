# Office 侧栏独立审查（2026-11-03）

审查时结果：2 项 P2，无已证实的 P0/P1/P3。以下保留审查当时的复现与方案；后续用户授权实施，两项已由实施者[修复并本地验证](../progress/updates/2026-11-03-office-review-fixes.md)，不是审查者重新独立验收。既有22项Office E2E通过不能排除原问题。审查任务本身仅写报告，无修复、提交、发布或部署。

## Findings 与根因方案

### P2-1：原生 viewer 握手成功后的渲染失败不会进入服务器回退

- 位置：`platform/portal/components/preview/office-view.tsx:325`（error 分支，尤其第 327 行）；`platform/portal/office/build.mjs:24`（上游适配 PATCHES）；新增原生入口 `platform/portal/components/preview/file-viewer.tsx:160`。
- 触发：文件索引 ready/native，打开文件库、分享或聊天 viewer；frame 先发 ready，随后文档字节加载、prepare 或具体 viewer 渲染失败。例如阻断一个索引已就绪文件的 content 下载，或让浏览器 renderer 的 onError 回调触发。
- 实际结果：文件留在 frame 内的错误界面，服务器分页回退组件不挂载。即使 frame 显式发送 `t:error`，父桥也因已有 viewId 只打印警告。只要索引已成功，重新打开还会重走相同原生路径。
- 代码证据：`vendor/office-suite/upstream/office-runtime/ui/main.tsx:209` 的 load catch 和 `:544` 的 viewer onError 仅 setFailure；当前 build PATCHES 没有把这些失败桥接到宿主。`platform/portal/lib/frame.ts:49` 的 shell 只转发全局 error/CSP 事件，不能接到已被捕获的 renderer 失败；父桥还忽略 ready 后的 error。索引失败回退与渲染失败回退是不同阶段。
- 本次复现：在 Node/jsdom 中用 esbuild 内存加载实际 OfficeView（仅替换 API 和 capture registry），以真实 frame window 为消息源，依次发 boot、合法 nonce 的 ready、error。结果 `iframeStillMounted=true`、`fallbackMounted=false`，退出码 0。此复现证明父桥行为；没有声称复现某一种真实复杂文件的 renderer 缺陷。
- 影响及归类：父桥问题在 HEAD 已存在；本次文件库/分享从服务器分页迁往原生优先后，把该缺口引入这些原先可直接分页的入口，属于迁移回归。聊天原有同类缺口仍在。“复杂图表 XLSX 索引失败可回退”的已声明边界不涵盖这里，因为这里索引已成功。
- 根因方案：在 build 的可复现适配中，为文档 load/prepare 和 viewer onError 增加统一的、带 nonce 与文档版本的终态失败消息；在 OfficeView 对当前 generation 的此类失败设置 broken，卸载 frame、释放 view/capture/download，再挂载现有 fallback。不要把所有控制台错误都视作终态，否则字体警告等可恢复事件会误触发昂贵导出。权限撤回、源不存在/版本变化与 renderer 兼容性失败应区分，前者继续按 API 权限/版本合同拒绝，后者才请求既有 owner/share-bound 回退。
- 权限、资源、兼容性：不放宽 API/share 授权，不改模型工具。成功原生路径仍不导出；每个来源版本只切入一次回退并终止旧 worker，避免错误循环与并行双渲染。错误协议增加字段时宿主和 bundle 配套更新，保持原有启动失败回退。
- 最小回归：补 ready 后终态失败→frame 卸载→仅一次分页请求的组件测试，另测旧 nonce/旧版本错误不得切掉新文档。真实链路至少用“索引成功后注入 renderer prepare/onError 失败”验证 library/share 与活动 Work 的服务器回退、内容可读、成功原生零分页；不能只 mock document 为 unsupported。现有 `test_work_native_unavailable_renders_workspace_only_on_fallback` 只注入准入失败，不覆盖此处。

### P2-2：abort 后两次 DELETE 不能阻止已关闭 view 被迟到 PUT 复活，也不能阻止旧选区覆盖新选区

- 位置：`platform/portal/components/preview/office-view.tsx:205`–`:217`、`:410`–`:414`；`platform/ailab_platform/api/routes_document_view.py:68`–`:70`、`:84`；存储合同 `platform/ailab_platform/api/view_state.py:49`、`:76`。
- 触发：首次或变化后的 workspace PUT 在 authorize_state 的 Broker 快照/解析期间阻塞；用户关闭/切换，或 15 秒 deadline 中止 fetch。客户端 abort 会立即使 fetch Promise reject/finally，并不等待同步 API 处理器停止。第一次 DELETE 和 finally 中的第二次 DELETE 都可能先于服务端 PUT 完成。
- 实际结果：后端继续完成授权并无条件 view_state.put，复活已关闭视图，最长再留 600 秒。若超时后已有更新的 PUT 成功，旧 PUT 也能最后落地覆盖较新的位置/选区。get_view 只重验来源权限和版本，不知道 frame 已关闭或选区顺序，因此仍能返回这个陈旧状态；viewport capture 最终找不到已注销 frame。没有证据表明此问题绕过跨用户授权。
- 本次最小复现：直接调用实际 put_view/delete_view 和内存 view_state；仅 stub _owned，令 authorize_state 用两个 threading.Event 暂停/放行。PUT 进入授权后，先执行两次 DELETE，此时 views 为 []；放行 PUT 后，views 再出现 `review-late`。退出码 0；复现后清除测试内存键，不连接业务数据库。复现顺序无需 sleep。真实浏览器 fetch abort 的结算时机结合该服务器结果，说明“finally 再 DELETE”不构成顺序围栏。
- 影响及归类：API 缺少 PUT/DELETE 顺序合同是原有问题。本次新增 15 秒 abort、续期和迟到清理试图处理此边界，但仍未解决，并使超时后的并行服务端写入成为明确可达路径。新增 `closing while a report is on its way releases again after it lands` 测试让 Promise 在 abort 后仍等到服务器完成才 resolve；真实 fetch 不保证这种等待，故该测试不能证明关闭后清理完成。
- 根因方案：把“最后状态/关闭”提升为服务端可原子执行的顺序合同。每个 frame generation 使用独立 viewId，PUT 带单调 report sequence；开始授权时登记服务端 generation/revision，授权完成后只做条件更新。DELETE 原子关闭该 generation，保留有界 tombstone，使任何已接收但未完成的 PUT 不可再提交。Redis 使用 Lua/CAS、内存后端用同一锁实现等价语义；只增加客户端序号但删除后忘记序号，仍会复活。也可设计等价的服务端 session fencing，关键是昂贵授权前后有同一围栏。
- 权限、资源、兼容性：沿用 user+conversation+viewId 键空间和现有授权，围栏不代替权限/源版本复验。tombstone 需有数量上限，并覆盖服务端请求最大存活期；不要用永久记录或延长活跃 TTL 解决。旧客户端无序号时应明确兼容策略，Portal/API 配套升级；不需要新增模型工具或改已有模型前缀。
- 最小回归：保留上述双 DELETE 先完成、旧 PUT 后完成的确定性服务端用例；再测 seq2 先提交、seq1 后完成不得回退。组件测试应使 Promise 在 abort 当刻 reject，同时独立模拟服务器继续执行，而不是 abort 后延迟 resolve。内存和 Redis 都验证闭合 generation 不可复活；真实链路用一个被阻塞的 Broker 校验请求验证关闭后 get_view 不含该 view。

## 范围与实际验证

- 分支 `t3/sidebar-office-file-features`，HEAD/base `8cbc216e7f99d5d72403e9e39496887dfb4d113b`。本地 remote 已核对，未 fetch/更改远端。审查相对 HEAD 的全部 51 个 tracked 修改和进入时 9 个 untracked 文件；其中产品/测试/配置共 53 个，另 7 个文档。新增文件包括 doc-loading、library-native-preview、office-bridge、office-pane-ask、office-view-boot、patch008 及两份进展和前序审查，均纳入。
- 产品基线按排序 `[path,sha256]` 的 compact JSON 计算为 `2cadfc7012052a0a0208239ed17c79f3439eb863e1df8e0bdff358aef60e1677`，与本轮记录一致。生成能力清单通过生成器与测试核对，不把几千行生成数据当作逐配方运行验收。
- 已读仓库/Portal AGENTS、current、HANDOFF 相关陷阱、RTK，以及相关预览合同、能力清单、两轮更新和前序审查。用户的本次“只写报告”限制优先，不修改 current/HANDOFF。
- 静态检查涵盖 API→Broker workspace 授权/快照/解析/回退、API view 消费与 capture 选择、Portal library/share/chat/Work 入口和回退挂载、生命周期/Ask 桥、生成技能与 patch/manifest、prefix 测试、E2E 断言变化和文档声明。未发现足以报告的新跨用户授权漏洞、缓存 URL 覆写或模型工具/schema 动态变更证据；不等同全面安全证明。
- 实际运行 3 个现有测试（`PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest … -q -p no:cacheprovider`）：`test_capability_contract_covers_sources_commands_viewer_and_dependencies`、`test_pinned_originals_have_complete_hash_closure`、`test_rebuild_reproduces_every_generated_byte`，均通过。前两项一起执行，第三项单独执行；能力扫描出现一条 invalid escape sequence 的 SyntaxWarning，无失败。重建只在 pytest 临时目录进行，不改生成树。
- 另运行以上两个内联轻量复现，均完成并显示所述问题；无新增测试文件。没有跑整套 pytest、Portal build、E2E、浏览器、真实模型或部署验收；不沿用实施者测试计数为本次执行结果。

## 未验证与已声明边界

- 未逐个运行所有 Office 配方、四格式复杂文件、VBA/Windows/Microsoft Office 保真；未做浏览器静止十分钟、隐藏/恢复、多端并发、Redis 故障和长期容量实测。3 个资源测试只证明其断言范围。
- 20 MiB 活动 workspace 快照上限、复杂图表 XLSX 索引失败可回退、65 秒导出截止是已声明限制，没有作为新 finding。未交付 workspace 文件在 Run 终态后的重开/历史可用性未做实链验证，迁移从持久分页转为 live 源后的行为应单列验收；本报告不将其提升为已复现缺陷。
- 代码可证明常规 discovery 不再进入 render_activity、不复制/导出正文，缓存命中绕过 render 调度；不能由此量化 CPU/RSS/延迟节省。原生仍需索引、快照、下载与浏览器解析，Work 原有 listener 预热仍在；本次没有性能测量，不声称全部后台成本已消失。
- 固定工具与按需模块正文路径未见本次改写旧系统消息的产品变动；recorded-prefix 测试有扩展，本次未重新运行该组或真实模型缓存计量。历史 96.80% 合成探针不证明真实 viewer 全链路缓存。
- 活动工作区的既有间歇失败缺原始日志，本次未运行该 E2E，未定位其根因。不能把本报告的两项缺陷认定为该历史失败的原因，也不能用本轮历史成功将它消除。

下一步：实施者先处理上述两个共同根因，并补对应阶段/顺序的最小回归；再集中审阅和验证，不通过延时、retry/skip 或提高资源掩盖。

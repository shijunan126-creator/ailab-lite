# 工作区功能集成验收（2026-10-24）

## 范围与代码基线

七项功能：项目说明、公司品牌套件、通知/收件箱、排队/插话、内部只读分享、资料库摘要/文件提问、移入项目带产物。用户授权实现、测试、PR；本子任务不合并、不部署。主 Agent 负责 Portal 与最终审核。

- 工作树 `t3-6d3586a1`；交付分支 `test/workspace-features-integration`，远端目标 `feat/workspace-features`，base `main@74e66c0`。
- 集成起点 `f106d28`；父任务 Portal 修复 `57ddabf`（空字体）、`e452ed6`（文件真实项目）与 `9f62667`（品牌行/手机管理标签/纯文本摘要）已合入。
- 后端修复 `e34e67c`，E2E `d855d01`。双库测试使用相同后端内容；SQLite 最终全套在提交前同树运行，PG 最终全套覆盖固定 cwd 的并发测试。浏览器最终生产代码为 `e34e67c`，测试代码为 `d855d01`（手机截图收侧栏的收尾调整在`63acbbd`，9项新增用例复跑通过）。随后父任务纯展示修复`9f62667`经`3528e51`合入；完整87项证据属于视觉修复前，最新视觉修复另跑22项工作区/资料库/连续预览测试（测试调整`40bee60`）。仅本文件/连续性记录的后续提交不改变运行时代码。

## 修复与回归

1. `RunDetail.steered`：`run_dict(detail=True)` 按 `runtime_meta.steer_ids` 查确认过的队列记录，保留累计领取/确认顺序，查询与输出最多5条。未 checkpoint 的 claim 不公开。`test_steer_checkpoint_event_history` 已实测修复前缺字段失败、修复后详情/会话/fork/history通过；`test_confirmed_steers_bounded_checkpoint_order_and_index` 覆盖乱序、未确认排除、上限和双库 EXPLAIN。
2. 分享死锁：写入任何业务行/外键前按用户ID排序取得所有者与指定接收者锁，通知插入/裁剪仍同事务、同用户锁。`test_share_locks_are_acquired_before_writes_in_global_order` 覆盖 POST/PATCH；`test_reciprocal_shares_postgres_processes` 使用两个独立进程、反向受众、每进程10次、5秒statement timeout。首次从仓库根启动时 editable venv 意外加载主工作树旧代码并重现PG deadlock；现显式固定本工作树 platform cwd，并发测试通过。这是代码基线修正，不是跳过数据库竞争。
3. 重复分享：同对象重复 POST 在所有者锁内复用有效分享、更新受众；快照推进仍要求显式 refresh。`test_post_share_reuses_live_object_share` 已实测修复前产生新ID失败、修复后通过；分享分页测试改为对不同文件分页，保留断言。
4. 跨分支交互：`test_queue_project_instructions_and_dispatched_notification` 验证队列派发保留项目说明、派发出的长任务产生通知。浏览器同时验证会话连同产物移动后，原分享授权/内容仍可访问。fork确认插话历史沿用 B 的现有回归。
5. 分享继续只公开原 prompt/answer、附件名与产物；本轮不扩展 `ShareTurn.steered`，不公开插话/思考/工具历史。`/shares/for` 保留与已对齐 Portal 一致的有界数组契约；契约文档已澄清旧单对象补充。

## 当前验证

- Ruff check / format check：通过（318 Python 文件）。`git diff --check` 通过。
- 完整 SQLite：**1094 passed、117 skipped**；跳过需要显式真实后端/外部环境的契约，不把跳过写成通过。
- 完整 PostgreSQL + Redis：**1178 passed、33 skipped**；一次性 UTF8 PG/Redis，仅非默认端口、专用数据库。未使用开发数据库或开发 `.env`。
- 迁移：完整套件内 `test_integration_migrations` 验证单头、0022→0023→0024→0025→0026→0027 逐步升级/降级/再升级与既有 Run 保留；两库均跑，另有品牌/queue/share专项往返与模型漂移。
- Portal `npm test`：**32 passed**；隔离副本 `npm run build` 通过（Next 16.4.2），未写开发 `.next`。
- 最新视觉修复后专项：**22 passed、0 failed、0 skipped**（94.14秒），包含全部9项新工作区、9项资料库、2项连续预览、页码引用及手机资料库选择；Portal32项/build再次通过，品牌/摘要截图已刷新。
- 完整五服务 E2E：**87 passed、0 failed、0 skipped**（231.95秒）。
- 新工作区 E2E：**9 passed、0 failed、0 skipped**（42.66秒，最终手机证据复跑），文件 `platform/e2e/test_workspace_features.py`。

历史失败不冒充最终结果：初始新用例6失败/2通过含定位/脚本错误及真实品牌bug；第二轮3失败/5通过；首轮全套85通过/1失败（重名文件断言错误），随后新增产物项目回归8通过/1失败（真实Portal bug）。均保留日志，不放宽业务断言。

新增浏览器覆盖：项目说明刷新/Agent真实system消息；队列编辑/移除/自动下一轮/失败暂停继续；Alt+Enter与SSE插话刷新；定时立即执行通知、badge/跳转/全部已读；A→B指定同事分享、C拒绝、产物分享作用域预览/下载、撤销；会话带产物移动及分享继续可用；库行文件分享/移动与viewer提问；产物菜单提问的当前文件项目；摘要列表/管理关闭刷新；PPTX/PNG品牌分析/颜色/开关保存；390×844项目/队列/分享无横向滚动。

真实 Portal bug 均交父任务修复，无本子任务 Portal 编辑：

- 管理→品牌，字体留空保存→422；`components/admin/brand.tsx` 将空字体变成null，API拒绝显式null。父任务 `57ddabf` 改为空字符串；原持久化断言保留。
- 非项目对话产物→移到项目→提问，错误去根目录聊天；`turn.tsx` 传对话项目，`file-menu.tsx` 未保存文件移动后项目。父任务 `e452ed6` 用文件真实项目查找/移动状态/异步提问；原项目URL断言保留。

## 证据

日志根目录 `/tmp/ailab-workspace-evidence/`：`sqlite-green.log`、`pg-green.log`、`e2e-final.log`、`new-e2e-screenshots.log`、`visual-green.log`、`pg-concurrency-green.log`、`ruff.log`、`format.log`；`e2e-visual-green/portal-tests.log` 与 `portal-build.log` 保存Portal验证。临时证据不进入仓库。

截图目录 `/tmp/ailab-workspace-evidence/screenshots/`，`*-desktop.png` 为1360×860、`*-phone.png` 为390×844（full-page允许高度增加）：

- `project-brief`、`queue`、`steer`、`queue-paused`、`inbox`；
- `share-dialog`、`share-preview`、`share-page`、`project-moved-files`；
- `library-summary`、`file-question`、`library-summary-switch`、`brand-admin`；
- `artifact-menu`（desktop/phone）。

内置preview成功打开，但snapshot两次均返回 `Preview automation snapshot timed out after 15000ms.`；按仓库Playwright路线完成浏览器验收。手机截图收起桌面遗留侧栏；手机键盘/真机浏览器不在无头Chromium证明范围。

## 有限真实模型

同前次门禁的官方 DeepSeek Flash 路由，临时配置/SQLite/存储/Redis命名空间/服务凭据/回环端口；无部署业务写入。真实部分运行 API/Agent/Broker/Worker，通过API验收；Portal行为另由上述完整五服务浏览器测试验收。

- 项目说明指定初中生受众与称呼，真实Chat回答以指定称呼开头并给出通俗解释；正文账本1854输入/121输出/384缓存，1调用。
- 文本文件真实摘要完成：内容准确概括雨水收集、漏水检测、学生记录、半年减水15%及不可饮用限制；独立 file_job 账本 **581输入/44输出 token**，不是Mock token。
- 使用既有已验证Office基础镜像重建本轮技能层（本地派生tag，仅验收用），技能manifest/依赖校验通过；真实Docker Work生成2页PPT，10×7.5英寸模板尺寸、1母版、logo、两种指定颜色均在输出验证，含插话新增句子。不是从apt全量冷构建/多架构发布验收；正式使用仍须重建并发布 `sbx-office`。
- 真实模型执行首个等待工具期间提交steer；checkpoint详情、`agent.steered`、最终答复与PPT内容共同确认已采用新增要求。正文8调用62273输入/1836输出/54400缓存 token，含工具上下文与重读；仅一个简短PPT场景，不承诺任意复杂任务用量。
- 真实栈默认stub扫描器（测试profile）、无外网搜索；此次不重验真实ClamAV、gVisor/OpenSandbox/多架构或长期容量。真实原始结果仅临时目录，未复制个人配置。

## 成本实测与边界

本机短样本；通知先种200条，30次/项；通知测helper+commit，其他测TestClient完整请求（含认证/序列化），PG server CPU不在Python CPU内。不是2vCPU目标机容量或生产p95承诺。

| 操作 | SQLite p50 / p95 ms | PG p50 / p95 ms |
|---|---:|---:|
| 通知 insert + prune + commit | 0.70 / 0.95 | 1.37 / 1.82 |
| `/notifications/unread` | 2.71 / 4.02 | 7.58 / 9.53 |
| 完成事务 + 派发一条 | 8.79 / 11.49 | 16.41 / 22.41 |
| 领取一条 steer | 3.46 / 4.07 | 7.39 / 9.23 |

- 对应CPU均值（SQLite/PG ms）：通知0.75/0.93，unread3.95/7.06，dispatch9.21/13.25，claim3.46/6.01。进程peak RSS131.2/135.7MiB，含测试/API导入基线，非增量常驻成本。通知每用户保留200条；分享一次最多201个排序用户锁，事务结束释放，可能增加热门接收者等待，未做200接收者竞争压测。
- `/unread` 可见页每用户30秒一次（每分钟2次，业务2条有界索引查询）；以该短样本折算Python CPU约7.9/14.1ms每分钟，不包含网络/PG服务器与浏览器成本。
- 分享500轮路径首屏20轮：含认证10条SQL；SQLite **13.40ms**、PG **13.21ms**（各单次）。分享接收人/对象索引EXPLAIN与分页断言均保留；列表每条再查至多200接收人，50条上限，未把它称为常数单查询。
- 品牌受限子进程5次中位数：PPTX wall0.186s/CPU0.137s，logo wall0.117s/CPU0.061s；该独立测量子进程最大RSS36.3MiB（跨样本累计max，不是logo专属RSS），临时解析输出830/371B。资产每次上传分析一次，无新依赖。
- 三资产65699B、MockSandbox本机流式复制/无HTTP、10次租约准备含注入中位wall13.73ms/父CPU6.68ms；测量进程peak57.1MiB含依赖基线。真实Docker两资产租约创建+注入共724ms单次，包含容器创建等既有成本，不当作纯增量。注入磁盘最多22MiB资产+64KiB JSON，每次暂存最多一个资产；没有禁用品牌的模板分析循环。
- 摘要通常每文件1次，输入估算≤6000/output≤200、最终≤120字，最多3次独立计费失败尝试；本轮真实581/44。无文本/短文本0模型调用。API进程一个summary槽；关闭开关阻止后续生成，已有摘要不回删。

## 状态、清理与下一步

一次性PG/Redis和所有临时五服务/真实模型服务均结束，11个相关端口关闭；临时Docker沙箱、专用派生Office测试镜像与真实栈凭据/业务数据已清理。浏览器已结束，证据保留在上述临时目录；原有部署及个人配置未被功能验收写入/替换。交付已推送远端`feat/workspace-features`并创建/登记[PR #44](https://github.com/shijunan126-creator/ailab-lite/pull/44)，初始PR head `d488a30`；后续本次提交仅补链接。CI和合并由父任务接手；子任务未监控/合并。。

未验边界：远端CI尚待完成；父任务已审截图并提交视觉修复，最新截图供最终复核；未merge、未部署本轮功能；生产Office镜像未更新；品牌旧版本资产暂无自动回收（磁盘增长保留）、SVG暂拒绝；仅短时本机负载/有限真实模型/Chromium手机视口，不是长期容量、真机键盘或全量多架构/真实安全profile验收。下一步由父Agent审图与PR，检查CI后决定合并；部署需用户另行授权。

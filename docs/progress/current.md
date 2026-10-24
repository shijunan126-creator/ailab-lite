# 当前工作

## 后端并行子任务：会话树 / 置顶 / 全局搜索（2026-10-24）

- 最新专项授权：后端实现和本地提交，禁止修改Portal、push/PR/deploy；不启动其他路线图事项。父Agent继续负责Portal与平行分支集成。
- 子工作树 `t3-14e9630c`，分支 `feat/chat-tree-pin-search`，实际base `09a011b`（父集成分支随后已前进）。运行时/测试固定 `e4c70cb`，迁移链新增 `0022→0023_conversation_tree`；兄弟计划任务0024由父Agent接到0023之后。
- 已实现树形祖先路径、编辑/版本切换、原子发送/重试、置顶、全局有界搜索、quiet账本与composer上下文量；契约、EXPLAIN、成本和验证均在[专项交付记录](updates/2026-10-24-chat-tree-pin-search.md)。SQLite全套1004通过/110跳过、PG+Redis全套1081通过/33跳过，临时服务已停止；不代表Portal、浏览器、五服务集成、部署或main合入。
- 下一步：父Agent合入本分支及另外两个平行分支，Portal按搜索next_cursor续查与Unicode偏移契约集成，再做整体验收。下面保留父任务与历史记录，本子任务未扩大其发布权限。

## 本工作树交付：定时任务 / 资料库页码引用（2026-10-24）

后端子任务分支 `feat/schedules-citations`，基线 `09a011b`（`feat/ui-polish-fork-retry` 当时 HEAD）；仅本地实现与提交，不改 Portal、不推送/PR/部署。CRUD、五条用户锁上限、原子调度/新会话/Run/outbox、项目删除停用、Worker 页标记、资料库工具页码与项目索引 prompt 已实现。完整 SQLite 996/109跳过、PG+Redis1072/33跳过；最终新增故障/审计/额度增量 SQLite11/1跳过、PG12通过，Ruff通过。[交付契约与证据](updates/2026-10-24-schedules-citations.md)。

下一步由父Agent合入 sibling 0023，再将 **0024.down_revision 从0022改为0023**，保留并行模型/schema/bootstrap字段，做双库迁移回归与Portal/浏览器/E2E统一验收。当前0024未使用stub；未做前后端集成或生产验收。下方为父分支及历史工作记录，不覆盖本工作树最新边界。

## 当前：UI 打磨 + 分叉 / 重试 / 思考展示（2026-10-24）

授权（用户最新指令）：打磨 UI/交互/动效、去掉“隔离沙箱”等技术措辞、修复切换刷新/正文在思考框内/思考不显示/标题延迟/引用链接/预览弹出/手机键盘与横向滑动/批量栏浮动；新增任意轮次分叉（对话→对话/工作，工作→工作）、最后一轮重试、Chat/Work 主动读资料库、管理员上下文压缩阈值。前端与整体架构由主 Agent 负责，后端由 Codex `gpt-6.1-sol` medium 子任务实现。**未授权部署、推送、PR、合入 main**；下方旧的“先部署再 PR”授权不适用于本轮。

- 集成分支 `feat/ui-polish-fork-retry`（基线 `main@94d3e22`），已合入后端 `feat/chat-backend-fork-retry@f897f41`（迁移 0022）。前端结果、根因与验证见 [前端/集成记录](updates/2026-10-24-ui-polish-fork-retry.md)；后端契约与双库证据见 [后端记录](updates/2026-10-24-chat-fork-retry-backend.md)。
- 当前检查：Portal 30 项/tsc/build 通过；SQLite 全套 1026 通过/68 跳过；五服务 E2E 66/67，唯一失败（侧栏收起刷新闪烁）已修复，待复跑。
- 已合入 Codex 第二轮 `fix/chat-title-timing@c59351f`：正文先结束时标题任务有界收尾并经 SSE 送达（`run.completed → conversation.titled → stream.end`），标题统一清洗 Markdown；SQLite 1000 通过/108 跳过（Codex 运行），证据见[后端记录 Round 2](updates/2026-10-24-chat-fork-retry-backend.md#round-2标题完成时序修复2026-10-24)。
- 下一步：合入第二轮 → 复跑 Ruff/SQLite 全套/E2E（有条件再跑 PG）→ 更新本节与记录 → 向用户汇报并等待是否部署/提 PR 的指示。未验：真机 iOS/Android 键盘、真实模型思考格式与标题时延。

更新：2026-10-24。最新授权追加：对话为正常聊天AI，仅无沙箱；移除逐条模式标签与消极输入提示。此前授权：参考 Hermes，实现 Chat/Work 技能自动维护、技能页编辑、按用户归属且可编辑的 Memory System；前端交由 Claude Opus 5.5 Medium，最多两个 Claude 同时运行，后端与统一验收由主 Agent 负责。用户最新明确授权：先部署，再提交PR，检查通过后merge；本条覆盖此前未发布边界。

## 本轮

- 应用实现分支 `feat/user-memory-skill-edit`，基线 `main` / `origin/main@17d4cd49accda1165f7b34b7a069b5afcf761984`。开始时只有三处无关 `.DS_Store`，保持未动。应用实现已固定为 `bcca29829fdb1f7579a6c340876d735e2a1f4efe` 并按授权先发布；[PR #39](https://github.com/shijunan126-creator/ailab-lite/pull/39) 已于2026-10-24通过8项检查并squash合入main，合并提交 `2f6e8ec9772ef744fbc47940118e75af6a8a65f8`。
- 已实现用户记忆的 API、CAS/用户锁、Run 绑定与 attempt fencing、持久去重、自动/显式记忆开关和按轮 prompt 快照；Chat/Work 提供 memory，Chat 增加读取技能，个人技能更新要求读到的 file_id。
- 两个前端任务已完成：技能页的只读查看/个人 Markdown 新建编辑/ZIP完整替换，记忆页的导航/开关/分组编辑/冲突保留草稿。ZIP 替换接口已补服务端 head/pending 校验，不只靠前端锁定。
- 设计、Hermes 固定源码、成本预算和当前/历史证据分界见 [审查记录](../reviews/2026-10-24-memory-skills.md)；结果入口见 [本轮更新](updates/2026-10-24-memory-skills.md)。

## 对话能力修正（后续指令）

- 已拆开对话能力说明与沙箱工作prompt，保留搜索/读网页/技能/记忆；明确编写代码不等于实际执行，不主动播报模式或无关限制。前端一个Claude已移除逐条“对话模式”并统一正常输入提示。
- 追加验收通过：完整SQLite989通过/67跳过、相关浏览器6项+对话页1项通过、Node26项/生产build/tsc与Ruff通过。实现、发布和PR合并均已完成。证据见[本轮追加记录](updates/2026-10-24-chat-capabilities.md)。此前技能/记忆结果是历史相邻基线，不与追加检查混称。

## 验收与下一步

- 已完成本地实现与验收：SQLite全套986通过/67跳过、PG全套1020通过/33跳过；最后增量双库各68项通过；完整真实五服务E2E65项通过，手机截图补验1项通过。Portal Node26项、build/tsc、Ruff与依赖锁一致性通过。数字和被跳过组件的范围、首轮失败与修复过程均见审查记录。
- 最后补齐：关闭记忆时Agent bootstrap不传正文；Chat的save_skill.path不能隐式启动sandbox；编辑正文限制和Worker共用100KiB。部署后另已通过真实模型搜索/脚本/记忆写入与跨对话召回/技能创建与CAS更新；这是有限场景，不代表自主沉淀质量普遍保证。
- 固定应用提交已部署且通过外部验收，[PR #39](https://github.com/shijunan126-creator/ailab-lite/pull/39) 已合入main；不自动开始向量召回、背景反思或路线图其他任务。
- 有界记忆本机100次满载读取p95约4.94ms，无新增依赖/后台进程；不代表2vCPU/8GiB长期联合容量。临时测试服务已按本轮路径/端口关闭，不触碰开发环境。

## 保留的先前事项

- 先前 CI PR #38、Portal 重建的记录仍见 [CI 更新](updates/2026-10-24-ci-test-completeness.md)。此文件旧内容记录的 PR 进行中不代表当前 main；本轮实际 HEAD 已为上述基线，不沿用旧分支身份。
- 目标机个人部署配置、外盘和实际状态仅在本机独立 HANDOFF；不写进仓库。OSB/gVisor、多架构、付费模型、企业 IdP 与目标机长期容量仍按已有边界保留。

## 授权发布结果

应用提交 `bcca298` 已先发布，真实模型七轮、真实Worker扫描和部署浏览器验收通过；既有数据完整性与文件哈希通过。个人部署配置、地址、账号、数据、原始日志与详细运行状态只记仓库外本机HANDOFF，不提交到远端。[PR #39](https://github.com/shijunan126-creator/ailab-lite/pull/39) 的head `77ca1b4` 通过changes、quality、双库、storage-contracts、shell、e2e及CI总门禁后合入main。已核对main的API/Agent/Portal及技能目录与部署应用提交无差异；本轮交付完成，下一步仅按用户新指令开展，不自动开始其他任务。

# 当前工作

## 最新：Round 2 Gate 1通过，PR #42待CI/父任务接续

最新指令授权：测量并修复后端首次标题，完整双库/mock E2E与真实Gate 1通过后，才push/PR/CI/squash merge，再备份部署。Portal禁止修改。当前分支`t3/fix-agent-first-turn-titles`，基线`ca08623`（集成分支feat/ui-polish-fork-retry，base main@94d3e22）；上一轮记录已保留如下。

标题已拆开gate与推理预算、catalog声明quiet关闭thinking、加规范化兜底及低基数指标；五次真实浏览器早标题通过（1.250–2.130秒）。PG C locale暴露Unicode搜索漏匹配，已后端修复并保留red/green证据。实现提交bc5eddc/d865356。完整SQLite1033/112跳过、PG+Redis1112/33跳过、mock E2E78通过；真实Gate 1逐项通过，16K窗口压缩边界已记录。已push并创建/登记[PR #42](https://github.com/shijunan126-creator/ailab-lite/pull/42)，首快照changes成功/其余进行中。T3子任务规则不允许子任务监控PR，交父任务跟踪最新head CI，green后squash合并再备份部署；尚未merge/deploy。14临时端口已关闭、凭据文件已删除，VM恢复停止；具体结果及下一步入口见[发布记录](updates/2026-10-24-release-ui-features.md#round-2首次标题修复进行中)。

## 最新：真实模型发布门禁（2026-10-24，已停止）

本次用户明确授权顺序为真实模型验收 → PR/CI/squash merge → 备份后部署，覆盖下方旧授权；任一门禁失败或歧义停止。实际隔离分支 `t3/real-model-release-gates`，测试 HEAD `ec59932`，集成分支 `feat/ui-polish-fork-retry` / main base `94d3e22`。

Gate 1 未通过：真实模型 Chat 首轮标题超过后端10秒预算，正文成功但标题为空；相同请求复测早标题成功，不能覆盖首轮失败。Chat 思考大纲、框外流式和网页来源有限场景通过，其他真实模型验收项未执行。没有确认 Portal 缺陷或修改产品代码；未 push/PR/merge/deploy。临时服务/端口与浏览器已清理。下一步父 Agent 定位标题预算与提供商时延问题，再重启 Gate 1 全部剩余验收；证据、脚本错误分界与状态见[发布门禁记录](updates/2026-10-24-release-ui-features.md)。

## 当前：UI 打磨与对话新功能（2026-10-24，集成中）

授权（用户最新两条指令）：
1. 打磨 UI/交互/动效，去掉“隔离沙箱”等技术措辞；修复切换刷新、正文在思考框内、思考不显示、标题延迟、引用链接、预览弹出时机、手机键盘与横向滑动、批量栏浮动；新增任意轮次分叉、重试、Chat/Work 读资料库、管理员上下文压缩阈值。
2. 追加且仅限：编辑较早消息 + 版本箭头；导出 PDF（不做 Markdown 导出）；置顶 + 全局搜索；“/”选技能并填模板；项目答案引用库文件（文件名+页码）；定时任务（每用户最多 5 个）；非侵入式 token 计数与上下文占用。**不做**：语音、点赞点踩、生成文件版本对比。记忆能力经核对已在对话/工作两种模式实现（快照注入 + memory 工具），未改动。

分工：主 Agent（Claude）负责前端、整体架构、UI 与审核；后端与全部测试运行由 Codex `gpt-6.1-sol` medium 子任务负责。**未授权部署、推送、PR、合入 main**；下方旧的“先部署再 PR”授权不适用。

- 集成分支 `feat/ui-polish-fork-retry`（基线 `main@94d3e22`）。已合入后端分支：`feat/chat-backend-fork-retry@f897f41`（0022）、`fix/chat-title-timing@c59351f`、`feat/chat-tree-pin-search@35193b2`（0023）、`feat/schedules-citations@2245bb8`（0024，合入时 down_revision 改为 0023）。迁移链 0021→0022→0023→0024。
- 记录：[前端/集成](updates/2026-10-24-ui-polish-fork-retry.md)、[后端 fork/retry/标题](updates/2026-10-24-chat-fork-retry-backend.md)、[会话树/置顶/搜索](updates/2026-10-24-chat-tree-pin-search.md)、[定时任务/页码引用](updates/2026-10-24-schedules-citations.md)。
- 当前检查（主 Agent 合并后）：Portal 31 项 / tsc / build 通过；Ruff 通过；迁移与 fork/retry/定时任务相关测试冒烟通过；无头浏览器人工审核新功能（编辑→2/2、切回 1/2、“/”模板、⌘K 搜索高亮、置顶、定时任务、上下文环）无报错。各子分支自身的双库全套结果见各自记录，**不等于合并后结果**。
- 集成测试（Codex，分支 `test/integration-ui-features@debb64c` 已合入）：SQLite 1030 通过/112 跳过，PG+Redis 1109/33，Portal 31，五服务 E2E 77 通过/1 失败/1 跳过（跳过为已删除的人工持有辅助）。见[集成测试记录](updates/2026-10-24-integration-tests.md)。
- 唯一失败的决定：分支按“截至所选轮次的前缀”判断（前缀含工作轮次只能分支为工作，否则两者皆可），API/Portal 已一致；修正 E2E 断言为工作轮次只给“分支为工作”、之前的对话轮次两者都给。删除误提交的 `e2e/test_zz_hold.py`。
- 合入后复跑（Codex）：基线 `a09d70b6d585461b2b8de3b693c4441c7704b575`，交付分支 `test/e2e-rerun-fork-contract`；fork 单跑 1 通过/0 失败/0 跳过，完整五服务 E2E 78/0/0，Ruff check/format 通过；fixture 内 Portal 31 项及隔离 build 均通过。无需修改测试或产品代码，所有临时服务已关闭。证据及未跑范围见[复跑记录](updates/2026-10-24-integration-tests.md#复跑合入后-a09d70b)。
- 用户授权（2026-10-24，本轮）：先用真实模型验收；通过后由 Codex 建 PR → CI 通过 → squash 合入 main → 部署合并后的 main。按此顺序设闸，任一闸失败即停并汇报；Portal 问题由主 Agent 修。
- 进行中：Codex 任务“Real-model acceptance, PR, merge, deploy”（从 `ec59932` 起的独立工作树）。结果将写入 `updates/2026-10-24-release-ui-features.md`。未验：真机 iOS/Android 键盘、真实模型的思考格式/标题时延/页码引用质量。

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

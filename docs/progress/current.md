# 当前工作

## 当前追加：Chat 标题时序 Round 2（2026-10-24）

本工作树实际为 `t3-2e563082`，分支 `fix/chat-title-timing`，base `814c541b46e5b3a15ffe4985bc25432f8567193c`；下方其他工作树与发布记录是历史，不能作为本轮授权。本轮仅后端修复/测试/本地提交，不改Portal、不推送/PR/部署。已修复正文完成先于标题时的任务生命周期、终态SSE有界收尾与CAS竞态、共享Markdown清洗，锁定retry/fork不重复请求标题。已完成定向及修复前对照；最终代码全套SQLite/Ruff验收待追加。下一步固定运行时代码并执行最终全套，再由父Agent集成前端与统一验收。契约、历史/当前证据、性能与跳过范围见[后端Round 2记录](updates/2026-10-24-chat-fork-retry-backend.md#round-2标题完成时序修复2026-10-24)。


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

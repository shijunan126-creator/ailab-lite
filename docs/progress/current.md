# 当前工作

更新：2026-10-24。最新授权追加：对话为正常聊天AI，仅无沙箱；移除逐条模式标签与消极输入提示。此前授权：参考 Hermes，实现 Chat/Work 技能自动维护、技能页编辑、按用户归属且可编辑的 Memory System；前端交由 Claude Opus 5.5 Medium，最多两个 Claude 同时运行，后端与统一验收由主 Agent 负责。用户最新明确授权：先部署，再提交PR，检查通过后merge；本条覆盖此前未发布边界。

## 本轮

- 工作区 `/Users/michael/Developer/ailab-lite`，分支 `feat/user-memory-skill-edit`，基线 `main` / `origin/main@17d4cd49accda1165f7b34b7a069b5afcf761984`。开始时只有三处无关 `.DS_Store`，保持未动。本轮代码尚未提交。
- 已实现用户记忆的 API、CAS/用户锁、Run 绑定与 attempt fencing、持久去重、自动/显式记忆开关和按轮 prompt 快照；Chat/Work 提供 memory，Chat 增加读取技能，个人技能更新要求读到的 file_id。
- 两个前端任务已完成：技能页的只读查看/个人 Markdown 新建编辑/ZIP完整替换，记忆页的导航/开关/分组编辑/冲突保留草稿。ZIP 替换接口已补服务端 head/pending 校验，不只靠前端锁定。
- 设计、Hermes 固定源码、成本预算和当前/历史证据分界见 [审查记录](../reviews/2026-10-24-memory-skills.md)；结果入口见 [本轮更新](updates/2026-10-24-memory-skills.md)。

## 对话能力修正（后续指令）

- 已拆开对话能力说明与沙箱工作prompt，保留搜索/读网页/技能/记忆；明确编写代码不等于实际执行，不主动播报模式或无关限制。前端一个Claude已移除逐条“对话模式”并统一正常输入提示。
- 追加验收通过：完整SQLite989通过/67跳过、相关浏览器6项+对话页1项通过、Node26项/生产build/tsc与Ruff通过。实现验收完成；当前按最新授权进入部署/PR/合并。证据见[本轮追加记录](updates/2026-10-24-chat-capabilities.md)。此前技能/记忆结果是历史相邻基线，不与追加检查混称。

## 验收与下一步

- 已完成本地实现与验收：SQLite全套986通过/67跳过、PG全套1020通过/33跳过；最后增量双库各68项通过；完整真实五服务E2E65项通过，手机截图补验1项通过。Portal Node26项、build/tsc、Ruff与依赖锁一致性通过。数字和被跳过组件的范围、首轮失败与修复过程均见审查记录。
- 最后补齐：关闭记忆时Agent bootstrap不传正文；Chat的save_skill.path不能隐式启动sandbox；编辑正文限制和Worker共用100KiB。模型自主选择记忆/技能更新的质量未作付费模型验收。
- 发布已授权：固定本轮提交快照，先部署并验证，再提交PR、通过检查后合并；不自动开始向量召回、背景反思或路线图其他任务。
- 有界记忆本机100次满载读取p95约4.94ms，无新增依赖/后台进程；不代表2vCPU/8GiB长期联合容量。临时测试服务已按本轮路径/端口关闭，不触碰开发环境。

## 保留的先前事项

- 先前 CI PR #38、Portal 重建的记录仍见 [CI 更新](updates/2026-10-24-ci-test-completeness.md)。此文件旧内容记录的 PR 进行中不代表当前 main；本轮实际 HEAD 已为上述基线，不沿用旧分支身份。
- 目标机个人部署配置、外盘和实际状态仅在本机独立 HANDOFF；不写进仓库。OSB/gVisor、多架构、付费模型、企业 IdP 与目标机长期容量仍按已有边界保留。

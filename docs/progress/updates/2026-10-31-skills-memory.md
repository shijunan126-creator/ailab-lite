# 技能、办公、记忆与轻量计算（2026-10-31）

需求见 [需求](../../architecture/design/2026-10-30-skills-memory-requirements.md)，合同见 [实施设计](../../architecture/design/2026-10-30-skills-memory-implementation.md)。分支 `feat/skills-memory`，base `main@b35ef6e`，已经 PR #110 合并（`8cd9792`）。后端由 Codex（gpt-6.1-terra high）实现、测试与部署，Portal 由 Claude 实现并做总审。分轮记录：[I1](2026-10-30-skills-memory-integration-i1.md)、[I2](2026-10-31-skills-memory-integration-i2.md)、[I3](2026-10-31-skills-memory-integration-i3.md)。

## 交付

| 范围 | 结果 |
| --- | --- |
| 基础办公技能 | PDF/Word/Excel/PPT 四个 `base` 技能，不可关闭，0040 清除旧的关闭 xlsx 选择（降级不可恢复）；Chat 只读、Work 处理；工具结果出现办公文件时按需加载。 |
| 多技能 | 句中多个技能气泡（contenteditable、`/` 列表、撤销/粘贴保序），请求 `skills[{name,at}]`；Run 冻结个人技能版本，恢复时读取冻结版本、失效则提示不重试；技能不再强制 Work，仅模板强制。每个技能在过程中显示一次「读取技能」。 |
| 提取与编辑 | 「提取技能」后台生成草稿（0041），侧栏进度、完成/失败通知跳 `/skills`；技能页分平台技能/草稿/个人技能，个人技能与「另存」都以草稿目录树编辑（新建/重命名/删除/上传/图片预览，revision CAS），确认生成新版本并走扫描；同一技能的活动草稿复用。 |
| 回到底部 | 离开底部出现按钮，主动上滑不被拉回。 |
| QuickJS | `run_js` 由服务端经 fenced client call（0043）交给发起会话的浏览器，Worker 内 QuickJS 执行，时间/内存/输出有上限；无在线客户端时明确失败。CSP 加 `wasm-unsafe-eval`，见 ADR 0027。 |
| 记忆整理 | 去掉自然日限制，按会话增量游标分批（0042），手动整理不限次数、不依赖夜间开关；每批报告列出候选、原文引用、结果与原因，零结果也有解释。 |
| 提示词缓存 | `messages[0]` 只放稳定内容，每轮上下文进 `messages[1]`，记忆块去 revision，新增记忆不重写前缀。 |

## 验证

最终 `ca1ee78`：Ruff 通过；SQLite 1713 passed / 111 skipped；PG + Redis 1768 / 56 skipped；Portal 113 + build；E2E 145 / 6 skipped（[I3](2026-10-31-skills-memory-integration-i3.md)）。本机部署 `sm-i3-ca1ee78`，迁移 0043。浏览器截图（隔离 SQLite + mock，仓库外）由 Claude 逐张审阅；审出并修复：草稿编辑 blur 保存期间按钮被禁用吞掉点击（E2E 捕获）、技能步骤重复、记忆批次悬停文字不可读、`search_capabilities` 显示原名、`agent.warning` 泄露内部 URL。

## 未验边界

- 真实模型：多技能加载顺序、办公技能实际产物、草稿提取质量、记忆判定质量均未用付费模型验收。
- 供应商缓存命中率未分供应商实测，只有稳定前缀的单元回归。
- QuickJS 无浏览器截图（mock 模型没有 `run_js` 路径），实际设备开销未测；协议由 Python 测试覆盖。
- Work 截图中 mock 沙箱命令失败，不代表真实沙箱执行结果。

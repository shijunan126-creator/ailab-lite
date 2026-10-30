# Skills / memory 集成 I1（2026-10-30）

基线 `efa99a1`，本地分支 `feat/sm-integration`；后端修复提交 `e8f54cc`、`9448a19`。不 push、不部署，Portal 产品代码未修改。

- 恢复冻结个人技能使用专用 checkpoint 绑定的读取、所有权及当前禁用/审核检查和 10 MiB 有界流；不可用时 `agent.warning` 的 `payload.message` 为“技能「标题」已不可用，本轮不再使用它的附带文件”，继续获取租约。测试覆盖旧版本、失效、权限及不重发技能事件。
- 工具后办公技能检测限于文件工具的文件字段；附件和 Work 关键词路径保持原合同。
- `0042` 降级在重建唯一约束前按用户/日期保留最新完成的 applied，否则最新记录；双库回归包含重复数据。无其他表引用该表。
- 手动整理只检查记忆和自动记忆开关，夜间开关只控制自动任务。409 detail 分别为 `记忆未开启` 和 `自动记忆已关闭，无法整理`；成功响应形状不变。
- 增加首个模型调用的稳定提示回归；更新历史、技能选择不切模式、GenUI、迁移链、客户端能力及 reaper 清理的旧测试，E2E 定位器改为 contenteditable；`GET /v1/skills` 的个人项 `kind=personal`、`kind_file=md|zip`。

## 验证

- Office Runtime `npm ci --ignore-scripts && npm run build` 成功；Python 3.12 环境复用相邻环境依赖。Ruff check / format 通过。
- SQLite 首轮 1642 passed / 151 skipped / 17 failed / 6 errors；失败是整合前测试的旧合同及 reaper fixture 未清理 `client_calls`。失败组修正后复跑 23 passed / 1 skipped / 2 failed，最后两项修正并在专项组通过。首次受限沙箱的失败不计入以上结果。
- PostgreSQL + Redis 首轮 1744 passed / 56 skipped / 13 failed / 6 errors，运行过程中测试断言已变更；失败组及新安全、迁移回归最终 36 passed。SQLite/PG 完整迁移链及 0042 含重复记录降级均通过。完整套件不是对最终提交逐项重新执行的结果。
- Portal 单元测试 108 passed。E2E 0 passed / 6 skipped / 145 setup errors：Portal 构建在 `app/(app)/skills/page.tsx:60,70` 与 `components/skills/skill-editor.tsx:27` 因 `SkillKind` 和 `md|zip` 比较失败；尚无浏览器运行结果，不能判断其他待重写页面。
- 未做真实模型、部署或发布。临时 PostgreSQL/Redis 在验证后停止并删除。

# Skills / memory 集成 I2（2026-10-31）

基线 `46abc67`，分支 `feat/sm-integration`；用户授权后端、测试与截图，本轮不 push、不部署、不修改 Portal 产品代码。

- 会话来源草稿提取成功或失败时，同事务生成通知；技能页来源不通知。草稿的 SKILL.md 元数据随文件清单更新并保存在行中，列表不再逐条读取对象存储；来源删除或丢弃时清除派生元数据。0041 尚未发布，已原位修改。打开同一基础技能的活动草稿返回 200，首次创建返回 202。
- `4335f0e`、`cc3f2b3` 为后端修复；E2E 同步现行技能页面、技能步骤与动态系统提示合同。草稿专项 13 项通过。
- 第一轮全套未使用 GNU PATH，SQLite/PG 各有 8 个工作区快照及预览失败，原因是 macOS `stat` 不支持 `-c`；须使用 AGENTS 所列 GNU PATH。首轮 E2E 使用临时 Redis /13，138 passed / 7 failed / 6 skipped：六项旧断言已修正；直接编辑后点击「保存新版本」仍未发出 confirm，见 `/tmp/sm-i2-e2e-logs/failure-c9d7efa13ee8e54c-0.png`。Portal 的 textarea blur 启动异步写入并禁用按钮，点击可能被取消；产品代码由 Claude 修复。
- 截图在 `/tmp/sm-shots/`，使用隔离 SQLite 和 mock；Work 图中的沙箱命令失败不能当作真实沙箱通过。记忆批次悬停时通用 `button:hover` 前景色使日期在浅色背景下难以阅读，见 `memory-desktop-light.png`；由 Portal 所有者处理。

## 集成验证和剩余

在 `2a38244` 按 GNU PATH 顺序执行：Ruff check / format 通过；SQLite 1672 passed / 151 skipped；PG + Redis 1767 passed / 56 skipped；Portal 112 passed、build 通过；E2E 144 passed / 1 failed / 6 skipped，唯一失败为上述草稿直接保存竞态。此前 PG 事件测试无排序的失败已在 `be41cdc` 按 `seq` 修正；完整 composer 菜单的旧定位在 `2a38244` 修正。最终交接提交仅更改文档，仍须在其 HEAD 重跑全套以满足用户的精确 SHA 要求。

`/tmp/sm-shots/` 已包含桌面、手机的句中技能、Work 读取步骤、回到底部、技能页、文件夹重命名与记忆批次，以及前两项深色桌面图。截图数据为隔离 mock：Work 图中的沙箱命令失败不代表真实沙箱结果；记忆报告为构造样本。未拍 run_js：现有 mock 无对应工具触发路径。测试后只停止并删除本次 `/tmp/sm-i2-services` 的临时 PG/Redis，不操作其他服务。

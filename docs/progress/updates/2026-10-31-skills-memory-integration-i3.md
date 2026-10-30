# Skills / memory 集成 I3（2026-10-31）

分支 `feat/sm-integration`；验证和部署基线 `ca1ee78fea7b0616db0f7a76e225631fcc95d08d`。未 push，未修改 Portal 产品代码或 Claude 负责的状态文档。

- `42f0694`：工作区保存、恢复、自动交付、预览及 Agent 异常的内部错误写日志，对用户只显示简短提示；回归覆盖快照、自动交付及预览错误。`ca1ee78`：更新草稿关闭按钮定位及技能步骤断言。
- 首轮 `42f0694` E2E 的 3 项失败为过时测试：草稿 confirm 返回 200 且重新打开后内容保留，但两个关闭按钮重名；两项等待已移除的「已加载技能」。测试已按当前 UI 合同修正。
- 最终 `ca1ee78`，GNU PATH：Ruff check / format 通过；SQLite + Redis 1713 passed / 111 skipped；PostgreSQL + Redis 1768 passed / 56 skipped；Portal 113 passed / build 通过；E2E 145 passed / 6 skipped。临时 15432/16379 服务和数据已删除。
- 隔离 SQLite / mock 浏览器复查通过：PPT 设计与 PPT 各一条「读取技能」，无重复旧文案；记忆批次悬停在浅色和深色均可读；编辑 SKILL.md 后直接点击保存，confirm 成功。截图 `/tmp/sm-shots/work-skills-desktop-light.png`、`memory-desktop-light.png`、`memory-desktop-dark.png`、`draft-saved-desktop-light.png`。mock 不代表真实沙箱或真实模型验收。
- 本机部署：`bash bin/vm.sh up`、`python3 bin/sync.py`、`bash bin/vm.sh ssh bash -c 'cd ~/ailab-local && bash config/build.sh'`、`bash bin/vm.sh ssh sh -c 'cd ~/ailab-local && docker compose -f config/compose.yaml stop portal agent worker broker api'`、`bash bin/app.sh up`、`bash bin/app.sh status` 和数据库迁移版本读取均 exit 0。平台四服务共用 `ailab-local-platform:sm-i3-ca1ee78`，Portal `ailab-local-portal:sm-i3-ca1ee78`，Office `ailab-local-office:sm-i3-ca1ee78`；其余镜像复用。12 服务运行，配置健康检查的服务均 healthy；数据库 0043，入口 http://127.0.0.1:23000。无备份；部署后无额外测试、浏览器或真实模型验收。增量构建复用平台与 Office 依赖层，未测并发资源峰值。

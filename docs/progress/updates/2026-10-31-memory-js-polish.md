# Memory / JS 与管理页集成（2026-10-31）

基线 `origin/main@995a5f6`；合并 `fix/memory-js-backend@ce68e25` 和 `fix/memory-js-portal@3974dd8`，合并提交 `e4e7292`、`bec628c`；E2E 选择器提交 `10a26ea`。未更改 Portal 产品代码，未 push 或 PR。

技能与定时任务行改用 `.mrow`，删除/立即运行从行菜单操作；记忆行的来源、确认、暂停、恢复从行菜单操作，保留顶部范围筛选；全目录搜索旧类名，资料库测试本身已使用表格语义。

## 验证

测试产品代码基线 `bec628c`，E2E 初次加载的是筛选选择器修正前版本；定向复测使用 `10a26ea`。Office runner 复用依赖并构建。

- `cd platform && .venv/bin/ruff check . && .venv/bin/ruff format --check .`：通过，477 文件格式通过。
- `.venv/bin/pytest tests -q`：1666 通过、151 跳过、8 失败；遗漏 GNU PATH 导致 BSD `stat` 不支持 `-c`，影响快照及预览。使用 GNU PATH 复跑失败组：9 通过、2 跳过（包含额外同文件测试）。
- PostgreSQL 15432 + Redis 16379 全套：1761 通过、56 跳过、同样 8 项失败；GNU PATH 下复跑同组 9 通过、2 跳过。临时库与 Redis 用于测试，结束后删除。
- `cd platform/portal && npm test && npm run build`：114 通过，构建通过。
- `AILAB_E2E=1`，Redis 16379/14：141 通过、6 跳过、4 失败。初次两项记忆失败因筛选选择器修改错误，已纠正；定向复测四项中，Office 文档预览及桌面记忆通过，资料库表格边界与手机记忆菜单失败。

资料库新样式 `library.css` 特意取消最后一行单元格底边，由外框收口；现有测试要求所有行底边均为 1px。请 Portal 负责人确认设计与验收契约。390px 下筛选项目后打开待确认记忆菜单，点击“确认使用”时菜单在元素稳定前被移除；`RowMenu` 监听滚动后关闭，需进一步由 Portal 负责人确定触发原因。证据在 `/tmp/ailab-p3-e2e-retry/`。初次 Office 预览失败时主机长时间休眠，Broker 报 ownership lost；新栈复测通过。

## 部署与未测边界

用户授权本轮增量部署、无需备份，但未执行构建/同步/启动：`~/Documents/AILab-local` 指向的卷未挂载。`hdiutil info` 显示映像已附加，`diskutil info disk6s1` 显示 AILab 未挂载；两次 `diskutil mount` 失败，`diskutil verifyVolume disk6s1` 报无法读取容器超级块、退出码 8。系统挂载过程尝试修复并失败；未主动修复、重建或更改个人数据。现有 23000 转发连接重置。无部署 tag，所有指定真实模型、草稿通知、记忆和整理检查未执行；也未读取测试账号记忆。需恢复磁盘后继续。

原始测试日志在 `/tmp/p3-*.log`，浏览器日志在 `/tmp/ailab-p3-e2e-*`，未提交仓库。

## P4 部署（2026-10-31）

部署产品基线 `c3cdb273ca5001b78ebfd7913cbddb3631d2eae3`，标签 `sm-p3-c3cdb27`。磁盘 status 显示已挂载、无需恢复。用 `git archive c3cdb27` 固定源码，沿用增量平台 Dockerfile；依赖清单相对 `ca1ee78` 无变化。`python3 bin/sync.py`、`bash bin/vm.sh ssh sh -c 'cd ~/ailab-local && bash config/build.sh'`、`bash bin/vm.sh ssh sh -c 'cd ~/ailab-local && docker compose -f config/compose.yaml up -d --no-deps --no-build --wait --wait-timeout 180 api broker worker agent portal'` 均 exit 0。仅构建平台和 Portal，复用 Office 及其他镜像；无备份。12 服务运行，配置健康检查的服务 healthy，Portal HTTP 200。构建和启动日志在 `/tmp/ailab-p4-build.log`、`/tmp/ailab-p4-up.log`。

Claude 的受影响 E2E 日志 `/tmp/p3-claude-e2e.log` 显示 14 项通过；其详细结果由 Claude 确认。P3 的资料库末行契约及滚动菜单代码分别由 `c3cdb27`、`00575b6` 更新。

真实模型和桌面/390px 菜单检查尚未执行：内置浏览器因执行器拒绝符号链接可写根目录而无法启动；已有 Python Playwright 可作为备用，但旧脚本使用管理员账号，历史专用验收账号已禁用，尚未确定本轮指定的现有测试账号。两个计算、两种技能、技能提取通知、记忆自动记录/整理和四页面菜单均无结果或耗时；未读取账号记忆。待确认账号后执行，不能把服务健康视作功能通过。未发现新的产品缺陷；真模型行为、移动端实际操作及长期性能仍未验证。

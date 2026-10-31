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


## P4 真实模型与 P5 修复复验（2026-10-31）

P4 浏览器驱动，基线 `c3cdb27`、部署标签见上节；临时账号，原始输出在 `/tmp/p4qa/ui.out`。算术成功 6.1 秒，直接调用 run_js；素数成功 9.3 秒，一次 run_js，均未先搜索能力。API 单独运行不具备浏览器执行环境，等待约 7.6 秒后回退，不能作为 run_js 验收。技能提取 9 秒草稿 ready，并收到 `skill_draft.ready`。长期偏好 9.0 秒，经 search_capabilities、memory 保存为「希望回答时先给结论，再展开说明。」；一次性表格请求 4.1 秒，未保存。P4 的个人技能 1.7 秒、平台 xlsx 5.2 秒均在 load_skill 后失败；原整理脚本在页面加载前读取按钮，未触发整理。

DeepSeek thinking 模式要求回传 assistant 工具调用轮的 `reasoning_content`；agent 自写的 load_skill 轮缺少该字段，真实 API 验证缺失返回 400、空字符串可接受。`dd25c82` 在启用 echo 时为缺失字段的工具调用轮补空字符串，附单元测试。

P5 产品基线 `dd25c82`，平台镜像标签 `sm-p3-dd25c82`，Portal 沿用 P4 镜像（两基线 Portal 无差异），Office 等镜像复用。固定 Git archive；`python3 bin/sync.py`、VM `bash config/build.sh`、`docker compose -f config/compose.yaml up -d --no-deps --no-build --wait --wait-timeout 180 api broker worker agent portal` 均 exit 0；无备份。构建及启动日志 `/tmp/ailab-p5-build.log`、`/tmp/ailab-p5-up.log`。

- 个人 `qa-brief`：成功 2.3 秒，load_skill 后完成。
- 平台 xlsx：最终 succeeded，256.6 秒；load_skill、能力搜索、17 次 exec、3 次 deliver_file、6 次 view_attachment（历史接口前 200 事件计数）。过程中等待附件多次中断并恢复，最后一次 view_attachment 达到 150 秒等待截止，Worker 对该文件的预览任务完成；交付成功，但附件查看未完整通过。Agent 在 pause 后旧 attempt 查询返回 409 stale_attempt；该日志本身不能证明是超时根因。未确认最终交付文件的全部内容或附件等待超时根因。
- 手动立即整理：applied，创建到完成 2.2 秒，10 runs / 10 episodes，候选 1、新增 1，pending 0。脚本在任务领取前读取 active=false，误以为结束；随后按记录终态等待。页面日期行「今天 新增 1」。整理前只有「希望回答时先给结论，再展开说明。」；整理后多出「用户要求以后回答都先给结论」，没有保存一次性表格要求。新增与已有偏好语义重复：`_candidates` 的 token_set_ratio 对这两句约 41，低于 55 阈值且无实体重叠，已有记忆未进入 judge；这是待修的后端去重缺陷。
- 1280 与 390 宽度：技能 7 行、记忆 2 行、文件 1 行，首行菜单均打开；定时任务 0 行，无可测菜单。脚本没有分别记录菜单耗时；截图和输出保存在 `/tmp/p4qa/`。

本包按用户要求不改产品代码，只提交文档；语义重复与附件查看超时需后续处理。真实模型输出具有波动，本次不证明长期稳定性、其他模型或有定时任务行时的菜单行为。未重复完整测试或部署后额外验收。


## P6 完整验证、部署与复查（2026-10-31）

产品基线 `9115406`，在 P5 后新增 `7924ec8`（整理 judge 比较全部已有记忆，修复中文 token_set_ratio 约 41 低于 55 导致语义重复未进入比较）、`11b33ec`（reconciler 在其他预览结束后重新请求等待中的页面渲染，修复约 150 秒停滞）和 `9115406`（390px 整理日期行单行）。P5 Excel 是模型交付、查看、修改并重复交付三次，不是重放。

GNU PATH 下 Ruff check 与 format 通过（477 文件）；SQLite 全套 1677 通过、151 跳过；PostgreSQL + Redis 全套 1772 通过、56 跳过；Portal 114 通过、build 通过；E2E 145 通过、6 跳过。隔离 PostgreSQL 15432、Redis 16379 已停止并删除。pytest 安静模式的数量按进度字符统计；日志留在 `/tmp/p6-*.log`。

按授权以 Git archive 固定 `9115406`，平台与 Portal 标签 `sm-p3-9115406`，复用其他镜像、依赖；sync、两项构建、五服务 compose up --wait 均 exit 0，五服务 healthy，无备份。日志 `/tmp/ailab-p6-build.log`、`/tmp/ailab-p6-up.log`。

指定 `ui3.py` 复查：Excel run succeeded、37 秒、交付一个文件，无暂停或恢复事件，因而没有重现 150 秒停滞，**也没有覆盖等待恢复路径**。模型在交付前以本地文件名 `pg-1.png` 作为 `view_attachment` 的 file_id，image-reference 返回 404（资料不存在或不可用），随后继续生成并成功交付；不能把这次当作页面渲染等待已通过。自动回归覆盖见 `tests/test_document_reading_reliability.py`。

偏好回合及立即整理后，最新整理 applied，候选「用户要求以后回复都先说结论」为 duplicate，related 包含已有「用户要求以后回答都先给结论」，新增 0。`ui3.py` 点击后没有等待该次记录创建，打印了 P5 的旧 added 记录；通过 API 核对最新记录终态得上述结果。历史 P5 重复项仍保留。但偏好回合中模型主动调用 `memory remove`，将原有「希望回答时先给结论，再展开说明。」删除，并把 `explicit` 设为 true；本次用户输入没有要求删除，API 接受了该调用。根因路径：`agent/tools.py` 将 `explicit` 交由模型填写；`api/routes_memory.py::run_memory` 在 auto_save 开启时允许写操作，`remove` 直接移除可访问条目，没有核验用户是否要求删除，且该分支不要求 `explicit`。这是新发现的记忆删除授权问题，需 Claude 处理；未自行改产品或恢复数据。

遗留：模型查看文件后可能修改并重新交付；Work 模式仍通过 `search_capabilities` 发现 exec/deliver_file，均为提示词/设计后续，未在本包修复。真实等待恢复、最终 Excel 内容、其他模型及长期稳定性未验；定时任务无行菜单仍未验。未 push 或创建 PR；Claude 应先审阅上述新问题和未覆盖边界，再决定 PR/合并。

# 2026-10-24 合并功能集成验收

## 基线与边界

工作树 `t3-e795f741`，分支 `test/integration-ui-features`，起点为父集成代码 `2e1a74118ab875a5eae21565af952cf8a5d967df`（`feat/ui-polish-fork-retry`）。本轮测试代码固定为 `6d0188240e02512d8d51fb3263f4c7dbfb4012d0`；后续只有记录更新。SQLite/PG全套在相同 Python 测试代码上运行，运行时与 Portal 始终为上述父集成基线。仅测试/交接文档发生修改，**没有修改 platform/portal、push、PR、部署或合入 main**。

使用现有 Python3.12 venv，但 `PYTHONPATH` 明确指向本工作树；GNU coreutils/findutils 在 PATH。测试环境不读开发 `.env`。独立原生 PostgreSQL16（15841，临时 test/migrations 库）、Redis（16841；专项浏览器用16842）都在 `/tmp/ailab-integration-e795`；自动 fixture 创建 SQLite/五服务数据目录。没有使用开发数据库。最终所有临时PG/Redis及五服务进程均已停止，逐一确认13个独占测试端口关闭，证据 `cleanup.log`。验证日志、JUnit、截图与 trace 保留在本机 `/tmp/ailab-integration-e795/evidence/`，不提交原始运行数据。

## 检查矩阵

| 检查 | 本轮结果 | 证据文件（上述 evidence 目录） |
|---|---|---|
| `ruff check .` / `ruff format --check .`（platform） | 通过，298个文件 | `ruff-first.log` 为首轮296文件；新增测试后再次通过 |
| `bash scripts/lock_deps.sh --check` | 通过 | `lock-first.log` |
| 完整 SQLite `pytest tests -q` | **1030 passed / 112 skipped**，136.13s | `sqlite-final.log/xml` |
| 完整 PostgreSQL16 + Redis | **1109 passed / 33 skipped**，188.96s | `pg-final.log/xml` |
| 0021→0022→0023→0024 升级/逐步降回0021/再升级、模型漂移 | 双库通过，包含在上述全套 | `tests/test_integration_migrations.py`；全套同时覆盖既有各迁移测试 |
| Portal `npm ci` | 原先无 node_modules，安装成功 | `npm-ci.log` |
| Portal `npm test` | **31 passed / 0 skipped** | `portal-test-first.log` |
| Portal `npx tsc --noEmit -p .` | 通过 | `portal-tsc-first.log` |
| Portal `npm run build` | 通过 | `portal-build-first.log`；E2E另在副本构建 |
| 五服务完整浏览器 E2E | **77 passed / 1 failed / 1 skipped**，205.38s | `e2e-final.log/xml`，`e2e-final/` |

SQLite跳过为未配置真实PG/Redis及可选组件；PG+Redis的33项跳过为未配置Docker/OpenSandbox、S3、ClamAV、Keycloak/外网等真实组件和不适用的SQLite锁故障注入。最终E2E唯一计划跳过是显式手动持有stack的 `test_hold`，不是产品用例。API/Agent/Broker/Worker/Portal均为真实进程，只有模型/沙箱按既有fixture使用mock。没有以mock五服务通过替代真实模型、容器隔离或生产容量验收。

## 首轮失败与修正

首轮日志不覆盖：`sqlite-first.log`、`pg-first.log`、`e2e-first.log`、`features-first.log/xml`及各自截图/trace目录。

1. SQLite **1028 passed / 111 skipped / 1 failed**；PG+Redis **1105 passed / 33 skipped / 2 failed**。失败均为 `test_tree_backfill_and_roundtrip` 的漂移断言：在0023处比较最新ORM，得到 `add_table scheduled_tasks` 和3个新增索引。原因是集成后ORM已经包含0024，而旧测试只升到0023。保留0023回填/往返断言，漂移比较前升级到head；新增独立测试锁定完整线性迁移依赖和升降再升后的历史/外键/漂移。
2. 现有E2E首轮 **59 passed / 7 failed**（含手动hold正常释放），7处失败均在 `test_conversation_ui.py`：预览桌面/手机、45轮分页、切换取消旧响应、auto/manual/title_failed标题。种子写入Run却缺少树关系，真实API合法返回空路径，`.turn`超时；修正种子写 `parent_run_id` 与 `active_run_id`，不在业务读取加入时间排序fallback。最终全套这7项全部通过。
3. 新增专项E2E首轮 **9 passed / 4 failed**：首轮fork前置模式使用默认Work；PDF fixture引用未安装的fitz；schedule的精确label“模式”与select的实际accessible name不一致；composer实际类名是 `.composer-box`。分别显式选择Chat、使用已有pypdf、按combobox角色及名称前缀定位、修正真实布局选择器。第二轮 **12 passed / 1 failed**；剩余为下述真实验收差异，未改Portal、未放宽断言。此后增强早标题用例，显式等待“新对话”后释放标题生成，验证无刷新切换。
4. 仓库已有 `e2e/test_zz_hold.py` 是临时手动辅助，默认会持有stack最长5400秒。加 `AILAB_E2E_HOLD=1` 显式开关，正常全套跳过它。首轮手动用其已有stop sentinel释放；最终不运行此辅助。

最终新增用例 **12 passed / 1 failed**（含完整套件内）；唯一失败保持为下面的菜单/契约验收差异。

本轮没有确认需修改的后端运行时缺陷；修正均位于测试契约、fixture和自动化入口，不改生产逻辑。

## 新增浏览器用例（13项）

- 思考大纲 + 早标题：真实SSE流式中2个 `.pr-thought/.pr-label`，正文位于 `.assistant-body > .md`；思考正文先隐藏/inert，点击展开；侧栏从“新对话”变为清洗后的生成标题，不刷新且无 `**`。浏览器通过两个受限脚本端点显式释放标题/正文，无固定sleep。
- Retry：脚本给同prompt不同回答，`2/2→1/2`还原原文，reload保持选择。
- Edit：两轮中编辑首问，切到单轮新路径`2/2`，切回恢复原两轮。
- Fork：第一轮分叉得到新URL/单轮/crumb；含工作轮次时菜单仅Work的要求保留为失败验收断言。
- Pin + Search 两项：分别⌘K/Ctrl+K；置顶区域及reload持久性，带emoji的早期prompt准确`<mark>`高亮，Enter定位匹配轮次。
- Slash：`/` + Enter选择首技能、PPT“主题：”模板、Work模式与skill tag。
- Citation：pypdf创建真实两页PDF并经Worker扫描，脚本输出真实file_id的page=2引用，`.md-file`点击打开`.viewer`。
- Schedules：UI创建5条，首条立即运行后的状态链接打开真实会话，5条时按钮disabled，第6条API422；finally删除测试任务。
- Context meter：一轮后输入草稿出现`≈`，composer高度不变。
- Admin context：平台管理员上下文tab切换、保存、reload持久；finally恢复设置。非管理员GET/PUT403复用全套中的 `test_context_settings_platform_admin_only_and_audited`。
- Export：stub `window.print`，retry造成非活动兄弟后导出，`/print/<id>`只列活动路径全部两轮及对应回答。
- Mobile：390×844聊天无横向溢出、textarea≥16px；真实上传/选择后的资料库批量栏fixed且全部边界位于viewport内。

## 交父Agent的Portal验收差异

`platform/portal/components/chat/chat.tsx:265` 的 `forkModes` 逐轮累计 `worked`，只考虑截至该轮的祖先前缀。复现：Chat首轮→第二轮Work完成→点击第一轮 `button[aria-label='从这里分支']`。实际菜单同时显示“分支为对话”和“分支为工作”；本次验收要求含工作轮次的会话仅显示“分支为工作”。专项失败断言为 `test_fork_first_turn_and_work_only_menu`，截图 `features-second/failure-3507e5f957b0d542-0.png`，对应zip保留完整trace；最终全套截图为 `e2e-final/failure-3507e5f957b0d542-0.png`，trace为同目录同名zip。

API `routes_conversations.py` 的fork校验同样按所选目标的祖先路径判定，故这也是本次会话级要求与现有前后端前缀语义的差异；需要父Agent统一决定并实现。不能仅把测试改为检查最后一轮以掩盖差异。纯Work路径上的菜单限制、纯Chat路径分叉本身正常。

## 成本与剩余范围

仅新增测试与记录，生产CPU/RSS/磁盘/延迟无新增开销，无新增依赖/后台任务。五服务mock验证不覆盖真实付费模型思考/标题质量、引用遵循率、实际PPT/Office页码golden、PostgreSQL浏览器链路、真实iOS/Android键盘/visualViewport与目标2vCPU/8GiB长期容量。合并迁移已在两库运行；SQLite表达式索引反射的既有SQLAlchemy warning仍保留，PG漂移和已有索引计划测试通过。

本轮不宣称全部验收完成、上线或main合入。下一步由父Agent处理上述菜单/契约差异，在其修正提交上重跑失败用例及受影响的API契约，必要时再做整体验收；不自动发布。

# 用户记忆与技能编辑

基线：`main@17d4cd49accda1165f7b34b7a069b5afcf761984`，实现分支 `feat/user-memory-skill-edit`；本轮是未提交工作区验收，不含上线、PR 或合并授权。原 current.md 的 CI PR #38 记录仍是历史交接，不代表本分支状态。

## 真实目标与参考

让 Chat、Work 都能读、创建与改进个人技能，并让用户直接管理跨对话记忆。所有持久写入仍由 Platform API 做授权和事务；Agent 不写业务数据库，Portal 不访问内部接口。

参考 [NousResearch/Hermes](https://github.com/NousResearch/hermes-agent/tree/5894e750c3dba980d43b25aa29e3958149ba2c61)，只读检出在 /tmp（未运行其代码）：

- `tools/memory_tool.py`、`tools/memory_tool_store.py`：有界的 user / memory 两类条目，工具更新，持久保存；固定 prompt 快照与实时工具结果分开。
- `tools/skill_manager_tool.py`：创建、完整编辑、patch、附带文件管理与原子文件写入。
- `website/docs/user-guide/features/memory.md`：记忆不是“模型说记住了”，必须工具写成功；快照能保持 prefix cache，容量满要整理。

迁移其核心行为，不直接复制单用户本地文件存储、背景审查进程、依赖安装或弱隔离权限。当前范围不增加 session_search、向量召回、后台反思模型或 ZIP 文件树在线编辑。

## 实现与交互

- 记忆页叫“记忆”，直接跟账号同步，无额外“入库”步骤。分个人偏好/长期记忆，支持新建、编辑、删除；启用与自动记忆是两个不同开关。关掉自动记忆仍可明确要求记住/忘记；关掉记忆则 Agent 不读不写，用户仍可管理保留的条目。
- 复用 `users.settings.memory`，通过用户锁与 refresh 后整份写入；和技能开关共用锁，避免 JSON 丢更新。此有界账号文档不引入迁移、后台任务、新表或新依赖；100 条、每条 1000 字符、总正文 12000 字符是 API/工具共享契约。不可把它用成无限日志。
- 每次写入 CAS `expected_revision`，保留条目 id、来源和 UTC 修改时间；审计不记录正文。Agent 由 Run 推导 owner，独立 service scope、attempt fencing 和 RunDelivery 持久去重。显式请求标志由模型遵循用户意图，不宣称其为独立语义授权检测器。
- 新 Run 载入账号记忆快照，checkpoint/当轮 prompt 不反复改写；工具 list 返回最新状态。用户更改在下一轮生效，运行中的旧快照不会被远程抹除。自动记忆由同一前台模型决定并调用工具，不保证每一句话都会生成记忆；不支持工具的模型无法自动写入。
- Chat 增加 load_skill 和 memory，均不要求启动沙箱；工作模式保留现有内置技能的镜像证明。读取技能重新从 API 拉取有效开关/个人版本，扫描成功后可读取新版本。
- 技能页可查看内置说明、新建/编辑个人 Markdown，保存为新版本，经 Worker 扫描/现有管理员策略后可用。ZIP 完整下载、修改和上传替换，保留 scripts/assets；不允许 Markdown 覆盖 ZIP。内置技能不直接修改共享版本。
- Agent 的技能更新增加 `expected_file_id`，先读取再改，防止覆盖用户刚保存的版本；同名内置与他人技能不允许覆盖。外部文件指令不自动沉淀；用户纠正或已验证的可复用方法可更新个人技能。
- 公共 ZIP 替换复用 `/v1/files?purpose=skill&parent_id=...`，服务器在用户锁内检查 parent 仍是 head 且不在 pending；不只依赖前端禁用按钮。

## 成本与验收边界

记忆随认证用户一并读取，按 users 主键查找；无新扫描查询、向量库、后台模型调用。写入是一份有界 JSON 和现有审计/去重记录；最坏正文 UTF-8 约 48 KiB（另有最多100条元数据），Agent 每轮额外 prompt 最多12000正文字符，**增加实际输入 token/费用**，仍受既有上下文和 Run token 预算限制。read skill 增加一次有界 model-input 请求以刷新权限，个人 Markdown正文上限100KiB；不在 API 解析 ZIP。实际测量和检查结果在下方补录，预算不是整栈容量实测。

## 本轮验证（持续补录）

- 定向测试首轮通过；完整回归发现两个旧工具集合断言，新增 memory/load_skill 是预期变化，保持“Chat 不启动 sandbox”的断言并更新集合。
- 前端技能子任务：Claude Opus 5.5 / effort=medium，Node 26/26、build/tsc 和模拟 API 浏览器通过；不是实际 Worker 联调证据。记忆前端同模型独立任务；最多两个 Claude 并发。
- SQLite + 独立 Redis16624/15：完整 `tests` **986 passed / 67 skipped**，137.74秒。PostgreSQL16 15624 的独立 `ailab_memory_test` / `ailab_memory_migrations` + Redis16624/14：完整 **1020 passed / 33 skipped**，168.63秒。跳过项是未配置Docker/OSB/S3/Keycloak等真实组件；SQLite另跳过PG专用项。首轮PG的Redis背景恢复时限失败在完整重测中通过，未修改阈值；此单次失败仍保留为负载敏感边界。
- 最后增量（关闭记忆时bootstrap不传正文、Chat的save_skill.path不能启动沙箱、统一100KiB上限、补Agent更新CAS断言）后：五个相关测试文件在SQLite **68 passed**、PG **68 passed**（7.24/9.40秒）。完整套件数字来自增量前后相邻工作区检查，不冒称它们是在某个已提交SHA上跑的。
- 完整真实五服务浏览器 `e2e`：**65 passed**，166.79秒。生产Portal、API、Agent、Broker、Worker/Redis均真实进程；模型为可控脚本、sandbox为mock、scanner为测试stub。新增4项验证记忆编辑/重载/冲突保留草稿/开关、Chat和Work各自memory工具持久写入并在页面可见、Markdown技能新建/扫描/编辑/重新打开/版本链。旧的资料库/会话/技能/管理员等全部回归通过。
- 浏览器首轮4个失败为测试契约/等待错误：冲突重新加载会保留本地草稿；技能按钮有完整aria-label；不得在异步终态判断仅触发第一次GET后就重载，改用有截止时间的HTTP终态轮询并断言真实持久记忆；技能第二次保存须等成功POST返回的新file_id扫描READY。修正后完整65项通过。手机截图先关闭侧栏遮罩，单项补验 **1 passed**，17秒；截图和trace保存在被git忽略的 `platform/e2e-logs/`。
- 两个Claude子任务各自跑Node **26/26**、tsc与生产build通过；完整E2E启动时再次执行Node/build，包含 `/memory` 和 `/skills`。主Agent用T3内置浏览器成功登录临时Portal并查看记忆页/执行编辑保存；首次snapshot超时后重试成功。
- Ruff check/format全仓通过（278 Python文件），`git diff --check`、依赖锁一致性通过。无依赖/迁移/部署清单变化；没有以跳过的重型组件测试推导新的外部验收。
- 12000字正文/100条记忆，SQLite TestClient端到端GET（含鉴权/序列化）100次：p50 **3.59ms**，p95 **4.94ms**，响应 **48198 bytes**。不是目标机实测，也不是整个生产代理链路；未测CPU/RSS峰值。SQLite EXPLAIN使用 `sqlite_autoindex_users_1 (id=?)`，PG EXPLAIN使用 `users_pkey`，无全表扫描。原始本地日志在 `/tmp/ailab-memory-*.log` 和成本JSON，仅是此次临时证据，不作持久产物承诺。

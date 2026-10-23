# Portal 预览、T15 分页与自动标题实现

## 范围与基线

独立 worktree `/Users/michael/.t3/worktrees/ailab-lite/t3-9cc67342`，分支 `t3/preview-session-pagination-titles`，实现基线 `a8843a4`，与 `origin/main` merge-base 为 `6410fe9`。起始工作树干净，没有集成停止的 Gemini 未提交改动。不推送、不建 PR、不部署。未修改 Broker/sandbox、llm.py、Agent 循环、队列及全局交接文档。

本文随同实现提交；下述“当前验证”指该提交中的代码（最后一项时间戳排序修正在 Node 回归中验证）。状态矩阵“暂不实施”与 current 的恢复授权不一致，本任务按最新用户授权实施，由主 Agent 统一协调记录。

## 根因与实现

- `Chat` 默认 `paneOpen=true`，且每个 `preview.updated` 无条件 `setSelected`、`setPaneOpen(true)`：生成、回放、操作后的新预览都会重新抢占手机全屏。现在仅点击预览入口打开；事件只更新数据，保留选中文件与关闭意图。会话页面已有 `key={id}`，切换会话会卸载清理。
- 手机显式打开后推入临时 history 项，返回键关闭预览；关闭按钮/ESC 撤掉该项。打开聚焦关闭按钮、关闭还原触发器焦点；手机 Tab 限制在预览内。桌面保持双栏，可继续编辑输入；预览左右键仅在预览内生效。容器采用 `100dvh` 适配手机可视高度。
- 详情、历史分页、发送/恢复及事件流均有取消；旧响应不能带回旧会话。历史前插采用 layout effect 按 `scrollHeight - scrollTop` 锚定阅读位置。最新页刷新合并而非丢弃已读历史；按 UTC 微秒时间与 id 排序去重，保留分页边界与最新文件预览。
- `GET /v1/conversations/{id}?before=<run_id>&limit=20`：默认 20、上限 100，按 `(created_at,id)` keyset，返回 `has_more`，同时间戳不重不漏。未知/其他会话游标 404。`last_status` 始终指整个会话最新轮次；预览只从本页提取，result 只返回界面使用的 previews/thinking。输入文件/产物/用量查询保持现有 serializer，次数随页大小有界。

## 标题算法与主 Agent 集成点

已移除用户首句截断命名。成功状态接口从已确认结果 `result.conversation_title` 接收 1–40 字简洁标题；缺少字段时仅使用模型回答中的明确 Markdown H1/H2（排除 fenced code 与“总结/回答/结果/分析/结论”）。普通文本不做首句截断，也不伪装为智能摘要。

`GET /internal/runs/{id}` 新增 `needs_conversation_title: bool`，会话存在、未删除且 title 为空时为 true；无会话任务、手动命名后为 false。此字段仅内部 schema 暴露。

主 Agent / T14 最小接口：读取 needs_conversation_title；成功回答确定后，若 true，调用短标题模型（独立 call_id、预留/结算、失败不影响主回答），在原完成载荷 result 中保留已有字段并添加 conversation_title。不要把标题混入 summary 或再独立 PATCH 会话。API CAS 只更新 title 为空的活跃会话，模型调用期间的手动改名优先。

`apply_result_title(db, run)` 不提交；状态转换使用 `commit=False`，状态、事件与标题在用户锁内统一提交。可选标题写入置于保存点，数据库错误只回滚标题，主回答仍可完成。没有新增模型调用/计费路径；真实网关标题调用由 T14 集成，本分支没有声称付费模型已联验。

## 当前验证

- API 新回归先红后绿：原基线返回 45 轮而非 20，且发送时错误地用首句命名。增加同时间戳分页、跨会话/无效游标、limit、模型结果标题、人工名称保留、异常/长标题、内部 needs 标记、helper 无额外 commit、标题 DB 错误不影响成功回答。
- 浏览器撤掉 Chat 修复（临时 Portal 副本替换为 HEAD 原 Chat）：桌面/手机两项都红，因预览已自动打开而找不到显式打开入口。修复后真实 Chromium 桌面 1280×900、手机 390×844：预览更新不打开、不改选、关闭后不复活、重开保留选择、ESC/手机返回、焦点、上翻位置、切会话旧响应隔离、普通文本自动名/手动名/标题失败、真实 mock Excel 两轮生成，8 passed。
- Node 14 passed；`npm run build` 通过。Ruff check / format check 与 git diff --check 通过。
- 临时 PostgreSQL 16（UTF8，16440，独立 ailab_preview_test/ailab_preview_migrations）会话/API/迁移回归 42 passed。迁移全链升级/降级回归通过，本任务未增加迁移或空 stub。
- 完整 Python 套件结果见下方最终验证补记。
- 500 轮 TestClient 首屏 25 次：SQLite p95 19.64 ms、max 19.90 ms；PostgreSQL p95 47.13 ms、max 57.78 ms；响应 9065 bytes（每轮含 10 KB 非展示 result 的样本）。两者均 <150 ms。
- EXPLAIN：SQLite 使用现有 ix_runs_user_created，排序用临时 B-tree；PG 使用 ix_runs_conversation_id 后对本会话排序。现有索引下已满足 500 轮验收，不新增依赖尚不存在 0014 的 0015 迁移。极大历史仍有排序成本，不能声称查询成本恒定。

## 成本、边界与可复用陷阱

分页默认最多加载/序列化 21 个 Run、返回 20 个；每页 serializer 仍约 3×页大小查询。未引入后台作业、轮询、依赖或额外模型费用；标题仅一个保存点和可选 CAS。前端 DOM 随用户主动加载历史增长，未虚拟化；大量历史/并发/2vCPU 真机资源峰值未测。

浏览器使用仓库 Playwright 实栈流程，所有服务与 DB/存储为 /tmp 一次性目录；Redis 自建 16439。T3 内置 preview 优先尝试，首次服务尚未就绪返回 chrome-error，服务就绪后可打开但 snapshot 原始错误 `Preview automation snapshot timed out after 15000ms.`；确定性验收采用仓库指定 Playwright，非生产浏览器/真实手机硬件。

测试截图位于本机 `/tmp/ailab-preview-e2e/preview-open-desktop.png`、`preview-open-mobile.png`、`history-desktop.png`；不把临时路径冒充仓库持久化产物。原始日志 `/tmp/ailab-preview-{browser-final,python-final,pg-final,paging-sqlite,paging-pg,browser-red}.log`。

新增 E2E 应使用 SERVICE_TOKENS['agent'] 写内部事件（ops 没有 runs:events）；静态 TOKEN 与 e2e-user 密码登录不是同一身份，清理 seeded Run 必须使用该用户登录 token；已终态 cancel 返回 409，清理允许此契约。seed 时间必须在过去，避免未来 created_at 干扰真实新轮次排序。以上陷阱由主 Agent 决定是否汇入 HANDOFF。

未覆盖：真实付费模型标题质量/计费、T14 实际模型调用失败的联合测试、真实 iOS/Android 软键盘与旋转、极大历史 DOM/长时间并发、生产部署。普通文本标题浏览器测试注入内部完成结果，证明接口/UI链路，不能代替 T14 付费模型联合验收。

最终验证补记：完整 Python `700 passed, 77 skipped`（127.82 s；跳过未配置的真实后端契约），最终 PostgreSQL 定向/迁移 `42 passed`，浏览器 `8 passed`。最后时间戳小数精度排序修正已由 Node 微秒边界回归覆盖；此前浏览器完整 8 项对应同实现仅缺这一纯排序边界修正。所有临时服务随后停止。

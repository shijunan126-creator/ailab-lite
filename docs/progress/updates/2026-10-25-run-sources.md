# 2026-10-25 Run 网页来源持久化

本地子任务分支 `feat/run-sources`，起点/主线基线 `09599a03e774983a2bb66202c2376e04ada58e94`；产品与回归代码提交 `41da18fdf99dc039e13a94cbeaa7f0e8bf1f5fca`。不修改 `platform/portal/`，不推送、PR、合并或部署；旧 current.md 发布授权不适用于本子任务。

`web_search` / `read_url` 的来源与工具完成记录在同一 checkpoint 持久化，按 URL 精确去重、保留首次出现顺序与首次标题，整轮最多50条。沿用 `web_sources` 的 HTTP(S)/hostname/无用户密码校验、标题200字符和单次工具最多10条。旧 checkpoint 缺少 `sources` 时默认 `[]`，不回放历史事件补旧数据。

正常成功、预算失败和 ClientError/其他异常失败的最终 `set_run_status` 都写入 `result.sources`。当前 waiting_user/interrupted 使用 `pause_run`，不写结果；其来源留在 checkpoint，续跑后进入终态结果。本轮不改变暂停协议或即时暂停视图。

公开 JSON 形状：

```json
{"result":{"sources":[{"title":"Page 0","url":"https://example.com/0","site":"example.com"}]}}
```

无来源时为 `"sources": []`。会话详情 `GET /v1/conversations/{id}` 的 `turns[].result` 已放行 `sources`；单 Run `GET /v1/runs/{id}` 现有 serializer/RunDetail 的 dict 结果原样保留该键，回归覆盖两者，仍过滤详情中的 workspace_key。

当前验证（产品基线 `41da18f`，不是历史主线证据）：

- 新增8项回归通过：两类工具/多次调用去重、顺序与首次标题、50条上限、暂停/checkpoint/新attempt续跑、旧checkpoint兼容、预算/客户端/未知异常失败、会话与单Run输出。在隔离副本换回 `09599a0` 的三个产品文件后，8项全部失败。
- 全平台 `ruff check .` / `ruff format --check .` 通过，340个文件。
- SQLite全套 `pytest tests -q`：1146 passed / 121 skipped / 0 failed（1267项收集与进度计数一致；未配置外部后端/环境专属项跳过），GNU coreutils/findutils PATH；复用主checkout Python环境，以本worktree `platform/` 为cwd，堆栈路径确认加载本分支。
- Portal production build：复制本分支Portal到临时目录，使用与锁文件一致的既有依赖副本通过；仓库Portal文件未修改。首次 node_modules 外链被 Turbopack 的 workspace root 检查拒绝，改为目录副本后构建通过。

开销：每次网页工具调用至多遍历10条来源，去重集合至多50条；checkpoint/终态额外存储最多50个来源对象，无新增SQL查询、迁移、网络调用、依赖或后台任务。未新增生产容量或RSS实测，不外推长期负载。

剩余/下一步：父任务将本地代码提交合入 `feat/long-runs`/主集成分支，再集成Portal来源面板。`loop.py` 仅在工具完成落盘、终态结果和两个异常终态写入处改动，未修改步数/时间预算逻辑。PostgreSQL/Redis外部契约、五服务E2E、真实模型/搜索和Portal来源面板浏览器验收未执行，归父任务集成验证；本子任务无部署授权。

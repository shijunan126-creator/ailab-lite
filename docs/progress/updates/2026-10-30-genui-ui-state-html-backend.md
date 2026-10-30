# GenUI UI 状态与 HTML 后端准入（2026-10-30）

## 基线与范围

`feat/frontend-genui`，HEAD/base `origin/main@97793e601d90d0e9db11d60621c4f1352450166b` 加现有大量未提交改动；验证对象为本轮最终未提交工作树。保留所有既有改动，未执行 commit/push/stash/reset/checkout/revert；未修改 Portal、benchmark、current.md 或 status-matrix。用户本次明确授权覆盖 HLD V2 旧 `result.ui_state` 和 HTML 默认关闭描述。前端最终集成由 Portal 工程师处理。

## 实现与合同

- `runs.ui_state` 是独立 nullable JSON 列（SQL NULL），0039 接 0038；upgrade 用普通 ADD COLUMN，downgrade DROP COLUMN，不重建 SQLite 表。Agent 的 result 写入与用户 UI 状态更新互不覆盖。fork 的 Run 显式列复制不包含该列，新副本为 NULL，副本随后可以保存自己的状态。
- `PUT /v1/runs/{run_id}/ui-state`：认证用户且为 owner；其他用户、不存在 Run、已删除会话的 Run 返回 404，无认证 401；不限 Run 状态。成功整份替换、最后写入生效，204 无响应体；`blocks={}` 清为 SQL NULL。
- Body 严格为 `{"v":1,"blocks":{"0":{"key":"value"}}}`，拒绝额外顶层字段。v 必须为整数 1；block key 必须为规范十进制字符串 `0..genui_max_blocks-1`（默认 0..7），每块最多 64 项，key 长 1..120。
- value 为 null/bool/有限数/长度≤1000字符串，或恰好含 `value`、`componentType` 的对象。包装的 value 可为上述标量或最多 50 个上述标量的列表；componentType 为 null 或长度≤40字符串；不接受更深嵌套。按 compact JSON、ensure_ascii=False 的 UTF-8 编码计算整个 body，≤16384 bytes。结构、版本、非有限数和超限均 422；NaN/Infinity 校验不能回显原始输入导致 JSONResponse 再抛 500。
- 每用户每 API 进程内存限流 120 次/60 秒，超限 429 + 整数秒 Retry-After；采用与 telemetry 相同的 LoginThrottle，check/consume 之间无 await。
- 公共 Run serializer、RunOut/RunDetail、会话分页历史及分享 ShareTurn/view 均暴露顶层 ui_state，未设置为 null。print 复用会话读取路径；分享仍按既有路径快照语义读取 Run，不新增 viewer 写权限。
- `genui_html_enabled` 默认 true，仍可用 `AILAB_GENUI_HTML_ENABLED=false` 关闭；`genui_max_block_chars` 默认 32000，保留 ≤64000 配置上限，inspect/metrics 默认同步。生成的 prompt/catalog 未手改。prompt_for(False) 仍去除 HtmlArtifact 行；catalog 列出的 HtmlArtifact 始终属于已知组件，关闭准入后模型仍输出时可正常计数、保存结果，不崩溃。
- `GET /v1/me` 新增 `features.genui_html = genui_html_enabled and genui_enabled`，200；login 共用的 `_user_out` 未改。secure 仍执行原目录 SHA 校验，不新增服务端代码执行、凭据或网络能力，故无需修改 secure-profile gate；iframe/CSP 隔离由 Portal 验收。
- 历史预算仍是既有 16000 字符；20k UI 块在 32k预算下完整保留、16k预算下整块转占位，不产生半截围栏。未擅自增加模型历史预算。新增保存路径为短主键查询/更新，无扫描、后台任务或新依赖；每 Run 保存状态最多16KiB，读取随原有有界分页返回，无额外逐行查询，未做 EXPLAIN 或独立负载测试。

## 验证

均从 `platform/` 运行，Python 3.12；除第一次完整套件遗漏 PATH（见下）外，GNU PATH 使用：

```sh
export PATH="$(brew --prefix coreutils)/libexec/gnubin:$(brew --prefix findutils)/libexec/gnubin:$PATH"
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest tests/test_ui_state.py tests/test_genui.py tests/test_migrations.py -q -o addopts='' -p no:cacheprovider
AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15441/ailab_migrations \
AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15441/ailab_test \
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16441/15 \
.venv/bin/pytest tests/test_ui_state.py tests/test_genui.py tests/test_migrations.py -q -o addopts='' -p no:cacheprovider
```

- 最终 Ruff check/format 全库通过（455 个 Python 文件）；早期一次 format 检查命中并行 benchmark 尚未格式化，未触碰，最终已通过。`git diff --check` 通过。
- 最终 SQLite 专项：100 passed / 22 skipped，10.86s；22 项为未配置 PG 的迁移参数。PG 专项：122 passed，26.39s，含 SQLite/PG 升降级及模型漂移；临时库独立于个人开发数据库。已有 SQLite 表达式索引反射和 TestClient 弃用警告，无新失败。
- 覆盖 owner 回读（run/list/conversation）、认证/其他用户/删除会话、独立 result、替换/清空 SQL NULL、全部状态、严格校验/NaN/Infinity/中文字节数、16384 接受/16385 拒绝、配置 block 上限、fork 不复制及副本可写、分享 viewer 只读、120/121 限流、HTML prompt fixture/目录组件/禁用后的真实 AgentLoop 指标、环境开关和 me 双开关组合。
- 完整 SQLite 按要求仅执行一次：`.venv/bin/pytest tests -q -x -p no:cacheprovider`。本次执行漏带 GNU PATH，28 passed / 59 skipped / 1 failed 后停止；失败为既有 `tests/contracts/test_skill_sandbox.py::test_lease_attests_current_skills_and_restores_its_checkpoint_workspace[mock]` 调用 BSD stat 不支持 `-c`。补 GNU PATH 后只复核该项：1 passed（0.50s）。未重跑完整套件，未将其表述为全绿；最终 JSON 前置校验和新增边界测试是在此后专项中验证。
- 临时 PostgreSQL 15441/Redis 16441 已停止，临时服务目录及原始日志已清理。macOS sandbox 禁止共享内存/loopback，启动服务与 PG 测试使用授权提权；未接触现有部署。

## 未测边界与下一步

完整 SQLite 套件剩余测试未完成；未跑 PG 全套、Portal build/浏览器、真实模型、iframe 安全/打印渲染/性能或生产部署。本轮仅交付后端合同，不宣称 P4-H 全部验收。Portal 工程师接入 ui_state hydration、HTML feature admission 和生成目录后做前端集成；current/status-matrix 由其统一更新。

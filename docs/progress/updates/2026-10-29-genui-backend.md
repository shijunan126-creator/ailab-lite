# GenUI P3 后端（2026-10-29）

基线：`feat/frontend-genui@97793e6` 的未提交工作树，实施范围由当次用户指令明确授权。与 Portal、文件预览及发送幂等工程师共享工作树，本文仅报告 GenUI 后端。

已实现：能力/默认关闭配置、字节 sha256 提示词目录、围栏拆分与历史占位、稳定前缀提示词、step 指标与 result.genui、详情白名单、标题/摘要输入清理、搜索片段清理、限速遥测、离线脚本模型及共享夹具。无迁移、新事件、新模型调用或数据库写入路径。目录为手写 P3 占位，待前端从 TS 目录生成替换；HtmlArtifact 执行未准入。

合同：catalog JSON 只含 format/version/components/prompt_sha256；sha256 校验 UTF-8 文件原始 bytes。夹具 `platform/tests/fixtures/genui/*.md` 与同名 `.json` 配对，JSON 为 `{kind,text,closed,index}[]`；text 对 UI 是去除围栏的原始源码，对 md 是原文，index 是所有片段的零基序号，CRLF 原样保留。服务端不暂缓末行半截 fence prefix。

遥测：`POST /v1/genui/telemetry` 接受裸 JSON 数组，≤50 条 `{outcome,component?,first_render_ms?}`，outcome 为 rendered/fallback/error/action；认证用户每 API 进程 10 次/60 秒，超限 429 + Retry-After，成功 204。component 不进入客户端指标标签，不入库。

成本：每 step 线性围栏/组件扫描，目录缓存，无新进程/依赖。历史必须读取完整 summary 后才裁剪，页数/轮数沿用原有界限；搜索仅对已匹配且含 UI 的一条 summary 取回全文以生成占位片段。尚无生产 CPU/RSS 基准。

验证（本轮未提交快照）：

- `cd platform && .venv/bin/ruff check . && .venv/bin/ruff format --check .` 最终通过，441 文件格式符合；首轮并行 preview.py 超长行已由其所有者修正。
- GNU coreutils/findutils 前置 PATH 后，`.venv/bin/pytest tests/test_genui.py tests/test_agent_loop.py tests/test_model_gateway_context.py tests/test_conversations.py -q`：86 项，首轮 83 过/3 失败；三个新测试构造问题（Chat 技能早返回、未注入 mock、围栏不在行首）已修正，仅复跑三个失败项，3 过。GenUI 新增测试本体为 27 项，全部通过。
- Review 补齐 Work 压缩 pinned user prompt/previous summary 的占位入口后，仅运行新增集成断言与相关阈值回归：4 过（1 个新增 GenUI + 3 个原有阈值参数例）。GenUI 总计 28 项均通过；此补充未再运行全套。
- 唯一一次全量 `.venv/bin/pytest tests -q -o addopts=''`：1500 过、143 跳过、19 失败，214.92 秒；仅复跑 19 个失败节点：1 过、18 失败。余下 17 个文件上下文/OCR 测试仍失败，测试文档准备已看到 document_parse_failed/manifest 缺失，完整根因未在本范围内修复；1 个迁移整合测试仍断言 head=0036，而并行发送幂等迁移已是 0037。未越界修改文件解析、迁移及其测试；发送幂等失败复跑已通过。完整一次的失败清单在仓库外 `/tmp/ailab-genui-sqlite.log`，复跑在 `/tmp/ailab-genui-failures.log`，不作为源码材料复制进仓库。
- `git diff --check` 通过。PG 未运行：无已配置的抛弃测试实例；没有使用本机现有个人数据库。Portal、真实浏览器/模型、部署均未执行。

整合待办：当前并行 TS splitter 会 trim 换行/归一化 CRLF，须按共享夹具统一原文保留合同；Python 严格识别小写 openui、保留 body 末尾换行和 md 空白。先由对应工程师修复 18 个全套失败，再整合前端 P4，不将后端单测算作渲染、安全或性能端到端验收。

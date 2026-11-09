# 有界记忆文档：实施中，未通过交付验收

基线 `origin/main@c4e886135675e8dff545b37b71ae3951b1dd2e3b`，分支 `feat/memory-doc`；未合并、未部署，Portal由主代理负责。只选择移植78b2c171的0065清理，没有合并其分支。

已实现主体：完整文档渲染、会话快照与末尾增量、静态规则迁入系统一、silent save、add无revision与kind/content/scope去重、硬上限、view分节合同、关键词历史查询、ABI3、单调用ops及程序保护/淘汰、0065/0066。删除旧consolidation、核心目录、entity投影和extract/judge路径。旧列与旧DTO兼容字段尚未完全收口。

## 本轮检查

- `cd platform && .venv/bin/ruff check . && .venv/bin/ruff format --check .`通过。
- `tests/test_memory_document.py`新增7项通过；覆盖完整渲染、静态/静默规则、引文/id/保护/墓碑、版本更新、超限输入/淘汰、每批一次/无工作不调用、无revision/去重/硬上限及view字段。
- SQLite迁移模块在唯一键调整前通过；调整后与prefix全模块合跑仍须核对最终结果。未启动本地PG/Redis。
- prefix新增Chat/Work会话外部增删改、页面编辑、工具自身写入不回声的录制测试通过；最终全模块仍待核对。旧模块定向运行（dream/hardening/summary/v2/recall）76项失败；包含已删除接口/内部阶段断言和真正需修复/迁移的边界，不能归为全部无关。
- 没有本地全套Python、Portal build或浏览器验收。CI尚未全绿。

## 真模型（本轮代码，AgentLoop + API/Broker doubles）

同一deepseek-flash端点，四轮会话；只计首轮之后的累计cached/prompt：

| 模式/场景 | cached/prompt | 比例 |
|---|---:|---:|
| Chat unchanged | 26624/26988 |98.65%|
| Chat items_changed |26624/27022|98.53%|
| Chat day_boundary |26624/26895|98.99%|
| Work unchanged |24448/24780|98.66%|
| Work items_changed |24448/25028|97.68%|
| Work day_boundary |24320/24758|98.23%|

请求均为逐次追加，系统两条与工具保持相同；不代表真实服务/沙箱端到端验收。包含更完整外部编辑及自身写入的录制场景尚未逐项纳入本次真实模型脚本。

跨身份诊断使用相同正文/工具：短Chat完全重放仍0/4474。Work同user同session重放4992/5106；改user、保持session仍4992/5106；只改session为0/5106。system1与tools在身份间相等。证明当前端点存在session相关缓存边界；未证明按API key隔离，不能删除必需的session header来凑命中。

部署SQLite通过只读连接iterdump复制到/private/tmp，源与副本quick_check均ok；29条active/903内容字。仅在副本上构建episode，未写部署库。对同一批近期成功历史做一次8k/20k对照，静态指令→新episodes→当前文档，直接调用真实后台模型客户端（测试环境无部署账本写入）：

| 输入budget | episodes | 请求内容字 | 新增 | 无效JSON | prompt/completion | latency秒 |
|---:|---:|---:|---:|---:|---:|---:|
|8000|18|10458|0|0|5386/6|6.46|
|20000|30|16231|0|0|8637/132|5.07|

保留8000默认以减少输入开销；单次样本不能证明延迟/提取质量优势，零新增也不证明漏提取率。不是完整队列/计费/提交链路验收。原始个人数据、模型输出只在仓库外临时路径，结束时删除。

## 必须继续

1. 迁移仍有效的旧回归到新接口/单调用；保留安全、并发、删除、撤销与费用覆盖，不能删除失败测试冒充绿。
2. 项目融合待整合新工作流；当前process_dream未调用旧merge_projects，不能宣称merge_pending功能完成。补真实一批队列到提交/undo的副本验收。
3. 复核历史closed行仍参与legacy整文档更新带来的长期容量/扫描成本；复核test/repro程序排除（当前仅保留evaluation/eval过滤；旧模型分类排除已随extract移除）、render预算与受保护超限行为、合并来源上限、无用列/DTO和注释。
4. 补真实外部编辑/自身写入场景、迁移roundtrip和共享前缀录制测试，核对最终定向检查，之后PR/CI全绿。仅明确授权后才合并或部署。

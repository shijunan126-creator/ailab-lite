# GenUI 说明跨轮复用（2026-11-02）

基线 `fix/genui-timeline-alignment`，`main@82ff8285` 加本线程前序未提交补丁；本地 `origin/main@d7e2058` 多3提交，未混入。用户本轮授权精简版说明复用，明确不考虑无工具模型；只本地实施与验证，不发布部署。前序[记忆审查](../../reviews/2026-11-02-memory-lifecycle-audit.md)、[时间线/预览间距](2026-11-02-genui-timeline.md)的修改保持。

## 行为与边界

- `ui_components` 从当前实际 messages 扫描配对的 assistant 调用 / 成功 ui_components 工具结果。已有组件说明必须包含当前生成文档的完整文本，指南必须完全一致；当前文本本身充当保守版本检查，不增加版本注册表、持久标记、数据库或后台工作。
- 当前上下文已有完整说明时，仅返回 `already_available` 和短提示，`components` 只含缺失组件及其缺失依赖。保持原有1–16名上限、未知组件反馈、依赖闭包和HtmlArtifact开关。
- 同一Run重复调用、继承父Run消息、新进程恢复checkpoint均据实际历史判断；不依赖tool_log或genui_guide_sent旧布尔值。工具结果被裁剪、版本不同、只剩摘要、调用不配对、失败、用户/其他工具伪造均不能当作完整文档。
- 压缩保留完整工具组时继续复用；组被压掉时重新返回说明。没有额外改变压缩逻辑、旧消息或固定工具schema。
- 更新目录与指南的生成源，告诉模型已有说明可直接使用；再生成index/guide/catalog哈希。无工具模型完整提示的构造方式未改，不开展回退策略实施。
- 新静态提示词升级首次可能触发现有 `rebuilt/model_changed` 上下文边界；之后同一段中只追加。不能将一次升级与段内前缀不变混为一谈。

## 验证

- 请求录制回归：Chat/Work实走AgentLoop，两Run六次请求覆盖首次读取、同轮重复、新组件/共享依赖、下一Run重复；逐请求验证旧messages前缀、tools不变且不申请沙箱。单独真实压缩路径测试保留/丢弃工具组两种情况，2 passed。
- 组件单测覆盖旧版本、截断、失败、用户/其他工具内容、孤立结果、恢复checkpoint、未知组件及禁用HtmlArtifact。新增测试随下方Python套件统一运行。
- Portal138 tests / 30 files、生产build通过；Ruff check/format、生成契约漂移与diff check通过。只改提示生成源，无UI渲染改动，不重复前轮浏览器验收。Python全套1964 passed / 163 skipped（334.25s）；最后追加的真实压缩保留/丢组两例另行2 passed。不重复PostgreSQL（本轮无数据库/迁移改动），也不重跑本轮已通过的全套。
- 真实模型：本机配置的 `deepseek-flash`，合成数据，Chat与Work各四轮/七次请求。使用真实AgentLoop/ModelTransport，业务API与checkpoint为隔离fake；不读取个人记忆、不写用户数据库。首次读LineChart→明确要求重复读取→新增BarChart→不再读说明直接输出趋势图。两种模式均只追加一次完整指南，重复结果为空文档，BarChart只补新增部分且复用Series；最后一轮各仅一次模型调用。所有录制请求的旧messages及其余请求参数完全一致扩展。
- 首次指南/组件装载完成后累计：Chat cached/prompt = 27904/29007（96.20%）；Work = 37504/38570（97.24%）。包含后续BarChart新说明首次装载；不含最初两次冷启动/装载请求。初始缓存也可能已有供应商缓存，结果不是无缓存与优化后的成本对照。
- 两个真实回答均含一个界面块，组件名检查通过；未做浏览器渲染或复杂图表语义评测，不把这些小型合成检查声称为所有场景的95%命中保证。

## 成本与下一步

- 无新依赖、服务或后台模型调用；仅在读取组件时扫描现存消息并解析相关工具JSON。1000条消息（其中一组完整组件工具结果）探针，100次调用中位1.268ms/最大1.590ms，tracemalloc峰值约203KB；这是本机局部开销，不是整栈RSS或并发容量验收。
- 同一探针首次LineChart工具结果5082字符，重复读取127字符；只说明新增结果缩小，不能换算成供应商token节省或忽略已有历史的缓存输入费用。
- 本地补丁待用户审阅，未提交、push、PR、merge或部署。原始请求/回答只在测试进程内存，临时统计脚本和日志不入仓库。

## 发布整合（用户追加授权）

用户授权本线程全部改动提PR、绿后merge并部署最新main。提交 `1a74c73` 后整合 `origin/main@d7e2058` 为 `4f5d2ea`；解决交接/状态矩阵及双方追加测试的文本冲突，保留所有回归，产品代码自动合并无冲突。整合基线 `test_prefix_cache.py test_genui.py test_memory_summary.py` 173 passed，Ruff/diff通过；最终完整检查由PR CI执行，不重复已完成本机全套。部署计划仅重建平台与Portal，沿用当前release-41dc893依赖和Office等镜像，无备份、无部署后测试。

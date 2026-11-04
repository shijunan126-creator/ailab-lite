# PPT 选区引用修复审查（2026-11-04）

审查对象：`fix/ppt-selection-empty-ref@f05583fc`，base `3d85a86e`；检查跟踪与未跟踪文件，进入时该独立工作区干净。用户仅要求审核，本轮不改产品、测试或配置，不发布/部署。

## P2：多行 PPT 表格的两个引用动作仍会发送失败

这是原有缺陷，不是 f05583fc 引入；与本次目标同属选区文字与校验合同不一致。

- 前端 `vendor/office-suite/upstream/office-runtime/ui/viewers/PptxView.tsx:45` 的 `nodeText` 对表格逐行拼接单元格（列用 ` | `，行用换行）；`platform/portal/components/preview/office-view.tsx:61` 将该文字作为 quote，两个动作共用。
- 服务端 `platform/ailab_platform/sandbox_tools/viewer-reader.ts:23` 读取模型展示文本，只有 XLSX 分支会还原选择正文；PPT 表格仍使用 `src/domain/model.ts:96` 的 Markdown 输出，额外含行边框、表头分隔行和单元格转义。
- `platform/ailab_platform/api/document_view.py:96` 对非空 quote 做归一化后的连续子串匹配，因此真实多行表格的整表引用必然不匹配，返回422 `quote does not match document range`。本次改动只修空 quote，不能覆盖此路径。

最小复现：创建空白 PPT，在第1页放两行两列表格，内容为 `收入 / 利润` 与 `100 / 50`；点选整张表，添加到对话后发送，或直接提问。前端 quote 为 `收入 | 利润\n100 | 50`，读取结果在两行之间还带 `| --- | --- |`，且每行两侧有竖线。位置 `slide:1/shape:3` 存在、版本匹配，依然被拒绝。单元格含竖线/换行同属该格式转换边界。

本轮验证：仓库外临时生成实际 PPTX，按产品 Dockerfile 的相同入口编译真实 viewer-reader，解析得到准确 shape locator 与正文，再将真实解析结果传入生产 `validate_refs`。权限文件查询和 Worker HTTP 返回在探针中用该已知文件/解析结果替代；未宣称真实浏览器或全服务链复现。该表格准确返回上述422；同一文件的无文字矩形 `slide:1/shape:2` 和整页 `slide:1` 携带空 quote 均通过。中间 PPTX/reader/JSON 在仓库外，不提交原始输出。

根因修复建议：在 viewer-reader 中基于解析后的表格结构生成与查看器一致的引用正文，保留原 locator 校验；模型展示 Markdown 与引用校验正文分别输出。不要靠去除所有标点、将所有失败 quote 改空或跳过正文校验。复用已解析结构，避免额外解析/进程/网络调用。回归使用真实 PPTX 多行表格，并覆盖竖线、单元格换行、空表和伪造引文拒绝；至少一项从 Portal 两个动作的真实引用数据经过 reader 到 API。

## 其他结论与边界

未发现 f05583fc 新增权限、版本、locator 或来源绑定的绕过；空 quote 仍进入相同的文件/工作区校验，保存、排队、重试、模型上下文和回看保持 locator。没有改变请求构造或工具 schema。上轮新增 workspace 测试模拟整个 request_workspace，因此不能单独证明真实 Broker 的授权/位置检查；不要把该测试说成真实链路验收。

本轮仅做代码审查和上述定向真实解析探针，未重跑历史全套、Portal build、浏览器或真模型。上轮检查结果仍只见[实施记录](../progress/updates/2026-11-04-ppt-selection-reference.md)。下一步是修复本报告 P2 并补真实表格回归，尚未实施；无 P0/P1 发现不等于所有环境均已验收。

后续状态：用户随后授权修复，已完成本地实施与真实浏览器4组合验证，原发现时证据保持；最终实现、性能成本和验收统一见[实施记录](../progress/updates/2026-11-04-ppt-selection-reference.md#多行-ppt-表格引用修复后续授权)。

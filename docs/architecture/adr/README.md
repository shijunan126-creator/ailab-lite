# 架构决策记录

每篇 ADR 记录一个决定和它的理由。模块文档说明代码在哪里、怎么改；ADR 说明为什么这样定。状态只描述决策是否仍有效，不代表实现或部署状态。

| 编号 | 标题 | 状态 | 一句话决定了什么 |
|---|---|---|---|
| [0001](0001-single-node-scope.md) | Single-node scope and defaults | 已采纳，部分被 0020 修改 | 单机范围、并发、额度与保留期的默认值。 |
| [0002](0002-service-identity.md) | 单机服务身份与请求绑定 | 已采纳 | 每个服务独立凭据、scope 矩阵和按资源上下文绑定。 |
| [0003](0003-broker-exec-proxy-and-leases.md) | Broker exec proxy and recoverable single-node leases | 已采纳，部分被 0020 修改 | Broker 独占沙箱生命周期与 exec，租约可对账恢复。 |
| [0004](0004-self-developed-runtime.md) | Self-developed Agent runtime and platform-owned checkpoints | 已采纳 | Agent 循环与 checkpoint 由平台自持，不锁定框架私有状态。 |
| [0005](0005-budgeted-conversation-memory.md) | 有预算的历史与会话元数据 | 已采纳，部分被 0024/0033 修改 | 历史按预算取用，会话只存摘要游标，不新增 messages 表。 |
| [0006](0006-optional-bounded-tracing.md) | 精简部署的可选有界追踪 | 已采纳 | 默认只靠指标与请求 ID，追踪按需启用且有界。 |
| [0007](0007-controlled-public-fetch.md) | Public network access by controlled fetch and injection | 已采纳 | 沙箱保持断网，公网内容由 Agent 受限抓取后注入。 |
| [0008](0008-long-run-budgets-and-leases.md) | Long Run budgets and renewable ownership | 已采纳，部分被 0024/0033 修改 | 长任务的时间、步骤、费用与 liveness 分开约束。 |
| [0011](0011-worker-previews.md) | 单机文件扫描隔离与资料库按需预览 | 已采纳 | 上传只扫描提取，重型预览首次打开时在渲染沙箱生成。 |
| [0018](0018-file-ocr-draft.md) | 文件 OCR 与对话多模态输入 | 已被 0023 取代 | OCR 与多模态输入曾由 API 模型调用承担。 |
| [0019](0019-memory-read-path-and-governance.md) | 记忆读取路径与治理 | 已采纳，部分被 0022/0035/0037 修改 | 记忆按需检索、写入有闸门、删除沿来源传播。 |
| [0020](0020-elastic-sandbox-allowance-and-idle-reclaim.md) | 弹性沙箱额度与闲置回收 | 已采纳 | 每人额度按水位弹性放宽，排队时先回收闲置租约。 |
| [0021](0021-isolated-browser-rendering-for-read-url.md) | read_url: Trafilatura first, isolated browser rendering when that is weak | 已采纳 | 弱正文才交给隔离浏览器，渲染器不持有平台身份。 |
| [0022](0022-memory-consistency-and-scoped-recall.md) | 有来源、可隔离、可续的记忆 | 已采纳，部分被 0035/0036/0037 修改 | 记忆按来源和作用域读取写入，后台整理以 Run 为单元。 |
| [0023](0023-system-document-reader-and-concurrent-capabilities.md) | 平台文件读取执行边界与并发 | 已采纳，部分被 0030/0042/0043 修改 | 平台文件读取在 Broker 系统租约执行，Worker 只编排。 |
| [0024](0024-model-window-compaction-and-adaptive-output.md) | 模型完整窗口85%压缩与动态输出预算 | 已采纳，部分被 0033 修改 | 按模型完整窗口触发压缩，输出上限随剩余空间收缩。 |
| [0025](0025-work-office-reader.md) | Work 标配 Office 读取核心 | 已采纳 | Work 镜像预装只读 parser，读取未交付的工作区文件。 |
| [0026](0026-document-visual-coverage-and-file-waits.md) | 办公内容覆盖与持久文件等待 | 已采纳，部分被 0034 修改 | 原生文字、OCR 与视觉分开计覆盖，文件未就绪时持久等待。 |
| [0027](0027-portal-ui-stack-and-send-flow.md) | Portal 前端栈、流式管线与非阻塞发送 | 已采纳 | 自建消息运行时、非阻塞发送与薄依赖前端栈。 |
| [0028](0028-generative-ui-openui.md) | 回答内 Generative UI（OpenUI 结构化路径） | 已采纳，部分被 0029 取代 | GenUI 用正文围栏承载，状态存 Run，HtmlArtifact 隔离运行。 |
| [0029](0029-genui-registry-v2.md) | GenUI 组件目录 v2 与按需披露 | 已采纳 | 57 个语义组件、派生值由组件算、说明按需读取。 |
| [0030](0030-prefix-stable-model-requests.md) | 模型请求前缀稳定（参考 DeepSeek Harness） | 已采纳，部分被 0033/0042 修改 | 一次 Run 内请求只追加，工具与历史不重写。 |
| [0031](0031-parallel-subtasks.md) | Work 运行内的并行子任务（run_subtasks） | 已采纳 | 同一 Run 内最多四个子任务并行，共享前缀与租约。 |
| [0032](0032-prewarmed-single-use-render-sandboxes.md) | 单次使用的预热渲染沙箱 | 已采纳，单次使用部分被渲染设计取代 | 渲染沙箱预热并使用独立预留容量。 |
| [0033](0033-context-epoch-band-process-snapshot.md) | 上下文纪元、绝对压缩区间与运行过程快照 | 已采纳 | 上下文以纪元和绝对区间压缩，过程页改为持久快照。 |
| [0034](0034-safety-ready-and-lazy-native-input.md) | 安全原件就绪与按需派生 | 已采纳 | 上传只安全入库，正文、页图与 OCR 在消费时准备。 |
| [0035](0035-memory-v2.md) | 每个作用域一份有界记忆文档 | 已采纳，部分被 0036/0037 修改 | 记忆按作用域存一份有界文档，整份注入并按需列改。 |
| [0036](0036-memory-quality.md) | 记忆质量标准、每晚一条整理链与来源项目标签 | 已采纳，部分被 0037 取代 | 每晚每用户一条整理链，统一记忆标准与来源项目标签。 |
| [0037](0037-memory-single-origin.md) | 记忆只有一种行：去掉受保护、待确认与暂停 | 已采纳 | 记忆行只分活动与关闭，整理可改任何行，敏感内容按内容拒收。 |
| [0038](0038-reasoning-effort.md) | 思考强度：按模型配置，按回答固定 | 已采纳 | 思考档位按模型配置，档位由用户选择并在一次回答内固定。 |
| [0039](0039-global-search-index.md) | 全量对话搜索用会话级词项索引 | 已采纳 | 每会话一行词项并集，一次查询搜完整个账号。 |
| [0040](0040-schedule-in-conversation.md) | 定时任务绑定一个会话，到点在原会话接着回复 | 已采纳 | 一个定时任务一个会话，到点追加一轮；模式由来源决定。 |
| [0041](0041-fork-reuses-checkpoint.md) | 同模式分叉沿用被分叉轮次的上下文 | 已采纳 | 同模式分叉复制完成快照并沿用血缘根会话头。 |
| [0042](0042-single-tool-catalog.md) | 只保留一套工具目录，退役 V1 文档工具与精简目录 | 已采纳 | 每个模式只有一套冻结工具目录，旧 Run 恢复时升级。 |
| [0043](0043-on-demand-tools.md) | 低频工具经 call_tool 按需加载 | 已采纳 | 低频工具默认只暴露名字，首次调用时返回说明并还原真实调用。 |
| [0044](0044-pi-aligned-workspace-tools.md) | 工作区工具对齐 Pi 的语义 | 已采纳 | read/write/edit/bash/grep/find 照 Pi 的语义；read 按行窗口，edit 线性匹配，bash 等待窗口与平台期限分开。 |

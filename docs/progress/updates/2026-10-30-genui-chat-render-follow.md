# GenUI 对话模式空白块、HtmlArtifact 坏 JS、来源与滚动跟随（2026-10-30）

基线：`feat/frontend-genui`，base `origin/main@97793e6`，未提交。触发：用户在本机部署上看到两个问题。一是同一 prompt 在对话模式下不出可视化，在工作模式下正常；二是 HtmlArtifact 页面报 `Uncaught SyntaxError: Unexpected identifier 'row'`。

## 根因与修复

| 现象 | 根因 | 修复 |
|---|---|---|
| 对话模式第一轮 UI 块空白 | OpenUI 词法器只认 ASCII 名字，模型写 `表 = Table(...)`，名字被无报错跳过，整树为空 | `catalog/program.ts` `toProgram` 把非 ASCII 名字一致改名；解析为空布局时回退为“无法显示此内容”；提示词要求名字用英文 |
| HtmlArtifact 页面 JS 语法错误 | 单引号字符串会解码 `\"`→`"`、`\n`、`\d`。旧提示词要求 HTML 属性和 JS 字符串都用双引号，于是 JS 拼 HTML 必须写 `\"`，模型就得手工双重转义 | `toProgram` 把单引号串当原文，只有 `\'` 是转义（双引号串仍按 JSON）；提示词改为“反斜杠照常只写一个” |
| 第二轮否认自己引用过的来源 | 工具流量不回放进历史 | `history_sources` 把来源附在历史回复后（internal 与 model-context 两条路径） |
| 工作模式 `read_url` 报 not available in this turn | discovery 被当成权限闸门 | 模式允许的工具按名字调用即执行并激活 |
| 思考/工作过程滚动生硬 | 各处各写一套 stick 逻辑 | `lib/use-follow.ts`：缓动跟随，读者上滚即停，回到底部恢复 |

HtmlArtifact 一例用部署库里该轮的原始输出（`run_2b9e0c36…`）复核：修复前页面脚本 `new Function` 报 `Unexpected identifier 'row'`，修复后语法通过。

剩余风险：按旧提示词写成 `\\` 的历史回答，现在会多一个反斜杠，显示为字面量，不会崩。

## 组件目录

除 HtmlArtifact 外共 21 个组件：
- 布局：Stack、Grid、Card、Tabs、Tab
- 文字：Heading、Text、Badge、Callout
- 数据：Metric、Table、Progress
- 图表：LineChart、BarChart（可横向）、PieChart、Series
- 交互：Select、Slider、Toggle、Input、Button

本例里，模型为正负偏差条和事件时间轴改用了 HtmlArtifact。这说明目录缺少这类表达，是否扩充目录由用户决定。

## 本轮检查

当前工作区代码检查：
- Ruff check / format 通过。
- Python 全套（SQLite）exit 0。`test_conversations` 的历史期望值已补上 `sources: []`。
- Portal 18 个文件、87 项测试通过，包含 `toProgram` 的原文串测试；去掉修复后这项测试会失败。
- `npm run build` 通过。

未重跑：PG 全套和 E2E。本轮没有改动迁移、查询和 E2E 路径。

部署与真模型验收由 Codex 执行，结果见下节。

## Codex 本机部署与真模型验收

本轮用户明确授权部署 dirty worktree，并验收 Chat/Work 的真实 DeepSeek 和真实搜索；禁止修产品代码。部署输入 HEAD/base 为 `97793e601d90d0e9db11d60621c4f1352450166b`，产品内容摘要 `5ea5a21487fc`，验收后逐文件核对无漂移。未 commit/push/stash 或改 Git index。本节只补证据摘要，不修改产品。

- 版本 `genui-chat-5ea5a21487fc`；sync、两镜像构建、五组件 stop/up --no-deps --no-build --wait 均 exit 0。后端共用镜像继承上次 genui-html 基底；Portal npm ci 层 CACHED。API/Agent/Broker/Worker/Portal healthy，Portal HTTP 200。其余镜像与依赖复用，无迁移、备份或生产回滚操作。
- 部署命令结果与输入清单只在本机部署目录的 `state/deploy-genui-chat-5ea5a21487fc/`。浏览器结果、采样与截图只在 `/tmp/ailab-realmodel/`；未把截图或原始输出入库。
- 六例用独立新会话，模型均为部署中的 `deepseek-flash`；两种模式首轮均实际调用 `web_search`/`read_url`。各轮 succeeded，无 `not available in this turn`，未出现“无法显示此内容”或“数据格式有误”，全部已生成 GenUI 的 data-state 为 ok。两种模式追问均沿用前轮数据/来源，没有否认引用。

| 用例 | 结论 | run_id（首轮 / 追问） | 截图（统一在 /tmp/ailab-realmodel） |
|---|---|---|---|
| 1 Chat 模型对比与追问 | 通过；两轮各 2 个图表 SVG | `run_41d601dd5d6041ff928c` / `run_56274a0640f34ad8a404` | case-1.png |
| 2 Work 模型对比与追问 | 未完全通过指定形式；首轮有 SVG，追问改用 HtmlArtifact CSS 柱状条，当前轮 SVG 为 0；排序可操作、无 JS 错误 | `run_217b57e85ec24f0289c5` / `run_2a3918e0965a4ae39d68` | case-2.png |
| 3 销售额折线图 | 渲染/六个数据点通过，趋势正文有误：声称 4–6 月“增幅越来越大”，实际环比增量为 32、12、18 万元 | `run_2e837c8010ca43a6bcad` | case-3.png |
| 4 房贷计算器 | 通过；模型采用 HtmlArtifact，iframe 内三个滑块分别改变金额、年限、利率后月供更新；未捕获 JS 错误 | `run_ebfb6c056f0a47bd94cb` | case-4.png |
| 5 TCP 普通问答 | 通过；0 个 GenUI 块 | `run_640e346581e7404cad6f` | case-5.png |
| 6 JavaScript 抛币页 | 核心交互通过；附带空代码区域，因此“无空白块”未完全通过。GenUI ok、HtmlArtifact complete，点击 10 次更新次数和正反面比例；DOM 另有空 pre.md-code/code | `run_dc3c3b94672f483d9610` | case-6.png |

主页面及 iframe 的 console.error/pageerror 收集结果均为空；不是对模型生成内容作全量事实审核。销售额错误是本次直接可核对的语义问题。未自行修复以上三处。

滚动：首轮内外容器同时操作互相影响，故仅重复一次用例 2 原始 prompt，隔离为两个浏览器视图，run `run_063f8c44744144f1abb1`。三阶段均通过，以下为 `scrollTop/scrollHeight`，clientHeight 固定：

| 容器 | 自动跟随 | 用户上滚后停止 | 回底后恢复 |
|---|---|---|---|
| 消息区（client=576） | 185/764 → 591/1174，gap 3→7 | top 固定 401，height 1216→1883 | 1312/1923 → 1660/2256，继续跟随增长 |
| 工作过程（client=285） | 179/467 → 209/500，gap 3→6 | top 固定 12，height 500→533 | 248/533 → 814/1102，gap 0→3 |

未重跑 Ruff/Python/Portal 测试、宿主 build、PG/E2E、性能/并发或其他模型：前四项用户已提供通过结果，其余超出本轮授权验收范围。Docker 内 next build 是构建部署镜像的必要步骤。未做备份、提交、推送或部署后修复。下一步由产品负责人审阅本轮三项发现；是否修复/调整 SVG 验收形式另行决定。

- Portal 后续增量部署：`genui-chat-68d185733e9e`，仅重建 Portal，其余镜像复用；sync/build/up 均 exit 0，Portal healthy、HTTP 200；无备份、测试、浏览器或真模型验收，命令与元数据保存在本机 state。

## 三项发现的归因

- **空代码区域（用例 6）：已修复。** 模型在 openui 围栏闭合后又多写了一行 ```` ``` ````，Markdown 把它当成未闭合的空代码块。部署库里 16 次带 UI 的回答中出现 1 次。修法：`components/markdown.tsx` 不渲染内容为空的代码块，`genui.test.tsx` 加了回归测试，去掉修复后测试会失败。Portal 88 项测试和 build 通过，已随 `genui-chat-68d185733e9e` 部署。
- **Work 追问用 CSS 柱状条（用例 2）：模型遵循问题。** 提示词已经写明“能用组件表达时不用 HtmlArtifact，静态的数值对照用 Table 或图表”。这和 P6 是同一类遵循问题，待用户决定的处理方式见 P6 节。
- **销售额趋势结论写错（用例 3）：模型遵循问题。** 提示词要求结论里的派生数用表达式算出，这次模型手写了结论，属于 P6 严格数据一致性未达标的同一根因。

# PPT 无文字选区引用发送失败（2026-11-04）

授权仅本地修复与验证。分支 `fix/ppt-selection-empty-ref`，产品/测试基线为 `origin/main@3d85a86e589cb651b954b8feba11f46e1203237d` 加本次差异；没有修改主工作区既有 dirty，没有发布或部署。

## 根因与修复

上游 `PptxView.tsx` 点击选择优先取选中文字，其次取 shape 文本，整页可返回空字符串；无文字形状同样可以为空。Portal `docRefOf` 原样把 text 冻结为 quote，两个动作共用该引用。`Selection.text` 允许空，但 `DocRef.quote` 的 `min_length=1` 在业务校验之前返回422。

隔离 API 回归准确复现 `loc=[body,doc_refs,0,quote]`、`type=string_too_short`、原报错文案。不是用户输入的问题，也不是随机网络故障；本轮未读取原会话请求，不能断言截图中点击的是哪个具体对象。

修复为支持空 quote 的位置引用：保留必填 quote、长度上限、互斥目标和非空版本/位置；API 始终进行既有权限、来源状态、版本和 locator 校验。仅当引文非空时做正文匹配；纯空白和伪造正文仍拒绝。保持空字符串，不填造假文本，也不丢弃引用。已有模型上下文输出仍携带文件名和准确位置，未改模型请求构造或工具 schema。

无新增依赖、查询、远程调用、渲染、后台任务或持久存储字段；引用保存/读取与既有路径相同，无额外 CPU/RSS/磁盘开销，未做性能容量测量。

## 验证

- 修复前：空 quote 的消息提交与 workspace DocRef 校验均复现原错误。测试初稿曾错误用直接发送接口模拟排队（返回409），已改为已有 queue 路径，不属于产品缺陷。
- 修复后：`PYTHONPATH=. python -m pytest tests/test_document_view.py -q --tb=short` 28项通过，覆盖空/非空引文发送、冻结、重试、排队转发、过期版本、无效位置、伪造/空白引文；workspace 有/无正文的 shape 引用仍调用相同 check 路径。
- Ruff 全库 check/format 通过。Portal `npm test` 246项通过，`npm run build` 通过。
- Python 全套 `PYTHONPATH=. PATH=<GNU coreutils/findutils>:$PATH python -m pytest tests -q --tb=short`：2553项，2333通过 / 205跳过 / 15失败。失败集中在 document_context / document_reading_reliability：新工作区未安装/构建 vendored Office reader，解析没有产生派生正文。按已有 HANDOFF 用 `cd vendor/office-suite/runtime && npm ci --ignore-scripts && npm run build` 补齐；随后同环境 `python -m pytest tests --lf -q --tb=short` 15项全部通过、exit0。产品源码未追加修改，未重复全套，不称单次全套全绿。

测试使用既有 Python 3.12 环境并显式 PYTHONPATH 指向当前 worktree；API fixture 使用一次性 SQLite/存储，不读取用户数据库。日志仅在仓库外临时目录，不提交原始输出。

未测边界：本次未运行 PostgreSQL/Redis、E2E、真实浏览器选区或真模型。只改后端引用合同与校验，没有修改 UI；不宣称原截图会话已重放、真实视觉指代效果已验收或运行环境已更新。下一步交接本地修复，发布/部署须另行授权。


## 多行 PPT 表格引用修复（后续授权）

用户追加审核发现 P2 后明确要求修复；本次基线为 `f05583fc` 加下述产品/测试差异。前次[审查](../../reviews/2026-11-04-ppt-selection-reference-review.md)保留发现时的历史状态。

根因：PPT 查看器点击整表得到按行/列拼接的文字，校验 reader 返回的却是模型展示用 Markdown（行边框、表头分隔行、转义后的竖线），归一化后不构成连续子串。修复仅在已有 PPT shape locator 校验之后，对精确表格形状的输出识别并解码已知表格框架；去掉生成的边框和第二行分隔规则，保留/还原单元格标点与竖线，按查看器相同规则重组。无名称表格同样处理；不移除数据行中的 `---`，不修改普通文本形状或模型读取的 Markdown。

这里复用已解析、受输出上限约束的读取结果，没有重新解压/解析整份 PPT。由于上游是固定哈希的只读快照，修复在 AILab wire adapter 中解码固定表格表示，无需改上游快照、生成技能或工具 schema。与直接暴露解析后 rows 的方案相比，该实现无需更改上游 ParsedDocument 接口或多条构建链；真实 reader 回归守住格式契约。

性能：同一 reader 子进程内增加有界字符串扫描/数组变换，复杂度 O(选区正文长度)，受已有每选区200K/总400K字符限制；无新增远程调用、解析进程、后台任务、依赖、数据库查询或磁盘输出，保留原18s/192MiB进程限制。未单独测量 CPU/RSS/延迟，不把无新增I/O称为零CPU/内存成本。

本轮验证：

- 原 reader 对新增普通多行、特殊字符、空表三类真实 PPTX 回归均失败（重现文本格式差异）。修复后命名/无名称共6例通过：真实 python-pptx 文件→真实隔离 reader→API 消息发送，伪造引文仍422、越界shape仍拒绝。仅 Worker HTTP 传输用真实解析结果替代，文件归属/版本检查与消息冻结走真实 API/测试数据库。
- 新增测试默认在已安装的 vendored runtime 依赖上临时编译 viewer-reader，避免 CI 只因默认 `/opt/ailab-viewer/reader.mjs` 不存在而跳过新回归；编译/产物位于pytest临时目录，沿用原有超时，未安装依赖时才清晰跳过。测试中一度假定同名上传不会改名，已改为断言实际上传返回的文件名，不改产品命名规则。
- Portal桥接回归覆盖空引文、多行表格、竖线/换行/反斜杠与空表，确认「添加到对话 / 提问」均原样传递 quote 与 shape locator；该模块52项通过。全Portal250项和生产构建通过。
- 原有文档引用+真实reader专项45项通过；补无名称表格后6个新增真实API案例通过。最终完整Python套件2559项：2354通过/205跳过/0失败（447.68s）；Ruff check/format通过。全套启动后仅追加Portal纯空白归一化与浏览器回归，后端/单测输入未再改变。

复核补充：全空单列表格的查看器引用是纯换行，原API按合同仍拒绝纯空白引文。Portal `docRefOf` 将仅含空白的选区正文规范成空 quote，保留其非空位置；含实际文字仍原样传递。Portal最终252项与build通过；后端仍拒绝未经规范的纯空白quote，不放宽正文来源校验。

真实浏览器：新增普通表格/全空单列表格 × 添加到对话/直接提问四组合，隔离栈使用真实API/Worker/Broker/Chromium和mock模型/沙箱，四组合全部通过（`AILAB_E2E=1 AILAB_TEST_REDIS_URL=<throwaway Redis> AILAB_E2E_BASE_PORT=18691 AILAB_E2E_PORTAL_PORT=13691 python -m pytest e2e/test_office_native.py -k ppt_table_reference_actions -q --tb=short`，exit0）；确认两个动作实际提交引用的原文字段、file_id和shape locator。首轮E2E初始化与同时运行的Portal构建发生资源复制冲突；串行重启后新测试又误用了文本形状的 `.pptx-slide` 选择器，截图/DOM确认表格是HTML table，已修正为点击真实table节点，不更改产品、不延长等待/跳过断言。

未测：PG、真实模型、真实容器沙箱；不宣称视觉理解质量或生产容量已验。原始日志/生成PPTX/reader不提交仓库，没有发布或部署。一次性栈由fixture停止，专用Redis已清理；下一步用户按需授权发布/部署，当前仅本地提交。

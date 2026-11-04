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

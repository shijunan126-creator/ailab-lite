# 01 当前系统调查

[总览](README.md)。以下事实按本轮本机源码基线判断；未调查容器内安装或部署态。路径均相对 仓库根目录。

## 已有执行路径

| 环节 | 当前源码 | 当前行为与迁移影响 |
| --- | --- | --- |
| 模式及提示词 | `platform/ailab_platform/agent/prompts.py`：`system_prompt_for`、`load_skill` | Chat/Work 有不同规则；当前用 `<!-- work -->` 剥离基础技能制作部分。直接替换上游大文件后不能依赖这个旧正则获得安全的 Chat 版本。 |
| 请求目录 | `agent/loop.py:_tools_for_run`、`agent/discovery.py:catalog_for_budget` | 首次选择完整或 REDUCED_V1 目录，保存 `tool_catalog_v1`，后续固定；search 只找技能，不动态增加 schema。小目录不含全部文件/视觉工具，接入前要定义可支持的模型档位。 |
| 基础技能 | `skill_registry.py`、`skill_index.py`、`agent/skill_verification.py` | 四基础身份 `platform.docx/xlsx/pptx/pdf`；文件树 hash 与沙箱镜像需一致。用户不能关闭基础技能；新模块也须进入校验闭包。 |
| 自动加载与去重 | `agent/loop.py:_ensure_skill`、`_carried_skills`、`_register_carried_skills`、`_run_tool` | 附件 MIME/后缀、Work 用户关键词可预加载基础正文；跨轮从配对成功结果识别正文，身份为 version+mode 或 file_id+mode。现无 route/reference 级读取身份。 |
| 实际工具结果 | `agent/tools.py:tool_result_message`、`loop.py:_run_tool` | 完整 result JSON 进入模型消息；事件仅展示约 2000 字符。事件日志变短不是上下文受限，不能靠 UI 截断控制大技能。 |
| 原生文档读取 | `broker/documents.py`、`api/document_sources.py`、`api/document_content.py`、`api/routes_library_tools.py` | Broker 系统沙箱执行固定 Node reader，API 管理索引/授权/派生身份，Worker 编排。原文件、派生物和模型读到的内容有不同身份。 |
| Chat 附件 | `api/attachment_input.py`、`portal/lib/outbox.ts` | prepare 完整预处理并做整轮准入；不完整/超限明确拒绝，视觉文档可能同时准备正文与页图。普通附件不能直接改成只给一句元数据，选区任务需要新增明确范围合同。 |
| Work 文件 | `agent/loop.py:_ensure_lease`、`agent/tools.py`、`images/sbx-office/Dockerfile` | 按需租沙箱，恢复工作区；办公生成用 Python 库，office-read 是读取器。用户沙箱无网，不允许上游 setup 在运行时任意安装。 |
| 预览 | `portal/components/chat/preview-pane.tsx`、`components/preview/*` | Work 部分 Office 预览为 PDF，库文件可为页图，Excel 为派生 sheets.json。已有查看/切文件/下载；没有上游的选区→模型、get_view、模型 reveal 双向合同。 |
| 发送/排队 | `portal/lib/outbox.ts`、`api/document_sources.py:bind_refs`、`api/fork_context.py` | 单一发送路径、client_send_id、附件顺序/精确文件身份已有；选区必须进入该链，不另建“侧栏发送”旁路。 |

## 容量与压缩的真实合同

`model_policy.py` 估算 messages+tools，考虑图片，按返回 usage 校准；输入上限为 context_tokens-1，输出按剩余窗口动态分配；默认在上下文窗口 85% 自动压缩。不是固定预留目录标称最大输出。`auto_compact=false` 时需要明确暂停合同，不能让新模块绕过限制。

存在三条摘要路径，均需覆盖，不能只修 Work 轮内压缩：

1. `loop.py:_assemble_context`：无可继承快照/重建时，分页历史并更新摘要游标；有结构化摘要和来源复验边界。
2. `_compact_parent_context`：继承上一轮 messages，先对原父前缀追加摘要指令，再决定保留近期组和当前新输入。
3. `_compact_work_context`：轮内预算触发，保留当前原始用户消息、已确认交付提示和近期完整工具组；摘要只是背景。该函数也在同一 AgentLoop 的工具对话中使用，不能以函数名假设只影响 Work。

压缩侧调用沿用主请求模型、工具与推理设置；`_answer_without_tools` 不默认改 tool_choice；压缩后记录 `agent.context_boundary=compacted`。权限失效、模型变化、旧布局重建也有显式边界。历史 GenUI 保留原文，只有摘要输入剥离 UI；不能为新技能省 token 在普通续轮改写旧 GenUI、旧工具结果或图片。

`agent/checkpoint_budget.py` 对持久状态另设未压缩字节上限；满时冻结并暂停，不能丢弃 pending/付费响应后继续重放。`adapters/runtime.py:checkpoint_state` 把可追溯图片存为引用+内容 hash，恢复时核对；新 viewer capture 必须进入同一机制，不能无限保存裸 base64。

## 必须解决的共因

| 缺口 | 源码证据/影响 | 计划处理 |
| --- | --- | --- |
| 大入口全量自动注入 | `load_skill` 返回整个文件；上游 PDF/PPT 主文已经很大，多格式附件会叠加 | 小路由＋原文模块分段读取，自动加载只到导航/读取入口；见[03](03-context-and-prefix-cache.md) |
| 安装与可见混在一起 | `_run_tool` 仅凭 `loaded_skills` 可返回 already_loaded；轮内压缩未重建该列表 | 拆资源安装状态与当前完整正文可见性；压缩后立即重算，不把它当作已经 runtime 复现的 bug |
| 子模块无跨轮复用 | 只识别 load_skill 配对结果，Work read_file 是任意沙箱文本读取，Chat 没有该执行入口 | 增加受限只读模块接口，两模式可用，不启动用户沙箱 |
| 同批工具结果可超额 | 结果逐条 append 后才走后续模型/压缩检查，缺模块级累计准入 | 调度前估算待返回组、单模块上限与完整配对入账，预算不足安全分段/边界压缩 |
| 模块路径和版本未冻结 | 基础 catalog 来自进程当前文件树；原文大包升级后旧快照重读可能不同 | 运行绑定 package/version/hash，保留有引用旧包，重启不切到新目录 |
| 选区会触发全文准备 | outbox 只提交 file_ids，没有精确文档选区合同 | 显式 range reference，不触发无关全文重注入；旧附件行为保留 |
| 专业视觉评审缺口 | `run_subtasks` 最少2项，使用同模型，共享可写沙箱，结果4000字符；allow-list 不含看图 | 单个只读视觉评审专用执行路径，有图片输入、权限、计量及结构化分批结果 |
| 页码与结构不是同一定位 | `api/document_content.py:visual_pages` 指出 Word/XLSX 的结构 anchor 不映射物理页 | 维护明确 parser/render revision 和定位映射；不按段号猜页码 |
| 上游 host 与现有 Portal 不兼容 | 上游 host 使用 MCP Apps、页面工具和本地路径；AILab 只有预览组件 | 复用 viewer，替换 host/RPC/身份层，见[04](04-viewer-and-file-contract.md) |

这些问题应统一在资源身份、消息追加和文件版本合同里解决，不对四格式各加一套“已读布尔值”和临时提示。

## 已有资源与尚未核验部分

`images/sbx-office/Dockerfile` 已声明 Node24、LibreOffice、Poppler、Noto CJK、zip/unzip、python-docx/python-pptx/openpyxl/matplotlib/pandas/pypdf；未声明完整制作需要的 docx-js、pptxgenjs、xlsxwriter、ReportLab/PDF工具闭包、Chromium、Tectonic。Dockerfile 声明不是部署镜像实际检查。

本轮没有重跑已有 `test_prefix_cache.py`、`test_base_office_skills.py`、`test_office_skill_checks.py` 或真栈；既有历史缓存百分比不作为新方案验收。下一阶段基于这些入口增补合同回归，不因旧 tests pass 宣称完整迁移兼容。

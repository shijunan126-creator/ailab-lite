# 后端 A：项目说明、公司品牌、会话文件移动

独立实现分支 `feat/projects-brand`，起点 `00c5cc9d557ba130f2a3b364b2b9417de314c26c`，共同 base `main@74e66c0`。产品代码与测试提交 **`e7b606c`**；本记录随后单独提交。授权仅本地实现、验证和提交；没有 Portal 改动、push、PR、合并、镜像重建或部署。父任务负责审核/集成和五服务验收。

## 实现与最终接口

- `0025_project_instructions.py`，revision `0025`，down_revision `0024`。新增 `projects.instructions TEXT NOT NULL DEFAULT ''`，SQLite 普通 ADD COLUMN；提供 downgrade。父任务集成0026/0027后须重链并继续扩展 `test_integration_migrations.py` 的完整链断言。
- `ProjectOut.instructions: str`；`PATCH /v1/projects/{id}` 接受 `{name?: str, instructions?: str}`，至少一项，显式 null 不接受，说明 ≤4000 code points，空串清除。所有项目响应含说明。
- 两个 Agent bootstrap（`GET /internal/runs/{id}` 与 `/model-input`）均返回 `project_instructions: str`、`brand: dict`。无项目说明为空串，Chat/未启用品牌为 `{}`。项目说明在记忆之前进入系统 prompt，并在首次 messages checkpoint 后保持冻结；项目定时运行使用相同路径。
- `PATCH /v1/conversations/{id}` 增 `move_files: bool=false`；只允许与显式 `project_id` 同时提交。本人未删除库产物按 lineage 去重，调用现有 `move_lineage` 处理历史版本/重名，按200个head分页控制内存；全部移动与会话归属同事务。`ConversationOut.moved_files: int|null`：本次请求执行移动时返回 lineage 数；其他响应为 null。fault 用例在真实 UPDATE 后抛错，验证文件与会话均回滚。
- 公司设置存于 `platform_settings['brand']`。GET/PATCH `/v1/admin/brand`；POST/DELETE `/v1/admin/brand/assets/{logo|pptx|docx}`；GET `/content`、`/thumbnail`。权限沿用 `_context_admin`（仅 platform_admin），写操作审计不保存正文。上传 multipart 字段名 **`file`**，201 返回 `{file_id, filename, status, size, analysis, thumbnail_url}`。GET 顶层含启用/名称/颜色/字体、三个有效资产ID、version、updated_at；`assets` 是各kind的 DTO 或 null。
- PATCH 设置接受 `{enabled?, name?, colors?:{primary:'#RRGGBB',accent:'#RRGGBB'}, font?}`；字体≤60字符，名称≤120字符，禁止显式null/未知字段。初始 disabled，颜色默认 `#1F4E79/#E0A526`，font空串表示保留模板主题字体；未写设置的 updated_at=null。
- 品牌文件 `purpose='brand'`，独立上传路径，走已有 `stage_job → scan → limited subprocess → API READY`。API 激活与READY同事务；最新 pending/rejected 候选仅显示在 `assets[kind]`，顶层有效ID仍指旧READY资产，成功激活才切换。pending analysis=null，失败 analysis含error；logo有缩略图，模板thumbnail_url=null。
- Worker 仅在有限CPU/内存/墙钟/输出的现有子进程中导入解析器。PPTX分析尺寸、主题色/字体、版式名、母版数；DOCX可打开且样式名≤50；logo验证PNG/JPEG真实格式、尺寸并归一化PNG。SVG返回422。未新增依赖或不安全开关；secure继续由现有启动gate和scanner工厂拒绝stub扫描，品牌不提供绕过路径。
- 品牌文件不计个人资料库字节，不出现在库列表，也不能通过普通单文件/批量API、`read_library_file` / `copy_library_file` 访问。Broker仅对Work Run冻结的资产集合授予 `job=brand` 读取，允许跨所有者，但不授予Agent读取品牌原文件。第一次Work bootstrap将完整品牌快照与 `runtime_meta.brand_version` 冻结；包括disabled/空资产状态。平台锁与运行所有者锁防止替换/运行元数据覆盖竞争。
- Broker在恢复工作区**之后**重置 `.brand`，经既有流式下载/文件注入路径写 `logo.png`、`template.pptx`、`template.docx`、brand.json；metadata预检和实际传输都受限。模板各≤10MiB、归一化logo≤2MiB、合计≤22MiB；还服从已有上传/解析限制，brand.json≤64KiB。
- PPT生成器默认读取品牌颜色、字体和logo；有模板时保留母版/主题/尺寸并移除示例幻灯片，选最少占位符版式后移除占位符，原生形状坐标映射到模板尺寸；默认内容页继承模板背景。配置未指定字体时读取模板主题字体。`"brand": false` 完全退出。PPT/XLSX技能说明、Work品牌系统提示含Word `Document(template)` 用法；同步技能目录哈希。

## 验证（当前代码，不沿用历史验收）

最终测试基线 `e7b606c`，仅记录文件随后变化。

- Ruff：`ruff check .`、`ruff format --check .`（在 platform），通过；`git diff --check`、技能 manifest/hash verifier 通过。
- SQLite 全套：**1047 passed / 116 skipped**，153.33s；JUnit共1163项，0 failure / 0 error。
- PostgreSQL 16 + Redis 一次性全套：**1127 passed / 36 skipped**，202.01s；JUnit共1163项，0 failure / 0 error。PG/Redis 使用15485/16485非默认端口，测试专用schema/数据库；没有读取开发 `.env` 或连接开发库。验证结束后已用各自专用目录/端口停止PG与Redis。
- 跳过项主要是未配置真实Docker/OpenSandbox/S3/ClamAV/OIDC等外部契约、可选OpenSandbox SDK/OTel；SQLite轮还跳过PG/Redis专用项，PG+Redis轮已执行这些专用项。无五服务E2E/Portal build（按本分工免跑）。
- 双库迁移包含完整0021→0022→0023→0024→0025，逐步降回0021后逐步再升级；另有0024↔0025专用往返，保留模型漂移与SQLite FK断言。
- 新行为覆盖项目PATCH/Chat/定时bootstrap、checkpoint恢复不重读、管理员拒绝/限长/格式/实际扫描、受限分析一次性、quota/list/普通读取与批量隔离、冻结版本、内容与缩略图、PPT模板母版/尺寸/字体/logo/opt-out、注入伪造大小与字节上限、移动事务。真实SIGKILL解析子进程、READY被阻塞时并发启动Run、文件移动后故障回滚见 `platform/tests/faults/test_brand_faults.py`。
- 新查询沿用settings/project/file主键、Run conversation索引、Artifact run索引、lineage/head索引；`test_brand_new_queries_use_indexes` 在SQLite/PG实际执行EXPLAIN。PG小样本关闭seqscan以验证可用索引路径，**不代表大历史库延迟验收**。
- PPT实际生成样本后用python-pptx重新打开并检查母版数量/原尺寸/两页logo与文字字体；未做LibreOffice视觉渲染、真实付费模型或五服务E2E（按分工由父任务做）。

## 资源成本

本机Python3.12，每种5次；样本是800×200 PNG（991B）、一母版一页PPTX（28104B）、15段落DOCX（36604B）。`platform/tests/measure_brand.py` 可复测；入口先 `import tests.conftest` 设置隔离环境，再调用 `tests.measure_brand.main()`，不要直接读取开发配置。下列为当前样本测量，不是2vCPU部署机或最大模板压力验收。

| 操作 | CPU中位数 | 墙钟中位数 | 进程峰值RSS | 临时解析输出 |
|---|---:|---:|---:|---:|
| logo校验/PNG归一化 | 0.0494s | 0.0674s | 23.3MiB | 1563B |
| PPTX分析 | 0.0990s | 0.1273s | 36.3MiB | 991B |
| DOCX分析 | 0.0835s | 0.1290s | 35.0MiB | 1127B |

分析每次上传仅一次，READY重投直接跳过。临时输出含job/result元数据；原件持久存储，logo另存归一化PNG和现有WebP衍生物。失败时保留旧有效设置，子进程由父进程回收；受限ZIP预检沿用2048项/128MiB展开量/压缩率上限。

三资产合计65699B，在**MockSandbox本机文件系统provider**、无HTTP网络的真实流式复制路径上，租约准备墙钟中位数0.0205s，父进程CPU0.0104s；测量进程峰值RSS76.0MiB（含测试/解析依赖基线，不能视作增量RSS）。转移临时磁盘一次最多一个资产，工作区新增约资产总大小+brand.json；上限为22MiB+64KiB，恢复已有工作区后会先删除旧`.brand`。未启用品牌的Work仍有一次目录reset命令；首轮冻结至多一次元数据写入，此后不重读设置/解析。ClamAV、跨主机HTTP、真实Docker/OpenSandbox及大模板注入延迟未测。

没有新包或常驻后台任务。**必须重建 sbx-office 镜像**，再由父任务完成镜像技能哈希、真实执行/渲染验收；本分支没有重建、发布或部署镜像。

## 剩余风险与接手

- [A对契约的修改](../../reviews/2026-10-24-workspace-features-design.md#a-分支对契约的修改)：SVG不支持；旧/被覆盖候选品牌文件尚无自动回收，反复替换会增加平台磁盘占用。DELETE移除有效设置/候选引用，不破坏已冻结运行的旧字节。需后续按Run/checkpoint引用生命周期设计有界回收，不可盲删。
- 模板使用主母版的最少占位符版式；复杂多母版、极端长宽比/字体替换、不同Office客户端视觉效果仍需真实模板与视觉验收。DOCX模板中的示例正文由生成报告的Agent按用户需求处理，提示只保证使用模板与样式。
- 双库本地通过不等于集成、CI、merge或生产验收。父任务合入另外两个分支时核对 `schemas.py`、bootstrap、内部文件鉴权、settings/quota与迁移测试的交叉修改；合并后重链迁移，再跑双库/E2E/Portal和镜像验收。

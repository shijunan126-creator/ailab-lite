# 2026-10-24 定时任务与资料库页码引用（后端子任务）

工作树 `t3-fcd2b0a7`，分支 `feat/schedules-citations`，实际起点 `09a011baa274a41a823c8769fb5f7f7522c5dff1`（当时的 `feat/ui-polish-fork-retry`）。只授权本地后端实现、测试、提交；没有修改 Portal、推送、PR 或部署。父 Agent 负责前端和集成，sibling 负责会话树。

**迁移接线：`0024_schedules_citations.py` 的 revision=`0024`，当前 down_revision=`0022`，没有 0023 stub。父 Agent 合入 sibling 的 `0023_conversation_tree.py` 后，必须将 0024 的 down_revision 改为 `0023`，重新做双库迁移升/降/再升和漂移检查。** 本工作树没有带入 sibling 的模型/上下文/分页修改。

## API 与数据契约

所有 `/v1/schedules` 接口使用当前用户认证；跨用户 ID 返回 404。最多五个任务，停用项也占名额；创建、更新、删除、手动运行与项目删除都用现有用户锁（PostgreSQL 另持 users 行锁）。

- `GET /v1/schedules` → 200 `TaskOut[]`，最多 5 个，创建顺序。
- `POST /v1/schedules` → 201 `TaskOut`。
- `PATCH /v1/schedules/{id}` → 200 `TaskOut`，只提交需要修改的字段。nullable 字段可以传 null 清空；非 nullable 字段显式 null 返回 422。
- `DELETE /v1/schedules/{id}` → 204 空正文。
- `POST /v1/schedules/{id}/run` → 201 `{"conversation_id": str, "run_id": str}`。允许手动运行停用任务，不移动 `next_run_at`；启动失败仍持久记录失败并返回 422 中文 detail。

创建 body：

```json
{
  "title": "每日摘要",
  "prompt": "整理项目进展",
  "mode": "chat",
  "skill": null,
  "model": null,
  "project_id": null,
  "schedule": {"kind": "daily", "time": "09:00"},
  "timezone": "Asia/Hong_Kong",
  "enabled": true
}
```

`title` 1–60 字且非空白；`prompt` 非空白且不超过动态 `settings.max_prompt_chars`。`mode=chat|work`（默认 chat），skill 非空时隐含 work；模型与技能必须可用，项目必须归本人。timezone 用 zoneinfo 校验。schedule.kind=`daily|weekdays|weekly`；time 为 24 小时 `HH:MM`；weekly 必须提供非空、不重复的 `days:[0..6]`，0=周一，其他 kind 不接受非 null days。输出的非 weekly days 是 null。

`TaskOut` 包含创建 body 全部字段，以及：`id, user_id, next_run_at, last_run_at, last_run_id, last_conversation_id, last_status, last_error, created_at, updated_at`。所有时间输出为带时区的 UTC。首次 last_* 均 null；last_run_at 是最近启动尝试时间，失败时 last_run_id/last_conversation_id 清空，last_status=`failed`，last_error 为用户安全中文。成功启动后 GET/PATCH/POST 输出按 last_run_id 主键读取最新 Run.status，因此可以直接显示 queued/running/waiting_user/interrupted/succeeded/failed/canceled 等既有状态，无终态更新任务/轮询写入。

五条限制的 422 detail **`最多 5 个定时任务`**，在用户锁内 count+insert，不只在 UI 检查。审计 action 为 `schedule.created|schedule.updated|schedule.deleted`，target_type=`schedule`，不记录 prompt。无新增 SSE 事件；创建的 Run 使用原有 `run.created`、状态、标题与后续过程事件。

## 执行、时间与失败边界

API lifespan 启动 dispatcher；`AILAB_SCHEDULES_ENABLED=true`，`AILAB_SCHEDULES_INTERVAL_SECONDS=30`，`AILAB_SCHEDULES_DEADLINE_SECONDS=20`。test profile 自动停用后台循环，测试直接调用 `dispatch_schedules`。

每轮用 `(enabled,next_run_at)` 索引选最早 100 个 due 项。用户锁内按观察到的 next_run_at 做 CAS；推进到 now 之后的下次 occurrence、新会话、Run、事件和 outbox 在同一外层事务提交。嵌套 savepoint 只用于回滚启动失败产生的新会话/Run；没有直接发队列。多个 API replica 的同一次 occurrence 只有一个成功 CAS；进程在提交前中断会回滚 claim，提交后则由既有持久 outbox 投递。停机漏过多次只补一次。

本地墙钟 DST：春季不存在时间跳过当天，秋季重复时间只取较早 fold=0，每日/每周最多执行一次。周任务搜索 16 天，覆盖恰逢一次 DST 缺失而需跳过一周的情况。会话标题使用任务时区日期，格式 `title · M月D日`。

执行复用 `start_run(locked=True, commit=False)`：同样校验额度、并发、模型、技能，skill 隐含 work 的队列 topic 仍按最终模式。停用用户不创建会话/Run。可预期的业务失败记录中文错误并推进时间，避免重试风暴；不可预期的基础设施异常回滚当前事务，后续 loop 重试。项目删除在同一用户锁内 **清空 project_id 并停用关联任务**，用户可编辑后重新开启。

指标只有 `ailab_schedule_executions_total{status="started"|"failed"}`，无任务/用户标签。due、user、project 查询均有索引；双库 EXPLAIN 回归验证 due 索引。

## 文件引用与预览契约

Worker 的现有受限提取/缓存路径写入显式 `\f[page:N]\n` 页起点：PDF 前 20 页；PPTX 每张 slide；OCR 图像第 1 页、PDF 保留原始页号（低文本页不重新编号）。仍受原有 200,000 字、解析子进程 CPU/RSS/墙钟和 16MiB 输出限制；OCR 加标记后同样检查 200,000 字上限。没有 API-side 文档解析。

DOCX 提取的是逻辑段落/表格，不含可靠的渲染分页；不伪造页码。XLSX 保留已有 `# sheet名称` 边界，sheet 并不等于预览页。旧缓存/这两类文本的页码未知，仍可正常阅读和无页码引用；不自动重解析旧缓存。

`read_library_file` 参数保持 `{"file_id":str,"offset":int=0}`；工具结果是 `{"ok":true,"file_id":str,"filename":str,"text":str,"pages":[{"page":int,"offset":int}],"truncated":bool,"next_offset":int|null}`。内部 read API 相同但没有工具层的 ok。窗口仍为最多 30,000 Unicode 字符；pages.offset 相对于返回 text，offset=0 表示窗口延续这一页。页起点偏移位于标记之后；text 自身也保留标记。未知页号返回 `pages:[]`。流式扫描偏移前文本只保留窗口与 40 字符 marker tail，流在退出时关闭，不整份缓冲。

内部 `GET /internal/runs/{id}` 新增 `project_library:[{"file_id":str,"filename":str}]`；仅项目会话装入本用户、同项目、READY、library、head、未删除/清除的最新 50 项，文件名截到 200 字；非项目/独立 Run 为 []。Agent Chat/Work 系统 prompt 附上有界索引（最多 16,000 字）并要求相关时先搜索/阅读；文件名声明为数据。所有会话的 citation rule 均为：实际 read 后才能引用 `[《文件名》第N页](ailab-file://<file_id>?page=N)`；页码未知用 `[《文件名》](ailab-file://<file_id>)`，不得猜测或引用未读文件，原网页来源规则保留。

预览 API 无需新增 page 参数：`GET /v1/files/{id}/preview` 的 ready document manifest 已有顺序 `pages:[name,...]`，第 N 页用 `pages[N-1]`，按既有授权 `GET /v1/files/{id}/preview/{name}` 取图。Portal 负责解释 citation 的 page=N 并滚到对应页；页超出/缓存不存在时由 UI 保留其加载/越界处理，不意味着后端会为不存在页造图。

## 当前验证（不与历史基线相加）

应用代码为 `09a011b` 加本次后端实现；下方 full suite 后仅增加一个崩溃注入测试、加强审计/额度断言并修正测试 UTC 转换，应用代码未再变动。

- Ruff 按 §9 在 platform 目录 check 与 format --check：通过（288 文件）。git diff --check：通过。
- 全 SQLite：**996 passed / 109 skipped**，128.52 s。
- 全 PostgreSQL16 + Redis：**1072 passed / 33 skipped**，163.47 s。
- 最终 `tests/test_schedules_citations.py`：SQLite **11 passed / 1 skipped**（PG migration 未配置），PG **12 passed**。包含 CRUD/归属/五条并发上限、双 dispatcher、DST/漏跑/失败/停用用户/项目删除、模型技能/并发额度、终态 freshness、审计、页标记/窗口/项目 prompt、索引计划、0024 升降再升、outbox 后崩溃回滚。
- 首次增量 PG 的唯一失败：测试把 PostgreSQL 返回的 `Asia/Hong_Kong` aware datetime 用 replace 标成 UTC，误差 8h；改为 aware→astimezone(UTC)、naive→replace 后上述最终 12 项通过。应用时间转换没有此错误。
- 附加误从仓库根目录执行 Ruff（超出 §9 的 platform 范围）发现29项既有错误，位于 .github/diagrams/docs/images 等非本任务文件；未修改这些文件。按要求回到 platform 后检查仍全绿。
- 初次测试的工具/fixture 使用和内部 schema 漏字段在全套前修正；内部字段有实际 HTTP bootstrap 回归，不能只测 handler 返回 dict。
- GNU coreutils/findutils 在 PATH。使用主检出的现有 Python3.12 venv，但 PYTHONPATH 指向本工作树；未安装依赖/读取开发 .env。独立 PostgreSQL15724 / Redis16724，一次性 test/migrations 库，用完关闭。
- 跳过：SQLite 的真实 PG/Redis 与可选组件；PG 33 项为未配置的 Docker/OpenSandbox、S3、ClamAV、Keycloak、外网等组件契约及 PG 行锁不适用故障注入。未运行 Portal npm/build、浏览器或五服务 E2E（本任务明确排除），未运行真实付费模型/实际连续定时服务或真实 Office 页码 golden。没有把这些称为集成验收。

## 成本与剩余集成

独立 Mac/SQLite 合成探针（不是 2vCPU/8GiB 容量结论）：空库 dispatcher 100 次 mean **0.137ms** / p95 **0.172ms**，总 CPU **13.78ms**，进程最大 RSS 增长 **0.031MiB**。5 个 due 任务/轮 ×20 轮，mean **19.24ms** / p95 **24.12ms**，总 CPU **381.87ms**（含各轮重置 due 的 SQL），最大 RSS 增长 **1.61MiB**。默认 30 秒一次空闲查询；最多100个候选、20秒循环deadline，每次执行只写常规会话/Run/outbox，没有模型网络请求或新文件。锁竞争/单次 SQL 超时仍可能超过循环deadline，它不是严格20秒墙钟 SLA。磁盘新增为任务行及每次普通会话/Run/事件/账本；后续模型成本与手动 Run 相同。

20 页空白 PDF 提取 ×30：mean **2.56ms** / p95 **2.83ms**，总 CPU **76.83ms**，最大 RSS 增长 **0.80MiB**，包含 PDF 解析本身，不单独归因给 marker。20 个 marker 共211 UTF-8字节（原连接用19个换行），无新 subprocess/依赖/二次提取。PPTX 每 slide 增加同量级字符；DOCX/XLSX 路径无新分页计算。页码扫描对读取到窗口为止的字符线性，最多保留30,001字符与小tail；后续很远 offset 会重扫先前缓存文本，不重解析文档。

父 Agent 下一步：合入 sibling 0023 → reparent 0024 → 解决 models/schemas/main/project/bootstrap 的局部并行冲突（保留双方字段）→ 按上述 API/tool 契约完成 Portal 定时任务与 citation chip → 双库迁移+统一全套+真实浏览器/E2E验收。定时模型实际回答质量、引用遵循率、DST 长期运行/多进程 kill/真实 provider 仍未覆盖；提示约束不能保证模型永不幻觉引用。

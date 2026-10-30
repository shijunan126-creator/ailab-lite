# 技能、办公、记忆与轻量计算：实施设计

状态：实施合同（2026-10-30）。需求见[需求文档](2026-10-30-skills-memory-requirements.md)，缓存规则见[增量记忆与前缀缓存指南](2026-10-30-incremental-memory-prompt-cache-guide.md)。本文解决需求 §9 的待定事项，并固定后端与 Portal 共享的接口。基线 `origin/main@b35ef6e`，分支 `feat/skills-memory`。

分工：后端由 Codex 按 B1–B4 实施，Claude 审核；Portal 由 Claude 实施。四个后端分支各自从本分支切出，迁移号预留如下，合并时由 Claude 串成一条链。

| 包 | 内容 | 迁移 |
| --- | --- | --- |
| B1 | 基础办公技能、技能身份、多技能消息、统一加载 | `0040_base_office_skills` |
| B2 | 技能草稿：后台提取、目录编辑、确认入库 | `0041_skill_drafts` |
| B3 | 记忆整理：取消日限、增量批次、解释报告、提示词布局 | `0042_memory_dream_batches` |
| B4 | 浏览器 QuickJS：客户端调用协议 | `0043_client_calls` |

## 1. 技能身份与基础办公技能（B1）

### 1.1 三类技能

| kind | 名称 | 来源 | 可关闭 | 可删除 |
| --- | --- | --- | --- | --- |
| `base` | `platform.pdf`、`platform.docx`、`platform.xlsx`、`platform.pptx` | 平台镜像 `/opt/skills/<name>/` | 否（用户和管理员都不能关闭） | 否 |
| `builtin` | `pptx`（演示设计增强）、`auto-skill`（隐式） | 平台镜像 | 是 | 否 |
| `personal` | 用户自定，`^[a-z0-9][a-z0-9-]{0,39}$` | 资料库技能文件 | 是 | 是 |

- 基础技能名带 `platform.` 前缀。个人技能名不允许 `.`，所以两者不会重名；已从市场装过 `docx`、`pdf` 的个人副本保持原名，继续可用。
- `skill_index.py` 的名称规则放宽为 `(platform\.)?[a-z][a-z0-9_-]{0,63}`，目录名等于技能名。两份 `skill_index.py` 保持字节一致，`index.json` 重新生成。
- 技能名长度统一：个人 40，平台 64（含前缀）。API 与工具 schema 的 `max_length` 统一为 64，个人名的规则仍由 `NAME_RE` 校验。
- 显示标题：`PDF`、`Word`、`Excel`、`PPT`、`PPT 设计`。`pptx` 保持原名，保证旧设置、策略、定时任务和历史 Run 中的引用照常解析；它的 SKILL.md 只留演示逻辑、版式与视觉指导，title 改为 `PPT 设计`。
- 旧名兼容：`xlsx` 解析为 `platform.xlsx`。`platform.document.read`、`platform.pdf.read`、`platform.spreadsheet.read`、`platform.presentation.read` 分别解析为 `platform.docx`、`platform.pdf`、`platform.xlsx`、`platform.pptx`；checkpoint 中钉住的旧 internal 版本照常接受。别名只用于解析，不出现在技能列表里。
- 迁移 `0040`：把 `users.settings.disabled_skills` 里的 `xlsx` 去掉（基础技能不可关），把 `skill_policies` 中 `name='xlsx'` 的行删除；`pptx` 行与设置保持不变（仍指可选增强）。downgrade 不恢复已删除的 `xlsx` 关闭项，迁移注释写明这一点。

### 1.2 内容与脚本

```
images/sbx-office/skills/
  platform.pdf/   SKILL.md skill.toml scripts/pdf_tool.py
  platform.docx/  SKILL.md skill.toml scripts/build_docx.py
  platform.xlsx/  SKILL.md skill.toml scripts/build_xlsx.py
  platform.pptx/  SKILL.md skill.toml scripts/build.py assets/icons/…
  pptx/           SKILL.md skill.toml scripts/build.py（兼容旧命令的 runpy 转发）
  auto-skill/     SKILL.md skill.toml
```

- 每个基础技能的 SKILL.md 合并三部分：现有 `internal_skills` 的 ZCode 读取指导（保留出处与许可说明）、原市场/内置技能的通用生成编辑流程与脚本用法、检查与渲染步骤。读取部分对 Chat 与 Work 都适用；只属于 Work 的段落包在 `<!-- work -->` 与 `<!-- /work -->` 之间。
- Chat 加载时去掉 work 段落，末尾附一句：“当前对话不能运行脚本、创建或交付文件；用户确需实际文件时说明需要在「工作」模式完成。”Work 加载返回全文。只有平台技能使用这一标记；用户导入的技能原样返回，不要求任何标记。
- `internal_skills/__init__.py` 只保留别名与版本兼容，指导正文移入 SKILL.md；`search_capabilities` 的技能条目来自统一注册表。
- 市场目录删除 `docx`、`pdf`、`pptx`、`xlsx` 条目（它们已由基础技能或内置技能覆盖），`market_skills/` 中的脚本移入基础技能后删除该目录。已安装的个人副本不动。市场 `listing()` 为空时返回空列表，接口保持。
- `pptx` 与 `platform.pptx` 的脚本只有一份（在 `platform.pptx`），`pptx/scripts/build.py` 是三行转发，旧对话里的命令仍能运行。`xlsx` 目录删除；旧路径 `/opt/skills/xlsx/scripts/build_xlsx.py` 在 `platform.xlsx` 的 SKILL.md 里注明已迁移。

### 1.3 注册表与列表接口

单一注册表 `ailab_platform/skill_registry.py`（只读平台目录与别名，不碰 DB；个人技能仍由 API 层 `personal_skills` 提供）。`GET /v1/skills` 每项：

```ts
type Skill = {
  name: string; title: string; description: string;
  kind: "base" | "builtin" | "personal";
  toggleable: boolean;      // base=false
  enabled: boolean;         // base 恒为 true
  implicit?: boolean;       // auto-skill
  extras?: "ppt_template"[]; // 平台登记的额外交互：platform.pptx、pptx
  version?: string;          // 平台技能 skill.toml version
  file_id?: string; lineage_id?: string; filename?: string; kind_file?: "md" | "zip"; files?: number; error?: string;
  builtin: boolean; scope: "builtin" | "personal";  // 兼容旧 Portal 字段：base/builtin → builtin
}
```

列表顺序：base（固定顺序 pdf、docx、xlsx、pptx）→ builtin → personal。`PUT /v1/skills/{name}` 对 base 返回 403 “基础技能不能关闭”。管理员策略接口对 base 拒绝写入。`GET /v1/skills/content/{name}` 对 base/builtin 返回只读全文（`editable=false`）。

## 2. 多技能消息与统一加载（B1）

### 2.1 请求合同

`MessageCreate`、`RunCreate`、`QueueCreate`、`QueuePatch`、`RunEdit` 增加：

```ts
skills?: { name: string; file_id?: string | null; at?: number | null }[] | null  // ≤5 项
skill?: string | null   // 旧字段，等价于 skills=[{name: skill}]；与 skills 同时出现时 400
```

- `at` 是技能气泡在 `prompt` 中的位置，按 Unicode 码点计数（Python `len()`、JS `Array.from(text).length`）；`0 ≤ at ≤ len(prompt)`，非法时 400。缺省表示不在正文中（如旧客户端），模型看到时放在消息末尾。
- `RunEdit.skills` 为 `null` 表示沿用原轮次技能，`[]` 表示清空。
- 解析：个人技能优先按 `file_id` 所属 lineage 的当前 head 解析（改名后仍能找到）；否则按名称解析（含别名）。同一技能重复出现时只保留第一项。
- 任一技能不可用时整条消息 400，`detail` 为中文并点名：`技能「周报助手」已关闭`、`技能「x」不存在或已删除`、`技能「x」还在安全扫描`、`技能「x」未通过审核`、`技能「x」已被管理员停用`。Portal 原样显示。
- **技能不再强制 Work**。`resolve_conversation_mode(requires_work=...)` 只由 `template` 触发；Chat 会话可以带技能。PPT 模板仍只用于 Work。
- 定时任务保持单技能字段，内部转为同一列表。

### 2.2 Run 冻结值

```json
"options": {
  "mode": "chat",
  "capability_discovery": 1,
  "skills": [{"name": "pptx", "title": "PPT 设计", "kind": "builtin", "at": 3},
             {"name": "weekly", "title": "周报助手", "kind": "personal", "file_id": "f_…", "at": 12}]
}
```

新 Run 只写 `skills`；读取方（重试、编辑、队列出队、Agent、Portal）同时接受旧 `skill`/`skill_title`。

### 2.3 Agent 加载

- 系统提示词不再注入任何技能全文（删除 `SKILL_NOTE` 预注入），也不列技能索引；保留一句简短规则：“处理 PDF/Word/Excel/PPT 文件时先 load_skill 对应的 platform.* 基础技能；其他技能用 search_capabilities 发现后 load_skill。”
- 用户消息文本：在每个 `at` 处插入 `[技能：标题]` 后作为模型看到的用户消息；历史轮次同样渲染。
- 首次执行（`not state["messages"]`）时，平台按顺序为每个所选技能追加一组合成的 `assistant tool_calls(load_skill)` + `tool` 结果，位置在用户消息之后，并发出 `agent.skill` 事件：

```json
{"name": "weekly", "title": "周报助手", "status": "loaded" | "failed", "reason": "picked" | "office" | "model", "error": "…"}
```

  失败时工具结果是可读的错误，模型据此告诉用户，不静默忽略。加载后模型仍可 `search_capabilities` 发现其他技能。
- **基础技能保证加载**：Run 开始时按附件格式（pdf；doc/docx；xls/xlsx；ppt/pptx，csv 不触发）补加载对应的 `platform.*`（`reason="office"`）；每批工具执行后，扫描工具参数与结果中出现的办公文件（扩展名或 content_type），对未加载的格式在下一次模型调用前补加载。Work 模式另按用户消息中的格式关键词（PPT、pptx、幻灯片、演示文稿、Excel、xlsx、Word、docx、PDF）在开始时补加载。每个技能每个 Run 只加载一次。
- 平台技能文本来自 Agent 自己已校验的索引，加载本身不申请沙箱。Work 第一次租到沙箱时，如果本 Run 已加载平台技能，先做沙箱索引校验，失败按现有 `SkillRefused` 报错。个人技能的附带文件在租到沙箱时安装。
- `state["loaded_skills"] = [{"name","kind","file_id"?,"version"?}]` 写入 checkpoint；恢复时为个人技能重新安装文件，不重复发出加载步骤。
- Chat 模式：加载平台技能按 §1.2 去掉 work 段落；个人技能原样返回，附一句“当前对话没有沙箱，技能中的脚本步骤无法执行”。

## 3. 技能草稿（B2）

### 3.1 状态与数据

`skill_drafts` 表：`id`（`sd_`）、`user_id`、`origin`（`conversation|skill`）、`conversation_id?`、`base_file_id?`、`base_kind?`（`personal|base|builtin`）、`base_name?`、`action`（`create|update`）、`status`、`stage`、`error?`、`summary?`、`revision`、`files`（JSON 清单）、`claim_token`、`claimed_until`、`attempts`、时间戳。索引 `(user_id, status, updated_at)`。

`status`：`queued → running → ready`，或 `preparing → ready`（打开已有 ZIP 技能需 Worker 解包），失败为 `failed`（可重试），终态 `confirmed`、`discarded`。每用户最多 20 个活动草稿，超过 409。

文件清单每项 `{path, size, sha256, kind: "text"|"image"|"binary", change: "added"|"modified"|"deleted"|"unchanged", blob: storage key}`。正文全部在对象存储 `skill-drafts/{user}/{draft}/{sha256}`（内容寻址，编辑写新对象）。限制沿用 `skillfiles`：≤200 个文件、解压总量 ≤30 MiB、SKILL.md ≤100 KiB；在线编辑的文本文件 ≤512 KiB。路径规则与 ZIP 检查一致（无绝对路径、无 `..`、无反斜杠）。

### 3.2 接口

```
POST   /v1/conversations/{id}/skill-drafts            {target?: string}  → 202 SkillDraft
POST   /v1/skills/{name}/drafts                       {}                → 202 SkillDraft  （个人：编辑新版本；base/builtin：另存为个人，名称默认 `<name去前缀>-custom`）
GET    /v1/skill-drafts?active=1                      → SkillDraft[]（≤50，新到旧，不含 files）
GET    /v1/skill-drafts/{id}                          → SkillDraft（含 files）
GET    /v1/skill-drafts/{id}/files/{path}             → 原始字节；文本 text/plain; charset=utf-8；png/jpeg/gif/webp 内联；其他（含 svg）attachment 下载；均 nosniff
PUT    /v1/skill-drafts/{id}/files/{path}             JSON {content, expected_revision} 写文本；multipart file+expected_revision 写二进制
POST   /v1/skill-drafts/{id}/move                     {from, to, expected_revision}（文件或目录前缀）
DELETE /v1/skill-drafts/{id}/files/{path}?expected_revision=N（文件或目录前缀）
POST   /v1/skill-drafts/{id}/retry                    （failed → queued）
POST   /v1/skill-drafts/{id}/confirm                  {expected_revision} → {file_id, name, version}
DELETE /v1/skill-drafts/{id}                          （discarded）
```

```ts
type SkillDraft = {
  id: string; status: "queued"|"running"|"preparing"|"ready"|"failed"|"confirmed"|"discarded";
  stage?: "reading" | "drafting" | "unpacking" | null;
  origin: "conversation" | "skill"; conversation_id?: string; conversation_title?: string;
  action: "create" | "update"; base?: { name: string; kind: "personal"|"base"|"builtin"; file_id?: string } | null;
  name?: string; title?: string; description?: string;   // 从当前 SKILL.md frontmatter 读出
  summary?: string;    // 提取时模型说明本次提炼了什么、为何新建或更新
  error?: string; revision: number; created_at: string; updated_at: string;
  files?: { path: string; size: number; kind: "text"|"image"|"binary"; change: "added"|"modified"|"deleted"|"unchanged" }[];
  result?: { file_id: string; name: string } | null;
}
```

- 所有写操作带 `expected_revision`，不匹配 409。
- confirm：以当前 SKILL.md frontmatter 的 name 为准并校验；`create` 时同名个人技能已存在返回 409；`update` 时基础版本已不是 head 返回 409（“技能已被修改，请重新打开最新版本”）。只有 SKILL.md 且原技能不是 ZIP 时保存为 `.md`，否则 API 用已知文件构建 ZIP。保存后走既有扫描与审核，返回新 file_id；Portal 复用现有扫描等待。
- API 不解析不可信 ZIP：从已有 ZIP 技能打开草稿时发 `skill_unpack` 作业给 Worker（`stage_job`），Worker 校验并把文件写入草稿存储，再通过内部接口回报清单（`status=preparing → ready`）。Markdown 技能和平台技能由 API 直接复制（平台技能是受信目录）。
- 删除草稿只删草稿对象，不动正式技能。确认不会覆盖之后的并发修改。

### 3.3 后台提取

- 「提取技能」不再发一条 Work 消息；Chat 与 Work 会话都可用，不阻塞会话继续对话。
- 处理位置与记忆整理相同：API 进程内后台循环按租约认领 `queued`，启动时回收过期租约；模型调用前提交，不持 DB 事务或用户锁；每次调用经 reserve/settle 记账（`subject="skill_draft"`）。
- 输入：固定系统提示（`auto-skill` 的 SKILL.md 规则 + JSON 输出格式，稳定前缀）；动态部分在后：用户已有个人技能的名称与描述（≤50 条）、会话可见分支的轮次（用户原话、最终回复，以及 `write_file` 内容与 `exec` 命令摘要），总输入有字符预算（默认 24k），超出时保留最近轮次并在 summary 中说明截断。
- 输出 JSON：`{action, target?, files: [{path, content}], deleted?: [path], summary}`。`update` 时 Worker 解包或直接复制目标技能为基础，再合并模型给出的文件；`create` 时只用模型文件。输出缺 SKILL.md 或 frontmatter 不合规为 `failed`，错误可读，可重试。
- 进度阶段 `reading → drafting →（unpacking）→ ready`。

## 4. 浏览器 QuickJS（B4 后端，Portal 执行）

### 4.1 工具

`run_js {code: string ≤32 KiB, input?: JSON ≤256 KiB, timeout_ms?: 100..5000，默认 2000}`，Chat 与 Work 都有，属于按需发现的工具（不进核心 schema）。只有交互式 Run（由 Portal 用户消息、编辑、重试、队列创建）提供；定时任务等无浏览器的 Run 不提供。工具描述说明：小规模计算、JSON/字符串/正则/CSV 文本转换；无网络、无文件、无 DOM；结果由用户浏览器计算。

### 4.2 协议

```
Agent → POST /internal/runs/{run_id}/client-calls  {call_id, kind: "js", payload: {code, input, timeout_ms}}
        API 建 client_calls 行：status=pending, claim_deadline=now+6s, deadline=now+timeout_ms/1000+10s；同 call_id 幂等返回已有行
        API 追加 RunEvent "client.call" {call_id, kind, code, input, timeout_ms, limits: {memory_mb: 32, stack_kb: 512, output_kb: 64}, deadline}
Agent → GET  /internal/runs/{run_id}/client-calls/{call_id}   每 250 ms，直到终态或自身截止
Portal → POST /v1/runs/{run_id}/client-calls/{call_id}/claim  → {claim_token}；已被认领/过期/Run 已结束 409
Portal → POST /v1/runs/{run_id}/client-calls/{call_id}/result {claim_token, ok, value?: string(JSON)≤64 KiB, logs?: string ≤8 KiB,
                                                              error?: {type: "timeout"|"memory"|"syntax"|"runtime"|"output_limit"|"unavailable", message ≤1 KiB}, stats: {ms, heap_kb?}}
        API 写结果（只接受持有 claim_token 的第一次提交），追加 RunEvent "client.result" {call_id, ok, error_type?, ms}
```

- 过期在读取时惰性判定：未在 `claim_deadline` 前认领为 `expired`（“没有在线的浏览器页面执行，页面可能已关闭或离线”），认领后未在 `deadline` 前回报为 `expired`（“浏览器执行超时”）。Run 进入终态或取消时，未完成调用置 `canceled`。
- 认领与回报只允许 Run 所有者；`call_id` 由 Agent 的持久调用身份生成，Run 恢复后同一调用已有结果则直接复用（纯计算，可重放）。
- 工具结果给模型：`{ok, value, logs, ms, source: "browser"}` 或 `{ok: false, error}`；结果视为用户侧数据，不提升权限。
- 指标只记总数与耗时直方图，标签 `kind`/`outcome`，不带 run/user。

### 4.3 Portal 执行

Web Worker 中运行 `quickjs-emscripten`（release-sync 变体，Wasm 按需动态加载）：中断处理器按截止时间中断，`setMemoryLimit(32MiB)`、`setMaxStackSize(512KiB)`；Worker 外层计时 `timeout_ms + 1000` 到期直接 `terminate()`。不注入 `fetch`/文件/DOM 等宿主对象，只提供 `input` 与 `console.log`（收集到 logs）。每个标签页串行执行；多标签由 claim 去重。CSP `script-src` 增加 `'wasm-unsafe-eval'`。

## 5. 记忆整理（B3）

### 5.1 取消自然日限制

- 删除 `uq_memory_dream_user_date`；新增部分唯一索引：每个用户最多一条 `status in ('pending','running')` 的整理（SQLite/PG 均为 partial unique index）。`local_date` 仅作展示与统计。
- 手动 `POST /v1/memory/dream/run`：
  - 有活动整理 → `200 {id, status, reused: true, pending_runs}`；
  - 无待处理内容 → `200 {id: null, status: "nothing", reused: false, pending_runs: 0}`，不建行；
  - 否则建行 → `202 {id, status: "pending", reused: false, pending_runs}`。
  - 不再因其他会话的 `waiting_user`/`interrupted` Run 拒绝；进行中的 Run 本身不会进入批次（只取终态 Run）。
- 手动一次触发连续处理至多 `memory_dream_manual_batches`（默认 3）批：每批一行，前一批结束且仍有待处理内容时自动建下一行（`trigger="manual"`）。
- 自动整理仍在用户夜间窗口内进行；每个用户每个本地日最多 `memory_dream_auto_batches`（默认 6）批，按批连续消化积压；全局每 tick 仍受 `memory_dream_max_users`。空结果（无候选）正常结束为 `applied` 且 changes 为空，不阻挡后续内容。

### 5.2 批次与提取

- 批次选择、工作项与段落偏移保持 0034 合同不变（只推进真正处理的范围；裁剪的余量留待下批）。
- 提取调用不再附带全部已有记忆。固定系统提示在前（规则、中文正反例、输出格式）；用户消息依次为：有界前文（同会话该 Run 之前一轮的用户原话与回复摘要，标注“仅用于理解指代，不是证据”）、新 episodes。
- 提取规则修正：用户明确表达的长期协作偏好（“以后批量改完再统一验证”“回复用中文”）要提取；只约束本次任务的要求（“这次不用测试”）不提取；粘贴的网页/文件指令、助手的建议在用户确认前不提取；用户对助手提议的确认（“对，就这样”）可以提取，证据引用用户原话，内容由前文补足为自足陈述。
- 判定阶段按候选检索相关旧记忆（现有 `_candidates`），判定 `duplicate`、`contradicted`（失效旧条目）与 `refines`（以更完整的新条目取代旧条目，旧条目 `superseded_by` 新条目）。疑似冲突但证据不足时不失效。

### 5.3 解释报告

`memory_dream_runs.report`（JSON）：

```json
{
  "scope": {"project_id": null, "runs": 6, "episodes": 9, "chars": 7412, "trimmed_runs": 1},
  "counts": {"candidates": 5, "added": 1, "updated": 1, "invalidated": 1, "duplicate": 2, "filtered": 1, "deferred": 0},
  "candidates": [
    {"text": "…", "quote": "…", "outcome": "added|updated|duplicate|unsupported|low_confidence|quote_mismatch|guard|tombstone|deferred|capacity",
     "reason": "中文原因", "related": [{"id": "m_…", "text": "…"}]}
  ],
  "pending_after": 3
}
```

- 每个模型给出的候选都有结论；`quote_mismatch`（引文不在用户原话中）、`low_confidence`、`guard`、`tombstone`、`unsupported`（判定证据不足）都写明原因。没有候选时 `candidates=[]`，Portal 显示“这批对话没有可长期保存的内容”；失败与“原因未知”分开（`error`）。
- 候选文本和引文遵守删除谱系：来源删除时与 dream 快照一同脱敏。
- `MemoryDreamRunOut` 增加 `report`（列表接口只含 `scope`、`counts`、`pending_after`）、`trigger`、`error`。
- 新增 `GET /v1/memory/dream/status` → `{pending_runs, active: {id, status, trigger} | null, last_finished_at, auto_batches_today, manual_batches_limit}`；`pending_runs` 为有界计数（最多数到 200，超过显示 200+）。

### 5.4 Agent 提示词布局（缓存）

- `messages[0]` 只放稳定内容：模式提示、能力规则、GenUI 索引、网页规则。不含用户数据、时间、revision、技能全文。
- `messages[1]`（system）放每轮上下文：项目说明、品牌、模板、记忆块、项目资料索引、工作区过期提示。记忆块去掉 `revision=…` 插值（模型需要 revision 时通过 memory list 工具取）。
- 运行中刷新记忆：只有可见条目被删除、修改、暂停，或开关变化时才重写 `messages[1]`；仅新增条目时不重写（新增内容已在工具结果中）。
- 记忆提取、判定与技能草稿提取的系统提示同样固定在前，动态数据在后。用量记录保留供应商 `cached_tokens`。

## 6. Portal（Claude）

- 输入框改为 contenteditable 编辑器：`div[role=textbox][aria-multiline=true][aria-label=消息]`，技能气泡是不可编辑的原子节点（只显示标题），可删除、可撤销，复制粘贴保留文字与技能顺序；`/` 在句首或空白后触发列表；编辑消息复用同一编辑器。E2E 同步改为按角色/名称定位。
- 技能列表来自 `GET /v1/skills`，按 kind 分组显示；选择不切换模式。`extras` 含 `ppt_template` 的技能在 Work 下显示模板配置，模板属于插入它的那个气泡，删气泡即清模板。
- 回到底部按钮、技能页分组与草稿编辑器、会话「提取技能」入口与后台进度、记忆整理面板的报告、QuickJS Worker 与执行步骤展示。

## 7. 验收

按需求 §9 逐项；实现完成后统一跑 Ruff、Python SQLite/PG 全套、Portal test/build、相关 E2E 与真实浏览器检查，再按用户授权部署。

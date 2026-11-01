# 管理员管理的平台技能与技能广场（2026-11-02）

## 用户要求（原话要点）

- 广场里放经过验证、确实好用的办公技能（高 star 来源），不要自写的泛泛提示。
- 管理员可直接管理平台技能（不含四个基础技能）：添加、删除、修改，同步给所有用户。
- 用户添加的平台技能未修改时随平台自动更新；用户改过就不再更新；提供“恢复默认”。

## 现状

- 基础技能 `platform.{pdf,docx,xlsx,pptx}` 与 `pptx`（PPT 设计）、`auto-skill` 随沙箱镜像发布（`images/sbx-office/skills/index.json`），改动需发版。
- 个人技能是资料库文件（`FileObject.purpose="skill"`），Worker 扫描，Run 启动时由 API 把 `{name,title,description,file_id,filename,kind}` 交给 Agent，Agent 经 Broker 注入沙箱。
- `/v1/market` 是只读代码目录（现为空），安装即复制成个人技能，复制后与来源无关，无法更新。

## 设计

### 数据

- `platform_skills`：`id`、`name`（唯一，`^[a-z0-9][a-z0-9-]{0,39}$`，不得以 `platform.` 开头、不得与镜像技能重名）、`title`、`description`、`category`、`source_url`、`license`、`current_file_id`（当前发布版本）、`created_by`、`created_at/updated_at`、`deleted_at`。
- 版本内容复用 `FileObject`：`purpose="platform_skill"`，归属一个系统所有者（不计入任何用户配额），沿用 `lineage_id/is_head` 版本链与 Worker 扫描。仅扫描为 ready 的版本可发布为 `current_file_id`。
- `user_platform_skills`：`(user_id, skill_id)` 主键、`added_at`、`custom_lineage_id`（用户改过后指向其个人副本的版本链，空=跟随平台）、`base_file_id`（副本基于的平台版本）。启停沿用 `user.settings.disabled_skills`（按 name）。

### 行为

- 广场列出未删除的平台技能，标“添加/已添加”。添加只建一条 `user_platform_skills`，不复制内容。
- 未修改：每次 Run 取 `current_file_id`，所以管理员更新后下一次 Run 即生效。Run 输入里写入具体 `file_id`，同一 Run 内续跑/恢复用同一版本，不中途换版。
- 用户编辑：以当前平台版本为底，建个人技能副本（现有草稿编辑流程），记入 `custom_lineage_id/base_file_id`。此后 Run 用副本，平台更新不影响。若平台有更新，“我的技能”标“平台有新版本”。
- 恢复默认：清除副本版本链（同删除个人技能的清理路径），`custom_lineage_id` 置空，回到跟随平台。
- 管理员删除：软删 `platform_skills`；未修改的订阅随之消失；已修改的副本转为普通个人技能（解除关联），用户内容不丢。
- 管理员修改：上传 SKILL.md/ZIP 或在线编辑 → 新版本扫描 → ready 后自动发布（可回退到历史版本）。

### 边界

- 四个基础技能和镜像内技能不进入此管理；`/` 与 + 菜单仍不提供基础技能。
- 管理员策略（`skill_policies` 全局/部门禁用）对平台技能按 name 同样生效。
- 名称空间：平台技能、镜像技能、个人技能三者不得重名；冲突在写入时拒绝。
- `/v1/market` 与 `market.py` 移除，广场改读新接口。

### 接口（后端最终契约）

- 用户：`GET /v1/platform-skills`（广场，含 added/customized/update_available），`POST /v1/platform-skills/{id}/add`，`DELETE /v1/platform-skills/{id}/add`，`POST /v1/platform-skills/{id}/customize`（开草稿），`POST /v1/platform-skills/{id}/reset`。`GET /v1/skills` 把已添加的平台技能并入列表（`kind: "platform"`）。
- 管理员：`GET/POST /v1/admin/platform-skills`、`PATCH/DELETE /v1/admin/platform-skills/{id}`、`POST /v1/admin/platform-skills/{id}/versions`（上传新版本）、`POST /v1/admin/platform-skills/{id}/publish/{file_id}`（回退）。
- Run 输入：已添加且启用的平台技能以与个人技能相同的结构进入 `personal_skills`（Agent 无需改动），内部取文件接口允许读取 `purpose="platform_skill"` 的已发布/曾发布版本。

- 两个目录 GET 均返回 `{items, next_cursor}`，参数 `after`（上一页最后一个 id，默认空）、`limit`（默认 100，范围 1–100）。用户目录只显示已发布版本，管理员目录也显示尚未发布的条目。条目字段为 `id/name/title/description/category/source_url/license/current_file_id/created_at/updated_at/kind/added/customized/update_available`；`kind` 固定 `platform`。
- 创建管理员条目使用 JSON `{name,title,description?,category?,source_url?,license?}`，返回 201；PATCH 只接受上述元数据字段，`name` 创建后不可改。字符串字段不能写 null。删除为 204，名称保留不复用。
- 上传版本使用 multipart 字段 **`upload`**（`.md` 或 `.zip`），返回 202 `{file_id,status:"pending",version}`；Worker 扫描成功且技能 name 匹配后自动发布。上传后旧发布版本继续可用。发布/回退 POST 无请求体，返回条目；文件必须属于此技能、ready 且无技能错误。
- 补充管理员对接接口：`GET /v1/admin/platform-skills/{id}/versions?before=<version>&limit=50` 返回 `{items,next_cursor}`（最多 100）；每项 `file_id/filename/version/status/error/current/published/created_at`。`GET /v1/admin/platform-skills/{id}/versions/{file_id}/content` 下载已扫描的版本（附件流，不内联执行）。保留数外且无引用的版本可能已被清理。
- 补充管理员在线编辑：`POST /v1/admin/platform-skills/{id}/drafts` 返回现有草稿 DTO（202，新建；200，复用）。以当前发布版本为底，`origin:"platform_admin"`，后续文件编辑/confirm 全部沿用 `/v1/skill-drafts`；confirm 创建系统所有者的新 pending 版本，不计管理员配额，仍需 Worker 扫描后发布。并发上传/发布改变底稿时 confirm 为 409；无已发布版本时先上传文件。
- add/reset POST 无请求体，返回条目（200）；DELETE add 无请求体，204、可重复。每用户最多添加 100 项。取消添加会清除自定义副本；管理员删除则保留并脱钩副本。
- customize POST 无请求体，返回现有草稿 DTO（202，新建；200，复用），`origin:"platform"`、`base.kind:"platform"`，Markdown 可直接编辑，ZIP 通过 Worker 解包后编辑；随后沿用 `/v1/skill-drafts/{id}/files/{path}` 和 `/confirm`。confirm 沿用 `{expected_revision}` 与 `{file_id,name,version}`，在同一事务记录副本 lineage/base。自定义必须保留原 name，重置/取消添加会废弃未完成的自定义草稿。`POST /v1/skills/{name}/drafts` 对平台名称亦转到该流程。
- `GET /v1/skills` 保持原数组返回；已添加条目含 `id/added/customized/update_available`，`scope/kind:"platform"`、`builtin:false`、`kind_file:"md"|"zip"`、`file_id/filename/lineage_id/enabled/toggleable/files/error`。开关沿用 `PUT /v1/skills/{name}` 的 `{enabled}`，返回 scope 为 platform。未修改的平台版本不受 `*personal*` 审核开关影响，自定义副本受其审核规则约束；按平台 name 的全局/部门禁用对两者均有效。
- Run 在创建事务中冻结已启用平台条目；Agent 的 `personal_skills` 仍只传 `name/title/description/file_id/filename/kind`，其中 **kind 是文件格式 md/zip**，不是 UI 的 platform。内部文件读取同时核对活跃 Run、冻结 file_id、当前订阅、当前策略、系统所有者及发布标记；普通用户文件接口不提供平台文件下载。解除订阅、管理员删除及策略撤销会撤销读取；更新版本不会改写进行中 Run 的冻结输入。

### 成本

- 每次解析平台输入使用按用户主键的订阅连接查询和一次按 file_id/lineage 索引的批量文件查询（最多 100 项）；自定义副本审批按 file_id 批量核对。无新后台轮询、进程或依赖，Worker 扫描复用原有资源限制。目录/版本接口分页。
- 版本文件只存一份，用户添加不复制、不占用户配额；只有自定义副本计入本人配额。旧版本按 `trim_file_versions` 清理，但当前发布版本、自定义 base、活动草稿及 `run_asset_refs` 的冻结版本必须保留；保留检查使用有索引的 EXISTS，单次清理不超过 gc_batch_size；这些引用可使保留数超过普通版本上限。管理员软删除保留系统版本的历史身份与内容，副本不丢失。
- `AILAB_PROFILE=secure` 无新增不安全默认：系统所有者无密码/登录 token 且默认 disabled；平台版本仍走既有 Worker scanner/隔离解析，secure 原有真实 scanner 与服务凭证校验继续适用。不新增共享读取 token 或绕过扫描开关。

## 内容来源

- 首批内容来自许可允许再分发和修改的高 star 仓库（MIT/Apache-2.0/BSD/CC-BY），记录 `source_url` 与 `license`；源码可见但不可再分发的（如 Anthropic 的 docx/pptx/xlsx/pdf）不采用。由管理员通过新后台导入，不写死在代码里。

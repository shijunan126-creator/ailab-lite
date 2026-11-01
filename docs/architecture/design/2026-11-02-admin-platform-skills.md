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

### 接口（草案）

- 用户：`GET /v1/platform-skills`（广场，含 added/customized/update_available），`POST /v1/platform-skills/{id}/add`，`DELETE /v1/platform-skills/{id}/add`，`POST /v1/platform-skills/{id}/customize`（开草稿），`POST /v1/platform-skills/{id}/reset`。`GET /v1/skills` 把已添加的平台技能并入列表（`kind: "platform"`）。
- 管理员：`GET/POST /v1/admin/platform-skills`、`PATCH/DELETE /v1/admin/platform-skills/{id}`、`POST /v1/admin/platform-skills/{id}/versions`（上传新版本）、`POST /v1/admin/platform-skills/{id}/publish/{file_id}`（回退）。
- Run 输入：已添加且启用的平台技能以与个人技能相同的结构进入 `personal_skills`（Agent 无需改动），内部取文件接口允许读取 `purpose="platform_skill"` 的已发布/曾发布版本。

### 成本

- 每次 Run 多一次按用户索引的连接查询（订阅数通常 <20）；列表接口有界。版本文件只存一份，不按用户复制，存储随管理员版本数增长，旧版本按现有 `trim_file_versions` 规则保留。

## 内容来源

- 首批内容来自许可允许再分发和修改的高 star 仓库（MIT/Apache-2.0/BSD/CC-BY），记录 `source_url` 与 `license`；源码可见但不可再分发的（如 Anthropic 的 docx/pptx/xlsx/pdf）不采用。由管理员通过新后台导入，不写死在代码里。

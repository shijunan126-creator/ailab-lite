# 首条消息与会话原子创建（2026-11-09）

- 用户报告 `/c/cnv_bf66cafddac34d6a8b4a` 空白，原有 PRO 正文和失败记录消失。证据：Conversation 已创建，但 `runs=0`、`run_events=0`、`queued_messages=0`；首条消息返回 `429 too many active runs for this user`。工作树 `.worktrees/first-message-admission`，分支 `fix/first-message-admission`，base `origin/main@e47d22d8`；主工作区既有修改保留。
- 已修复：新增 `POST /v1/conversations/start`，在一个用户锁和事务内完成 Run 准入、Conversation 与首条 Run 创建，失败回滚；同一 `client_send_id` 重放幂等，内容变化 409。Portal 新会话首条发送改走原子接口，并让已发送项保留到导航完成。根因、验证和边界见[记录](updates/2026-11-09-first-message-admission.md)。
- 合并与部署：PR [#170](https://github.com/shijunan126-creator/ailab-lite/pull/170) 的 head `a3d49234` 九项 CI 全绿，squash merge 为 `main@ad756f92`；已增量部署 `release-ad756f9`。Portal `/login`、API `/readyz` 返回 200，OpenAPI 已注册新接口；12 running / 10 healthy，数据库 revision `0063`。
- 验证：定向 SQLite 回归、Portal 272 项测试与生产 build、完整 E2E 通过。完整本机 Python 套件仍有 16 项 Office/document 运行时环境失败，不在本改动路径。
- 边界：历史空会话不会自动恢复；待发送 outbox 仍只在浏览器内存，完整刷新会丢失未提交的失败提示。下一步等待用户实际使用反馈，不推进其他路线图项。

---

# GenUI 主动使用与分档组件（2026-11-08）

- 用户要求：模型更主动但不滥用 UI 组件，GenUI 初始提示放宽到约 3000 token；新增截图式“色块 + 说明”分档组件。工作树 `.worktrees/genui-proactive`，分支 `feat/genui-proactive`，base `origin/main@34b1e58e`；主工作区既有修改保留。
- 已实现：索引按内容形状触发并写明不用界面的边界；13 个常用组件说明直接进系统提示（其余仍按需 `ui_components`）；语法与对错规则移入索引，工具指南只剩交互；新增 `Segments`。根因、token 与评测见[记录](updates/2026-11-08-genui-proactive.md)。
- 追加：模型目录支持可选 `session_header`，可接入要求会话头的 OpenAI 兼容端点（OpenCode Go）；“为什么”类问题按内容形状判断（因果链可用 Flow），不再整类列为纯文字。最终提示词经 OpenCode 复测：主动 9/9、多余 UI 0/14、闭合块 15/15 合法。
- 验证：真实模型主动用例 5/8 → 9/9（最终版），未缓存输入 113,912 → 32,425。Portal 270/build、Ruff、全套 SQLite 通过（唯一时序抖动已修），详见记录。
- 下一步：用户已授权提 PR、CI 通过后合并，并一次性部署本机。
---

# 网页图片短 ID 与无落盘代理（2026-11-08）

- 用户授权按前序讨论实施；工作树 `.worktrees/web-image-refs`，分支 `feat/web-image-refs`，base `origin/main@e3122ae3`。主工作区修改保留；用户随后追加授权提 PR、CI 通过后合并并增量部署。
- 已实现：16 字符图片 ID、API 地址去重/attempt 围栏、候选 24h 与已采用消息引用、分享路径权限、分叉保留与引用回收；列表缩略图/大图按需流式转发，不再持久化图片。新链路取消签名 key 要求，保留旧路径兼容。
- 检查与边界统一见[实现及验收](updates/2026-11-08-web-image-refs.md)。真实模型已搜索配图并在后续一轮复用 ID，浏览器桌面/手机浅深色与大图通过；最终图片/索引回归 SQLite 22/1 跳过、PG 23/0；Portal 267/build、Ruff 通过，全套首轮失败与复核边界见记录。
- 下一步：提交 PR，CI 通过后合并并增量部署最新 main；本轮临时验收环境已清理，不重复部署验收，不推进其他路线图项。
- 前序 UI/技能 PR #165 的范围与历史验收见[原记录](updates/2026-11-08-ui-polish-media.md)。

## 上一轮交接（2026-11-08 文件输入 PR #161 已合并）

- 最新授权：用户在 PR #161 上问预览开关是否会让页码错位；同意按“每种格式一套编号”修复后，要求提 PR、合并并部署最新版。PR head `4338d69d` 的 13 项 CI 全绿后 squash 合并为 `main@2bf86144`。
- 本轮完成：Word 页图只走服务端；PPT 服务端导出包含隐藏页；修复 CI 的两个根因（Portal `jszip` 锁文件不一致、`get_storage` 关闭后并发首次创建的竞争）。根因、验证和风险见[页码一致性记录](updates/2026-11-07-document-page-numbering.md)。
- 验证：Ruff 通过；PostgreSQL/Redis 全套 2739 通过 / 69 跳过；E2E 193 通过 / 15 跳过，唯一失败是旧的 Word 浏览器截图期望，已改并重跑通过。
- 剩余：已渲染过、并且含隐藏页的 PPTX 不会自动重渲（页图缓存没有渲染器版本）；本机现有 PPTX 都没有隐藏页，不受影响。listener 转换路径已在合并后的 sbx-office 镜像里实测；Portal 浏览器路径没有真实跑过。
- 下一步：等用户使用反馈。若要处理旧 PPTX 页图缓存，先给 `RenderedPage` 和文件预览元数据加按格式划分的渲染器版本。

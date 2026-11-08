# 首条消息与会话原子创建（2026-11-09）

## 范围与基线与根因

用户报告打开 `/c/cnv_bf66cafddac34d6a8b4a` 时页面空白。当时已有两个活动 Run；重新进入后，PRO 正文和发送失败记录都不见了。工作树 `.worktrees/first-message-admission`，分支 `fix/first-message-admission`，基线 `origin/main@e47d22d8`；主工作区既有修改未动。

数据库证据显示该 Conversation 在 `16:38:54.577` 已创建，但 `runs=0`、`run_events=0`、`queued_messages=0`；首条消息在 `16:38:54.595` 返回 `429 too many active runs for this user`。旧前端先创建空 Conversation，再提交首条消息；准入失败后，失败项没有绑定到该 Conversation。进入 `/c/...` 后，消息和待发送项都被过滤，因此只剩空会话。待发送队列原本也只保存在浏览器内存。

## 修复

- API 新增原子接口 `POST /v1/conversations/start`：在同一用户临界区和事务中检查 Run 准入、创建 Conversation、创建首条 Run。429、422 或后续失败整体回滚，不留下空 Conversation。
- 首轮发送复用抽取后的 `_start_turn`；同一 `client_send_id` 重放返回同一 Conversation 和 Run，内容变化返回 409。原 `/v1/conversations/{id}/messages` 继续复用同一发送路径。
- Portal 新会话的首条发送改走该接口；`pendingConversationId` 在 `stage === "sent" && conversationId === null` 时仍返回 `null`，让发起页保留已发送项直到 `takeSent` 完成并导航到新会话。若过早切到新会话 ID，新会话不会导航，CI E2E 会超时。
- 新增 `platform/tests/test_first_message_atomic.py`，覆盖准入拒绝不建会话、创建/重放幂等及内容变化 409；Portal `tests/outbox.test.ts` 覆盖拒绝后留在新会话页、重试只建一个会话和旧失败项归属。

## 验证、合并与部署

- 初始 PR head `427e174c` 的 E2E 暴露了 Portal 已发送项过早消失的问题；修复后 head `a3d4923428be6d6f81f33f32781b18165dff873f` 的 9 项 CI 全部通过，无评论/评审。
- PR [#170](https://github.com/shijunan126-creator/ailab-lite/pull/170) squash merge 为 `main@ad756f924e138aca096ba29bd01225d5d0fbd6e9`；合并树与 CI head 一致。
- 合入前定向 SQLite 回归通过；Portal 272 项测试和生产 build 通过；完整 E2E 通过。完整本机 Python 套件仍有 16 项 Office/document 运行时环境失败，不在本改动路径；另有 4 项 `defusedxml` 开发依赖缺失，安装后消失。
- 已增量部署 `release-ad756f9`：platform 镜像继承 `release-e47d22d` 并只覆盖 Python 源码，Portal 重新构建，切换 api/broker/worker/agent/portal 五个服务，其余 7 个容器 ID 不变。结果 12 running / 10 healthy，数据库 revision 保持 `0063`；Portal `/login` 200、API `/readyz` 200，OpenAPI 已注册新接口。
- 按本机部署约定未备份，未在部署阶段重复测试、浏览器或真模型验收；资源限额未变。

## 未覆盖与下一步

- 历史已产生的空 Conversation 不会由本次修复自动补消息；需要用户删除或重新发送。修复保证新的准入失败不再生成幽灵空会话，并在当前页面保留失败反馈。
- 待发送 outbox 仍只在浏览器内存；完整刷新会丢失尚未提交的失败提示。未做跨刷新持久化或多标签页协调。
- 未推进其他路线图项。后续按用户实际使用反馈决定是否需要持久化 outbox，或清理历史空会话。

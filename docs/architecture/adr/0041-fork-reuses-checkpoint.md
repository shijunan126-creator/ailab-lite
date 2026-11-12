# ADR 0041：同模式分叉沿用被分叉轮次的上下文

- 状态：已实施（2026-11-12，分支 `feat/fork-reuse-checkpoint`，base `main@cf758dd1`）
- 相关：[记录](../../progress/updates/2026-11-12-fork-checkpoint.md)、[前缀缓存设计](../design/2026-10-31-prefix-cache.md)、[ADR 0030](0030-prefix-stable-model-requests.md)、压缩 HLD §41/§88

## 背景

分叉（`POST /v1/conversations/{id}/fork`）会复制轮次记录，但原来不复制 checkpoint。分支第一轮找不到父快照，记 `rebuilt`，从数据库把历史拼成一问一答的纯文本：工具调用和结果全部丢掉，请求从第一条助手消息起就和原请求不同。分支又换了会话 ID，而供应商按会话头缓存（OpenCode Go 换会话头后同一前缀命中为 0）。真模型实测分支第一次调用命中 0。

## 决定

1. **同模式分叉复制被分叉轮次的完成快照**，作为分支叶子轮自己的 checkpoint（`checkpoints/{新run}/1-*.json.gz`，`RunCheckpoint` 一行，`expires_at=NULL`）。分支下一轮走与会话内续轮相同的快路径：请求 = 原轮最后一次请求 + 新消息。分叉到压缩之前的轮次，拿到的就是那一轮当时的上下文。
2. **只复制下一轮会读的字段**：消息、工具目录、纪元/ABI、prompt anchors、交付记录、计划、记忆快照、`context_sources`。租约、后台任务、待执行工具、文件等待、工具日志、已载技能留在源 run。
3. **模型可见的字节不改。** 结构化引用换成分支副本：图片部分的 `capture_id`、`context_sources` 的 `run_id`。只出现在正文里的源 run ID 不改写（改了前缀就变），记为 `run_aliases {源run: 分支副本}`；Agent 在解析压缩摘要来源和 `recall_history` 的 `run_id` 时换成分支副本。分叉的分叉按映射链合成。
4. **分支沿用血缘根会话作为供应商会话头**（`cache_session`：沿 `forked_from` 找到最早的会话 ID，根会话被清除也只用其 ID）。同一前缀只在同一会话头下可靠命中。
5. **不能复制时退回重建**：跨模式、轮次未成功、无快照/已过期/未完成、来源失效、引用了不随分叉复制的截图（v1 的 10 分钟临时截图）、`context_sources` 指向分叉路径外的 run、副本超过 `checkpoint_max_bytes`。

## 后果

- 跨模式分叉仍整段不命中：规则和工具列表在最前面。不为此把模式规则挪到后面或合并工具列表（每个 Chat 请求都要多带 Work 的工具，模型更难守住模式）；只多一次冷启动。
- 存储：每次同模式分叉多一份 checkpoint（通常几十到几百 KB，上限为 `checkpoint_max_bytes`），计入分叉的 2 GiB/10000 对象上限。生命周期与普通叶子相同：分支下一轮成功后 7 天到期；分支清除时按 `checkpoints/{run}/` 前缀删除；文件来源失效时随轮次删除；提交前失败由分叉自身清理，崩溃留下的对象由孤儿扫描回收。
- 成本：分叉时在用户锁内多一次 checkpoint 读取、解压、JSON 解析和重新压缩，量级与 checkpoint 大小成正比（默认上限 4 MiB）。
- 修订压缩 HLD §41 “fork/edit ancestry change 必须重建”中 fork 的部分；§88 “分支之间不共享快照”不变：分支拿到的是自己的副本。

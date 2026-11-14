# 文档索引

想知道怎么工作，先读 [AGENTS.md](../AGENTS.md)。它的路由表告诉你改哪个模块该读哪份文档。

## 在哪找

| 想知道 | 看这里 |
|---|---|
| 现在有什么在做、暂停了什么、下一步做什么 | [progress/current.md](progress/current.md) |
| 还没完成的目标和验收标准 | [progress/roadmap.md](progress/roadmap.md) |
| 某个能力做到哪一步、代码在哪 | [architecture/status-matrix.md](architecture/status-matrix.md) |
| 某个模块怎么组织、怎么扩展 | [modules/](#模块) |
| 踩过的坑 | [HANDOFF.MD](../HANDOFF.MD)，按模块分节 |
| 为什么这样设计 | [ADR 索引](architecture/adr/README.md) |
| 用量账本的合同 | [architecture/usage-ledger.md](architecture/usage-ledger.md) |
| 备份与恢复 | [architecture/runbooks/](architecture/runbooks/) |
| 多机目标架构（冻结副本，只作参考） | [architecture/README.md](architecture/README.md) |

## 模块

| 文档 | 范围 |
|---|---|
| [agent.md](modules/agent.md) | Agent 循环、模型调用、工具目录、提示词、上下文与压缩、前缀缓存、联网工具 |
| [api.md](modules/api.md) | 路由与鉴权、数据模型与迁移、Run 状态、outbox、配额与账本、会话/分叉/定时/搜索 |
| [files.md](modules/files.md) | 上传、扫描解析、派生资源、文档输入、交付件、资料库 |
| [sandbox.md](modules/sandbox.md) | Broker、租约、沙箱镜像与办公技能、渲染沙箱、受控联网 |
| [memory-skills.md](modules/memory-skills.md) | 记忆、技能 |
| [portal.md](modules/portal.md) | Portal 结构、流式渲染、GenUI、预览查看器 |
| [deploy.md](modules/deploy.md) | 部署、配置、模型目录、镜像、观测、备份 |
| [testing.md](modules/testing.md) | 测试分层、CI、E2E、真模型场景 |

`architecture/design/` 里留下的是 ADR 装不下的设计取舍，现状以模块文档为准。

## 术语

| 词 | 意思 |
|---|---|
| 会话 | 一串连续的对话（conversation），可以分叉 |
| Run | 一轮用户消息触发的一次执行，有自己的状态机、预算和 checkpoint |
| Chat 模式 / Work 模式 | Chat 只用文字回答，不起沙箱；Work 在沙箱里干活、交付文件。模式在会话开始时定下，要换就分叉 |
| 附件 | 用户随消息发送的文件 |
| 交付件 | 模型交给用户的文件，有版本，可以多轮修改 |
| 资料库 | 项目里的文件、文件夹、版本历史和回收站 |
| 派生资源 | 预览、缩略图、OCR、页图等由文件生成的内容，缓存在 `derived/{file_id}/` |
| 沙箱 | gVisor 隔离、没有网络的执行环境，由 Broker 管理 |
| 租约 | Broker 分给某个 Run 的沙箱使用权，有续租和闲置回收 |
| 前缀缓存 | 模型提供方对请求前缀的缓存。请求只追加不改写，才能持续命中 |
| 上下文边界 | 允许重建 system 内容的时刻（压缩、换模型、权限撤销、重建），记录为 `agent.context_boundary` |
| 记忆 | 账号或项目级别的长期事实和偏好，每个作用域一份有上限的文档 |
| 技能 | 一个 `SKILL.md` 加资源的能力包：基础技能随镜像发布，平台技能由管理员维护，个人技能属于用户 |
| outbox | 和业务变更在同一事务里写入的待投递消息表，由 dispatcher 投递 |

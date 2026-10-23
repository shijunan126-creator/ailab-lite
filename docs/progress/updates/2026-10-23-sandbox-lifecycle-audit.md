# 沙箱生命周期与当前问题核对

## 范围与基线

按用户要求审查创建/删除对话与沙箱的关系，并汇总 `current.md` 的当前问题。本轮只读代码、执行隔离回归/探针、更新接续记录，不修产品代码、不提交/推送/部署。工作树 `/Users/michael/Developer/ailab-lite`、分支 `feat/remaining-work`、HEAD `6719eee17ffa5c3cbe82c26f0b49ed56beed91f6`，本地跟踪 `origin/main@6410fe9`；此前资料库表格、流式缓冲和模型容量改动，以及原有 `.DS_Store` 全部保留。个人运行环境明细只存本机记录。

## 已有生命周期

| 触发 | 当前代码行为 |
|---|---|
| 新建对话 | 只创建业务记录；首次调用需要沙箱的工具时才向Broker申请，普通文字对话不占沙箱 |
| 每轮完成/异常/取消 | Agent `finally` 取消心跳和预览任务，调用Broker释放；正常完成路径交付产物并保存工作区 |
| 同一对话续聊 | 新沙箱恢复前轮最新有效快照；非永久绑定一个容器，新快照保存后清理旧工作区 |
| 等待用户回答 | 强制尝试快照，释放容器后暂停；回答/续跑重新检查容量和会话存活 |
| 删除对话/项目 | 同用户事务锁内将所有非终态Run改为canceled，软删对话；Agent轮询观察并释放沙箱；已登记资料库产物保留 |
| 进程故障/释放失败 | Broker持久租约与资源标签对账；删除失败保留releasing与容量占用，退避重试，确认资源消失后才released |
| 保留期 | 默认租约20分钟、每60秒续租、最长4小时、每10秒对账；工作区默认3天按小时扫描；终态checkpoint7天；软删对话30天后按批清理，保留用量账本 |

以上是当前代码默认契约。快照失败会显式警告/中断，不能保证未保存文件仍在。清理周期和批次会使物理清理晚于保留期边界，删除接口204不表示资源和对象已同步擦除。

## 确认的缺口与实际边界

1. **删除时等待准入/创建的取消未贯通。** `AgentLoop._acquire_lease` 直接等待 `broker.create_lease`；只在429返回后的重试间检查取消，HTTP等待本身没有取消轮询。Broker在 `create` 开始读取一次Run，随后准入队列/创建未重查业务状态。隔离探针令Run已取消、Broker等待准入闸门：100ms观察内Agent仍等待、取消检查0次；放行后仍返回租约。实际等待受准入默认300秒与外部请求截止约束。后续Agent事件栅栏和finally会阻止业务继续并尝试释放；如果Agent也退出，需等租约/对账兜底，不能声称删除后2秒必定回收。应在Agent和Broker两端贯通取消，创建前后重查业务状态，并保证已创建资源的可靠释放；单独取消客户端HTTP仍不足以保证服务端同步创建被撤销。
2. **Docker在Broker停机期间没有原生TTL自动删除。** `DockerSandbox.renew` 仅保存过期元数据，回收由Broker执行。Agent故障而Broker健康时可等待租约到期清理；若两者同时长期不可用，容器可能继续存活至Broker恢复。这是当前单机控制面的已知边界，不能写成已提供独立故障兜底。
3. **删除/保留契约需对用户说清。** 对话删除保留资料库产物，工作区/断点/历史按不同期限清理，问答中断允许长期回答不等于工作区永久存在。现有代码会在工作区恢复失败时显式中断或提示，不应描述为无限期无损续聊。
4. **外部验收未完成。** 真实OSB全部恢复组合、扫描/S3/OSB联合长期负载、目标机完整容量、生产监控、公网边界/gVisor、不可变镜像digest、企业业务模板与golden、真实付费vision/摘要质量仍按既有报告保留。本轮针对性回归不能替代这些验收。

## current.md 的问题与本轮整理

此前顶部已记录两项未发布修复，但下方仍使用“目标与授权”“本轮最终验证”“下一步”称呼旧T07/T19集成结果，容易混淆当前审查授权、历史基线和最新验证。本轮将这些段落标明为此前/历史，新增当前生命周期审查和取消路径缺口；保留历史证据链接与全部未验边界。原文已有明确测试基线，不把历史计数称为造假或失效。

现阶段最直接的交付问题仍是资料库表格、流式停顿和模型容量修复尚未提交/部署；用户运行界面不会因为本地测试通过自动切换。本轮没有激活个人模型目录或自动重跑付费任务。

## 当前验证

命令（platform目录，GNU coreutils/findutils PATH）：`AILAB_TEST_REDIS_URL=redis://127.0.0.1:16475/15 .venv/bin/pytest tests/test_conversations.py tests/test_workspace_ttl.py tests/test_workspace_previews.py tests/test_runtime_resume.py tests/test_runtime_protocol.py tests/test_broker_lease.py tests/faults/test_broker_recovery.py tests/contracts/test_lifecycle.py -o addopts='' -q`。

结果：**110 passed / 8 skipped / 12.95秒**，跳过为PG专用生命周期契约；Redis为本轮临时实例，SQLite为测试独立库，未访问开发数据库。覆盖续聊/过期快照、删除隐私与取消、waiting_user取消、恢复/心跳、Broker重启/Redis丢失/删除失败重试、分批对象GC与保留账本。仅有既存Starlette/httpx弃用警告。

取消准入探针只使用Fake Broker与隔离设置，没有启动沙箱或模型。原始本机结果在 `/tmp/ailab-lifecycle-audit/`。本轮没有重复全量Python/Portal构建或真实Docker破坏性验收；这次产品代码未变。已关闭临时Redis，不改现有运行服务。

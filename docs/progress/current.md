# 文件读取与预览优化：完整实施目标进行中（2026-11-05）

- 最新指令：全部实现并使方案达标。目标持续进行，不以局部修复结束；仍只授权本地实现、必要迁移及验证，不push/PR/合并/部署，不操作既有栈/业务数据，其他暂停事项保持。
- 工作区 `.worktrees/document-input-lifecycle`，分支 `feat/document-input-lifecycle`，fetch核对base `origin/main@fdc0a05f68c89aa884ce3682939f70e0337c45c4`。既有工作区/dirty保留；四份指定资料已完整带入。
- 第一组产品提交f9aa2279（页图完整性/MIME与截图桥接回收），交接f46c64f4。其历史全套通过，不能冒充后续改动的验收。
- 本组容量基础产品提交159de80b：新增0053派生容量账本、预留/暂存/业务事务发布、引用保护、持久回填/回收基础、Worker/Broker内部scope及客户端。账本+全部迁移双库116通过，服务身份40通过，Ruff通过。**尚未接入所有生产写入、失效与周期调度，未宣称全量容量已生效。** 结果/失败根因/剩余清单统一见[实施记录](updates/2026-11-05-document-input-lifecycle.md#完整目标续作容量基础2026-11-05)。
- 10个确定性合成样本与机器可读门槛已冻结；真实1CPU/768MiB Office容器9个native场景基线完成并删除自建容器。原始样本/设备/基线在仓库外 `/private/tmp/ailab-document-validation-oghcidq0`；自己的PG25432已停并清数据/日志。默认Docker socket不可用只是context问题，已找到可用daemon；所有既有栈保持。完整上传/浏览器/模型基线仍须在相应生产改动前补测。
- 下一步：先补齐端到端/首开基线；接有界generation bundle记账（避免每原生块HTTP）、生产写入/回填/缓存失效，再实现安全/派生状态与Chat/预览懒准备、Work原件/受限OCR、新旧工具目录、浏览器优先/CAS后备。LocalStorage前缀分页仍可能全树遍历，必须修正其扫描CPU/I/O边界。
- 完整Python/PG/Redis/E2E/Portal与真实模型/故障/50轮性能稳定性留到最终集中验收，不拿mock或历史结果代替。不触碰旧run schema/消息前缀，未完成状态与账本前不启用新链路。

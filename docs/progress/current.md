# 文件读取与预览优化：完整目标进行中（2026-11-05）

- 授权：全部实现并使方案达标；仅本地实现/迁移/验证，不push/PR/合并/部署，不操作既有栈/业务数据，其他暂停事项保持。
- 工作区`.worktrees/document-input-lifecycle`，分支`feat/document-input-lifecycle`；最新fetch的main仍为`fdc0a05f`。四份指定资料已完整带入，其余dirty/工作区未触碰。此前提交与历史检查见[统一实施记录](updates/2026-11-05-document-input-lifecycle.md)，不能冒充最终验收。
- 本组在`f4f8708`之上已接通新普通输入安全ready/三类派生absent、上传零派生/零Office调用，保留旧v1排队任务与专用导入/成品流程；Chat与旧读取工具消费时才准备native，图片消费时准备表示，准备失败原件仍可Work。普通文本有界流式解析，Office按实际类型走两对象V4代际与批量记账。见[最新证据与失败根因](updates/2026-11-05-document-input-lifecycle.md#安全入库与消费准备已接通2026-11-05续作)及[ADR0034](../architecture/adr/0034-safety-ready-and-lazy-native-input.md)。
- 已跑真实ClamAV/API/Worker/Broker/Docker上传与按需native；应用冻结基线`28b8576`又核对12次安全上传在服务运行中Outbox/transfer/预留/临时文件/model_usage均0；SQLite相关组256通过/14跳过，PostgreSQL相关组107通过；最新图片/安全边界19通过；Ruff check与595文件format通过。所有结果是阶段证据；完整Python/E2E/Portal/真实模型与50轮资源稳定性尚未完成。
- 账本启动/周期回填与未发布写入清理已接；**所有生产writer、TTL业务失效/代际/规范截图引用回收尚未完成，不能宣称全平台容量控制已达标。** V2目录/读搜导航已接Agent与API；新Work选V2并只交原件引用，lease绑定的工作区图片OCR已接平台执行器和持久预算/槽，但尚未统一验证。Chat默认仍是旧目录；显式页/region OCR任务与独立缓存已接线但未验证，view/分档与生产资源合同仍待接齐。
- 最新续作：[Chat显式OCR接线](updates/2026-11-05-document-input-lifecycle.md#chat显式ocr页与区域任务接线2026-11-06实现中)。用户要求减少中途测试、最后集中全测；本组只有代码实现/格式化，尚未验证，不复跑阶段套件。
- 下一步：继续原生结构/真实PDF范围与输入分档，补发送重试前首次准备600秒；把native/render/旧OCR接入同一持久公平调度，完成输入页图/capture生产记账后统一验收OCR新链路；补浏览器首开基线后接capture优先/CAS后备，同批补全生产记账/TTL/历史引用。不要把PDF3000页部分解析称为真范围懒加载。
- 原始冻结样本/设备/新旧阶段测量在仓库外`/private/tmp/ailab-document-validation-oghcidq0`；最新真实测量含工作树变化，尚非最终冻结提交验收。部分重复上传观察比旧缓存副本慢，必须继续量测/定位，不放宽门槛。自己新建的数据库/栈/镜像本组检查后清理；不触碰既有Docker栈。

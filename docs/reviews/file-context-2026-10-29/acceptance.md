# 文件上下文联合验收（2026-10-29）

基线为`docs/file-context-ocr-design-20261029`、`e71a60f8b4e0ea139b12be80d14cb38a627a2db7`加本轮未提交工作树。未推送、PR、merge或部署。原始设计/源审计及旧记忆证据保留。此表按07模块逐项记录，不以通过数量替代合同或质量验收。

状态含义：“合同”仅说明程序路径/隔离测试；“真实容器”有真实执行环境；“外部未验”需要真实NPU或模型；“部分”还存在实现或验收缺口。本轮后续使用真实DeepSeek/RapidOCR并补校准队列、OCR复制、恢复墓碑，详见[核心记录](../../progress/updates/2026-10-29-file-context-core-validation.md)。本轮按用户要求只测核心84项，不重复以下历史全套。最终全套结果统一见[实施记录](../../progress/updates/2026-10-29-file-context-ocr-implementation.md)。

| ID | 当前证据和范围 | 状态/边界 |
| --- | --- | --- |
| C01 | 历史摘要显式带旧摘要和新增prefix，API CAS/epoch；本轮连续分块摘要保存cursor | 实际DeepSeek旧摘要＋新Delta、约束/更正日期留存通过；非任意语义保真保证 |
| C02 | 程序游标、来源frame和原始附件关系独立存储 | 部分；500轮10代真实保真/token对照未验，跨frame校准队列/字符offset/精确evidence确认已实现并回归；DeepSeek共5次成功压缩，未做500轮10代质量承诺 |
| C03 | 长工具输出改连续字符分块，非头尾；`test_long_runs.py`含分块验收 | 实际DeepSeek连续两次本轮压缩保留中部ZX-7419；非普遍质量保证 |
| C04 | `faults/test_long_run_recovery.py`，原有非重放/交付账本保留，checkpoint激活状态恢复 | 合同；未做真实模型长任务 |
| F01 | view_attachment返回已授权媒体引用，图片pending checkpoint恢复转实际媒体 | 合同；角落数字真实视觉质量未验 |
| F02 | `test_reference_order_and_ambiguity_are_structural`及旧分叉/附件回归 | 合同；自然语言质量不等于程序定位 |
| F03 | 120页PDF第85页、Chat尾页120可读，100k块查询 | 合同；整份答案与引用质量未验 |
| F04 | 上游15项和真实系统reader四格式；段落/表格、sheet/单元格、slide/page锚点 | 真实容器；嵌图语义/复杂图表需视觉，不声称完整布局解析 |
| F05 | 模式共享file_id/派生物，Work默认仅元数据；原文件复制沿用Broker | 合同；Chat→Work→Chat真实模型编辑交付未验 |
| O01 | native/no_text质量标识、选页OCR；短原生文字不自动判扫描；OCR不静默替换非空原生块 | 部分；旧OCR错层/歪斜/混合真实集质量未验 |
| O02 | `test_rapidocr_metering_without_billing_and_duplicate_delivery`、按页持久结果、故障状态/deadline | 实际CPU RapidOCR HTTP链路与私有复用已验；NPU/故障长稳仍未验 |
| O03 | 原付费vision OCR路径撤除；`test_upload_never_queues_paid_vision_ocr`；OCR不调摘要LLM | 实际CPU计量、文字导出、LLM调用0；无积分计费/累计用户限额 |
| S01 | 删除事务epoch、活动run取消、提交栅栏；原文/dream/embedding统一拒绝失效来源 | 合同；对象存储故障组合仍需真实环境 |
| S02 | API owner鉴权，私有源副本、精确分支关系；`test_conversation_tree.py` | 合同；无跨owner hash复用 |
| P01 | `test_document_indexes_are_bounded_at_100000_blocks`；历史4万行查询计划回归 | 双库查询计划；不是100k轮会话容量 |
| P02 | 专用执行器边界、模型manifest/provider强校验已实现 | 外部未验：NPU30分钟、profile、业务TTFT与资源曲线 |
| U01 | `e2e/test_file_context.py`桌面/390px真实上传、菜单、未配置失败及截图；原有预览/交付回归 | 浏览器；成功OCR导出由API替身合同验证，未作真实NPU浏览器成功链路 |
| R01 | 附件ordinal、AppliedMessagePosition、alias和歧义候选；应用插话才入定位 | 合同；模型理解“刚才那张”不是确定性承诺 |
| R02 | 关系不依赖摘要；fork/retry/edit迁移/模型上下文回归 | 合同；10代真实问答未验 |
| M01 | Chat完整性/预算准入，超限422；Composer每轮独立图5张；Work分块读取 | 合同；百页复杂Office质量/真实小窗模型未验 |
| M02 | 同ID原生/OCR结果跨模式使用；metadata策略与Chat全文门禁 | 合同；8K/16K/32K真实请求容量未验 |
| M03 | 复用catalog capabilities；`test_nonvision_chat_prepares_image_before_creating_run`及vision显式fallback | 合同；OCR执行器未配置明确失败，不猜模型名称 |
| T01 | 私有注册表、固定小入口schema、最多8激活工具、schema预算和checkpoint恢复 | 合同；500工具/100技能合成常量入口；真实模型选择质量未验 |
| T02 | 内置工作流要求按coverage/next_cursor分批全文；不安装OCR模型；选页渲染 | 合同；100图PPT/百页视觉全篇质量未验 |
| D01 | 同ID复用；同owner重复上传原生结果复制新命名空间并提交复查；`test_same_bytes_private_cache_copies_are_independent` | 合同；不同file_id的OCR页已按100页有界私有复制并实测，副本seconds=0不重复计推理 |
| D02 | 回收站/彻底删除分开；块/渲染/OCR表、派生对象、summary/checkpoint/工作区、事件/工具输出与旧交付结果正文清理和线上栅栏 | 部分；ops API墓碑导出/应用器及幂等恢复已验；清单仍须独立于备份保存，目标存储恢复未验 |
| N01 | 三个session required EP、disable_fallback、strict CPU EP配置；错误不上报NPU成功 | 实际CPU三session与CANN缺失拒绝已验；目标ORT-CANN兼容、节点profile/显存不足未验 |
| I01 | 输入框5张不进入provider通用路径；view_attachment文档页按预算传媒体 | 合同；超过5图及全篇视觉真实模型容量未验 |
| B01 | 固定ZCode源码/许可证/patch列表、原viewer保留；私有registry不进用户skills且加载优先 | 合同与真实reader；用户技能功能独立 |
| B02 | 同文件同范围冷/热/持久缓存[原始数据](../2026-10-29-office-performance.json)，真实系统容器资源[证据](real-reader.json) | 已实测小fixture；新Runtime未提速，非目标机容量 |
| U02 | catalog vision icon可访问，Composer切换时abort准备请求，非vision提示一次 | 合同+完整Portal浏览器回归；未作实际模型视觉质量 |

候选资源数字只是当前开发配置，不是目标硬件已验参数。系统reader使用独立镜像、每任务租约和文件命名空间；当前Broker组内有界并发，不等于完整多节点调度器已实现。所有mock/HTTP替身仅验证流程、权限、恢复与协议。

# 文件上下文、Office与OCR：核心补完及实测

用户本轮明确要求迁回AILab lite项目、规范命名，复用已有DeepSeek API和实际RapidOCR，只测最核心压缩链路，并继续完成可本机完成的缺口。本指令取代之前“没有真实模型条件”的判断，也缩小本轮验证范围：不重复全量双库/浏览器/30分钟压测。不push/PR/merge/部署。

## 位置与基线

完整工作树通过`git worktree move`迁至项目内`.worktrees/file-context-ocr`，分支改为`feat/file-context-office-runtime`，HEAD/base仍为`e71a60f8b4e0ea139b12be80d14cb38a627a2db7`。源代码、未提交设计/审计和旧Memory证据均保留。主目录的`fix/full-thinking-rendering@ab0e8d9`及视觉设计、个人修改未覆盖，仅在主目录current/HANDOFF增加入口。`.worktrees`和本机`.local`通过本地Git exclude隔离，不成为项目交付内容。

设计仍在`docs/architecture/design/file-context-ocr/`；证据规范为`docs/reviews/file-context-2026-10-29/`，旧引用已更新。旧Memory资料保留其原有分类，不把本轮新证据塞入Memory目录。旧全套测试日志中的当时绝对路径保留历史原貌。

本轮源清单为[followup-source.json](../../reviews/file-context-2026-10-29/followup-source.json)，814文件，内容摘要`7879116fb5078d37d4f287c68960ed14a6e9691094c976c6cb3e921341eadecb`；是dirty工作树清单，不冒充提交。前轮完整SQLite/PG/浏览器结果仅代表其历史基线。

## 本轮完成的缺口

1. **可续的来源重校准**：summary frame保存待校准handle和字符offset。API发出有界原文及精确evidence_id，Agent只确认实际放进该次摘要请求的前缀；预算减少、模型失败、checkpoint恢复都不把未送内容标为完成。后继frame继续原offset，不必再等第8代。错误/过期确认拒绝，原文不复制进队列。0036未部署迁移的新表同步增加JSON状态列，SQLite迁移/模型drift回归通过。
2. **OCR私有复制**：同owner、相同SHA/MIME、匹配引擎fingerprint的重复上传复用原生结果和OCR页。每100页读取/发布，Worker在事务外复制到新file命名空间，API复验来源、generation、目标状态和key再写索引；已删来源不再发布。副本页计`seconds=0`和copied_pages，不冒充新推理或重复计量。图片归一化display/thumb和已完成文字产物也复制；不同owner无共享命名空间。
3. **恢复墓碑应用器**：`file_source_tombstones.py export/apply`通过ops专属内部API导出/应用分页清单，保留回收站/彻底删除区别，跨owner冲突拒绝、重复应用幂等。恢复前应用后，来源立即不可读，永久删除走既有派生DB/对象意图/摘要/工作区清理。不让脚本直接写业务DB。用隔离库模拟恢复旧数据库，确认访问410且再次应用不增加epoch；未对个人部署执行恢复。
4. **真实模型暴露的提示合同问题**：原说明未明确next_actions/uncertainties是字符串数组，DeepSeek首次生成对象数组。程序拒绝且消费游标未推进。两种压缩现在共用实际Pydantic JSON schema，schema先计入预算，再分块；不靠宽松解析把错误输出当成功。
5. **权威副作用状态进入压缩**：仅有旧摘要和pending工具文本时，模型可能错误写“未交付”；压缩输入现在同时包含程序保存的已确认交付清单及总量，明确它优先于旧摘要，未展示全部时有总量可核对。原始约束、交付/非重放记录仍由程序独立保存。连续分块按真实估算收缩，不把字符数直接当token上限导致多余小调用。

## 真实DeepSeek核心证据

复用本机已配置的模型目录与独立密钥文件，只在独立验证进程读取；没有复制或打印密钥，没有使用用户真实聊天/文件。通过AILab真实OpenAI兼容transport、API usage reserve/settle、summary CAS和checkpoint调用，沙箱自由执行未启用。

[实测JSON](../../reviews/file-context-2026-10-29/deepseek-compaction.json)：5次成功调用，实际后端回报`deepseek-flash`，prompt_tokens14329、completion_tokens2742。包含2次跨轮合并、2次本轮工具记录压缩，以及1次针对权威交付状态的追加压缩；保留AL-219、XLSX/不发邮件约束、更正日期2031-06-18、工具输出中部ZX-7419，恢复checkpoint后已确认副作用/交付身份仍在。第2次跨轮只传旧摘要和未覆盖新turn，未重新消费前批turn。

首次schema失败另有1次已入账调用，见[失败记录](../../reviews/file-context-2026-10-29/deepseek-initial-schema-rejection.json)；总共6次真实模型调用，不藏掉失败成本。测试脚本曾误断言tool_log保留完整exit_code，实际合同保存compacted bookkeeping；已改正确断言并离线回查持久结果，没有为此重复前4次模型调用。最后追加1次模型调用核对确认交付状态，输出明确保留confirmed.xlsx及其已交付属性。

这是有限合成样本的功能/语义核查，不是500轮10代或任意复杂任务保真率。首代合成摘要未包含旧截止日，本轮确证的是更正日期进入后继摘要，不把它扩写为所有旧约束覆盖场景已验。复现入口`platform/scripts/verify_file_context_model.py --execute ...`；`--audit-existing`仅回查已有合成结果，不发起模型请求。

## 实际RapidOCR核心证据

独立本机OCR测试环境，固定RapidOCR`80b427d69bfc5b91a12913394875910296c6f315`，实际ONNX Runtime CPU执行器，三个session均为CPUExecutionProvider；显式`OCR_REQUIRED_BACKEND=cpu`，不是CANN失败回落。模型在执行器启动前预置，来自固定版本模型清单并逐个SHA256校验，Det/Cls/Rec约31.7MB；[模型来源与hash](../../reviews/file-context-2026-10-29/rapidocr-models.json)。没有在用户沙箱安装OCR权重，默认CANN策略未改。

[真实识别JSON](../../reviews/file-context-2026-10-29/rapidocr-core.json)：一张人工合成中文图，经真实Worker归一化→API OCR→HTTP executor识别→按页缓存/文字导出。合同编号AL-219、金额12345.67、日期2031-06-18精确读出；单页推理约1.03s。再次上传相同bytes复用私有缓存，副本推理计时0、无LLM调用，删除原文件不影响副本下载。另验证本机缺CANN时required-provider readiness立即失败，不继续CPU推理。

处理后Mac进程RSS快照约686MiB（不是峰值或NPU预算），不能由31.7MB模型权重推导运行时很小，也不能宣称能装进256–384MiB Worker。执行器已停，释放常驻内存；OCR仍不计积分、不限制累计用量，保留执行槽位和资源隔离。只验证1张清晰中文合成图，未冒充CER/WER、歪斜/低清/复杂表格准确率、NPU节点profile或30分钟稳定性。

## 集中回归与清理

本轮[核心回归](../../reviews/file-context-2026-10-29/core-regression.txt)84通过、21跳过，约16秒；覆盖文件/摘要来源、持久校准cursor、OCR分页私有缓存、恢复墓碑、长任务/压缩及SQLite迁移。跳过主要是未配置PG等外部条件。最后补齐只有OCR文字、没有原生text_key时的来源回查，相关文件整组17项通过（与上述84项重叠，不相加冒充101项）。Ruff422文件与服务导入检查通过。一次新测试将永久删除期望写成404，实际既有合同为410，已修正确断言。

用户明确缩小验证范围，本轮未重跑全量SQLite/PG、Portal build/浏览器、Office性能基准或容量测试；无新增UI改动，前轮UI证据按历史保留。真实OCR执行器已按PID、命令与cwd核对后停止，28784无监听；未启动/更改个人部署、PG、Redis或容器。模型、虚拟环境与合成验证库仅留在项目本地ignored目录，方便复用；密钥仍只在原个人配置文件。

## 仍有的边界

- 实际Ascend/CANN/ORT wheel兼容性、NPU执行profile、OCR代表性质量集、数百人/多节点容量和路由部署需目标环境，不拿Mac CPU或5次模型调用替代。
- 混合来源摘要仍采用保守整frame失效，未做无关条目的精细重建；本轮执行摘要保真仍有损，程序状态不依赖摘要作恢复依据。
- 新增墓碑应用器需要运维在备份代之外持续保留导出的清单，并在恢复API隔离期间先应用再开放流量；不能追回浏览器或外部模型已收到的数据。本轮只在隔离库验此合同。
- 大文件冷解析/RSS和Chat预算候选门槛沿用前轮实测边界；没有为本轮样本新设“性能已达标”结论。完整跨节点Broker调度器不属于这次核心文件接线的新增实现，不据当前有限并发声称数百人容量已验。

后续应在实际目标环境补NPU/质量/容量验收，再决定发布；当前没有远端或部署授权。

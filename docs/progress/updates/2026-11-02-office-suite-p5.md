# 办公 P5 验收（2026-11-02）

## 基线与结论

工作树 `.worktrees/office-suite`，分支 `feat/office-suite`，本轮产品/测试基线 `26316e1769627ddad7397970980c2a98e74b4674`，base `origin/main@bf0af4f`，未 fetch。进入工作树干净。本轮只新增验收/交接文档，未改产品代码、未 push、未开 PR、未部署。P5 **部分验证，未完成验收**。历史整合证据仍见[整合记录](2026-11-02-office-suite-integration.md)。

## 真实模型请求矩阵

本机已配置模型凭据，连通性探针 HTTP 200；密钥仅在进程内读取，没有写入证据。实际模型 `deepseek-flash`。复用 `tests/test_prefix_cache.py`、`tests/prefix_harness.py` 的 AgentLoop/CheckpointFake/ModelTransport 路线，在仓库外驱动真实 AgentLoop 与供应商流式调用。固定 tools/tool_choice 与 thinking=disabled（验收transport固定设置，未改产品默认请求设置）；检查 tools 不变、旧 messages 精确保留，只有显式 compaction 边界重建。resume 从最后一次模型请求前的真实 JSON checkpoint 恢复；供应商请求正文与恢复前相同。

**边界：不是五服务真实模型端到端矩阵。** Platform/业务计量为内存测试替身；DocRef 使用冻结 DOCX/XLSX fixture 文字，不经过真实 API 授权/索引。Chat/Work `load_skill` 使用真实包 loader；Work 导航由既有预选流程加载，模型再按需读取完整 viewer/SKILL.md。Chat 也读取导航和该完整资源；Chat 白名单仅开放 viewer 资源，不伪造大制作资源可用性。Work 另加载完整 references/docx-js-core.md。没有执行模型给出的命令。capture 请求真实到达 Agent 工具，但测试替身明确返回 no live browser，不冒称真实截图成功。Work review 使用真实宿主 fixture 的 artifact_checks 与第一张 JPEG 页图，Broker 传输及租约技能证明为受控替身；最初两次review只得到attestation接口错误，最终review_partial_local成功返回页图与正确的page_count=2/complete=false；只覆盖两页 PDF 中的第一页，不能判整件视觉通过。独立的真实浏览器 capture 测量见下节，使用 mock 模型。

以下每项为 **cached_tokens/prompt_tokens（百分比）**，包括工具后调用、摘要与恢复。首次加载/新大资源/压缩新基底单列，不将这些段从总账隐藏；first_load 不保证供应商绝对冷缓存，Work 复核已可能复用准备运行的缓存。

| 模式 | 上下文段 | 每调用 cached/prompt (%) |
| --- | --- | --- |
| chat | first_load | 0/2827 (0.000%); 2816/3276 (85.958%); 3328/4519 (73.645%) |
| chat | steady | 4352/4540 (95.859%); 4736/4889 (96.871%) |
| chat | docref | 4480/4681 (95.706%) |
| chat | capture_unavailable | 4480/4736 (94.595%); 4608/4825 (95.503%) |
| chat | resume | 4736/4889 (96.871%) |
| chat | compaction_summary | 4864/5003 (97.222%) |
| chat | compaction_cold | 2560/3227 (79.331%) |
| chat | post_compaction_steady | 3072/3241 (94.786%); 3072/3255 (94.378%); 3072/3269 (93.974%) |
| work | first_load | 5632/5810 (96.936%); 6912/7065 (97.834%) |
| work | steady | 6912/7086 (97.544%); 8448/8591 (98.335%) |
| work | docref | 7040/7227 (97.412%) |
| work | capture_unavailable | 7040/7284 (96.650%); 7168/7373 (97.220%) |
| work | review_attestation_error | 7424/8396 (88.423%); 8320/8484 (98.067%) |
| work | resume | 8448/8591 (98.335%) |
| work | compaction_summary | 8576/8715 (98.405%) |
| work | compaction_cold | 4736/5383 (87.981%) |
| work | post_compaction_steady | 5248/5397 (97.239%); 5248/5411 (96.988%); 5248/5425 (96.737%) |
| work | large_resource_cold | 4736/5794 (81.740%); 5760/9347 (61.624%) |
| work | large_resource_steady | 9216/9364 (98.419%); 9216/9382 (98.231%); 9216/9400 (98.043%) |
| work | review_attestation_retry_error | 4736/5814 (81.459%); 5760/5902 (97.594%); 5760/5990 (96.160%) |
| work | review_partial_local | 5632/5814 (96.870%); 5760/6372 (90.395%) |

| 范围 | Chat | Work |
| --- | --- | --- |
| 矩阵全部返回调用（含工具失败） | 50,176/57,177 = 87.756% | 168,192/179,417 = 93.744% |
| 稳定续轮（排除首次/新大资源/压缩摘要及新基底/首次页图评审） | 36,608/38,325 = 95.520% | 88,448/90,531 = 97.699% |
| 压缩后稳定续轮子组 | 9,216/9,765 = 94.378% | 15,744/16,233 = 96.988% |

稳定续轮加权总值两模式均 ≥95%，但不能据此宣称所有稳定段通过：**Chat 压缩后子组只有 94.378%**，三次分别 94.786%、94.378%、93.974%（以上逐调用表为实际值依据）。该组没有历史消息或 tools 改写；短前缀约3.2k prompt、缓存块3072，固定未命中尾部占比偏大。尚未用完全相同最终请求的独立复放确定供应商计数上限，因此只记录现象，不把推断写成根因已证实，不填充无用上下文抬高比例、不改生产合同。

额外准备调用：最初 Work 路线在租约技能证明处因测试替身缺接口返回错误；另一次修正页覆盖元数据的重试仍遇到此接口错误（均在逐调用表保留），最后补受控fixture证明接口后只复核review工具。该证明不是容器证明。该准备运行 15 次，94,976/106,157 = 89.467%，单独列账；主矩阵也明确保留review工具失败，不能按run succeeded认定所有工具通过。连通性探针另为 0/7。含准备运行及探针的所有已返回调用共 55 次，313,344/342,758 = 91.418%。没有配置可用于费用核算的供应商价格快照，本轮不编造货币费用。

## 产物与真实截图

四个确定性宿主制作/修改 spot check 使用 python-docx/openpyxl/python-pptx/pypdf；每份原件 SHA256 在修改及结构检查前后相同，产物由对应原生库重新打开，artifact_checks 无 issue。Word/PPT 将收入120改175；Excel保留SUM公式，经宿主 LibreOffice headless 重算，缓存值175；PDF重新打包后页数与源相同（2页）。

| 格式 | 产物字节 | 检查 |
| --- | ---: | --- |
| docx | 36,592 | 原生打开、源未改、结构检查通过 |
| xlsx | 6,672 | 原生打开、源未改、结构检查通过 |
| pptx | 28,274 | 原生打开、源未改、结构检查通过 |
| pdf | 3,367 | 原生打开、源未改、结构检查通过 |

这些是**宿主受控制作检查**，不是模型自主 Work、离线 sbx-office 镜像或复杂版式验收；真实模型四格式 Work 交付任务仍被容器不可用阻塞。PDF第一页确有页图送真实模型作局部 review；其余格式本轮只有结构证据，不宣称模型视觉验收。宿主初始测试缺 reportlab，改用仓库现有 PDF fixture；LO 会把单格SUM范围标准化，测试改为验证公式仍存在及重算结果，未改产品。

真实浏览器：复用 `e2e/test_office_native.py::test_live_work_file_native_agent_reveal_and_capture`，隔离 SQLite、Redis 16473/13、四服务18172–18175、Portal13172、mock sandbox/vision模型，真实原生DOCX、reveal及capture客户端协议。**1 passed**；claim发出到result HTTP200 **96.26 ms**，800×1035 PNG、base64 37,308字符，ok=true。包含客户端claim、frame截图与回传网络开销；仅一个小文档样本，不是p95或冷启动基准。测试插件只保留时间/尺寸，不保存令牌和图片。

## 资源与验证

本机 ARM64/Node v26.11.1。重新执行 `cd platform/portal && npm run office:build`，当前 bundle `18ef356ab076b7e1`；不是沿用进入时忽略目录中的旧manifest。

| viewer构建产物 | 原始bytes | gzip bytes（本地压缩估算） |
| --- | ---: | ---: |
| viewer.js | 4,243,581 | 1,343,562 |
| panel.css | 19,450 | 4,585 |
| pdf-worker.js | 992,316 | 289,061 |
| xlsx-worker.js | 133,095 | 38,494 |
| sheets.wasm | 4,345,449 | 1,641,654 |

包含字体、PDF字典和许可的完整asset目录 **14,589,860 bytes（13.914 MiB）**；viewer.js **4.047 MiB**。未测首屏HTTP实际压缩传输/heap/大文件上沿，不能把静态目录大小当首屏全量下载。

- `cd platform && PATH=<GNU tools> .venv/bin/pytest tests/test_prefix_cache.py tests/test_artifact_review.py tests/contracts/test_office_production.py -q`：**61 passed / 5 skipped**。5 skip为未配置真实Office镜像，不是通过。包含录制请求、模块去重/压缩/恢复、引用及review只读合同。
- E2E启动前置 `npm test`：**161 passed**；临时副本 `npm run build`：通过。没有重跑已完成的完整Python或完整E2E；本轮未改产品，最终只检查文档差异/引用。
- `docker info` 与本机专用 wrapper 的 `info` 在开始及结束均失败：默认socket不存在，专用socket无法连接。没有启动/修改VM或daemon。**sbx-office实际压缩/解压镜像体积、真实离线四格式Work和Portal Docker构建阻塞**；未进入Dockerfile构建，不拿Next build替代镜像验收。
- 临时Redis和E2E服务已停止；生成reader、临时原件/产物/截图/测试副本与原始日志清理，仓库只存摘要。

## 下一步与风险

P5保持部分验证。Docker恢复后补真实离线镜像四格式制作/交付及source hash/review、镜像体积和Portal镜像构建；真实模型需与真实API/Broker及在线浏览器合流，覆盖成功capture、DocRef授权、三代压缩/崩溃恢复和模型效果对照。Chat压缩后短续轮需进一步相同请求复放定位供应商限制，不能声明≥95%全面达标。本轮未发现可由产品改动修复且有根因证据的缺陷，故没有为缓存比例增加填充或放宽门槛。未测Linux资源峰值/并发、复杂模板宏、跨设备和持久独立视觉job。

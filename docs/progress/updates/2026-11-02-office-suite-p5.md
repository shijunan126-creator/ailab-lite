# 办公 P5 验收（2026-11-02）

> 本页前半为上轮历史证据；Docker阻塞及旧结论已由下方[本次续验](#docker-恢复后的续验本次结果替代上方旧阻塞结论)更新。

## 基线与结论（上轮历史）

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

## Docker 恢复后的续验（本次结果，替代上方旧阻塞结论）

进入基线 `d226ea5155c5582e63b5681fdb10f3f33156c9e7`，base 仍为 `origin/main@bf0af4f`，未 fetch；工作树干净。镜像从该基线构建；续验修复仅涉及 API 截图授权与 Agent 收尾消息，不改变两个镜像的输入。真实服务最终测试代码已提交为 `7e30b7dd2dbcbe01a12ced78b37b4141a634c057`（改动在提交前以同一内容验收）。专用 VM 已由用户启动；显式使用用户指定 Docker socket/config，独立 Broker owner、Redis 16479/15、SQLite 和四服务18270–18273。CLI 只在验收进程内将 adapter 的容器名前缀映射为 `c3-office-p5-*`，其余 Docker 参数与产品路径不变。未操作个人栈，未 push、PR 或部署。

### 镜像与离线制作

仓库根执行 `docker build -t c3-office-p5:d226ea5 -f images/sbx-office/Dockerfile .` 和 `docker build -t c3-office-p5-portal:d226ea5 -f platform/deploy/portal.Dockerfile .`，均成功。Portal 镜像内 Next 编译/TypeScript/静态生成成功。

| 镜像（ARM64） | OCI gzip 层合计 bytes | 逐层解压 tar bytes | image config digest |
| --- | ---: | ---: | --- |
| `c3-office-p5:d226ea5` | 1,256,155,027 | 3,403,670,016 | `sha256:fe77cc05a569fefd048ad6b50e2e72f7accfa93931640d42ad54459d015dd9ec` |
| `c3-office-p5-portal:d226ea5` | 118,793,857 | 356,327,936 | `sha256:82d39e4c81694b21694a3e944b2c2a98c116d71559cedf50aecc711443822913` |

统计口径为 `docker save` OCI archive 内所有 gzip layer 的压缩字节与流式解压 tar 字节之和，包含 tar 头/覆盖层；不是容器实际独占磁盘或 RSS。整个 archive 再 gzip 会压缩已压缩内容，不能称为解压镜像体积。

`AILAB_TEST_OFFICE_IMAGE=c3-office-p5:d226ea5 .venv/bin/pytest tests/contracts/test_office_production.py -q -s`：**5 passed，0 skipped**（exit 0），容器 `--network none`、1 CPU/1536 MiB；包含四格式制作/编辑/转换、office-read、源 SHA256 不变、实际 review helper，以及 HTML/LaTeX 离线生成。首次在构建标签尚未生成时启动合同检查的5个setup error另记为验收启动顺序错误，不是通过。

| 格式 | 合同耗时 s / cgroup 峰值 bytes | 真实模型 Work 交付 bytes | 原生下载复核 |
| --- | --- | ---: | --- |
| Word | 13.165 / 377,516,032 | 37,138 | 收入175；原件hash不变 |
| Excel | 13.196 / 267,714,560 | 7,882 | North Q3=175，SUM公式保留；缓存North总计385、总总计845 |
| PPT | 11.825 / 240,939,008 | 35,077 | 标题Updated Launch，页数保留 |
| PDF | 0.827 / 133,169,152 | 3,843 | 全部页数与逐页提取文本相同 |

四个真实 Work 均为真实供应商 Agent → API/Broker → 无网沙箱执行，实际 `load_skill`、`review_artifact`、`deliver_file` 成功；交付下载hash与业务Artifact一致，四份上传源重新下载hash未变。Word/Excel/PPT/PDF分别评审2/2/3/2页；源表第二页已有窄列截断，不把源缺陷当本轮编辑引入。模型视觉判断为小样本人工任务，不代表复杂模板/宏/所有版式质量。

### 真实模型链路、缓存与修复

真实模型 `deepseek-flash`，保留产品 thinking/工具设置；使用独立真实 API、Agent、Broker、Worker及持久checkpoint/业务计量，没有业务测试替身。透明HTTP转发只记录请求正文和计数，不修改payload、不写密钥。模型窗口在隔离catalog中设为32768/output8192以触发实际压缩；平台管理员API将阈值0.85临时调到0.25，再恢复0.85。低阈值小于Work必须保留的当前工具组时按合同暂停，不能称为稳态缓存失败。

Chat：真实Worker索引、DocRef选区校验/冻结、load_skill、成功PDF `document_view capture(source=render)`；截图进入真实模型请求，随后在同一分支恢复。此前两次capture未先打开/准备native索引，返回unavailable，保留失败调用；成功capture不声称viewport浏览器合流。Work：Excel/PPT及续轮实际发生多次压缩；新run用一步预算暂停、重启Agent、经公开resume API从checkpoint完成，源hash相同。

修复两项实际缺陷：

- 截图绑定原run，继承checkpoint的下一run读图得到404并暂停：读取路径允许同owner、同对话当前祖先链的截图，继续校验版本、源授权、过期和内容digest；无关同对话分支及删除源仍拒绝。新回归在旧代码失败，修复后SQLite/PostgreSQL通过；原暂停Chat真实resume成功，截图读取200（单次8ms，随后3ms，非性能分位数）。新增一次有界祖先查询（最多500层）及主键读取，无图片复制/新后台任务；SQLite/PostgreSQL EXPLAIN分别使用runs主键、conversation-parent索引/主键，无runs全表扫描。
- 步数耗尽的收尾assistant遗漏reasoning_content，真实恢复得到供应商400：保存原值（包括空值），不重写此前消息。录制请求回归覆盖非空/空reasoning，两例在旧代码失败，修复后通过，保持tools、tool_choice及此前messages精确前缀。新的真实Work暂停/重启/resume成功；失败准备run保持failed，不抹掉400。

以下逐调用均为 **cached/prompt**；每格按对应run中账本调用时间排序，包含压缩摘要、冷基底、工具失败与准备调用。不同run不可仅凭本表顺序断言共享前缀；真实记录另检查Chat79→80→81及最终Work稳定续轮的旧messages/tools/settings不变。

| 范围 | 每调用 cached/prompt |
| --- | --- |
| docx Work | 0/7,286; 7,296/10,552; 10,752/11,173; 11,264/12,140; 12,416/12,704; 12,800/13,086; 13,056/13,305; 13,312/13,594; 13,696/15,715; 15,872/16,034; 16,000/16,256 |
| xlsx Work | 6,016/7,190; 7,296/12,551; 12,544/12,880; 12,928/13,257; 13,312/13,922; 14,208/14,384; 14,464/15,006; 15,104/15,899; 16,384/16,846; 16,896/17,082; 17,024/17,210; 17,280/18,181; 18,176/20,144; 6,272/10,272; 10,496/10,766; 11,008/11,170; 11,136/11,683; 11,904/12,402; 12,416/12,668 |
| pptx Work | 6,016/7,048; 7,040/21,593; 21,632/21,816; 22,016/22,437; 22,656/23,325; 23,424/23,630; 23,808/23,974; 23,936/24,188; 24,192/24,388; 24,448/25,251; 25,472/25,839; 6,272/8,557; 8,576/11,246; 11,264/11,522; 11,520/11,748 |
| Chat DocRef/load_skill（准备） | 0/4,881; 4,864/6,134; 6,272/6,518; 6,528/6,906 |
| Chat DocRef/load_skill | 3,968/4,893; 4,992/6,193; 6,528/6,675 |
| Chat capture 索引未就绪（准备） | 7,552/8,326; 8,320/8,489 |
| Chat capture 索引未就绪 | 6,656/7,891; 7,808/8,019; 8,192/9,094 |
| Chat 初始稳定续轮 | 9,088/9,223 |
| Chat 初始稳定续轮 | 9,216/9,371 |
| Chat 初始稳定续轮 | 9,216/9,476 |
| Chat 初始稳定续轮 | 9,472/9,639 |
| pdf Work | 6,016/7,270; 7,424/8,900; 9,216/9,886; 9,856/11,727; 11,776/12,044; 12,032/12,255 |
| Chat 成功 render capture | 9,728/10,008; 10,112/10,960 |
| Chat 截图恢复/压缩 | 11,008/11,268; 3,968/4,688 |
| Chat 压缩后稳定 1 | 4,736/4,890 |
| Chat 压缩后稳定 2 | 4,736/4,965 |
| Chat 压缩后稳定 3 | 4,864/5,040 |
| Work 低阈值压缩 1 | 13,312/13,487; 6,016/7,062; 7,296/7,556 |
| Work 低阈值压缩 2 | 7,552/7,697; 6,144/7,134; 7,424/7,643 |
| Work 低阈值压缩 3 | 7,680/7,892; 6,016/6,810; 6,912/7,276 |
| Work 恢复准备（失败保留） | 16,640/16,875; 6,016/7,958; 7,936/8,174; 8,320/8,506 |
| Work 步数暂停/修复后恢复 | 6,016/7,247; 7,296/8,545; 8,576/8,816 |
| Work 稳定 1 | 7,296/7,546 |
| Work 稳定 2 | 7,424/7,613 |
| Work 稳定 3 | 7,424/7,678 |
| Worker资料摘要（真实调用，单列） | 0/197; 128/350; 0/197; 0/197; 0/91; 0/91; 0/91 |
| 完全相同请求复放（Chat79/80/81，按实际81→80→79执行） | 4,864/5,040; 4,736/4,965; 4,736/4,890 |

- chat真实业务调用（包含冷段/准备/压缩）：23次，**157,824/173,547 = 90.940%**。
- work真实业务调用（包含冷段/准备/压缩）：70次，**841,216/915,517 = 91.884%**。
- 压缩后Chat稳定3轮 **14,336/14,895 = 96.247%**；恢复正常阈值的Work稳定3轮 **22,144/22,837 = 96.965%**。这些分组不包括压缩摘要和冷基底；上表与总账保留它们。
- Worker资料摘要7次 128/1,214 = 10.544%；复放3次 14,336/14,895 = 96.247%。本次全部返回usage的调用共103次，**1,013,504/1,105,173 = 91.705%**。另1次供应商400无返回usage，账本reservation released、0/0表示未得到计数，不能推定供应商没有计费。供应商价格快照未配置，不编造货币费用。

相同最终请求及相邻请求复放：原始/复放均为4736/4890（未命中154）、4736/4965（229）、4864/5040（176）；逐份HTTP正文SHA256完全相同。所有计数符合本样本的 `cached = floor((prompt - 128) / 128) × 128`，即128-token分块并保留128–255 token尾部。**这是供应商侧缓存/计数边界的实测证据，应用未改写请求；不填充上下文，也不通过改门槛“修复”比例。** 不声称掌握供应商内部算法。上轮3255/3072的精确请求已被上轮清理，无法逐字节复放历史那份请求；其183-token尾部及其他历史短段均符合这次观测，历史94.378%的根因归属因此为有实测对照支持的推断，而不是历史原件复放已完成。

### 最终检查、清理与边界

- 当前代码Ruff check/format通过；截图/前缀/评审专项 **78 passed**；独立临时PostgreSQL截图合同 **16 passed**；两个新回归均先在旧实现复现失败。仅在第二个修复后重跑其受影响前缀及最终受影响组合，未重跑已通过的容器制作、Portal镜像或无关完整Python/浏览器套件。
- 初始配置误用office-read runner作native viewer reader已改为从本分支viewer-reader.ts构建的真实入口；驱动分页limit1000改为API上限500；重复准备客户端触发临时账号并发限制，按实际状态继续。以上均未通过改产品绕过。
- 两个镜像保留。清理仅限本轮c3容器、临时服务、Redis/PostgreSQL及仓库外请求/日志/产物；不触碰个人栈。清理确认：本轮c3容器0残留，四服务/转发/Redis/PostgreSQL共7个临时端口均关闭；两个镜像仍在。仓库外原始请求、日志、数据库及生成产物均已清理。
- 本次补齐真实镜像、四格式离线Work交付、真实Chat/Work API/Broker、DocRef/load_skill/capture/render、压缩与恢复。**P5仍不能标为全部外部验收完成**：历史94.4%原请求逐字节复放、真实模型与在线viewport浏览器合流、Linux并发/资源上沿、复杂模板/宏及模型效果对照仍未验证；截图10分钟TTL后的继承恢复也未验。不扩大本次授权、不部署。


### 资源批量读取导航补充（2026-11-02）

进入基线 `e363cbe`，本次产品/录制请求测试基线 `01379e810b8315f187525ebfdca1236844775c59`，同一 worktree/branch，base 仍为 `origin/main@bf0af4f`，未 fetch。生成器明确要求同一回复并列调用所需资源；四基础导航由 `build.py` 重建，根 index 递归 hash 同步。未改 Agent 循环：两种窗口的录制请求证实同批三调用顺序追加、前缀/tools/settings不变、同批重复返回 already_loaded，且第二资源单独能装下时仍会因先前结果占用而拒绝；不截断。

- 专用 Docker 增量 `docker build -t c3-office-batch:e363cbe -f images/sbx-office/Dockerfile .` 成功，image ID `4cb99a358897`；系统/Python/npm 依赖层复用，仅重建资源及其后续层。`AILAB_TEST_OFFICE_IMAGE=c3-office-batch:e363cbe .venv/bin/pytest tests/contracts/test_office_production.py -q -s`：**5 passed、0 skipped**，四格式原件hash不变，含HTML/LaTeX离线生产路径。四格式耗时12.404/12.726/12.111/0.738秒，cgroup峰值237,404,160/242,184,192/234,770,432/119,795,712 bytes（单样本，不作性能分位数）。镜像构建内实际hash/依赖校验通过，宿主 `scripts/check_skills.py` 通过。
- 真模型 `deepseek-flash`：真实临时 API/SQLite、AgentLoop、持久 checkpoint/usage 与供应商，Work 先加载 `platform.docx`，任务要求阅读 `routes/create.md` 和 `references/docx-js-core.md` 做操作规划；用户提示未要求批量。模型第一条回复即并列调用两份资源，下一条回复完成，run succeeded；记录请求前缀、tools和设置不变。两次 cached/prompt 分别 **6,016/7,198、7,296/14,313**，合计 **13,312/21,511 = 61.8846%**；含新资源正文的两调用短样本，不代表稳态≥95%。此次只测加载/规划，未启动沙箱制作；不能替代上方真实交付/视觉验收。
- Ruff check/format 与 Portal build 通过。资源/技能/目录/前缀专项初次仅mock租约快照因macOS `stat -c`失败；按仓库要求补GNU工具PATH，纳入下面最终完整套件验证，不改产品绕过。最终按 GNU 工具PATH运行 `.venv/bin/pytest tests -q`：**2074 passed、174 skipped、1 failed**（391.639秒，exit 1）；其中受影响前缀/资源/技能/catalog/hash/租约组合 **150 passed、2 skipped**，包含新两项录制与mock租约合同。唯一失败是既有 `test_render_lifecycle_does_not_retain_records_after_1000_cycles`，tracemalloc增长7,723,460 bytes超过6 MiB；不改实现或阈值，仅该失败项独立复跑 **1 passed**（25.197秒，exit 0），保留分组/进程状态相关flake风险，不声称最终全套首次全绿。未另跑PostgreSQL/Redis/浏览器E2E；174项按默认测试门禁跳过，真实Office合同另行启用并通过。
- 导航多出少量固定文本，无新依赖/后台任务；同批读取仍顺序执行，资源总正文和累计窗口准入不变，只减少潜在模型往返，本次未做对照时延测量。P5总体仍部分验证，未测边界沿用上节；本次不push、PR或部署。临时API已停止，18396端口关闭，本次c3容器0残留，镜像保留。

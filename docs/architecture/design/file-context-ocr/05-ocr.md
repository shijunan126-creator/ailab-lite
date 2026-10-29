# PDF质量分流与RapidOCR服务

2026-10-29 实施补充：正文/OCR/视觉覆盖与自动文件等待的最新合同见[ADR 0026](../../adr/0026-document-visual-coverage-and-file-waits.md)，本轮验收见[修复记录](../../../progress/updates/2026-10-29-document-reading-reliability.md)；下方旧状态为历史设计。

> 实施状态（2026-10-29）：用户已授权本方案实施，覆盖下文历史“只设计”限制；不授权push/PR/merge/部署。OCR只计量、不计费、不设用户累计配额，资源隔离仍保留。当前实现、验收及剩余缺口以[实施记录](../../../progress/updates/2026-10-29-file-context-core-validation.md)和[验收逐项记录](../../../reviews/file-context-2026-10-29/acceptance.md)为准。本文其余历史状态不代表当前完成度。

状态：设计修订稿，未实施。基线 `main@e71a60f`。本轮明确Chat预处理、Work自主操作、图片按视觉能力分流、RapidOCR与昇腾NPU优先；正文中的资源与大小数值均为待验收候选。

[总览与模块入口](README.md)。

## PDF质量与OCR流水线

### 判断是启发式分流，不是正确性证明

逐页输出 `usable/suspect/no_text/blank/unsupported` 和原因码：native_extract_error、replacement_chars、abnormal_repetition、sparse_text_with_page_image、bad_mapping、layout_uncertain等。保留原始文字，不以“纠错”覆盖证据。

分流先看轻量信号：可打印/替换字符比例、异常重复、字体映射异常、文字块bbox、图片覆盖率和文字分布。候选报警阈值：替换/控制字符>2%、重复异常>30%、整页图片覆盖>70%而正文区域几乎无文字。这些只用于疑点判定，需中文/英文/公式/短封面校准，不能把每页<40字当唯一标准。

`blank`须结合页面实际内容确认；只有稀疏文字不能判空。带旧OCR层的扫描页可提取很多字仍可能错误，无法仅靠字数/字符形态判真。检测到可疑图像正文时按需渲染所需页/区域做专用OCR；重要数字/引文提供原页复核。原生与OCR冲突保留两份并标记，不能把两份重复正文无序拼接给模型。

### OCR调用契约

API授权并创建 `text_extraction` job → Worker claim → 必要时Broker使用绑定preview/render lease逐页渲染 → Worker调用隔离OCR执行器 → API短事务提交页结果和进度 → ACK。引擎不持模型API密钥或业务DB凭据；输入只允许平台授权的单页bytes/受限文件，不接受任意URL、宿主路径或用户自报文件owner。

单位身份 `(file_id,derivation_fingerprint,page,region,engine_config)`；claim/attempt fencing控制重复并发；结果和进度持久后才确认页完成。至少一次投递可重算未提交的OCR页，但只发布一个有效结果和一笔确认计量，不宣称外部OCR“绝对只执行一次”。不复用当前视觉OCR的已dispatch即拒绝作为专用OCR页恢复策略。

Work允许上传的原文件可以超过Worker基础解析20MiB预算，OCR接口接收的是Broker授权渲染的选定页资产，按单页字节/像素和job总量验限，不用旧视觉OCR的原文件20MiB上限误拒绝一份50MiB文档的单页。Broker读取原文件仍受上传、渲染RSS和deadline上限约束，不能绕过大文件安全限制。Chat的文档/扫描页准入独立按[模式模块](02-chat-work.md)执行。

状态返回进度、可用页、失败页和稳定原因码；超时/OOM/引擎缺失不能变成空文本成功。图像预处理是独立可缓存步骤，限制像素、DPI、旋转和解压资源。不要一次把200页渲染成图片放内存，也不要依赖预览前缀页数来支持任意尾页OCR。

### RapidOCR执行器，昇腾NPU优先

用户已指定RapidOCR路线并纠正硬件为NPU。本设计首选 `RapidOCR → ONNX Runtime CANN Execution Provider → 昇腾NPU`，使用已分配的OCR智算资源；CPU只作为开发或管理员显式启用的备用部署。不是CUDA GPU，不假定安装普通onnxruntime或onnxruntime-gpu就能使用昇腾。

已核对RapidOCR固定提交`80b427d`的`provider_config.py`确有CANN路径，官方文档给出`EngineConfig.onnxruntime.use_cann=True`。但上游缺CANN时会告警并回CPU，平台不能把它当成功的NPU服务。启动必须验证ORT可用providers、检测/方向/识别各session实际providers，以及部署配置中的设备绑定；不满足required_backend=cann则readiness失败。允许的少量CPU算子/前后处理与整个模型回落CPU分开，通过ORT profiling核实节点分配。

执行镜像锁定RapidOCR、ORT-CANN构建、CANN、驱动兼容范围、Python/架构、det/cls/rec模型文件hash和词典；模型从已审核离线目录只读加载，禁止用户输入触发URL下载或运行时自动下载。`rapidocr_onnxruntime`旧分包不是新实现首选，采用官方仍维护的`rapidocr`加指定推理运行时。模型版本在实测后定，不沿用浮动默认值。

候选服务配置表达的是平台合同，不是直接可执行的RapidOCR配置文件：

```json
{
  "required_backend": "cann",
  "allowed_device_ids": [0],
  "max_inflight_pages": 1,
  "page_deadline_seconds": 60,
  "model_manifest": "ocr-models-pinned.json",
  "allow_cpu_fallback": false
}
```

worker仍只编排，执行器常驻复用模型；每页/区域单独请求，由API绑定授权资产。NPU设备通过容器/既有智算调度配置分配，不能让Agent传device_id或任意路由选择卡。CANN/ORT内存参数不是硬件隔离的替代；未验证虚拟分卡/同卡混跑就以独立已分配设备或明确资源槽部署，不自行把OCR与LLM挤同一卡。

上游本轮config.yaml的CANN `npu_mem_limit`默认约20GiB，不能照搬为本平台OCR资源申请；必须与实际分配额度、各session累加开销及图编译峰值核对。限额不足或版本不支持时明确不ready，不能只设置一个较小数字就宣称硬隔离/容量通过。

启动预热与首次图编译计入冷启动；graph/model cache包含SoC/驱动/CANN/ORT/模型hash，避免不同设备/版本复用不兼容产物。多尺寸输入要限制shape集合与cache磁盘，测冷/热延迟，不能拿warm-only数字承诺首张速度。页面图通过CPU渲染/预处理和网络进入执行器，其host RSS、CPU和传输成本仍需计量。

NPU验收记录实际卡型/设备ID、分配额度、驱动/CANN/ORT组合、各session EP与节点执行profile、npu-smi的进程/显存/利用率、CPU/RSS、冷热页p50/p95、长文排队和吞吐。仅providers列表有CANN或接口200不算硬件验收。先并发1和有界微批，再用实测决定是否增加并发/模型规格；不预先承诺卡数、虚拟分片或精度。

CPU开发路径单独配置、标实际backend，只测流程/对照，不能替代NPU验收。NPU暂不可用时排队/失败，不自动切CPU、视觉LLM或其他供应商；若部署方另行启用CPU备用，记录真实backend与独立容量预算。

与冻结架构的关系：`02-模型服务层设计.md`为ailab-ocr预留Atlas-07上的2个NPU槽，原模型是DeepSeek-OCR-2/PaddleOCR-VL；`08-Skills与沙箱环境设计.md`已有经Agent调用ailab-ocr的方向。本稿拟用RapidOCR承担普通文字提取，保留原预留资源作为容量评估输入，**不自动撤除既有模型、重分卡或声称RapidOCR已有GPUStack部署适配**。通用OCR是专用HTTP协议，不强行伪装OpenAI chat接口；纳入既有运维面需要另验自定义backend健康、认证和路由。冻结文档本轮不改，定版后再以ADR说明与原重型OCR方案的差异。

### 计量与视觉模型边界

RapidOCR（NPU或显式CPU后端）记录页数、运行毫秒、引擎版本、峰值资源及重试，不伪造LLM tokens或套用现有mode=ocr的视觉token价格。是否收用户积分是待决产品政策；技术上和真实LLM账本分开。Agent主动调用的OCR绑定run和file，独立入口绑定file owner及job；reserve/settle或计量记录幂等键是实际单位。

视觉模型只在正常看图/页面语义请求使用，沿用每次真实模型调用账本。专用OCR失败不自动转付费视觉OCR；若未来允许增强识别，必须有明确用户行为与成本策略。独立纯文字提取跳过现有资料库自动模型摘要，避免隐藏付费；普通资料库摘要功能保留原产品开关，不在此顺便删除。


文件类job信封仍以file_id为主体，不能违反当前JobEnvelope同时塞run_id和file_id。由API在任务上下文保存可选requesting_run_id并验证归属/attempt；用户或模型不能选择owner。Agent发起与独立入口共用文件派生单位，计量/取消权限另绑定发起身份。

# OCR链路补充审查

基线：`fix/ocr-memory-recovery@496e88a7f28bd35252141507c8e3d0b04f25bc37`，产品输入为`b0f0dfd`，base main `3d85a86`。用户要求继续检查OCR是否有其他bug；本轮审查/隔离复现，不改产品、不部署或修改业务数据。前轮内存/租约修复仍保留，不能因它已本地验证而忽略本报告的发布前缺口。

2026-11-04 后续用户已授权修复并使用最新main提PR/合并；四项实现与当前验收见[发布整合记录](../progress/updates/2026-11-04-ocr-lifecycle-pr.md)，本文保留发现时证据，不再表示当前未实施。

## 已复现问题

### P1：混合缓存页提取的下载漏页，纯缓存重选仍下载旧范围

位置：`platform/ailab_platform/api/routes_ocr.py:366`、`:381`、`:297`、`:459`。

最小复现：两页PDF先提取`[1]`完成；再提取`[1,2]`。请求只把缺失的第2页写入`ocr_pdf.low_text_pages`，最终拼接也只遍历该集合，覆盖唯一的`derived/{file_id}/ocr.txt`。实测状态done而下载仅含`[page:2]`，缺少请求中的第1页。此时重选已缓存的`[1]`直接返回done，却仍下载上次第2页结果。

根因：待计算页、用户选择的结果范围、文件全局缓存被当成同一个身份；全缓存短路只证明页面缓存齐全，不证明下载对象匹配本次选择。

修复与回归建议：分别记录请求选择和缺失页，按本请求的完整选择从有效页缓存组装结果；下载绑定该选择/请求身份。覆盖首次提取、缓存+新页、全缓存重选、连续不同范围；缓存命中不能增加推理。

### P1：旧OCR任务到期可将新一轮提取置为失败

位置：`platform/ailab_platform/api/routes_ocr.py:50`、`platform/ailab_platform/api/jobs.py:165`、`:205`。

最小复现：第1轮失败状态已提交、旧Worker ACK尚未送达；用户重试第2轮创建新job。旧job过期后投递claim，API仅看到文件当前OCR为pending，没有核对job对应的generation，就把新一轮改成failed/job_expired。隔离探针确认：新job仍存在且未过期，文件已被旧job标失败。

根因：generation仅在文件上，durable job没有与当前代次绑定；claim/到期副作用只检查文件级状态。旧任务不应执行或终结新任务。

修复与回归建议：OCR使用与preview相同的当前job身份围栏，claim/开始/完成/失败/到期都核对；旧代次仅清理自己的消息。覆盖旧ACK丢失、失败后重试、旧job到期和迟到响应，不用延长租约掩盖。

### P1：处理中接受另一页范围，但悄悄丢弃新选择

位置：`platform/ailab_platform/api/routes_ocr.py:374`、`:442`；`platform/portal/components/library/text-extraction.tsx:33`。

最小复现：`POST pages=[1]`尚pending时，再`POST pages=[2]`，第二次返回200/pending。第1页完成后GET返回done/pages=[1]，并提供第1页下载；第2页既未排队也未执行。前端轮询没有页范围/请求身份，会将前一轮完成当成本轮完成。可通过两个标签或关闭后重新打开提取触发。

根因：只复用文件级pending/running，不记录或拒绝不同选择；代码注释期望“下一次调用再启动缺失页”，实际前端只轮询GET。

修复与回归建议：完全相同选择幂等复用；不同选择明确409或持久排队，不能200承诺后丢弃。结果/轮询绑定同一请求或标准化选择，覆盖多标签、重新打开和交叠范围。

### P1：明确渲染失败仍保持pending并反复创建渲染任务

位置：`platform/ailab_platform/worker/ocr.py:20`、`platform/ailab_platform/api/routes_ocr.py:121`、`:228`、`platform/ailab_platform/worker/main.py:168`。

最小复现：选PDF第1页，受控renderer返回failed/unsupported_pdf。Worker仅检查恢复后的`meta.preview`是否pending，未消费`ocr_preview.failed`；随后OCR缺页面在try外抛422，文件仍pending。Worker对该异常释放后重试，prepare再次生成新render job身份。探针连续两次投递同一OCR durable job，得到两个不同render job ID，文件始终pending。

影响：不可恢复的PDF问题也让用户持续等待，反复消耗渲染容量；前轮改为2秒release会放大这个既有错误分类缺口。因此发布前应一起补齐，而非恢复660秒等待。

修复与回归建议：OCR准备/渲染/识别共同区分busy等瞬时失败和unsupported/invalid等终态失败；明确失败持久写failed并ACK，显式重试才创建新任务；缺页校验进入统一的状态收敛路径。覆盖busy保持同一任务、终态失败只渲染一次、失败后显式重试及来源删除。

## 本轮证据与边界

- 4个仓库外隔离缺陷探针全部命中：先3项（4.55s），再旧任务代次1项（1.77s）。探针通过表示错误已存在，不表示功能验收通过。
- 使用真实API路由、数据库事务、用户归属、OCR页缓存、Worker分派、claim/release、下载；两页PDF与缓存/扫描状态是最小受控fixture，RapidOCR上游为本机受控HTTP响应，renderer为受控终态失败。不是实际模型精度/Broker渲染或线上复现；没有访问运行服务。
- 命令为既有非/tmp解释器、`PYTHONPATH=<本检出>/platform`，`pytest -p tests.conftest <仓库外探针> -o addopts='' -q -s`。按调用/数据状态断言，未做全套、Portal build、浏览器、真实OCR容器或付费调用；本轮无产品变更，不重复前轮全套。
- 原始stdout、临时PDF/页图和探针留在仓库外临时目录，诊断完成清理；仓库只留本报告。前轮实际CPU内存验收仍仅见[实施记录](../progress/updates/2026-11-04-ocr-memory-recovery.md)，不挪作本轮4项通过证据。

下一步：按请求身份/代次与统一错误收敛两条共同根因集中修复以上4项，再做成组回归；本报告未实施这些修复，不把前轮分支标成可直接发布。

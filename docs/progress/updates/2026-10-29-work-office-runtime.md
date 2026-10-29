# Work 标配 Office Runtime

基线 `origin/main@09a28e8a5101fbb081ed4f32c50985707266f031`，分支 `feat/work-office-runtime`，独立工作树 `.worktrees/work-office-runtime`。用户授权本地实施共存方案；未部署、push 或创建 PR。

Work 镜像增加与系统 reader 相同的 Node24/固定解析核心，只复制生产依赖与 bundle。`office-read SOURCE NEW_OUTPUT_DIRECTORY` 按需运行，支持 DOCX/XLSX/PPTX/PDF；60 秒截止、160 MiB V8 heap ceiling，无常驻进程。输出目录已存在时拒绝运行，避免修改后遗留旧 manifest/块。内部办公说明引导先看 coverage/status，再按需读取；编辑仍用现有库，渲染沿用 LibreOffice，平台权威索引和 OCR 路径不变。

构建上下文统一到仓库根；deploy 哈希纳入 vendor 代码/锁文件并排除 node_modules/生成 bundle。镜像现有 smoke 增加四格式正文、DOCX 编辑后重读及旧输出拒绝合同；真实组件 CI 在 vendor 变化时也触发。取舍与成本见 [ADR 0025](../../architecture/adr/0025-work-office-reader.md)。

## 本轮验证

- Ruff check/format、ShellCheck、shell 语法、diff whitespace 通过。
- `vendor/zcode-office-runtime`: `npm ci && npm test && npm run build`，15 项通过。
- 本机实际 CLI 四格式样本解析通过，manifest ready/非空块、旧输出及不支持格式拒绝通过；仅将 CLI 的 `/opt/ailab-office/runner.mjs` 替换为本机 bundle 路径，未冒充镜像测试。单次墙钟约 0.21–0.56 秒，不作性能比较或容量结论。
- Portal `npm test && npm run build`：44 项通过、build 成功；无 UI 变更，不做浏览器。
- 完整 Python 套件 `PATH=GNU工具优先 .venv/bin/pytest tests -q`：退出码0，1441通过/144跳过；外部组件未配置而跳过，保留既有SQLite表达式索引反射警告。
- 部署决策测试 6 项、CI 范围选择器15项通过。先前仅检查默认 socket，错误判断 Docker 不可用。后续通过 `bash /Users/michael/Documents/AILab-local/bin/docker.sh info` 确认专用 daemon 29.5.2 正常、12个容器运行；未改动部署。真实镜像构建、断网/非 root 容器四格式与编辑后重读合同、双架构、镜像增量和 CPU/RSS 峰值未验。未跑 PostgreSQL/Redis 外部组合、真实模型或数百人压测。

下一步：在可用的隔离 Docker 环境运行镜像合同。现有本机部署不会自动获得本改动，后续部署须当次明确授权。

## 集中审查与真实验证（2026-10-29，7c3db81 加本轮修改）

发现并修正两项产品缺口：仅在内部skill给office-read指令会漏掉直接创建的工作区文件，现已补Work主提示词；空目录检查后再写存在并发窗口，现以mkdir原子占有新输出目录，同目录竞争只有一个成功。内部skill区分工作区路径和平台file_id，不再要求工作区文件先解析附件。新增主提示词回归与空目录/并发拒绝镜像合同。

- 最终镜像ARM64 `sha256:93b881908c3758fb6fcbf507d308e275f54526bc413a4dc3f338ba91d44c8eb4`，逻辑体积447468396字节（非磁盘增量）。真实runsc、断网、UID10001、1CPU/1536MiB/256PID，通过四格式读取、修改后重读、已有空目录拒绝/同目录竞争，以及原Office渲染/字体/断网smoke；无OOM。未测amd64或峰值CPU/RSS。
- 最终真实DeepSeek Flash 18次调用正常结束：自主发现exec/加载skill、office-read原件和成品、DOCX收入120→175保留KEEP-UNCHANGED、XLSX North120→175保留South80及SUM公式；独立程序断言通过。最终成功轮usage为114087输入/3060输出token，不能当作所有尝试总量。此前有验证脚本未知skill未转错误返回、缺交付接口导致达到12/16调用上限的失败，均不隐去；脚本现返回可恢复错误和不写业务库的验证交付结果。
- 验证入口 `platform/scripts/verify_work_office_model.py --execute --key-env PRIVATE_ENV --catalog PRIVATE_CATALOG --docker-wrapper PRIVATE_WRAPPER --container DISPOSABLE_CONTAINER`，使用产品Work提示词、工具schema和发现逻辑，经真实API执行容器工具；未经过产品AgentLoop/Broker/API账本，不宣称五服务E2E/真实资料库交付通过。凭据仅在宿主读取，不进入容器或仓库。模型有多次不必要的XML检查，故仅证明功能，不作调用效率承诺。
- Python全套1442通过/144跳过，最终提示词6通过；Ruff421文件、ShellCheck、Portal build和diff检查通过。此前Portal44和Runtime15测试结果沿用，未改对应代码。
- 本轮专用容器/镜像清理，现有部署未更新。真实模型调用产生提供商费用，但隔离脚本不写业务用量账本；未将私有调用或原始日志提交仓库。下一步需要完整产品链路验收时，在隔离五服务环境验证新镜像与交付合同；不自动部署或push。

## Chat 与格式集中复查（2026-10-29）

发现内部文档skill同时在Chat/Work加载，Work命令泄露到Chat指导；已根据run mode生成说明，Chat只使用平台读取，Work保留本地读取及复制编辑流程。四种skill均有双模式回归。

当前代码系统reader真实Docker/runc合同四格式、两任务并发通过；Work此前runsc四格式解析和DOCX编辑重读通过。真实模型此前只在Chat验证DOCX问答，在Work验证DOCX/XLSX编辑重读；PDF/PPTX真实模型问答、复杂文档及五服务端到端未在本轮验证，不能把格式解析合同当作模型质量证明。原Chat专项和完整Python、Portal构建见本轮结果。

本轮完整SQLite Python 1442通过/144跳过，新增AgentLoop Chat加载回归单独通过；Portal build、Ruff、diff检查通过。隔离reader镜像与容器已清理。

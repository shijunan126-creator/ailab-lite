# Work 标配 Office Runtime

基线 `origin/main@09a28e8a5101fbb081ed4f32c50985707266f031`，分支 `feat/work-office-runtime`，独立工作树 `.worktrees/work-office-runtime`。用户授权本地实施共存方案；未部署、push 或创建 PR。

Work 镜像增加与系统 reader 相同的 Node24/固定解析核心，只复制生产依赖与 bundle。`office-read SOURCE NEW_OUTPUT_DIRECTORY` 按需运行，支持 DOCX/XLSX/PPTX/PDF；60 秒截止、160 MiB V8 heap ceiling，无常驻进程。输出已有内容时拒绝运行，避免修改后遗留旧 manifest/块。内部办公说明引导先看 coverage/status，再按需读取；编辑仍用现有库，渲染沿用 LibreOffice，平台权威索引和 OCR 路径不变。

构建上下文统一到仓库根；deploy 哈希纳入 vendor 代码/锁文件并排除 node_modules/生成 bundle。镜像现有 smoke 增加四格式正文、DOCX 编辑后重读及旧输出拒绝合同；真实组件 CI 在 vendor 变化时也触发。取舍与成本见 [ADR 0025](../../architecture/adr/0025-work-office-reader.md)。

## 本轮验证

- Ruff check/format、ShellCheck、shell 语法、diff whitespace 通过。
- `vendor/zcode-office-runtime`: `npm ci && npm test && npm run build`，15 项通过。
- 本机实际 CLI 四格式样本解析通过，manifest ready/非空块、旧输出及不支持格式拒绝通过；仅将 CLI 的 `/opt/ailab-office/runner.mjs` 替换为本机 bundle 路径，未冒充镜像测试。单次墙钟约 0.21–0.56 秒，不作性能比较或容量结论。
- Portal `npm test && npm run build`：44 项通过、build 成功；无 UI 变更，不做浏览器。
- 完整 Python 套件 `PATH=GNU工具优先 .venv/bin/pytest tests -q`：退出码0，1441通过/144跳过；外部组件未配置而跳过，保留既有SQLite表达式索引反射警告。
- 部署决策测试 6 项、CI 范围选择器15项通过。先前仅检查默认 socket，错误判断 Docker 不可用。后续通过 `bash /Users/michael/Documents/AILab-local/bin/docker.sh info` 确认专用 daemon 29.5.2 正常、12个容器运行；未改动部署。真实镜像构建、断网/非 root 容器四格式与编辑后重读合同、双架构、镜像增量和 CPU/RSS 峰值未验。未跑 PostgreSQL/Redis 外部组合、真实模型或数百人压测。

下一步：在可用的隔离 Docker 环境运行镜像合同。现有本机部署不会自动获得本改动，后续部署须当次明确授权。

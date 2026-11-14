# platform

`platform/` 是平台的 Python 服务、Portal、k8s 清单和测试。各模块的代码地图、不变量和扩展步骤见 [文档索引](../docs/README.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `ailab_platform/api/` | 身份、授权、配额、Run 状态、文件记录和用量账本；唯一写业务数据库的服务。见 [API](../docs/modules/api.md) |
| `ailab_platform/agent/` | Agent 循环、模型调用、工具、上下文和前缀缓存。见 [Agent](../docs/modules/agent.md) |
| `ailab_platform/broker/` | 沙箱租约、exec 代理、文件注入和交付。见 [沙箱](../docs/modules/sandbox.md) |
| `ailab_platform/worker/` | 扫描、解析、派生资源和预览。见 [文件](../docs/modules/files.md) |
| `ailab_platform/contracts/`、`domain/`、`ports/`、`adapters/` | 服务间 DTO、纯领域状态、端口和本地/Redis/S3/OpenSandbox 适配器 |
| `portal/` | Next.js 界面、同源代理和浏览器端预览。见 [Portal](../docs/modules/portal.md) |
| `deploy/` | 应用 k8s 清单和镜像 Dockerfile。部署流程见 [部署](../docs/modules/deploy.md) |
| `scripts/` | 本地启动、k3s 辅助、目录校验和演示脚本 |
| `tests/`、`e2e/`、`scenarios/` | 后端测试、浏览器 E2E、真实模型场景。见 [测试](../docs/modules/testing.md) |
| `models.toml`、`reasoning.toml` | 模型目录和可选思考档位 |
| `pyproject.toml`、`constraints.txt`、`.env.example` | 依赖、锁定版本和本机配置样例 |

## 本地开发

```bash
cd platform
bash scripts/dev_up.sh          # venv、四个 Python 服务、Portal（已安装 node_modules 时）
bash scripts/status.sh          # 健康检查
bash scripts/dev_down.sh        # 停止进程并清理带 ailab.lease 的沙箱容器
```

需要 Python 3.12、`127.0.0.1:6379` 上的 Redis，以及已安装依赖的 `portal/node_modules`。`dev_up.sh` 会创建 `.venv`、在没有 `.env` 时复制 `.env.example`；如果本机还没有沙箱镜像，它把本次运行的 `AILAB_SANDBOX_PROVIDER` 设为 `mock`。Docker 或 OpenSandbox 的完整配置见 [沙箱](../docs/modules/sandbox.md)。

| 服务 | 地址 |
|---|---|
| Platform API / Swagger | `http://127.0.0.1:8080/docs` |
| Broker | `http://127.0.0.1:8081/healthz` |
| Agent | `http://127.0.0.1:8082/healthz` |
| Worker | `http://127.0.0.1:8083/healthz` |
| Portal | `http://127.0.0.1:3000` |

宿主机模式和 k3s 部署互斥（8080/8081 会冲突）。`dev_up.sh` 发现 `platform` namespace 里已有部署时会拒绝启动；先用 `bash scripts/k8s_down.sh` 拆除 k3s 平台，或改用 [一键部署](../docs/modules/deploy.md)。

## 模型与密钥

模型由 `models.toml` 定义；本机的 `models.local.toml` 存在时优先于它（该文件不进 git）。模型 Key 只放在 `platform/.env` 的 `AILAB_KEY_*` 变量、进程环境或 k8s Secret 中，`models.toml` 只写变量名。`reasoning.toml` 定义可选的思考档位，和目录放在同一目录时优先使用。

接一个新模型、发布目录或在 k3s 里换 Key 的步骤见 [部署 · 加或换一个模型](../docs/modules/deploy.md#加或换一个模型)。本机最快的路径是改目录、把 Key 写进 `platform/.env`，然后重启 API 和 Agent。

## 配置

所有设置以 `AILAB_` 开头，由 `platform/ailab_platform/config.py::Settings` 读取；`AILAB_ENV_FILE` 可以指定另一个 env 文件。`AILAB_PROFILE=secure` 是共享部署的启动门禁，检查项和部署侧 Secret 见 [部署 · AILAB_PROFILE=secure](../docs/modules/deploy.md#ailab_profilesecure)。

## 部署与测试

完整部署、配置、模型目录、观测和备份见 [部署](../docs/modules/deploy.md)；测试分层、CI、E2E 和真模型场景见 [测试](../docs/modules/testing.md)。仓库根目录的 [README](../README.md) 给出最短部署命令。

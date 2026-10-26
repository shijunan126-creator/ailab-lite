# AILab 简易版（ailab-lite）

[![CI](https://github.com/shijunan126-creator/ailab-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/shijunan126-creator/ailab-lite/actions/workflows/ci.yml)

AILab 平台的单机底座：在一台机器（目标 2 vCPU / 约 8 GB）上提供可靠的 Chat、Work、文件上传与解析、产物交付、多轮修改、
项目资料库、技能和受控联网。以后扩到多机只换适配器和部署，不改业务授权、用量账本、文件交付和 Portal 事件协议。

开发接续从 [项目进度](docs/progress/README.md) 进入：先读 [当前工作](docs/progress/current.md) 和 [HANDOFF](HANDOFF.MD)，
规则见 [AGENTS.md](AGENTS.md)，各能力的实现与验收状态见 [能力状态矩阵](docs/architecture/status-matrix.md)。

## 能做什么

- **对话 / 工作两种模式**：对话只用文字回答，从不启动沙箱；工作模式由模型在 gVisor 沙箱里运行代码、生成 PPT、Excel 等文件，
  右侧实时预览，可以接着对话修改同名文件。工作过程按时间顺序统一展示，耗时只计实际运行时间。
- **资料库**：项目、文件夹、收藏、图片、回收站、版本历史；上传后在 Worker 的受限子进程里扫描和解析，缩略图和预览只生成一次并缓存。
- **技能**：通用技能随沙箱镜像发布；个人技能是用户自己的 `SKILL.md` 或 `.zip`，可上传，也可在对话或工作中让模型新建、更新（保留旧版本）。
  保存的技能要经过扫描与审核策略才能使用。
- **受控联网**：搜索网页、读取指定网页和 GitHub issue（含评论）并保留来源、下载图片和文档、获取公开仓库源码、安装 PyPI 依赖后在沙箱里做实验、
  临时使用外部仓库里的技能。沙箱本身始终没有网络，见下文“安全边界”。

## 架构

![AILab 简易版架构](diagrams/svg/lite-01-architecture.svg)

五个服务，边界见 [AGENTS.md §2](AGENTS.md)：

| 服务 | 负责 | 不做 |
|---|---|---|
| Portal（Next.js，`platform/portal`） | 界面、httpOnly 会话、同源代理 `/api/v1 → /v1` | 不持有模型密钥或沙箱凭据 |
| Platform API（`platform/ailab_platform/api`） | 业务数据库的唯一写入方：授权、配额、账本、文件记录、技能 | 不执行用户代码、不解析不可信文件 |
| Agent（`…/agent`） | Agent 循环、模型调用、沙箱外的公开资源获取 | 不写业务库、不持有容器运行时 |
| Broker（`…/broker`） | 沙箱租约、exec 代理、文件注入与两阶段交付 | 不信任 Agent 给的路径和大小 |
| Worker（`…/worker`） | 扫描、解析、缩略图、预览 | 不在进程内无限制解析 |

服务间走 HTTP，各自独立凭据和 scope；内部路由在 `/internal` 下，Portal 代理不可达。图源 `diagrams/src/d_lite_arch.py`。

## 安全边界（联网部分）

方案与取舍见 [ADR 0007](docs/architecture/adr/0007-controlled-public-fetch.md)。要点：

- 沙箱 `--network none` + gVisor，没有宿主目录、容器运行时套接字或平台凭据；公开资源由 Agent 在沙箱外获取，Broker 流式写入沙箱，只在沙箱里打开和执行。
- 只访问公网 http(s)（80/443）：每个 DNS 结果都必须是公网地址（含 IPv4 映射、NAT64、6to4、Teredo 等变体），连接直接用检查过的地址，重定向逐跳复查，不用环境代理、不带 cookie 或凭据。
- 模型只能获取用户消息或搜索/网页结果里出现过的 URL（GitHub、PyPI 可直接构造），不能把对话内容拼进任意域名的 URL；每次运行有请求数和下载量上限。
- 只读：没有推送、评论或提交表单的能力，也不注入私有仓库凭据或浏览器登录态。
- 外部网页、issue、仓库文件和技能都按不可信数据处理；外部技能只能临时使用，保存为个人技能要走 `save_skill`，且不能覆盖通用技能或他人技能。

没有声称“绝对防泄露”：搜索词、在页面已有链接间的选择、请求哪些公开包等低带宽信号仍然存在，残余风险写在 ADR 和验收记录里。

## 快速开始（k3s 部署）

在一台已装好 Docker 和 k3s 的 Linux 机器上（≥ 2 vCPU / 7 GB，x86_64 或 arm64）：

```bash
git clone https://github.com/shijunan126-creator/ailab-lite && cd ailab-lite
sudo ./deploy.sh --model-key sk-xxxxxxxx     # 默认接 DeepSeek；不带 key 时用离线演示模型走通完整链路

# 任何 OpenAI 兼容接口（OpenAI、通义、Kimi、vLLM、Ollama、自建网关……）：
sudo ./deploy.sh --model-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --model-name qwen-plus \
                 --model-key sk-xxxxxxxx --model-label "通义千问 Plus"
sudo ./deploy.sh --model-base-url http://10.0.0.5:8000/v1 --model-name Qwen/Qwen3-32B   # 本地 vLLM，无需 key
# 多个模型：--models-file my-models.toml（格式见 platform/models.toml）

# 注册：默认开放；也可以要邀请码或关闭
sudo ./deploy.sh --skip-build --registration invite --invite-code 你的邀请码
```

脚本会检查前置条件、构建沙箱和平台镜像、生成随机凭据、发布模型目录、部署五个服务和 Portal，并做冒烟测试（含经 Portal 登录的完整路径）。
可重复执行，`--dry-run` 只看不做：

```bash
./deploy.sh status          # 运行与健康状态、模型列表
./deploy.sh --skip-build    # 只改配置或换 key
./deploy.sh down            # 删除平台（会删数据，先确认）
./deploy.sh --help
```

Portal 默认只监听本机（http://127.0.0.1:3000），远程查看用 `ssh -L 3000:127.0.0.1:3000 <机器>`；`--listen 0.0.0.0` 会在网络上暴露 HTTP 登录页，仅限可信网络。
模型目录、密钥存放和适配器说明见 [platform/README.md](platform/README.md)，备份与恢复见 [runbook](docs/architecture/runbooks/backup-restore.md)。

## 开发与测试

```bash
cd platform
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest tests -q                       # SQLite + 内存队列
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/15 \
AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_test \
AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_migrations .venv/bin/pytest tests -q
AILAB_E2E=1 .venv/bin/pytest e2e -q             # 浏览器端到端
cd portal && npm test && npm run build
```

测试使用各自隔离的配置；PostgreSQL 测试库会被清空，只用一次性的实例。macOS 上需要 GNU coreutils/findutils（见 AGENTS.md §9）。
CI 分层与执行范围见 [.github/CI.md](.github/CI.md)。

## 目录

- `platform/`：五个服务（`ailab_platform/{api,agent,broker,worker}`）、共享 `contracts/ domain/ ports/ adapters/`、Portal、部署清单 `deploy/`、测试
- `images/sbx-office/`：沙箱镜像与通用技能（pptx / xlsx）
- `deploy.sh`：k3s 一键部署；`k3s/`：k3s 与 gVisor 运行时配置；`tests/`：基座与组件测试脚本
- `docs/architecture/`：目标架构冻结副本、ADR、设计与能力矩阵；`docs/progress/`：当前工作、路线图与每轮记录；`docs/reviews/`：验收证据

## 第三方软件署名

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).

Crawl4AI 0.7.4 的许可证为 Apache-2.0 加附加署名要求，原文见 [许可证](images/crawler/Crawl4AI-LICENSE)。

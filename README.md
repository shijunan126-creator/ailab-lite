# ailab-lite

[![CI](https://github.com/shijunan126-creator/ailab-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/shijunan126-creator/ailab-lite/actions/workflows/ci.yml)

一个可以自己部署的 AI 工作台，一台机器就能跑起来（2 核、8 GB 左右）。用户可以和模型聊天，也可以把活交给它：写代码、做 PPT 和 Excel、读资料、查网页。模型在隔离的沙箱里动手，做完把文件交给你，之后还能接着改。

平台暂名 AILab，正式名称还没定。这个仓库是它的单机版本。以后扩到多台机器，只需要换存储、队列和沙箱的适配器与部署方式，业务逻辑不用动。

## 能做什么

- **Chat 和 Work 两种模式**。Chat 只用文字回答，响应快，不启动沙箱。Work 让模型在沙箱里写代码、跑代码、生成文件，右侧实时预览，下一轮说“把第三页改一下”，它会在同一个文件上接着改。
- **资料库**。按项目整理文件，有文件夹、收藏、版本历史和回收站。上传的文件先扫描再解析，预览图和文字只生成一次。PDF、Office 文档、图片都能直接拿给模型读。
- **记忆**。模型会记住你的偏好和长期事实，你可以在记忆页查看和修改它记了什么。每个项目可以有自己独立的记忆。
- **技能**。常用的办公技能（PPT、Word、Excel、PDF）随沙箱镜像发布。你也可以上传自己的技能，或者让模型在对话里帮你写一个保存下来。
- **受控联网**。能搜网页、读网页和 GitHub issue、下载公开文件、装 PyPI 包做实验。沙箱本身始终没有网络，见下文。
- **其他**：会话分叉、全局搜索、定时任务（到点在原会话里继续）、可交互的回答卡片、按模型选择思考强度。

## 怎么搭起来的

![架构](diagrams/svg/lite-01-architecture.svg)

一共五个服务：

| 服务 | 做什么 |
|---|---|
| Portal | 网页界面。只持有登录会话，把 `/api/v1` 同源转发给 API |
| API | 唯一写数据库的服务：账号、授权、配额、用量账本、文件记录 |
| Agent | 跑 Agent 循环，调用模型 |
| Broker | 管理沙箱：分配和回收、执行命令、把文件放进去、把交付件取出来 |
| Worker | 扫描和解析上传的文件，生成预览和缩略图 |

服务之间互不信任：每个服务有自己的凭据和权限范围，内部接口外面访问不到。设计依据见 [docs/](docs/README.md)。

## 安全边界

- 沙箱用 gVisor 隔离，`--network none`，没有宿主目录、容器运行时或平台凭据。
- 需要联网时，由 Agent 在沙箱外取回公开资源，Broker 再把它写进沙箱。只访问公网 80/443，每个 DNS 结果都要是公网地址，重定向逐跳检查，不带 cookie 和凭据。
- 模型只能访问用户消息或搜索结果里出现过的链接，不能把对话内容拼进任意网址发出去。每个 Run 都有请求次数和下载量上限。
- 只读：不能推送代码、发评论或提交表单。外部网页、仓库和技能都当作不可信内容。

这并不等于“绝对防泄露”：搜索词、在页面链接之间怎么选、请求了哪些公开包，这些低带宽的信号依然存在。取舍见 [ADR 0007](docs/architecture/adr/0007-controlled-public-fetch.md)。

## 部署

一台装好 Docker 和 k3s 的 Linux 机器（至少 2 核、7 GB，x86_64 或 arm64）：

```bash
git clone https://github.com/shijunan126-creator/ailab-lite && cd ailab-lite
sudo ./deploy.sh --model-key sk-xxxxxxxx      # 默认接 DeepSeek；不给 key 就用离线演示模型走通全流程
```

任何 OpenAI 兼容接口都能接：

```bash
sudo ./deploy.sh --model-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
                 --model-name qwen-plus --model-key sk-xxxxxxxx --model-label "通义千问 Plus"
sudo ./deploy.sh --model-base-url http://10.0.0.5:8000/v1 --model-name Qwen/Qwen3-32B   # 本地 vLLM
sudo ./deploy.sh --models-file my-models.toml                                          # 多个模型，格式见 platform/models.toml
```

脚本会检查环境、构建镜像、生成随机凭据、部署全部服务，最后做一次冒烟测试。可以重复执行：

```bash
./deploy.sh status                                   # 运行状态和模型列表
sudo ./deploy.sh --skip-build                        # 只改配置或换 key
sudo ./deploy.sh --skip-build --registration invite --invite-code 邀请码   # 注册方式：open / invite / closed
./deploy.sh down                                     # 删除平台和数据，先确认
./deploy.sh --help
```

Portal 默认只监听本机 `http://127.0.0.1:3000`，远程访问用 `ssh -L 3000:127.0.0.1:3000 <机器>`。`--listen 0.0.0.0` 会把登录页暴露到网络上，只在可信网络里用。配置、模型目录和备份恢复见 [部署文档](docs/modules/deploy.md)。

## 开发

```bash
cd platform
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest tests/test_xxx.py -q        # 只跑改动相关的测试，全量交给 CI
cd portal && npm test
```

改代码前先读 [AGENTS.md](AGENTS.md)：里面有工作规则，也告诉你改哪个模块该读哪份文档。测试怎么分层、怎么在本地跑 PostgreSQL 和 E2E，见 [测试文档](docs/modules/testing.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `platform/ailab_platform/` | 五个服务的 Python 代码和共享的 `contracts/ domain/ ports/ adapters/` |
| `platform/portal/` | Portal（Next.js） |
| `platform/deploy/` | k8s 清单 |
| `platform/tests/`、`e2e/`、`scenarios/` | 单元与故障测试、浏览器端到端、真模型场景 |
| `images/` | 沙箱镜像和随镜像发布的技能 |
| `vendor/` | 上游办公技能原样副本 |
| `deploy.sh`、`k3s/`、`tests/` | 一键部署、k3s 与 gVisor 配置、基座检查脚本 |
| `docs/` | 模块文档、设计决策、能力状态和进度，入口 [docs/README.md](docs/README.md) |

## 第三方软件署名

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).

Crawl4AI 0.7.4 的许可证为 Apache-2.0 加附加署名要求，原文见 [许可证](images/crawler/Crawl4AI-LICENSE)。

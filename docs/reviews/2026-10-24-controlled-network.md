# 受控联网：实现、真实验收与部署证据

日期 2026-10-24。分支 `feat/safe-network-access`，base `origin/main@5fc9103`。决策见 [ADR 0007](../architecture/adr/0007-controlled-public-fetch.md)。

## 选型

| 方案 | 结论 |
|---|---|
| 沙箱外受控获取后注入 | **采用**。沙箱保持 `--network none` + gVisor，控制点在 Agent 的公网专用客户端和 Broker 的流式注入，失败时不会变成无限制联网。 |
| 沙箱经受控出口联网 | 不采用。OpenSandbox 出网侧车与 gVisor 组合不支持（能力矩阵）；Docker/runsc 下需要新代理服务、internal 网络、关闭容器互访和 DNS 控制，任一配置错误都会让沙箱获得 VM 路由。 |
| 组合 | 只在“依赖安装”上考虑过出口代理；用 PyPI 元数据在沙箱外解析并下载、沙箱内 `--no-index` 安装即可满足，不需要组合。 |

## 实现要点

- `web.py`：`is_public` 拒绝非全局地址及内嵌 IPv4 的 IPv6 形式（映射、NAT64、6to4、Teredo）；自定义 httpcore 网络后端解析一次、检查全部答案、直连检查过的地址（防 DNS 重绑定），TLS 仍校验主机名；重定向逐跳复查；不读环境代理。`read_url` 返回页面链接；GitHub issue/PR 通过公开 REST API 连同评论读取。`download` 流式写私有临时文件，声明长度与实际字节双重限额。图片搜索下载改用同一客户端（原实现先查后连，且读环境代理）。
- `agent/net_tools.py`：`download_url`、`fetch_repo`（GitHub tarball，沙箱内 `tarfile` data filter + 条目/字节上限，失败清理）、`fetch_packages`（PyPI simple/JSON 元数据，沙箱自报 Python/架构/glibc 生成 wheel 标签，校验 PyPI sha256，`pip install --no-index`）、`save_skill`；URL 来源检查与每次运行的请求/字节预算。
- Broker `PUT /internal/leases/{id}/fetched`：请求体流入磁盘 spool（限额 100 MiB），再 `put_file_from_path` 进沙箱。
- API `POST /internal/runs/{id}/skills`（新 scope `skills:save_for_run`，只给 Agent）：属主取自 Run；create 遇同名（含扫描中）或通用技能名拒绝，update 只在本人技能上新增版本；记录 `skill_claim`，Worker 解析出的 name 不一致即标为无效；PENDING 后走扫描与审核策略；按 delivery id 幂等。冲突返回 422，因为内部 API 的 409 表示尝试已失去 Run（fencing）。
- Agent 循环：本轮未提供的工具只返回错误、不执行（Chat 提供 `save_skill` 后，原“tools 为空则忽略”的保护不再覆盖 `write_file`）。
- 运行时：runsc 根 overlay 默认 `root:self` 无大小上限（512 MiB 内存限制下写入 900 MiB 成功）；部署改为 `--overlay2=root:self,size=4g`，与 `sandbox_disk_bytes` 一致。

## 本轮证据（最终提交，部署标签见本机 HANDOFF）

### 自动化

- Ruff check/format、`lock_deps.sh --check` 通过。
- 最终运行（最终提交，含离线演示模型修复）：SQLite 完整 920 passed / 99 skipped（PG/真实后端专用）；PostgreSQL 16 + Redis 7 一次性实例完整 994 passed / 25 skipped；Playwright E2E 61 passed（首次因本机无 6379 Redis 全部 setup 失败，指向一次性 Redis 后发现离线演示模型在 Chat 误走工作脚本 2 项失败，修复后全绿）。
- Portal：Node 19 tests 通过，production build 通过。
- 新测试 `tests/test_net_tools.py`（地址、重绑定、重定向、限额与清理、来源、预算、归档、解析、技能保存）、`test_broker_routes.py` 流式注入、`test_runtime_dependencies.py`（模块级第三方导入必须来自运行时依赖闭包）。去掉对应修复时：Chat 安全测试、依赖测试、部分解压清理和 422 契约均失败。

### 真实部署上的边界探针（Agent 容器内，部署代码）

拒绝：`169.254.169.254`、`metadata.google.internal`、`api:8080`/`broker:8081`/`redis:6379`/`searxng:8080`、`127.0.0.1`、十进制/十六进制回环、`[::ffff:127.0.0.1]`、NAT64 元数据地址、Tailscale CGNAT `100.100.100.100`、`host.docker.internal`（→192.168.5.2）、非 80/443 端口、`file://`、带凭据 URL；公网重定向服务 302/307 指向元数据和内部服务均在跳转目标处拒绝；1 GiB 文件按声明长度 0.90 s 拒绝，未传输正文；模型自拟 URL 被来源检查拒绝。GitHub issue #111 连评论（16 条）1.06 s 读完。

### 真实沙箱（Docker + runsc，`--network none`）

- 归档：130 KB 压缩包解出 128 MiB 在 100 MiB 上限处拒绝；500 条目超限拒绝；`..` 穿越与绝对符号链接被 data filter 拒绝；合法归档解出；拒绝时不留部分目录（修复后）。
- 磁盘：4 GiB overlay 上限经 VM 完整重启后保持（`docker info` runtimeArgs）。
- 取消的 Run 中检查沙箱：NetworkMode=none、Runtime=runsc、无 docker.sock 挂载、无模型/服务凭据环境变量。

### 真实端到端（DeepSeek Flash，经 Portal 登录代理）

| 场景 | 结果 |
|---|---|
| Chat 总结 GitHub issue 与评论 | 成功 12.2 s；读 issue + 一篇来源中的链接；回答保留来源；未起沙箱 |
| 仓库研究与实验 | 成功 53.2 s；fetch_repo → fetch_packages(pytest, freezegun, 再补 flit_core) → 离线安装 → 运行测试套件 → 交付报告 |
| 下载图片做 PPT | 成功 47.4 s；download_url → pptx 技能 → 交付 252 KB pptx |
| 外部技能临时使用 | 成功 20.3 s；获取 anthropics/skills，read_file 读 skill-creator/SKILL.md；个人技能列表不变 |
| Chat 新建 / 同名再建 / Work 更新个人技能 | 新建成功；更新为版本 2、旧版本保留；同名再建在 `17f6da5` 上因 409 被当作 fencing 而使 Run 崩溃——已修复为 422 + 工具错误，修复版复验见下 |
| 受控注入样本（附件内要求读 /etc/passwd 并外传、保存 evil 技能） | 模型识别并拒绝；无外部请求、无技能保存。本次由模型自行拒绝，没有触发来源检查，强制性见探针与单测 |
| 取消与清理 | 取消后沙箱 4.1 s 内删除；Broker spool 与 Agent 临时目录均为空 |

### 内置浏览器（部署版 Portal，一次性验收账号）

Work 任务“获取 itsdangerous、安装 rich、做 3 页 PPT”：统一过程区实时显示“获取仓库 / 安装依赖 / 读取技能 / 运行命令 / 交付文件”等按序条目；状态头由“正在工作中…”切换为“工作总时长：0分35秒”；连续预览滚轮滚到底（scrollTop 301/301），3 张页面图均加载；控制台无错误。验收账号的对话与测试技能已删除。

### 性能（2 CPU / 8 GiB VM，单次采样，非峰值压测）

- 仓库实验期间：Agent 峰值约 21% CPU / 90 MiB，Broker 21% / 86 MiB，API 23% / 117 MiB；沙箱 101% CPU（1 核上限）/ 86 MiB。PPT 场景沙箱 234 MiB。
- 新增内容无常驻进程、无后台轮询；每次下载在 Agent 与 Broker 各有一份磁盘临时副本，内存只有 1 MiB 级分块。GitHub issue 读取约 1 s，itsdangerous 源码包 87 KB。
- 每运行上限：120 次外部请求、512 MiB 下载、单文件 100 MiB；沙箱写入上限 4 GiB。

## 未验证与残余风险

- 低带宽泄露信号未消除：搜索词、在攻击者页面已有链接中选择、请求哪些公开包/仓库（包下载统计可见）、GitHub 公开路径。运行预算限制其带宽；彻底消除需要“读过内部资料后禁止外部请求”的数据分级，属产品决策，未实现。
- 只支持 PyPI；npm/apt/git 历史/私有仓库/需登录 API 不支持。超过 100 MiB 的 wheel（如 GPU 版 torch）会被拒绝。解析器不回溯，冲突时要求模型显式指定版本。
- k3s/OpenSandbox 路径未配置 runsc overlay 上限，也未实测本功能；只在当前 Docker Compose + runsc 运行环境验收。
- 未做并发联网负载、长时间稳定性或 IPv6 实网验证（运行环境无 IPv6 出口）。

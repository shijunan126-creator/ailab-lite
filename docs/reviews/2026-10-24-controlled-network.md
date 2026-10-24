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
- 未做长时间稳定性或 IPv6 实网验证：本机网络本身无公网 IPv6（仅 Tailscale ULA），VM 与容器网络也未开 IPv6。代码对 IPv6 地址同样做公网检查并在多地址间回退到 IPv4，双栈站点不受影响，纯 IPv6 站点不可达。

## 补充：并发（同日，部署 `net-944d977`，迁移到外置 APFS 卷之后）

- 网络层（Agent 容器内，一个事件循环同时发出）：87 个请求 2.0 s 完成；42 个内网/元数据/重定向目标全部拒绝，45 个公网读取与下载全部成功，p50 0.98 s / p95 1.52 s；同一 URL 各轮下载哈希一致（无串流）；探针进程峰值 99 MiB，Agent 容器峰值 140 MiB / 384 MiB。
- 端到端（两个账号同时，DeepSeek Flash）：每人 1 个 Chat 读 issue + 1 个 Work（仓库 + 依赖 + 测试 297 通过；下载图片做 PPT），4 个全部成功，总 93 s；第 3 个同时提交按 `max_active_runs_per_user=2` 返回 429。Work 期间 Chat 按 `agent_concurrency=3` 排队（第二个 Chat 等 6 s）。图片任务中模型自拟 URL 被来源检查拒绝后改为先搜索，属预期行为。峰值：API 144 MiB / 30%、Broker 141 MiB / 33%、Agent 113 MiB、沙箱 220 MiB / 1 核；结束后无遗留沙箱、Broker spool 与 Agent 临时目录为空。

## 补充：k3s / OpenSandbox / gVisor（实现分支，2026-10-24）

基线 `main@1a384558b4ac27deea660e5314d82eddf462b386`，分支 `fix/k3s-osb-network-path`；保留上方 Claude 的并发/IPv6补充。以下是本轮新证据，和前面的 Compose/真实模型验收分开。现有 Compose 运行环境未切换或重启。

### 部署与根因修复

- 独立 ARM64 VM：3 CPU / 5 GiB，k3s `v1.34.3+k3s1`、containerd `2.1.5-k3s1`、gVisor `release-20261019.0` 完整发行包（官方 SHA512 校验）；OpenSandbox Server/SDK 1.1.1，CRDs/controller/execd 镜像为上游 `release-1.1.1`。临时镜像是 `python:3.12-slim` + uid/gid10001 + 可写 `/workspace`，不是 Office 全量镜像。镜像导入后为 `sha256:e5ca698fc1c26affec947b3320d795fda1e6046183bc545d6e0714d87329a341`（Docker manifest/index ID；CRI config ID为`4591dd8b05022243964130c72571e082dae0924e4448016c8ebabd60f3465a7b`）。
- **磁盘**：节点 `ConfigPath` → `/etc/containerd/runsc.toml`，`[runsc_config] overlay2="root:self,size=4g"`；namespace 增加每容器 5 GiB ephemeral-storage limit / 64 MiB request。`k3s/install-runsc.sh` 接入节点安装流程；deploy preflight检查实际 CRI runtime options、根限额、IPv4 Pod CIDR和ICMP规则。不是依赖模型遵守 `sandbox_disk_bytes`。
- **网络**：真实 Server 创建 `gvisor + NetworkPolicy(default_action="deny")` 返回 `INVALID_PARAMETER`，明确指出侧车依赖不支持的 iptables NAT。新增显式 `osb_network_mode=kubernetes` 委托 namespace/CNI/节点断网；默认仍是 sidecar，创建失败不自动去掉策略重试。secure 在缺隔离验收声明时拒绝启动；声明只是运维对外部基础设施验收的确认，不能防止之后策略被删除。
- **额外根因**：kube-router先于default-deny放行ICMP；gVisor无CAP_NET_RAW仍能创建SOCK_DGRAM ping socket，`net-raw=false`及ping_group_range不能关闭它。新增节点systemd一次性规则：raw PREROUTING丢弃`10.42.0.0/16`源ICMP；也影响其他Pod的诊断ping。范围固定IPv4单节点，preflight拒绝不同/双栈Pod CIDR。允许的execd入站请求的回应仍必须通过，不能把“无主动出口”说成完全没有网络包。
- **注入目录**：OSB两条写入路径在既有exec预检中完成路径约束检查后，以执行用户mkdir父目录，再交SDK；流式仍使用同目录临时文件和mv。没有额外exec往返或全文件缓冲。
- **部署接线**：OpenSandbox模板固定gvisor、非root、drop ALL、无提权/ServiceAccount token；`osb_up.sh`核对runtime/namespace/template及已有进程PID/命令行/启动配置摘要，拒绝复用Docker配置或来源不明的健康端口。deploy现在导入并分别校验sandbox和platform两个镜像，原来只导入platform。已在临时VM用仓库脚本实际生成私有配置、启动server、检查0600权限并跑node preflight；整套平台deploy.sh仅做dry-run和脚本回归，没有声称五服务重部署通过。

### 本轮真实验收

| 项目 | 证据 |
|---|---|
| 4 GiB 强制上限 | uid10001、512 MiB内存Pod，`statvfs`=4294967296；实际逐MiB写入4294967296字节后errno28 ENOSPC（10.78 s），finally删测试文件。不是只看配置或容量报告。 |
| TCP主动出口 | 公网443、集群API Service443、正在运行的CoreDNS Pod53、节点上正在监听的OSB端口、元数据169.254.169.254:80均拒绝。前四个目标从节点先连通，避免把目标停机误当隔离。 |
| UDP/ICMP | 节点UDP echo先自检成功，沙箱不能收回echo。移除节点ICMP规则时真实OSB契约因`AssertionError: ICMP egress`失败；finally恢复后通过，raw规则计数增加。Docker `--network none`同一契约通过。 |
| 父目录/流传输 | mock/Docker/runsc/真实OSB共6项：新多层含引号中文目录的两种上传、exec用户属主、后续写文件/解压、SHA256、超限和symlink拒绝。SDK fake上传预检回归原实现4 failed、修复4 passed。 |
| 生命周期与取消 | mock/Docker/真实OSB生命周期、续租、失联重连、真实创建后取消、排队取消、过期物理删除、stale listing回收边界，共10项；与传输合计16 passed。不是通过真实Agent Run发起取消。 |
| Agent受控获取→Broker HTTP→沙箱 | Docker与OSB各通过1个综合契约：真实公开文件哈希、itsdangerous 2.2.0源码→解压、平台标签探针→PyPI wheel→`pip --no-index`→import。 |
| 归档拒绝与清理 | 同一综合契约生成并经真实Broker注入的解压bomb、过多条目、`..`穿越、绝对symlink全部拒绝，目标/`.partial`均不遗留。样本用较小配置阈值触发同一代码分支，没有生成1GiB归档。 |
| save_skill | 真实OSB/Docker读取新目录中的SKILL.md并将完整载荷交API stub；本轮全套API/Worker测试覆盖归属/PENDING/扫描/版本，不是本轮真实五服务保存技能端到端。 |
| 回收 | 综合契约finally释放，provider inspect确认不存在、Broker transfer spool为空；本轮取消契约也确认无遗留provider资源。 |

Agent侧防护不依赖沙箱runtime：公网专用客户端拒绝k3s的私有集群/Service/节点网段，仅允许HTTP(S)的80/443；DNS固定、逐跳重查、来源与请求/字节预算继续生效。直接沙箱出口与Agent公网获取是两套独立边界。

### 成本、范围与验收限制

- 新增常驻应用任务为0；节点规则是systemd oneshot，单条raw匹配。上传在既有预检exec里增加mkdir，不增加网络往返。新增ephemeral-storage请求每容器64Mi，根overlay每容器最多4GiB；Agent和Broker下载临时副本成本与原设计相同。
- 单次空闲采样（不是峰值/负载基准）：OSB host server RSS约128.4MiB、进程平均CPU0.6%；controller约16MiB/2m CPU；k3s进程RSS约498MiB、进程平均CPU7%。4GiB写满探针10.78s只是本机临时盘观测，不是交互延迟承诺。没有运行全平台+ClamAV/Office的联合资源验收。
- 根overlay限额不约束新挂载的可写卷；LimitRange依赖kubelet最终驱逐，不是硬实时磁盘配额。固定模板不提供用户可写额外卷；以后加入卷必须另设同步限制。节点磁盘还需为镜像、日志、OSB安装器卷和系统留余量。
- 未验：完整五服务k3s部署、真实模型、Office渲染、真实技能保存/扫描端到端、长时间/并发负载、IPv6、多节点或其他CNI。现有Compose部署与数据没有被本轮切换。临时VM期间发生外盘卸载/重挂导致管理socket异常，只恢复临时VM；现有Portal仍200，但现有VM管理通路需协调者另行检查，本轮没有重启它。
- 必需平台Ruff和新增k3s脚本Ruff、ShellCheck warning、bash语法、双overlay渲染及diff检查通过。依赖未改，lock check不适用；Portal未改，由E2E fixture执行其Node tests与production build。额外全仓Ruff扫描发现历史图形/证据/技能脚本28项问题，未修改无关文件。

### 全套检查与代码基线

- 本轮代码/配置基线：上述main提交加本PR工作树，23个变更代码/配置文件的排序`path NUL content NUL`聚合SHA256为`e9f3ccb34cc4117a58453781b0ae54f965225d6ada47ec14d4a6e17a472b7ee4`（不含文档和.DS_Store）。核心改动：SQLite + 临时Redis完整968 passed / 67 skipped；PG16 + Redis完整1003 passed / 33 skipped（204.86s）。核心双库后最后增加的启动进程/配置绑定 helper由脚本专项5 passed及真实Linux启动→复用成功→改配置拒绝验证，没有重复两套完整Python。
- `AILAB_E2E=1` + 临时Redis：61 passed（176.03s）；fixture执行Portal Node tests与production build。未修改Portal。
- 真实Docker/OSB：传输+生命周期16 passed；受控获取综合契约2 passed / mock skipped；网络契约Docker/OSB各通过，OSB的ICMP去规则红/恢复绿；再用仓库生成的Kubernetes服务器配置执行OSB传输/网络3 passed（14.55s）。fake SDK/mode/secure门禁7 passed。临时节点实际执行installer和node preflight，ICMP systemd单元enabled/active。
- 已核对始终没有创建真实模型调用，未使用任何真实密钥；不把这些契约称为完整平台/生产验收。个人机器故障与原始日志只留私有运行交接。
- 清理完成：临时PG/Redis容器与卷、k3s VM及其独立目录已删除，临时端口关闭、无遗留sandbox Pod或Lima进程；外盘可用空间实测增加10495976 KiB（约10.01GiB）。保留原始小体积日志在私有运行交接目录。

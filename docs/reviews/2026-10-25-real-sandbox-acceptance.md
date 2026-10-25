# 真实沙箱可运行性验收

2026-10-25。用户要求确认项目实际能否跑通，尤其补足前轮mock沙箱没有覆盖的OpenSandbox。起点main `9cfadfa`，代码/测试基线 `6933c72`（前两项修复分别为`6d5c305`、`1ca7c6d`）。未更换已有部署、个人配置或业务库；原始日志、JUnit、文件hash和运行审计仅保存在仓库外。

## 结论与组合

真实沙箱业务流程能跑通；初始默认配置并未满足完整deny-all，需要本轮配置修复。以下是当前本地证据，不是前次mock E2E或历史部署结果：

| 组合 | 实际执行 | 结果 |
|---|---|---|
| Docker + runsc + 当前技能Office镜像 | 生命周期/TTL/取消创建、流式传输/限额/符号链接、技能不可变及工作区恢复、Broker重建/Redis记录丢失/外部杀容器 | 8通过/0跳过，另命令超时/取消1通过 |
| OpenSandbox server/SDK 1.1.1 + Docker/runc + egress dns+nft | 同上，另命令超时与执行中取消 | 8通过/0跳过 |
| 同一OpenSandbox + 网络探针 | 外部正常容器先证明非root TCP/UDP/ICMP可到达活跃目标，再从沙箱验证阻断 | 1通过/0跳过，仅IPv4本机端点 |
| SQLite + Redis + OpenSandbox + 五服务生产Portal | 浏览器登录、多轮历史、命令执行、Excel生成/预览/修订、PPT生成、上传/扫描/下载/删除、DOCX真实Office预览 | 7通过/0跳过，160.52s；此轮server仍为DNS-only，仅作业务证据 |
| PG16 + Redis + OpenSandbox dns+nft + 同上五服务 | 同一7条浏览器路径 | 7通过/0跳过，168.87s |
| SQLite + 真实Redis全Python套件 | 包括新增生命周期及启动保护回归 | 1178通过/81跳过，179.81s |
| PG16 + 真实Redis全Python套件 | 包括迁移与查询/账本/备份 | 1224通过/35跳过，223.43s |

浏览器栈模型为确定性mock、扫描为测试扫描器、对象存储为独立本地目录；沙箱、Office工具、HTTP、队列、数据库和Portal生产构建真实运行。没有调用付费模型。Portal原生测试35通过、生产build通过；Ruff check/format共339文件、ShellCheck与bash语法通过。主线PR #46新增auto-skill，本轮验证包含该主线内容。

Office测试镜像复用已存在、Dockerfile与依赖相同的Office基底，COPY本工作树的技能与校验器后执行真实构建校验和租约attestation。execd镜像v1.1.0、egress镜像v1.1.7由server 1.1.1示例配置选定；此轮未从零下载/重建Office系统依赖。不能把此结果称为冷构建可复现证明。

## 发现、失败与修复

1. **DNS-only不是deny-all。** server生成的`[egress] mode = "dns"`即使配合SDK的`NetworkPolicy(default_action="deny")`，仍能通过字面IP连接活跃TCP目标。原测试明确报`TCP egress`。改为`dns+nft`后，外部正对照仍能连，沙箱TCP/UDP/ICMP检查通过。`osb_up.sh`现在对新配置启用包过滤，拒绝旧DNS-only配置，不通过修改个人配置或丢弃网络策略解决。健康server必须匹配存档的启动PID/配置hash，防止改文件后复用旧策略进程。
2. **Docker到期竞态。** 实测容器在首次inspect后、读取根元数据的exec前退出，触发`docker lease metadata unavailable`；finally删除还可能撞上自动删除的`already in progress`。现在仅在失败路径复查真实状态；仍存活的权限/元数据错误、daemon失败继续报错。对正在删除的容器最多等5秒，并确认不存在才成功。新增回归原实现3项失败，修复后10项适配器测试通过；真实Docker全组复验通过。
3. **测试资源边界。** 首轮沿用旧Office镜像缺auto-skill，真实attestation正确报409；同步专用测试镜像后通过，未绕过校验。同daemon上的Docker/OpenSandbox都把owner写在Docker标签里；共享默认owner会让对账互相看见，首轮同时运行出现OSB工作区快照期间资源消失。provider夹具及浏览器栈现在各自使用唯一owner，后续并行/完整复验通过。
4. **验收启动条件。** 一次测试选择器名称写错，收集0项即退出，纠正后实际执行7项；一次临时server重启尚未完成就发请求，发生连接断开，按重启完成顺序复验。临时平台基底没有curl，首次直接运行启动脚本无法判断ready；脚本现在在起进程前检查curl依赖，并绕过代理检查回环健康。补齐专属测试容器依赖后，真实脚本生成配置、启动、再次核对复用、按PID停止均通过。上述探针/夹具失败不计为业务通过，也不以重试掩盖最初网络漏洞。

## 成本、隔离与清理

没有新增生产常驻服务、迁移或依赖。Docker正常存活路径无新增外部调用；仅失败/删除冲突路径增加状态确认，删除等待有5秒截止。OpenSandbox仍是已有可选后端；本轮server临时配额512MiB/0.5CPU，短时Docker内存采样约145–173MiB（含测试探针的时刻不同），不是峰值或2CPU/8GiB容量验收。`dns+nft`增加的是原egress侧车的包过滤，不把DNS-only当作安全降级。

专用Redis和PG使用非默认端口及独立数据目录；两个完整测试库与浏览器库分开。停Redis前核对pidfile与INFO process_id一致，PG按专用data目录停止。临时OpenSandbox列表确认0沙箱，删除专属server容器及本轮Office/execd/egress镜像标签；不执行全局prune。五服务/浏览器子进程已结束，专属端口无监听；保留原始证据文件和测试数据库目录，不清理其他并行任务的服务。内置preview两次返回`Preview automation snapshot timed out after 15000ms.`，实际浏览器验收走项目Playwright路线。

已有部署只做健康、Portal登录/API列表读取和数据库完整性检查：五应用始终健康，provider为Docker，数据库仍0028，quick_check正常/FK0。所有开始时的业务ID仍在；运行期间有用户新建对话/Run，保留新增数据。没有向该部署发起模型任务或上传文件，不宣称其代码已更新到主线。

## 未验边界

未重跑付费模型/ClamAV/S3全栈组合、真实OpenSandbox Kubernetes+gVisor、IPv6、多节点、24小时稳定性、硬件存储链路及生产容量。OpenSandbox此轮使用Docker/runc+网络侧车；Docker provider使用runsc，二者不能合称OpenSandbox+gVisor通过。网络探针范围为已知活跃本机IPv4端点，不替代所有公网/metadata/CNI路径的独立验收。现有部署没有应用本轮代码或0029迁移，仍需后续发布授权。

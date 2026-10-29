# 真实组件验收 · 2026-10-23

工作树：`/Users/michael/.t3/worktrees/ailab-lite/t3-4a3b78af`。分支：`verify/real-components`，基线 `feat/arch-hardening@9c0c808`。仅本地提交，未推送。

## 环境与边界

专用 Colima `ailab-verify`，原生 aarch64 Linux 6.8.0-117-generic，2 vCPU、7,922 MiB RAM、无 swap、60 GiB 数据盘；k3s `v1.35.0+k3s1` 使用 Docker runtime。所有 Docker 命令显式使用 `--context colima-ailab-verify`；集群命令通过该 VM 的 `sudo k3s kubectl`，没有切换其他 context。启动命令增加 `--activate=false`。宿主原本缺 kubectl，安装客户端后才启动成功。

ClamAV 原要求 `clamav/clamav:1.4` 没有 arm64 manifest，临时 amd64 验证不能代表 ARM64 资源验收。用户指出的 `clamav/clamav-debian:latest` 实测为 1.5.4，支持 amd64/arm64/ppc64le；最终清单固定本次原生镜像 digest `sha256:d7ede3cefeaf3b8a72ebcdee849d13f504bbca3f3b3cfade5bc7b6ee06a4f62c`。最终资源、扫描和 k3s 结果均使用它，签名版本 28162。

MinIO 指定的 Docker Hub tag 返回 pull access denied，两次 Quay tag 返回 401，官方 Linux arm64 二进制下载返回 410。用官方源码 tag `RELEASE.2025-04-22T22-12-26Z` 在 VM 内构建真实 MinIO，未换成 S3 模拟器。PostgreSQL 16、Redis 7、Keycloak 26.6.4 均为容器；realm/client/bucket/database 全部一次性。离线模型 `ailab-demo`，没有付费模型调用。

## 验收结果

| 项目 | 结果 | 实际证据 |
|---|---|---|
| ClamAV | 通过 | 原生 clean/EICAR 契约 1 passed / 0.04 s；Worker EICAR→rejected、clean→ready；暂停 clamd 时 pending/scan_unavailable 30.027 s，恢复后 ready/clean 36.858 s。暂停模拟不响应的真实进程，签名/容器不重建；不是重新下载签名的恢复时延。 |
| S3 | 通过（列明边界） | Local/S3 共用契约 15 passed / 1 local-only skipped / 2.29 s：presign、freeze、copy、异常关闭流、分页/delete_prefix。全局 `storage_backend=s3` + PG16/Redis 最终全套 753 passed / 9 skipped / 101.26 s；真实桶中观测到 preview 与 thumbnail 对象。Office renderer 在单元测试中的双替身边界仍存在，真实 OpenSandbox Office 端到端本轮使用 Local PVC。未测大规模 S3 历史版本延迟。 |
| PostgreSQL + Redis | 通过 | Linux 容器内全套，含迁移/PG dump 导入与真实 Redis 契约。首次 741 passed / 15 skipped / 2 failed；PG17 pg_dump→PG16 导入因 transaction_timeout 失败，换成同大版本 PG16 客户端；libxml OOM 分类修复后全套绿。 |
| Keycloak | 通过 | gate 1 passed / 2.74 s；生产 Portal + Playwright 实际登录、/qa→dept_admin+auditor+user 及 verify-dept、Strict/httpOnly Cookie、logout→401、停用账号回调拒绝且 /me→401。 |
| Office 镜像 | 通过 | `images/sbx-office` 原生构建，smoke 10 页 PPT 渲染、字体映射、网络拒绝均通过，cgroup peak 158 MiB。`render_preview.py` 的中文 DOCX/PPTX 各一页 JPEG 已逐图检查，中文字形完整。 |
| Docker / OpenSandbox | 通过 | 生命周期 Docker 1 passed / 2.81 s，OSB 1 passed / 6.60 s。新增真实 provider 故障用例共 2 passed / 9.07 s：Broker 重启、移除该 owner 的 Redis 记录后恢复、外部删除资源后 typed lease failure；未执行所有 mock 故障组合的真实 provider 版本。 |
| Work / 资料库渲染 | 通过 | k3s→OSB Work PPTX succeeded / 14.192 s；资料库 DOCX reserved render slot ready / 8.119 s，JPEG 29,924 B。资源负载单独 DOCX 上传+扫描+预览共 9.057 s。 |
| k3s | 通过（lite 边界保留） | 7/7 Pod Ready；Agent→ClamAV ConnectionRefused，Worker→ClamAV/Broker 连接成功；公共 /internal、/metrics、/docs 均 404。实际 Deployment Secret 引用仅自身 identity+verifier，model 仅 API/Agent，OSB key 仅 Broker，账号凭据仅 API。deploy.sh 原有 smoke 函数 WITH_PORTAL=0 离线 Run 8 s 成功；Portal 登录另验。 |
| readiness 故障 | 通过，新增修复 | 真实暂停 Redis 后 API 在 1.098 s 返回 200/degraded（只依赖队列的能力降级），Agent/Worker 1.007/1.010 s 返回 503；unpause 后均 200。修复前探测读超时超过 10 s，结构性回归 3 s 超时红，修复后 1.16 s 绿。 |
| 资源 | 通过单轮负载，非并发容量承诺 | 原生 50 MiB 上传 3.600 s；20 MiB PPTX 扫描+解析 27.906 s；50 MiB Broker 注入 3.570 s；100 MiB artifact 交付 3.020 s；无 OOMKill，存在 CPU throttling，见下表。 |
| k3s 备份恢复 | 通过 | 暂停 API/Agent/Worker/Broker 写入，备份实际 PVC：57 对象 / 252,762,640 B；create 1.826 s、verify 0.391 s、restore 1.572 s、verify-restored 0.355 s；进程 peak RSS 60.06 MiB。空目录恢复后独立真实 API 下载 100 MiB artifact，SHA256 一致（0.562 s）、DOCX preview JPEG 可读、原 Run succeeded。不是生产规模 RPO/RTO 或异机备份验收。 |

最终全套的 9 个 skip：Keycloak gate（另跑通过）；Docker/OSB lifecycle（各另跑通过）；Docker/OSB real fault（各另跑通过）；内存队列不适用 Redis cursor 的用例；Local inode-only 用例；宿主测试镜像没有 Office/PDF 工具的 2 项（Office 镜像与真实 OSB 单独通过）。这些 skip 未冒充全套内的 pass。

## 资源与成本

RSS 为 Pod 主容器 cgroup 内所有进程的 VmRSS 之和，含解析子进程；共享库页面可能重复计入。cgroup memory.current 含可回收文件页缓存，是限额比较的另一依据。负载以 0.1 s 采样，886 样本 / 91.445 s，采样器 CPU 1.884 s（约一个核的 2.1%，未扣除）。steady 为修复 readiness 后另做的 5 s 空闲采样；负载峰值在 readiness 修复前，同一资源限额、原生 scanner，健康探测新增一个有界连接池，未重跑全部负载。

| Pod | 空闲 RSS MiB | 负载最大 RSS MiB | 最大采样 cgroup MiB | limit MiB | 负载区间 throttled CPU 时间 s |
|---|---:|---:|---:|---:|---:|
| API | 105.36 | 110.07 | 277.20 | 384 | 0.219 |
| Broker | 116.70 | 284.37 | 301.30 | 384 | 0.277 |
| Worker | 89.76 | 173.21 | 178.13 | 384 | 5.708 |
| Agent | 94.46 | 94.21 | 133.62 | 256 | 0 |
| ClamAV | 1045.90 | 1084.67 | 1095.18 | 2048 | 6.786 |
| public nginx | 8.53 | 8.48 | 47.53 | 48 | 0.179 |
| Redis | 8.90 | 8.86 | 14.05 | 128 | 0 |

所有观察到的 memory.events.oom_kill=0。ClamAV 主进程独立 `/proc` VmRSS 1,047,884 KiB（约 1023.3 MiB），上表是整个 scanner 容器。nginx 页缓存峰值接近限额但 RSS 小，单轮未 OOM；不把它理解成还有大量不可回收内存余量。Broker 注入阶段 peak RSS 265.4 MiB，交付阶段 284.4 MiB，确认 HTTP/SDK 缓冲带来的整文件复制成本；现有 384 MiB 下单次负载通过，不证明并发安全或满足流式内存要求。Office 构建/镜像磁盘与实际容器总 RSS 不包括在业务 Pod 的表内。

备份探针限制 0.5 CPU / 256 MiB，约 241 MiB 备份+恢复各占一份磁盘；总 wall 5.553 s，使用顺序对象复制与哈希。没有新增运行时依赖；健康探测每个 RedisQueue 多一个最多 1 连接的 pool，1 s connect/read，无重试，业务队列 I/O 超时保持原值。

## 已修复

- `96a0ca8`：OSB 服务端校验 metadata 为 Kubernetes labels，ISO 时间戳和 JSON 直接传入导致 INVALID_METADATA_LABEL。adapter 对不合法值逐字段 base32 分块，读回解码；普通 owner label 保持可筛选。真实契约先失败后通过，codec 回归涵盖长值、中文、空值、PATCH 后旧块尾部。
- `9e37e23`：Linux RLIMIT_AS 下 libxml 的 XMLSyntaxError/ERR_NO_MEMORY 被归为 corrupt。按结构化错误码归为 memory_limit，原始真实内存故障测试先红后绿。
- `cc5fafd`：Keycloak 302 回跳在当前 Chromium 未触发 Playwright route 拦截，gate 误报失败。用真正的本地 callback listener 接收授权码，不记录敏感 query，真实 gate 通过。
- `f06880e`：补真实 Docker/OSB Broker 故障验收，不再把 mock provider 的 Redis 用例称为真实 provider 故障验证。
- `fe93f13`：scanner 清单从没有 arm64 的 tag 换到已验证的 ClamAV Debian 多架构 digest。
- `02ab438`：Redis socket 停滞时 readiness 超时，独立短超时无重试 health pool；真实故障与回归均通过。

## 保留的问题与未覆盖范围

- Broker 注入/交付/工作区路径仍整文件缓冲，根因是平台客户端和 provider SDK 的 bytes 接口。建议改为端到端有界流，并增加并发负载验收；本轮没有重设接口或以单次通过消除该设计问题。
- lite Broker hostNetwork 受宿主网络规则约束；Pod NetworkPolicy 不可保证该路径隔离。公共 nginx 路径白名单和 Pod ingress 已测，未验证公网宿主防火墙、外部攻击面、gVisor 或多节点。
- CI 仍引用已不可拉取的 MinIO Docker Hub tag；真实本轮用官方源码构建绕过。建议维护镜像镜像源/digest 或在 CI 构建固定源码，再独立验收；本轮未修改 CI 工作流。
- 原始任务的 clamd “stop”使用 Docker pause 模拟运行中网络停滞（强于立即拒绝连接），恢复为 unpause。没有重新启动容器并重载签名的恢复峰值测量；ClamAV 1.4 amd64 的历史记录仅保留诊断，不作为原生验收。
- Work、Office、scanner 真实链路使用 k3s Local PVC；S3 下的 preview/thumbnail 写读和全套 API/Worker 行为通过，但真实 OSB Office renderer+S3 联合部署、生产规模版本链性能、长期 soak/并发饱和未验。
- 备份恢复针对本轮有限数据集，恢复后启动 API 而非第二套 k3s；没有第二台机器、异机备份介质、真实企业 OIDC 或付费模型。
- 中断后 VM 已停止、原采样结果未写完，未使用那次不完整峰值。重启仅专用 profile 后重做原生负载；未恢复或触碰开发者 `.env`、var 或其他 profile。

## 可复查证据

命令入口和原样脚本见 [commands.md](real-components-20261023/commands.md)。关键结果、故障红绿与资源/备份摘要见本文；复现脚本及必要中文 JPEG 保留在 [real-components-20261023](real-components-20261023/)。不包含生成的服务 token、OIDC 授权码、Cookie、数据库备份或大文件。完整本轮临时材料保留在 `/Users/michael/.cache/ailab-real-verify-20261023`，私密 token 文件仅位于任务临时目录，清理时删除。

清理已完成（2026-10-23 15:26 +08:00）：删除专用 `platform` namespace/PVC、本轮测试容器及卷、全部本轮自建镜像与无引用构建层；仅保留上游基础镜像和 profile。OSB 后台进程、宿主 Portal/API 与端口转发均停止，私密 token/kubeconfig 临时副本删除。`colima list` 显示 `ailab-verify Stopped`；没有本轮服务留在运行。

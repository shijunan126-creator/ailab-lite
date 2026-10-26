# 2026-10-26 已合并 PR92/93/94 验证与超时修复

本轮授权：按用户给出的交接补测并修复问题，仅本地，不 push/PR/merge/deploy。原主目录 `fix/full-thinking-rendering@ab0e8d9` 的未提交文档和 `.DS_Store` 保留；隔离工作树 `/private/tmp/ailab-reader-validation`，分支 `fix/merged-reader-validation`，base `origin/main@c6ff8f9`。产品与回归提交 `fa562ab`。

## 已确认的合并状态

本轮实时查询：PR92 `ddaf756`、PR93 `f2964ec`、PR94 `c6ff8f9` 的合并后 main CI 均已完成并成功，分别为 [40309867797](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40309867797)、[40317411007](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40317411007)、[40317523306](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40317523306)。后两项不再是“尚未完成”。这不是本轮修复提交的远端 CI；本轮没有发布授权。

原 `af2f98b`、`371798f` 是合并后的本地文档，不是遗漏的产品修复。本轮记录整合其验收状态，未把历史分支整条合回，也未另推文档 PR。此前浏览器 115/115、原生包/代理结果属于历史证据，以下区分本轮实测。

## 发现与修复

共同根因：把阶段或空闲超时当成了整次调用截止时间。

- Agent 的 HTTPX read timeout 只限制相邻数据块之间的静默；缓慢持续回传可以无限延续，调用前 DNS 也不在预算内。`_read_rendered` 用一个 `asyncio.timeout(crawler_timeout_seconds)` 覆盖 DNS、服务请求和响应读取，超时仍归一为既有 WebError，并关闭流。
- egress 原来 DNS 无截止，每个地址各享完整 connect timeout；现在一个 CONNECT_TIMEOUT 覆盖 DNS 与所有连接尝试，超时返回受控 502。
- crawler 原来仅包围 `arun`，不含浏览器启动/退出；现在同一读取预算包围浏览器上下文生命周期和导航，不增加原预算。

红灯证据：原 main 在新增三个 Agent/proxy deadline 测试上 **3 failed**；原 Linux 镜像在浏览器启动永不就绪的新增测试上 **1 error / 4 passed**（外层测试 deadline 到期）。修复后定向 **42 passed**；镜像内真实 Crawl4AI 0.7.4/aiohttp 3.12.15 的 **5 tests OK**。未弱化原断言、跳过或超时。

## 本轮验证

- 基线 `c6ff8f9`：完整 SQLite **1351 passed / 140 skipped**（首轮未配置 Redis）；PostgreSQL16 + 专用 Redis **1439 passed / 52 skipped**，474.94s。外部 Docker/OpenSandbox/S3/ClamAV/付费服务及可选 opensandbox/OTEL 等按环境跳过；不能当作全外部后端验收。
- 最终 `fa562ab`：完整 SQLite + 专用 Redis：**1394 passed / 102 skipped**，209.97s，0 failures/errors；共1496项，外部服务/PG专用路径及可选依赖等跳过。
- 最终 `fa562ab`：PostgreSQL16 环境相关定向 **42 passed**；未重跑修复后的完整 PG suite。此次不改数据库/查询/迁移。完整 PG 上述计数明确是 main 基线。
- 基线五服务真实 Playwright **115 passed / 0 skipped**，含首页、桌面/390px 项目菜单、旧详情响应覆盖队列、项目删除导航和 PPT。Portal **42 Node tests**、production build/TypeScript 通过；修复未改 Portal。内置预览 snapshot 两次均返回 `Preview automation snapshot timed out after 15000ms.`，采用仓库规定 Playwright 流程。初次 symlink 复用 node_modules 被 Turbopack 的根目录边界拒绝，改成本工作树内硬链接后构建通过；不是产品失败。
- 最终 platform Ruff check/format **386 files**，crawler 两文件同项目配置检查通过。首轮从根目录调用用了其他配置；没有为消除该环境差异改动代码。
- Linux/ARM64 首次完整构建成功（平台与 crawler）；修复后两镜像再次成功构建，crawler 镜像约 **759 MB**（未压缩）。真实镜像内测试通过，不再只依据原生 macOS 环境。

## k3s 与组件资源

独立内盘 VM：2 vCPU / 8 GiB、k3s v1.35.0+k3s1、Docker29.5.2，无主目录共享/SSH-agent/全局 context 切换。使用仓库原 crawler/egress manifests，只替换为本轮镜像；不涉及个人生产 VM、业务库或活跃 Compose。

- 实际 UID10002、read-only root、drop ALL、无 service-account token 和平台/模型凭据；正常 HTTPS 与 JS quotes 正文读取成功。Agent `web.read_url` 完整静态→弱页→renderer 链路得到 JS 生成的 Albert Einstein 正文。
- 最终镜像真实契约 **7 passed**：HTTPS/Markdown、真实 JS 正文、错误 bearer、loopback、同容器 `/healthz`、云元数据、RFC1918 拒绝。HTTP错误页仍为错误，不能交成文章。凭据仅为一次性测试 fixture。
- k3s **16 个 TCP 正反探测**：普通 Agent/outsider Pod 均可直达公网443及私网测试 HTTP80，证明目标可用；crawler 两者均被拒；proxy可连公网但不能连私网。仅 Agent 可连 crawler8090；仅 crawler 可连 egress4750。与真实浏览器私网拒绝共同证明本轮 NetworkPolicy 生效，非只检查 YAML。
- 初轮 native-like k3s负载：JS页约13.16s、双并发React约31.24–31.29s，观察crawler最高658.8MiB；最终镜像独立双并发复验两请求均200，约 **5.75–5.81s**，各6000字符；JS页约5.11s。公网/缓存/负载会波动，不把前后耗时差当作性能优化证明。
- 最终约2秒间隔9次 Docker stats 采样，crawler **89.16→最高684.1→96.21 MiB**，CPU最高100.81%；egress **40.75–40.84 MiB**、CPU最高2.07%。单JS请求阶段同时有一次Agent链路读取，React双并发阶段无其他读取；因此只宣称观测上限，不宣称精确RSS峰值。较早把真实契约与负载并行导致一次合法503 busy，该组不作为双并发耗时，已单独重验。所有测试 Pod 重启0，crawler OOMKilled=false。
- 资源为相同规格隔离 VM 的组件短测，不是线上整栈容量：未测任意超大/恶意页面、长期并发或真实手机；不保证两浏览器对所有网站都能容纳在1GiB。新截止时间无新增依赖/后台任务，仍每请求新浏览器，slots上限2；DNS/慢响应超时会更早释放槽位。

[可检查的网络/资源证据](../../reviews/2026-10-26-reader-validation.json)。原始日志在本机 `/private/tmp/ailab-reader-{image-build,image-fixed,platform-build,platform-fixed,deadline-red,server-red,targeted,pg-tests,pg-targeted,e2e,sqlite-final,real-contract,network-final,load-final-isolated}.log`；仅为本机临时原始证据，不依赖其长期保留作能力声明。

## 收尾与下一步

专用readercheck VM及其容器/卷/镜像已删除；PID核对后关闭专用Redis66555及端口转发。26389/25432/46443/48090、38180–38183/33100无监听，Docker context仍default；原主目录git状态与进入本轮一致。生产VM/数据/个人配置未改动。

本轮未 push/PR/merge/deploy。个人 Compose 仍未接入 crawler/egress；上线前需单独授权，准备两容器、内部独占代理网络、Agent专用token/地址及资源限制，再做备份、固定合并SHA发布和线上整栈验收。不能把 k3s NetworkPolicy 直接当作 Compose 已有隔离，也不能只给两个容器加普通共享 bridge 网络就声称隔离完成。Transitive crawler 依赖尚未全锁定，首次实际构建验证不保证未来重建版本相同。当前交付下一步仅为用户审阅本地修复/记录；不自动发布或开始其他路线。

## 后续发布授权

用户已授权本轮修复全部提交/merge、最后测试清理并部署最新版。上一节“不发布”是历史阶段；当前执行顺序为PR CI→固定head squash→合并main完整CI→个人Compose隔离接线验收→备份/切换/线上验证。发布进度由current.md和本节后续更新接续，个人配置/原始数据仅留本机。

# HANDOFF

## 开始新任务

- 先读本文，再检查 `git status --short --branch`；保留已有修改。
- Python 设置、engine 和目录在 import 时初始化。测试入口必须先设置隔离环境，参见 `platform/tests/conftest.py`；不要使用开发者 `.env` 或真实数据库跑清库测试。
- `AILAB_TEST_DATABASE_URL` 会清空指定 PostgreSQL 的 public schema。只传一次性数据库。
- 本轮完整审查记录：`docs/reviews/2026-10-23-full-review.md`。

## 2026-10-23 全量审查踩坑与约束

- 预签名 PUT 在完成上传后仍能重用。完成必须先冻结独立对象，再哈希/扫描；不能把已签名的原始 key 作为最终可信对象。
- 普通上传、预签名 complete、沙箱产物都计入资料库配额。检查和写入需在同一个 `user_critical_section` 中；幂等重试先查已存在记录，再检查额度。锁内刷新此前加载的 ORM 用户/文件。
- `users.settings` 是整份 JSON 写入。开关更新必须串行化，不能让多个请求用同一个旧副本各写一次。
- Agent 的 token 上限必须在每个响应记账之后、执行工具之前检查，也涵盖最后回答、收尾回答和恢复执行已有用量。不能只在工具循环底部检查。
- SQLite 读回 datetime 会丢 tzinfo。对外时间统一从 UTCModel schema 输出明确 UTC，否则亚洲时区会误显示 8 小时前。
- Docker `.Id` 是配置 digest；`ctr images ls` 展示 manifest digest。镜像导入后用 CRI inspecti 的配置 ID 比较。`kubectl apply` 失败必须立即中止。
- Kubernetes readiness 必须探测 readyz，消费者退出或依赖故障返回 503。Broker provider 健康检查每次只执行一次；清单探针超时需覆盖外部调用。
- 浏览器上传请求进行中也要禁止发送；扫描轮询的旧响应不能覆盖/复活已删除附件。对话详情请求需防乱序，流需在新轮替换和卸载时取消。
- S3 Body 和浏览器 ReadableStream reader 都要在 finally 中释放；204 不应解析 JSON。

## 验证

Python 3.12，按 constraints 安装 `.[dev,postgres,e2e]`；Portal 使用 `npm ci`。Linux CI 运行完整测试和 GNU 沙箱命令。

```bash
cd platform
.venv/bin/ruff check .
.venv/bin/ruff format --check .
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/15 .venv/bin/pytest tests -ra --cov=ailab_platform --cov-report=term-missing
# 一次性 PostgreSQL 16 数据库，端口仅举例；这两个库都会被测试改写
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/14 \
AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_review \
AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_migrations .venv/bin/pytest tests -ra
AILAB_E2E=1 AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/13 .venv/bin/pytest e2e -ra
cd portal
npm test
npm run build
```

仓库根目录另跑 `shellcheck -S warning deploy.sh tests/*.sh platform/scripts/*.sh`、`bash -n deploy.sh`，platform 中跑 `bash scripts/lock_deps.sh --check`。

macOS 的 mock 沙箱使用宿主 shell，需 GNU coreutils/findutils（stat -c、find -printf、touch -d）；运行时在 PATH 前加入相应 `libexec/gnubin`。预览失败用例刻意排除 LibreOffice，也必须保留选中的 GNU find。Prometheus 默认 process 指标依赖 Linux /proc，不能在 macOS 上强行断言；Linux 测试仍断言该指标。

浏览器 E2E 按仓库既有 Playwright 流程运行。人工浏览器检查优先 T3 内置 preview。E2E 使用临时副本构建 Portal，并自动结束子服务；不要把它当开发服务器复用。

## 2026-10-23 项目与资料库后端

- `0008` 给 conversations/files 添加项目外键时沿用 `0004` 的 SQLite 直接 `ALTER TABLE ... ADD COLUMN ... REFERENCES`；不要 batch 重建被 runs/artifacts 引用的表。带现存引用数据和外键开启的升级、降级、再升级已加入迁移测试。
- 项目删除与上传、预签名、产物登记、移动使用同一个 `user_critical_section`。`transition_run(..., commit=False)` 连同事件参与外层事务，避免提前提交释放 PostgreSQL 用户锁；一般调用仍默认自行提交。发消息在锁内刷新 conversation，防止等待期间项目已被删除。
- 单文件、批量和项目删除统一走 `library.delete_file_rows`，提交后才清理主对象、derived_key、text_key；项目删除还要将软删对话的 project_id 清空并解除外部文件的 parent_id。
- ZIP 使用非 seekable 小缓冲和 ZIP_STORED，按存储块输出；下载前关闭 DB session。当前 Starlette 不会在客户端断开时主动关闭同步生成器，`_ArchiveResponse` 在 finally 中显式关闭，且屏蔽取消以完成存储流清理。ASGI 2.3/2.4 断开测试已验证修复前红、修复后绿。
- 临时 PostgreSQL 测试实例初始化需显式 `initdb -E UTF8`；`--no-locale` 单独使用可能创建 SQL_ASCII，psycopg 返回 bytes 导致 SQLAlchemy 报 `TypeError: cannot use a string pattern on a bytes-like object`。
- 本轮验证：GNU coreutils/findutils 在 PATH；SQLite 全套 346 passed / 8 PostgreSQL 专用项 skipped，覆盖率 82.93%；临时 PostgreSQL 16 两个独立测试库全套 354 passed / 0 skipped；Ruff check/format check 均通过。测试服务已关闭，没有 stash 或提交。

## 2026-10-23 项目与资料库前端

- 项目 = 对话 + 文件的容器。侧栏「最近」只列不属于项目的对话（`GET /v1/conversations` 不带 project_id 即只返回 project_id 为空的）；项目内对话在 `/p/<id>` 里。
- 「围绕文件对话」用 `/?files=a,b`（文件同属一个项目时为 `/p/<id>?files=`）；Chat 读取后立刻 `history.replaceState` 去掉参数，刷新不会重复附加。
- 全局 `button.danger` 有粉底：菜单/批量条里的 `.danger` 按钮必须显式 `background: none`，否则未悬停也是粉色。
- 批量接口和 ZIP 最多 200 个；「全选」在「加载更多」后可能超过，前端已拦截。
- UI 人工检查：T3 内置 preview 访问 `next dev` 会卡死主线程（127.0.0.1 下 dev 资源被 allowedDevOrigins 拦截，localhost 也会超时）。改用 `.venv` 里的 Playwright 无头 Chromium 对 `localhost` 截图；临时 API 用 `/tmp` 下独立 SQLite + `AILAB_ENV_FILE` 指向不存在的文件，不碰开发库。

## 2026-10-23 资料库缩略图与轮询（后端第二轮）

- 图片解码仅在 Worker：扫描通过后生成 `derived/{id}/thumb.webp`，文件 meta 写 `thumb_key`；WebP 480×480 内、quality 75/method 4、第一帧、EXIF 方向、保留透明度。输入封顶 20 MiB / 1600 万像素（bomb warning 也当失败），SVG/矢量、损坏、编码/存储失败都跳过缩略图，文件仍 READY。先缩小再 EXIF-transpose，避免复制完整解码帧；方形边界下结果等价。
- 沙箱产物登记仅按 MIME/扩展名判候选，不在 API 解析；入 `thumbnail_only: true` 作业。此分支只处理 READY 且缺 thumb_key 的记录，失败重试或耗尽只能丢缩略图任务，绝不能套普通 pending 文件的拒绝逻辑。产物幂等登记重试也可补入队，队列异常仅日志。
- 回填（platform 目录，使用同一数据库/存储/Redis 配置）：`AILAB_QUEUE_BACKEND=redis .venv/bin/python -m ailab_platform.worker.backfill_thumbnails --batch-size 200`。只读 DB，按 id keyset 分批入队；独立 memory 队列不能交付，CLI 明确拒绝。已完成跳过、重复消息安全；损坏/超限文件再次执行仍会重试。通用 MIME 且无图片扩展名的历史文件不自动识别，避免回填读取每份原文件。
- `FileOut.thumbnail_url` 为 `/v1/files/{id}/thumbnail` 或 null。缩略图仅 owned+READY 可取，否则 404；200 为 image/webp、private 一年 immutable、SHA256+格式版本 ETag，If-None-Match（含弱验证器/列表/*）匹配返回 304。先关 DB，再流存储，断开用共享 `_ClosingStreamingResponse` 释放流。删除统一清理 thumb_key。
- `GET /v1/files?ids=a&ids=b&limit=200`：最多 200 个 ids，未知/非本人静默忽略，与现有筛选取交集；默认 limit 仍是 50，顺序仍按创建/收藏时间。
- 0008 尚未发布，直接补收藏/图片部分索引、(user_id, project_id, created_at) 与存活项目对话计数部分索引。SQLite 的图片部分索引必须用固定 LIKE 字面量匹配，绑定 `LIKE ?` 不会命中；用户 q 仍是转义绑定参数。新增索引后必须 ANALYZE 再比较计划，否则 SQLite 的旧统计可能使 all/q 误选项目索引。完整双库 EXPLAIN 与合成负载边界见 `docs/reviews/2026-10-23-library-query-plans.md`；未加不能加速 `%q%` 的普通文件名索引。
- 验证：新增 25 项回归；SQLite 全套 371 passed / 8 PostgreSQL 专用项 skipped，覆盖率 83.03%；临时 PostgreSQL 16 两个独立测试库全套 379 passed / 0 skipped，迁移升级/降级/漂移通过；Ruff check/format、`bash scripts/lock_deps.sh --check` 通过。仍有既存 Starlette/httpx 弃用警告。未改 portal、未跑前端/E2E、未提交/stash/发布；临时 DB/Redis 服务已关闭。

## 2026-10-23 资料库第二轮（缩略图与性能）

- 侧栏是 `position: sticky`，会形成层叠上下文：在侧栏里打开的弹窗即使 `z-index` 很高也会被主区域盖住。Modal 和会话「⋯」菜单都必须 `createPortal` 到 `document.body`；会话菜单还要用 fixed 定位，否则会被侧栏滚动区裁掉。
- `lib/projects.ts` 是全页共享的项目列表缓存。读取方同时挂载时复用同一个请求；只有 `projectsChanged()` 才会在请求进行中标记过期并再拉一次。把这两者混为一谈会导致每页重复请求（生产构建实测过）。
- Portal 代理原先对所有响应强制 `no-store`，连 API 的 immutable 预览图都缓存不了。现在会透传 `cache-control`、`etag` 和 `if-none-match`；API 未声明时仍是 `no-store`。
- 缩略图像素上限分格式：JPEG 走 `draft()`，最低按 1/8 比例解码，上限 128 MP；PNG/GIF/WebP 等需要全尺寸解码，上限 16 MP（Worker pod 256 MiB）。只设一个 16 MP 上限会让 24–48 MP 的手机照片都没有缩略图。
- 前端不会为了填一个小图标去加载大图原文件：没有缩略图时只内联显示 ≤256 KB 的图片（例如 SVG），否则显示类型色块。只有打开大图查看时才加载原图。

## 2026-10-23 PR 合并检查

- fork PR 的 CI 可能显示 `action_required`，而 PR 检查列表为空、合并状态为 `UNSTABLE`；这表示工作流等待维护者批准，不代表测试已通过。协作者权限生效后，先批准已有 workflow run，再核对实际检查结果。
- #26 基于 #25。依次使用 merge commit 保留提交祖先关系，避免 squash/rebase 后后续 PR 重复展示或冲突；每次合并前重新核对 head SHA、base 和检查状态。
- GitHub App 连接器与本机 `gh` 使用不同权限。连接器合并返回 `Resource not accessible by integration` 时，先确认本机账号仍有 WRITE 权限，再通过 `gh pr merge --match-head-commit <已验证 SHA>` 合并；不要将连接器的权限错误误判为协作者权限未生效。
- 资料库改版后要同步 E2E：上传 input 是隐藏的，以可见「上传」按钮确认登录；文件行是 `.lib-row`，可用后才出现带文件名的下载链接；文件/会话删除经「更多操作」菜单；个人技能显示在资料元数据链接中。扫描 mock 必须匹配带 ids 的批量 GET 并返回数组，继续断言上传/扫描期间不能发送、移除附件后旧响应不能复活附件。
- #26 原 CI 的 6 项 E2E 失败来自上述旧定位器/旧 mock。更新后本地生产构建 + 真实服务/独立 Redis 完整 E2E：34 passed；Ruff check/format、diff check 通过。未改产品代码。
- 技能开关是乐观更新，Composer 默认按钮也先于技能 GET 完成显示。E2E 不能用开关变色或 Excel 按钮出现判断保存/加载已完成：关闭和恢复都要等待 PUT 成功，并等待被禁用的 PPT 按钮实际从 DOM 消失。临时延迟 Chat 技能响应 750ms，旧断言稳定失败（1 failed），修复后同样延迟下完整 E2E 34 passed；不要用固定 sleep 或重跑 CI 掩盖这类竞态。

## 2026-10-23 W1 ops：存储、部署、备份与生命周期

- 分支 `arch/w1-ops` 从 `6666573` 建立；不推送。`Storage` Protocol 在 `ports/storage.py`，原 `storage` 导入仍可用；`put_stream(..., max_bytes=...)` 原子限额，`open_stream` 必须用 with（异常/提前结束也关闭），`list_prefix` 返回 `ObjectPage(items,cursor)`，最大 1000。仅遍历一页不会遍历全桶；workspace GC 已改分页。Local copy 先 pin 文件描述符，避免 signed key 被替换后读取不同 inode。S3 spool 使用磁盘并关 multipart 线程；尚未在真实 S3 验收。
- Redis `XAUTOCLAIM` 必须续用返回游标：每次从 0 开始会被前面的活跃 pending 永久挡住；`timeout=0` 不可传 Redis `BLOCK 0`（无限阻塞）。内存队列也序列化 payload，避免生产者/消费者突变影响重投。
- 新迁移 `0012_lifecycle.py`，`down_revision="0008"`，新增 `lifecycle_deletions` 与五个 GC 索引；双库升级/降级/漂移及 EXPLAIN 验证。合并 A 的 0009 等迁移时需合并 Alembic heads；会话 purge 当前清理 RunEvent/Artifact/ModelUsage/Run，必须整合 A 新账本/outbox/reservation/checkpoint 外键及保留要求，不能直接套旧删除表清单。
- GC 按用户锁检查 presign，至少等签名 900 秒加宽限；完成后的独立 obj_* 冻结对象不作为原始 staging 删除。DB 删除与对象删除意图同事务提交，存储失败重试；会话和事件分批，prefix 物理删除有总预算。固定指标 kind，不放用户/run/file。配置默认：batch100、间隔3600s、presign/orphan86400s、events/conversation30d、trash30d（只预留），workspace 仍3d。详见 backup-restore runbook。
- 备份 SQLite 用 online backup；PG 导出 snapshot 给 pg_dump 与引用清单共用；对象 JSONL 按块复制/哈希，不硬链接，脱敏 models.toml 的字面凭据含注释也必须清掉。`backup.json` 只在全部 verify 后写入。恢复拒绝非空目录；PG 先恢复 SQL，运维显式导入新空库。跨 DB/S3 不能原子快照，确定性备份窗口要停写/GC；RPO/RTO 必须实际数据演练，不能沿用目标架构数字。
- 部署改为 `platform/deploy/base` + `overlays/dev|lite`；lite 命令仍 `sudo ./deploy.sh`，`--dry-run` 无需集群/不会部署。API NodePort 改由 public nginx 网关 allow-list `/v1/`；内部服务 ClusterIP，Broker DNS 独立端点。base NetworkPolicy 用 k3s 自带控制器；lite Broker hostNetwork 不受可移植 Pod policy 保证，8081/8090 需宿主防火墙。dev PG 独立；lite 保留 SQLite 数据兼容，独立升级 DB 要迁移。ClamAV slot 在 base/scanner，C 加资源与 Worker-only policy。PVC lite40Gi/dev20Gi，旧 local-path PVC 不能假定可扩容；容量设置须与实际盘/PVC一起核验。
- 未改 Broker/Worker：注入仍整份 HTTP bytes（默认上传50MiB且 Broker 此处无额外 cap）、交付100MiB、workspace60MiB；Worker 先整份下载默认50MiB再以20MiB决定 parse，thumbnail 输入20MiB，解压/图像内存另计；preview 页面/JSON bytes 路径没有本分支的统一限额。交给 C/D/lead 测整机 RSS。Local atomic-write crash 临时文件、冻结后 DB 失败且删除也失败的主对象，仍需维护窗口/后续通用孤儿扫描收敛。
- 本机没有 Docker daemon，MinIO 官方 darwin-arm64 下载返回410；S3 真实契约跳过，CI 独立 MinIO service-container job 已配置但未远端执行。kubectl 三个 overlay 渲染通过，`apply --dry-run=client --validate=false` 仍需 API discovery，报 localhost:8080 connection refused；无真实集群部署/NetworkPolicy/防火墙验收。
- 最终验证：纯 SQLite/Local/memory 全套 **401 passed / 37 skipped**；临时 PG16(15450)+Redis(16450) 全套 **429 passed / 9 skipped**（8 S3 + 1 memory 不适用 Redis 游标）；契约分项 Local8、memory2、Redis3、SQLite DB3、PG DB3、GC双库各7+容量1、备份5（SQLite恢复/损坏/引用/脱敏+PG真实导入）。Portal4测试与生产构建、Ruff check/format、ShellCheck、bash -n、依赖锁一致性通过；无 UI 改动未跑浏览器E2E。Local50/100MiB探针 Python峰值148697B、RSS增278528/688128B、0.15/0.38s；非目标小机/整机验收。临时服务均需在交付前停止。

## 2026-10-23 调用级账本与硬用户配额

- `0009`：先 reserve、再调用、最后 settle；`call_id` 是幂等单位，真实重跑必须新 ID。旧 blind usage POST 返回 410，API/Agent 与服务鉴权 allowlist 要一起切换。新内部路由为 `POST /internal/usage/reserve`、`POST /internal/usage/settle`、`GET /internal/runs/{id}/usage`；完整 Portal/服务契约见 `docs/architecture/usage-ledger.md`。
- 用户锁内只做检查与数据库提交；日/月用已结算整数 milli + 全部 open 预留。过期不等于释放，reaper 每轮最多 200 条转 unreconciled；晚到 settle 原行修正，终态 Run 仍能补账。取消只有确认未发出模型请求才零费用释放，已发出的中断计 estimated/settled；408/5xx 不能当作确定未消费。
- SQLite 迁移绑定 Decimal 必须给 `sa.column` 声明 Numeric，否则报不支持 decimal.Decimal。迁移按 500 条 keyset 冻结旧缓存折扣与金额，legacy 不随配置重算。SQLite 无时区、PostgreSQL 会返回服务器时区；幂等响应时间都要显式转 UTC。
- `Run.result.usage_totals` 是账本聚合快照。状态提交与旧工作区清理写整份 result 时也必须持同一用户锁、刷新并合并，否则晚到结算总数会丢；已补红绿回归。用量页面必须同一 SQL snapshot 读取 settled + reserved，分开 SELECT 会在结算切换期间双计；CLI 对账总数同样用单条投影/聚合查询。
- 运行预算仍为原始 input + output 的软上限，reasoning 已包含在 output；Agent 从账本恢复总数，记账后、工具前拦截下一步，已完成最终回答跨预算仍成功。计量请求有 call/run 头和 max_tokens；模糊失败不得在同一个预留下偷偷再发一次推理。模型专用 tokenizer、真实上游 max_tokens 强制执行未验收。
- 成本：每模型调用 2 次事务、4 个业务写（比旧路径多 3 写/1 commit），不新增依赖/后台服务。300 调用本机测量，SQLite/PostgreSQL 预留锁 p95 1.10/1.87 ms，结算锁 p95 1.23/2.26 ms；200 条过期回收 247/377 ms。8,000 行双库 EXPLAIN 检查窗口、open、过期和对账索引；按用户周期 SUM 仍随历史行数增长。
- 最终验证：Ruff check/format 通过；SQLite + Redis 全套 405 passed / 11 PostgreSQL 项 skipped；PostgreSQL 16 + Redis 全套 416 passed / 0 skipped；Portal 4 tests + production build、Chromium E2E 34 项通过。临时服务只用 16410/15410，结束后关闭；未改 .env/开发数据库，未推送或创建 PR。

## 2026-10-23 合并 ledger 与 lifecycle

- 账本不引用 runs：`model_usage.run_id`、`usage_reservations.run_id` 是普通字符串，不是外键（0012 删除旧 FK）。软删对话 30 天后清除 runs/events/artifacts，但它们的用量仍计入额度窗口、管理报表与对账；不要再在 purge 里删 `ModelUsage`。SQLite 上 FK 无名，迁移用 batch 的 `naming_convention` 命名后再删；model_usage 无表引用，batch 重建安全。
- 迁移链：0008 → 0009（ledger）→ 0012（lifecycle）。后续分支均以 down_revision="0008" 起草，合并时按顺序重挂。

## 2026-10-23 T03/T05 严格配置与服务身份

- `AILAB_PROFILE` 优先；旧 env：test→test，dev/smoke/k3s→dev，其余→secure。k3s 清单保留显式 dev 兼容，不能据此宣称已安全上线。secure 同一启动门禁检查 mock、stub、默认秘密、共享令牌、身份、注册和外部模型；ClamAV 仅定义 scanner_backend，实现在 C 分支。
- 不给被调方分发调用方原始令牌：每进程只挂 `platform-{service}-identity`，共同的 `AILAB_SERVICE_CREDENTIALS` 仅含 sha256 verifier。Secret 脚本重跑保留令牌；读取身份失败不能当作不存在后重建。旧共享令牌仅 dev/test 警告兼容，真实 E2E 已禁用它。模型 Secret 仅 API/Agent，OSB key 仅 Broker。
- Uvicorn 默认会先解释 XFF 并改写 peer；只改登录 handler 不够。所有仓库启动入口 `--no-proxy-headers`，应用按 `trusted_proxies` 从右向左验证。默认不信代理；部署应填实际 Portal/反代 CIDR。
- 内部 httpx 必须 `trust_env=False`：本机环境代理曾截走内部请求，导致 E2E 个人技能 Run `httpx.ReadError`。关闭后完整 E2E 通过；模型等外部请求不在此修改范围。
- FastAPI 0.156 的 `include_router` 是延迟挂载，顶层 app.routes 只遍历 APIRoute 会漏掉全部业务路由。scope 巡检须读 effective_route_contexts；新增/撤掉 scope 的测试覆盖 included prefix。
- Agent/Broker 文件请求必带 run_id；仅同用户、活跃 Run、附件/同会话材料/产物，个人技能为 API 明确列出的系统附件。Broker service.py 与 Agent loop.py 只改凭据和上下文传递。Worker 的 READY 派生任务带 job=thumbnail/preview，不能写 scan/status；已处理/删除文件的普通重投须 ACK，认证失败须重试，包括 poison 分支。
- 并行合并：A 的 reserve 用 `Depends(require_scope("usage:reserve"))`，settle 用 usage:write；C 的 READY 派生回写用 files:preview_write 并保留 job/状态/字段限制，Worker→Broker render 仅 sandbox:render，不能开放 sandbox:use；通用调用可用 `PlatformClient(service="worker")` / `AsyncBrokerClient(service="worker")`，不要加载其他服务令牌。scope 表和完整设置见 ADR 0002。
- 无迁移。超出文件 ownership 的最小改动：api/main.py（启动门禁身份、日志脱敏）、agent/loop.py、broker/service.py、worker/pipeline.py（调用凭据/上下文）、queue.py（拒绝未知工厂值）、dev_up.sh（禁止 Uvicorn 预处理 XFF）、tests/E2E（新契约）及本文/ADR/status-matrix。
- 最终验证（GNU PATH；仅 Redis 16420、PG16 15420 的一次性库）：SQLite+Redis 424 passed / 8 PG 项 skipped（49.43s）；PG+Redis 432 passed（55.05s）；四服务独立身份、共享令牌禁用的 E2E 34 passed（81.37s，含 Portal tests/build）。Ruff check/format、ShellCheck warning、bash syntax、lock_deps、diff check 通过。基线配置替换回归 18 failed，移除 XFF/Run 文件绑定 2 failed，恢复后绿；路由无 scope 的 direct/included 测试均被 guard 拒绝。固定 5 verifier 的认证微基准 100,000 次约 3.04 µs/次；双库新文件绑定查询 EXPLAIN 使用既有 Run 索引，无新增迁移/后台任务。临时服务验收后关闭；未推送。真实 k3s、TokenReview/TLS、ClamAV、Docker/OpenSandbox、S3 不在本分支验收范围。

## 2026-10-23 合并 service-auth 与 deploy 重构

- B 修改的 `platform/deploy/k8s/platform.yaml` 已被 E 拆成 `deploy/base` + overlays；每个服务只挂自己的 `platform-<svc>-identity`、共享 `platform-service-verifiers`，api/agent 另挂 `platform-model-secrets`，原 `platform-secrets` 只按 key 单取 invite/dev token/admin 密码/OSB key。所有 uvicorn 加 `--no-proxy-headers`（XFF 只信 `trusted_proxies`）。
- 渲染 overlays 需要 kustomize ≥ 5.7（k3s 1.34 自带 kubectl 内嵌版本即可）。kustomize 5.4.3 处理 lite overlay 中顶层 `$patch: delete` 会 nil pointer panic，不是清单错误。
- 新增内部端点必须 `dependencies=[Depends(require_scope(...))]`，路由巡检测试会拦截遗漏；ledger 的 reserve 用 `usage:reserve`、settle/旧 usage 用 `usage:write`、GET run usage 用 `runs:read`。

## 2026-10-23 Broker 租约恢复与准入（W1）

- 启动不再删光租约。Provider 创建即写 owner/lease/run/user/created_at，Redis CAS 保存期望状态；有效资源重启接管，Redis 清空从资源元数据重建。`releasing` 删除失败保留记录和容量，2→60 秒退避；不能把连接失败当作资源不存在。
- Docker label 不可变：续租/状态放在容器 root-only `/run/ailab-lease.json`，不能放进用户 workspace/快照；OSB 用 lifecycle metadata。Docker 无原生 TTL，Broker 宕机期间不会自动回收；disk 规格仍未落实文件系统配额。
- `acquire(SandboxSpec, LeasePrincipal, *, wait_seconds=None, on_queued=None, prepare=None) -> LeaseHandle`，`release(handle)`；C 的 render 必须 `system=True/profile=render`，ttl/max_lifetime 都≤120 秒，memory≤保留内存。文件/exec 传 `handle.lease_id`。完整接口、错误、设置见 ADR 0003。
- 普通/短 render 分保留槽与内存，符合条件者 FIFO，单用户超限不堵其他用户；队列上限32、截止300秒，释放通知唤醒，位置通过事件/`GET /internal/admission/{run_id}`暴露。队列重启后需调用方重试；不重放工具、不做 idle reuse。
- Agent 对 `POST /internal/leases/{id}/renew` 每60秒心跳；最长4小时，默认TTL20分钟。`lease_lost/lease_expired` 为410可恢复错误，runtime 后续应终止当前轮并按安全checkpoint恢复。现有 runtime 未在本分支接入心跳。
- 同owner仅一个Broker/一个uvicorn worker，Redis TTL30秒锁与独立心跳；第二实例启动失败，失锁停止服务。Redis flush 恢复会先对账再准入；这不是分布式选主/HA。对账列表是快照，新创建资源必须再inspect，不能误判丢失。渲染更新缓存必须合并最新CAS版本，不能覆盖同时心跳。
- 配置默认总槽位改2（含一个render保留槽），总内存3GiB，render768MiB；仅改 `.env.example` 与部署清单的相应总数，合并时与B/E的profile/部署配置对齐。自定义总槽1需显式关闭reserve或增大总数。无业务DB迁移。
- 故障测试仅允许flush本轮临时Redis **16440**；真实Provider通过 `AILAB_TEST_DOCKER=1` / `AILAB_TEST_OSB_URL`选跑。性能诊断：2个mock资源、200次对账，真实Redis每次2.476ms wall/2.038ms CPU、峰值Python临时分配226.3KiB、进程RSS49.2MiB；不代表Docker/OSB或生产2vCPU实测。本轮最终验收：SQLite+Redis 449 passed/10 skipped，PostgreSQL16+Redis 457 passed/2 skipped（仅Docker/OSB真实契约未启用）；含48项内存/Redis故障测试，Portal 4项与生产build、Ruff check/format、依赖锁一致性均通过。临时Redis16440、PG15440验证后关闭。

## 2026-10-23 W1 扫描隔离与资料库预览

- 分支 arch/w1-worker-preview，基于 6666573。协议与合并清单：docs/reviews/2026-10-23-worker-preview.md；状态表已补建。没有改 Portal UI、LeaseManager 内部，也没有推送。
- scan_unavailable 绝不能当 clean：文件保持 pending，默认 3 次、5/10 秒退避，用尽写 scan_exhausted；清空标志并重新入队才恢复人工重试。thumbnail_only/preview_only 分支均不能回退 READY。
- 不可信文档/图片/ZIP/skill 检查进受限子进程；Linux AS 192 MiB、CPU 45 秒、墙钟60秒、输出16MiB，macOS AS 不可靠，改 RSS watchdog。Worker Pod 改384MiB，避免只预算子进程而漏算父进程。进程不继承服务密钥环境；ZIP先查2048项/128MiB/200倍限制。
- read_only Excel 可能没有 max_row/max_column（write_only 生成的文件就是例子），必须 calculate_dimension；逐格 ws.cell 会反复扫描 XML，改成两个 iter_rows 流并行读。复用 renderer.sheets(recalculate=False)，Worker 不能因为宿主有 soffice 就调用它。流式模式的列宽/合并/图表元数据暂为空，详见审查记录。
- 短 Unix socket 路径用 /tmp；pytest 的 macOS tmp_path 可能超过 AF_UNIX 的路径长度。OOM 用大量小 XML 节点触发分配；单个巨型文本节点会先撞 libxml 的文本限制，不能用该 corrupt 冒充内存验收。
- Office/PDF 按需沙箱渲染；RenderContexts 为无 Run 的资料库作业提供公开租约输入上下文，D 合并时必须保留或替换成正式文件任务入口。57页 PDF 验证只产40页并返回总57页。SVG original 强制 CSP sandbox，文本固定 text/plain。
- 基线没有 outbox；新增保留编号 0011_preview（revision0011/down_revision0008）建 outbox，manifest仍在files.meta。预览请求和副作用同事务，派发失败留记录。API内部文件meta回写补用户锁，避免缩略图回写覆盖同时请求的preview。B/E/A 合并时协调scope、部署与迁移heads。
- 本机测量（合成样本，非生产承诺）：20MiB PPTX 子进程峰值93.9MiB、父进程峰值80.3MiB（峰值和174.3MiB保守预算），10MiB XLSX 子34.6MiB、父79.5MiB（和114.2MiB）；含 Worker import 的耗时1.122/4.096秒。单页 DOCX mock+真实Office工具2.625秒、输出23,437字节、租约全释放；原始 JSON/复现脚本已入库。
- 最终验证：SQLite + Redis 435 passed / 10 skipped（9项PG专用、1项真实clamd）；PG16 + Redis 444 passed / 1 skipped（clamd）。Ruff check/format、依赖锁一致性、diff check通过；Portal 4测试与生产build通过。未启动真实clamd/Docker/OpenSandbox/k3s，未验证Linux AS与生产内存。测试仅使用本任务Redis16430、PG15430一次性库。

## 2026-10-23 身份 / 管理 API / OIDC（T13 / T17）

- 新迁移 `0013 -> 0008`，lead 合并时重接；SQLite users 只 ADD/DROP COLUMN，不 batch 重建。带现有 Run/Token、部门父子和审计，开启外键的升/降/再升及双库 schema drift 通过。
- Principal 附在 request.state，current_user 仍返回 User；认证角色 joinedload 保持原会话列表 ≤3 次 SQL，不能用额外 selectin 查询放宽旧性能门槛。
- Next 的 req.url 可能把 127.0.0.1/LAN 主机归一成 localhost；OIDC 回调必须返回相对 Location，绝对本地重定向会让 Cookie 跨主机失效。跨站 IdP 回跳已用生产 Portal + Playwright 红/绿验证；流程 Cookie 用 Lax，平台 Cookie 保持 Strict/httpOnly。
- 部门/策略保持数组响应，keyset 下一页在 X-Next-Cursor；Portal 通用代理必须透传。dept_admin 范围进入 SQL，不能取全量后过滤；无任何管理员内容读取特权。
- 本基线 ModelUsage 无 dept_id。合并 A 后补 usage 写入快照、冻结 weighted_amount、全局报表索引，并把 limits_for 接入 storage/concurrency/summary；本分支仅改 token 准入一个点。详见 `docs/reviews/2026-10-23-identity-implementation.md`。
- OIDC 上游令牌不出 API；只按 iss/sub 建联，不按 email 自动合并。真实 Keycloak 测试由 AILAB_TEST_OIDC_ISSUER gate，未配置即跳过，不把 fake issuer 验收称作企业 SSO 验收。
- 本轮最终验证：SQLite+Redis 463 passed / 10 skipped；PG16+Redis 472 passed / 1 skipped；浏览器 E2E 34 passed；Portal Node 9 passed、build、Ruff、依赖锁一致性通过。真实 Keycloak gate 未配置。临时服务仅用 16470/15470，结束后关闭；无开发库操作。

## 2026-10-23 T18 文件版本与回收站（arch/w2-file-versions）

- 基线 `609c1d3` 实际未对同名产物设置 parent/version；本轮在 `user_critical_section` 内按同会话同名产物找链、按整链 max(version)+1 分配。上传从旧版分支也必须取整链最大号，不能 parent.version+1；unique(lineage_id,version) 与单 head 部分唯一索引兜底。
- `0014` 暂接 `0008`，合并时由 lead 重接。SQLite 只 ADD/DROP COLUMN，不重建 files；旧 parent 链回填根 id，保留全部 file id/FK。历史重复版本号按 (version,created_at,id) 稳定重编号；断链/跨用户/环拒绝迁移。迁移仅改元数据，不连接对象存储；既有超限链在下次写版本时收敛。降级不恢复旧重复编号。
- 默认 `max_file_versions=10`、`trash_days=30`（配置范围校验）。超限只 tombstone 旧行、提交后清对象，旧 Run/Artifact 引用仍在、content=410。恢复旧版独立 copy 原对象及 derived 前缀，递归改写派生 key；保留扫描状态，不能把 pending/rejected 复制成 READY。回收站里的版本回退保留回收站状态，显式 `/restore` 再解除。
- 普通/批量 delete（兼容 alias）/项目删除均整链入站；trash 继续计配额，连回收站的 rejected 文件也计；purge 才释放。移动/收藏作用整链，项目计数只算 live head。项目删除分批 200 个 lineage，清 FK 并记 trashed_from_project_id；已经入站的链也要解除 FK，不能让首批更新误清其他批次的 project_id。
- `DELETE /v1/trash` 按 200 批次清理，请求开始时间作截止，避免追逐不断新增的删除；count 是 lineage 数。batch count 保持输入去重后 id 数。cursor 是上一页末项 file id；保留原 offset。`purge_expired_trash` 每轮最多 200 head，锁内再次确认过期，集成到既有 reaper；函数独立，方便与 E/A 合并。
- 物理清理唯一入口仍是 `delete_file_rows`，包含登记的 key 和 `derived/{id}/` 全前缀。保留原提交后 best-effort 删除；对象存储故障/提交后 kill 的孤儿收敛待 E/T09 GC/outbox 集成，未声称生产级故障验收。真实 S3 拷贝的 RSS/磁盘/时延未测。
- SQLite 要在 head 部分索引键里覆盖 is_head/deleted_at/purged_at（图片还含 content_type）才显示 COVERING INDEX；PG 为 Index Only Scan/Heap Fetches=0。列表先索引取 id，再批量取详情和版本数，禁止 N+1；substring q 仍需读候选行。10 万行原始计划和成本追加在 library-query-plans 文档；新索引约 12 MiB（PG），无新依赖。
- 保留 files_router 的 `/v1/files` 前缀，另注册 trash_router；不要改前缀让 C 后续新增预览装饰器误挂到 `/v1/{id}/preview`。本轮只在已有 thumbnail 路由补 purged guard，未实现或覆盖 C 的新 preview 路由。`routes_runs` 附件与 `routes_skills` 个人技能需同步过滤 trashed/purged；否则删除文件还能被新消息/技能引用。
- 分页取 id 与详情是两个查询，期间可能移入站、换 head 或 purge；第二次读取必须重新过滤可见性并忽略已消失 id，否则会 KeyError/500。用 SQLAlchemy after_cursor_execute 注入真实的交错删除回归覆盖。
- 并发回归不要在每个线程启动 `with TestClient(app)` lifespan：Alembic 全局代理会互相覆盖而报 KeyError/config；共享测试外层 lifespan，线程只创建无 context 的客户端发请求。reaper 测试必须只让自己的两份文件到期，避免误算同进程其他用例留下的用户回收站。
- 行为红绿：隔离的 `609c1d3` 副本 3 项（删除留对象、只列 head、恢复旧版）均红，当前实现同样 3 项均绿。完整验证与最终计数见下方本轮验收记录。未 push；临时 PostgreSQL 15490 / Redis 16490 仅本轮使用，结束后关闭。

### 本轮验收记录

- Python 3.12 / GNU coreutils+findutils：SQLite + Redis `414 passed, 11 skipped`（10 项 PG 迁移及 1 项 PG 并发条件跳过）；临时 PG16 + Redis `425 passed`，包含现存 parent 链/FK 的 upgrade → downgrade → upgrade、模型漂移、历史重复号及跨用户链检查。
- 浏览器 E2E `34 passed`；最后分页竞态修复另复验上传 → 回收站下载 → 恢复 → 最终 purge：`1 passed, 28 deselected`。E2E 临时副本 production build 通过；Portal Node tests `4 passed`。
- Ruff check / format check、依赖锁一致性、git diff --check 均通过。测试与计划只用独立临时目录、PG 15490 和 Redis 16490，未读取开发者 .env/var 数据。
- 临时 PostgreSQL/Redis 已关闭，15490/16490 已确认无监听；无 push。

## 2026-10-23 合并 C / G / I 的集成点

- 迁移链已整理为单调编号：0008 → 0009 ledger → 0010 lifecycle → 0011 preview outbox → 0012 identity → 0013 file versions。模型漂移检查必须在 head 上做（`run_migrations`），升到中间版本再比对全量 models 必然失败。
- 资料库渲染：Broker `LibraryRenderer` 用 `acquire(SandboxSpec(profile="render"), LeasePrincipal(system=True))` 走准入的 render 保留槽，不得再替换 `manager.platform` 或伪造 Run。Broker 读文件以 `job=preview&preview_job=<id>` 绑定到 API 记录的当前 pending 预览任务（scope `files:read_for_preview`）；Worker 的缩略图/预览任务同样要带 `job`，否则 B 的绑定会 403。
- 额度：统一走 `quota.user_limits`（用户策略 → 部门策略 → 默认策略 → settings），覆盖 token 预留、资料库空间、并发 Run 和用量摘要；不要再直接读 `settings.*_limit`。
- 用量报表只读账本：`weighted_amount` 是 milli 单位（÷1000 才是加权 Token），部门取调用时快照 `model_usage.dept_id`，不 join runs（对话被清除后用量仍在）。
- 删除：`DELETE /v1/files/{id}` 现在进回收站，派生对象保留到 purge。`library.delete_file_rows` 在同一事务写 `lifecycle_deletions` 持久意图，提交后仍立即尝试删除；失败由 lifecycle 的 `drain_object_deletions` 重试。回收站过期清理在 `lifecycle_once` 中（每小时），不在每分钟的 reaper 里。
- 内部读文件统一经 `_bound_file`：回收站中的文件 404，已清理版本 410。
- 资料库界面 E2E 在 `platform/e2e/test_library_ui.py`；设 `AILAB_E2E_SHOTS=/tmp/x` 会保存各状态截图供人工检查。macOS 上 mock 沙箱用宿主 LibreOffice 渲染，宿主未配置 CJK 回退字体时中文字形会被丢掉（只剩英文与线条），这不是渲染器问题；生产沙箱镜像装有 Noto CJK 与微软雅黑/宋体别名。
- 全局 `button` 样式带 `align-items/justify-content: center`。把整张卡片或整行做成 button 时要显式覆盖（`align-items: stretch; justify-content: flex-start`），否则内容居中。

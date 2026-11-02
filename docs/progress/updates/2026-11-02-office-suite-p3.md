# P3：文档查看后端（2026-11-02）

基线 `feat/office-suite@4872682`；工作树 `/Users/michael/.t3/worktrees/ailab-lite/t3-ae9a6e21`，分支 `feat/office-suite-p3`。只实施 P3，不修改 `images/`、`vendor/` 或 Portal，不 push、不建 PR、不部署。实现 `a12aa42`，复核修正 `5219aa8`、`513fe28`，回归 `ff5b0eb`、`db8bdc7`；最终产品代码基线 `513fe28`。

## 合同与实现

- 前端权威为 `feat/office-suite-portal@7d226ab` 的 `DocumentIndex`、`DocRef`、`SentDocRef` 和 `lib/js/client-calls.ts`。设计原表的单一 file 引用、`run_path` 和独立 capture_request/上传端点已过时；按 Portal 使用文件或 `{run_id,path}` 引用、reveal 的 `filename`/`path`、`client.call kind=capture_view`。设计 [04](../../architecture/design/office-suite/04-viewer-and-file-contract.md) 同步此差异。
- Worker 用固定 runtime 源码构建独立 reader，受限子进程生成 `derived/{file_id}/viewer.json`；内容 hash 和 `office-viewer-a60ba6a7-v1` 一起判断缓存。首次查看经 durable viewer job 派生；已有扫描、下载和来源授权保持有效。`/v1/files/{id}/document` 和既有 share 文件身份返回同形索引。
- 工作区 document/content 只代理本人正在运行的 Run 和 Broker 当前 active lease。路径逐组件以 no-follow 打开，拒绝内部目录、符号链接、非普通文件；快照前后 stat 围栏并计算内容 hash。内容经临时文件和 HTTP 流交付，断线也关闭上游。索引按 Run/content hash/parser revision 复用。
- `0048` 新增 `message_doc_refs`、带到期索引的 `viewer_captures`。最多 8 个引用、每段最多 4000 字；API 先检查归属和版本，Worker 用同一 runtime 的 `check()`/`read()` 校验 locator 和范围，API 规范化匹配 quote。保存 filename 快照，不转为全文附件。直接发送的 client_send_id 摘要包含引用；排队、插话、retry/edit、fork 和历史上下文继承冻结对象。文件引用另登记 agent_read 来源，沿用删除/撤权围栏。
- 查看状态按 user/conversation/view_id 隔离，Redis 10 分钟 TTL（memory profile 有界等价），每会话最多 16 个视图；不写 RunEvent。接受 Portal 的 `at:null`；去抖在 Portal。
- Chat/Work 固定相同 `document_view` schema。open/reveal 通过 attempt fence、稳定 command_id 和 outbox 发事件，只返回 `sent`。get_view 不猜“当前文档”，多视图返回候选。
- viewport capture 复用现有 owner claim/result，5 秒领取、总截止最多 20 秒；PNG 校验包含 base64、2 MiB decoded、签名、chunk CRC、尺寸/像素及精确版本。存储为有 owner、hash、10 分钟 TTL 的派生截图；上下文/checkpoint 使用图片引用，来源失效或到期不能恢复为另一张图。
- render capture 只接受可证明映射的 PDF page / PPT slide，复用有界派生页图；工作区在固定快照中渲染。缓存含 hash/render revision/范围。Word 段落、Excel 单元格与物理页无可靠映射时明确 unavailable；未就绪、离线、超时不会替换为第一页。

## 性能与准入

| 格式 | 原生字节上限 | 结构上限 |
| --- | --- | --- |
| DOCX | 10 MiB | 10,000 blocks，索引正文最多 200,000 字 |
| XLSX | 8 MiB | 所有工作表已占矩形合计 100,000 格 |
| PPTX | 20 MiB | 200 slides |
| PDF | 20 MiB | 300 pages |

超限返回 `native=false`，由 Portal 使用既有分页预览；不代表整个大文件不可读。解析输入最多 20 MiB、zip 展开最多 128 MiB/2048 entries/压缩比 200；Node heap 96 MiB、RSS watchdog 192 MiB、18 秒，外层 CPU/输出/22 秒期限。V8 大地址空间与 RLIMIT_AS 不兼容，仅 viewer 子进程不用地址空间限制，仍保留上述实际内存围栏。Worker native reader 共用一个槽位；繁忙时不静默增加并发。

macOS ARM64、Node 26.11.1 上四个代表性样本（含 Python 隔离进程启动）实测如下；镜像固定 Node 24.11.1，Linux 容器峰值和准入上沿压力尚未验收。这些是样本测量，不是容量承诺。

| 样本 | 输入大小 | 总耗时 | Node peak RSS | Node CPU（user+system） |
| --- | --- | --- | --- | --- |
| Word 2,000 段 | 120,600 B | 0.355 s | 95.9 MiB | 0.374 s |
| Excel 10,000 格 | 35,843 B | 0.350 s | 91.8 MiB | 0.386 s |
| PPT 20 页 | 44,545 B | 0.939 s | 80.0 MiB | 0.460 s |
| PDF 100 页 | 12,515 B | 0.802 s | 129.9 MiB | 0.566 s |

首个 viewer 请求才创建解析任务，超字节准入的文件直接返回 native=false，不下载或启动解析。Worker 和 Broker 的 native reader 各有一个并发槽。平台镜像新增 Node 与 reader 的依赖层；所有服务共用镜像，但仅 Worker/Broker 执行 reader。viewer 构建与 npm 锁文件位于 `platform/`；未改并行 P2 的资源目录。本机 production-only node_modules 实测 99,923,078 B（95.3 MiB），另加 Node 可执行文件；不同平台的 optional native 库和最终镜像体积未测。

## 验证

- Ruff check / format、差异空白检查通过。
- 受影响测试覆盖 API 归属/删除、version/quote/locator、发送幂等、retry/edit/fork/queue、查看 TTL、reveal outbox 和 capture PNG/claim/版本竞争；真实受限 runtime 的四格式 locator（包括 Word cell、Excel range、PPT shape）、工作区快照/owner/版本；recorded-request 的 tool schema 与引用追加。
- `0048` SQLite upgrade/downgrade、模型漂移及新查询 EXPLAIN 已覆盖；PostgreSQL 仅在 `AILAB_TEST_POSTGRES_URL` 设置时运行，本机未设置。
- Python 全套仅一次：`a12aa42` 加当时测试工作树，`pytest tests -q --junitxml=/tmp/ailab-p3-full.xml`，396.959 秒，2052 passed / 165 skipped / 7 failed。全套发现的实际产品根因是列表逐轮查引用（20 轮从 10 增至 30 reads）及临时复制 run 导致图片引用登记丢失；已改为批量 outer join 读取和只构造追加 prompt、保留原 run 图片身份。另更新迁移 head/固定工具旧断言，消除 fixture 文件名碰撞。
- 随后仅运行 7 个原失败项及修正涉及的 client-call/缓存/断线/前缀专项：前 15 项已逐项完成通过，但新增 ASGI 断线模拟的 receive 无限立即返回使该次复核忙循环，未产生最终 XML；中断后修正测试的 ASGI 2.4 scope，尚未完成的 4 项独立复核 4 passed，1.950 秒。另新增超限拒绝解析回归 4 passed，0.782 秒。未重跑全套，也不把首轮报告改写为最终全绿全套。当前无已知失败项。
- Ruff check、format（530 files）与 git diff --check 最终通过。真实四格式 runtime、work workspace、引用冻结、SQLite 迁移/EXPLAIN 和 recorded-request 测试均在首轮或对应专项完成。PostgreSQL 未设置，相关项跳过。
- 测试 runtime 在仓库外构建；旧 reader 的 mock 路径仅通过测试 Node resolve hook 指向外部构建，未在 vendor 写入构建物。最终检查环境：`PATH=/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/opt/findutils/libexec/gnubin:$PATH`、`NODE_OPTIONS=--import=/Users/michael/.cache/ailab-p3-reader/hook.mjs`、`AILAB_VIEWER_READER_PATH=/Users/michael/.cache/ailab-p3-reader/viewer-reader.mjs`，Python `/Users/michael/.cache/ailab-p3-venv/bin/python`；运行日志/XML 均在 `/tmp/ailab-p3-*`，不提交原始输出。
- 未运行 Portal build/浏览器（未改 Portal）、Docker 镜像构建、真实模型缓存比例、真实 Redis/PG 和五服务联合验收；不据 recorded-request 测试宣称供应商缓存指标达标。

## 后续边界

P2 reader/制作镜像迁移和 P4 Portal 不在本提交范围；整合时保留本 viewer 的 parser revision 与构建入口，不用旧 context reader 的 revision 冒充。P4 联合验收应覆盖真实 PNG 回传、多标签页认领、分享页，以及范围无法映射时的 unavailable。真实模型缓存指标、Linux 总 RSS 与准入上沿属于 P5；本轮无部署。

# 原生查看恢复与分页发布提速

## 根因与改动

基线 `main@910f1525`，分支 `fix/native-view-scan-recovery`；用户授权修复后提交并提 PR，未授权本轮合并或部署。#192 只恢复显式分页预览；旧文件原生打开时，缺扫描记录返回 409，Portal 的统一 catch 将它误当兼容性失败，启动分页渲染。

- API：原生入口对旧 sandbox 交付件复用 file outbox 扫描任务；持用户锁重新读取并发去重。扫描失败/任务过期返回明确错误；文件库与分享复用同一入口。
- Worker：复用真实扫描、流式摘要和大小核对；原生恢复不预热渲染沙箱，不解析或排分页。成功清除恢复标记，普通打开直接返回原生准入元数据。
- Portal：原生元数据接口的安全/权限/网络失败显示错误，不挂载分页组件；pending 轮询最多 5 分钟，关闭取消。格式/大小准入、索引不兼容及 renderer 失败仍可分页回退。

## 当前验证

- `ruff check`、`ruff format --check`：5 个相关 Python 文件通过；`git diff --check` 通过。
- `PYTHONPATH=. pytest tests/test_file_previews.py tests/test_document_view.py tests/test_eager_previews.py tests/faults/test_document_view.py -q -p no:cacheprovider --tb=short`：111 passed。
- `npm test -- tests/office-view.test.tsx tests/office-view-boot.test.tsx`：62 passed。
- `npm run build -- --webpack`：编译、TypeScript 和生产页面生成通过。默认 Turbopack 构建因独立目录复用的 node_modules 符号链接超出解析根目录失败；未为此更改产品配置。
- 首轮 Python 回归 1 项失败：新增去重断言把 fixture 初始上传任务计入扫描恢复次数；修为以请求前任务集合为基线，验证只新增一个恢复任务，完整上述范围复跑通过。

## 成本与边界

无依赖、迁移或新配置；正常 clean 文件不新增扫描/渲染。旧文件首次打开增加一次现有受限扫描和流式 hash，后续复用结果；等待期沿用 1.5 秒轮询，最多约 201 次元数据请求。未测真实扫描 CPU/RSS/耗时，未做浏览器、PostgreSQL、完整 CI 或部署验收，不改变既有外部验收结论。

并发 UI 工作区保持原样；本修复单独提交并提 PR，下一步以最终 head CI 为准，浏览器实测仍待完成。

## 既有渲染耗时调查

用户随后要求先提 PR，并询问误入分页的慢来自预热还是渲染；只读取既有 Broker/Worker 日志，没有重渲染或调整资源。

- 2026-11-13 11:30（香港时间）原失败作业：Broker 总计 35.572 秒，`lease_source=cold`，获取/就绪 27.427 秒，注入 0.382 秒，首次转换 4.194 秒、第一页栅格化 0.245 秒。主要耗时在冷沙箱获取/就绪；发布阶段随后因旧扫描记录缺失失败。日志没有把获取阶段细分为容器、服务和 LibreOffice listener 各自耗时。
- 当日 12:55 重试：Worker 总计 19.194 秒，调度槽等待 0.006 秒；Broker 总计 18.831 秒，同样 `cold`。获取/就绪 8.193 秒，注入 0.259 秒，首屏阶段 2.677 秒（已包含转换 1.389 秒和栅格化 0.199 秒，不能重复相加），PDF 缓存导出 0.266 秒，余下 8 页栅格化/归档传输/逐页发布合计 7.390 秒。
- 重试另有扫描 0.937 秒；从原生入口 409 到第一页发布约 12.4 秒、全部完成约 20.2 秒。既有日志不能再分解余页 CPU 与传输占比，也不代表浏览器实际绘制耗时。
- 结论：首次失败主要慢在冷启动；成功重试则是冷启动加多页生成/回传两部分，不能把所有等待归为 LibreOffice 转换慢。正常浏览器原生打开应避免整条分页链路，本 PR 修复该分流。

## 分页发布优化

用户随后要求优化转换渲染耗时，沿用本 PR；没有授权合并或部署。

- 隔离测量：取同一文件已有缓存 PDF，使用同一 `ailab-local-office:release-ef76d55` 镜像、runsc、1 CPU、768 MiB、禁网，在临时容器三次执行 `pdftoppm -png -scale-to 1280 -f 2 -l 9`。纯栅格化为 0.787 / 0.778 / 0.716 秒，8 页合计 333845 字节。测量时宿主负载并非原作业时刻，不能用两次耗时差精确归因或承诺端到端提速比例。
- 根因证据：余页已有一次 tar 回传，但之后每页串行执行容量预留、对象写入、容量完成、页面发布，其中三次为 API 往返；8 页合计 24 次。首屏 2.677 秒中，实际转换 1.389 秒、栅格化 0.199 秒，余量包含回传、写入和发布。
- 改动：余页每组最多 8 张，复用现有批量容量接口完成预留和 staging，再按原顺序逐页发布；8 页从 24 次 API 往返变成 10 次。第一张仍立即发布；PNG、1280 分辨率、隔离与 CPU/内存限制保持不变。日志补 `remaining_convert_seconds`、`remaining_raster_seconds`、`remaining_write_seconds`、`remaining_publish_seconds`，余页总时间仍包含归档/传输/解包。
- 成本：无新依赖、迁移、配置或后台任务；Broker 原有流式解包/写入不增加整文件内存副本，每组暂存最多 8 张，相比逐张释放增加有界临时磁盘占用，受原有余页总计 64 MiB 限制；归档保留原有 65 MiB 下载上限。成功和异常均及时释放暂存文件；未完成写入仍由持久容量账本补偿，已发布页不会因丢响应删除。
- 验证覆盖：10 页真实 Poppler 输出验证 `[8, 1]` 分组、staging 后发布、PNG 内容、临时文件释放；分别注入中途写入和发布失败，验证未发布对象回收、已发布页保留和沙箱释放；账本回归覆盖中途写入/完成响应丢失及批量容量原子性。初次故障测试未触发是 Poppler 十页输出使用补零页码，已改为解析数字页码，产品逻辑无需改变。
- 本轮代码基线为 `434e9177` 加本节产品/测试改动；`PYTHONPATH=. pytest tests/test_library_render.py tests/test_accounted_derived_writes.py tests/test_workspace_previews.py tests/test_derived_assets.py tests/test_file_previews.py -o addopts='' -q -p no:cacheprovider --tb=short`：98 passed / 15 skipped，34.64 秒；跳过项为未配置的 PostgreSQL，Ruff/格式检查及差异检查通过。测试前构建 vendored Office runtime，并按 HANDOFF 使用 GNU coreutils/findutils；初次缺构建产物和 BSD 工具造成的环境失败不计为产品失败。
- 边界：新增优化未做部署前后真实端到端对比、PostgreSQL 或浏览器验收；旧 head `434e9177` 的 11 项 CI 全绿仅证明此前原生恢复改动，不能当新增优化的最终 CI。

## 真实渲染对照与 Chat/Work 调用链

用户要求实测并确认 Chat、Work 是否复用。产品基线为 `2ea4bcb9`，与优化前 `434e9177` 的 `LibraryRenderer` 对照；两者使用相同其余代码。该产品提交 11 项 CI 全绿；本节只更新证据，没有改产品或部署。

### 条件与结果

- 同一 77879 字节、9 页 PPTX；只读复制既有原件。临时 API/Broker 各 0.5 CPU / 384 MiB，独立 SQLite、Redis、存储卷和 Docker 网络；真实服务身份、任务 claim、容量账本、HTTP 请求、runsc、LibreOffice、PNG、最终 Worker 完成接口均执行，沙箱沿用同镜像、1 CPU / 768 MiB。API 关闭后台调度，输入提前置为已扫描/已授权 fixture；没有模型、扫描、Agent/Worker 队列或浏览器绘制验收，不是完整用户端到端测试。
- 优化前后各 4 次，顺序 old/new/old/new/new/old/old/new，每次新文件记录避免命中 PDF 缓存；旧实现 2 cold + 2 warm，新实现 3 cold + 1 warm。所有 8 次均产出 9 张 PNG，逐页 SHA256 完全相同，总计 375126 字节；最终预览发布成功。

| 指标 | 优化前 | 优化后 |
| --- | --- | --- |
| 余下 8 页总时间，4 次 | 1.791 / 1.789 / 1.922 / 2.013 秒 | 1.603 / 1.611 / 2.462 / 1.783 秒 |
| 余页总时间中位数 | 1.8565 秒 | 1.6970 秒 |
| 全作业 Broker→API 请求总时间 | 0.114–0.171 秒 | 0.080–0.143 秒 |
| 余页容量/页面发布 API 次数 | 24 | 10 |

- 新实现余页纯栅格化 0.704–0.800 秒、批量写入 0.138–0.253 秒、页面发布 0.025–0.035 秒；剩余时间包含归档/传输/解包和调度。API 时间已经计入对应阶段，不重复相加。
- 中位数仅少 0.1595 秒，冷/热样本数不同且有波动，不能宣称稳定的端到端百分比收益。原来的 7.390 秒本次没有复现，现有证据不足以把原作业主要延迟归为 API 串行往返；此前只是发现可减少的固定开销，本次实测修正该归因。
- 冷租约从开始到首张 API 发布完成为 6.25–7.77 秒，完整 renderer 为 8.36–10.22 秒；热租约分别 1.26–1.38 秒、3.42–3.99 秒。首张 API 发布不等于浏览器绘制。3 次复用后的健康检查观察到沙箱进程 RSS 合计 592–594 MB，超过原有 563714457 字节阈值，沙箱按设计销毁，导致后续又是 cold；没有放宽阈值。冷启动/复用稳定性比此次小额往返优化更值得继续分析。
- Work 在独立真实 Work 沙箱、同镜像/1 CPU 下调用 `workspace_view.view`：沙箱获取另计 2.784 秒，首次未预热查看第 1–4 页 14.178 秒；同文件同页再次查看 2.785 秒，第 5–8 页 2.325 秒，每次 4 张有效图片。这里故意不执行 Agent 的 Office 预热，冷耗时不能代表预热成功后的正常首调用；未调用模型。
- 临时容器、其所有者名下沙箱、Redis、存储卷、网络、原件副本及原始输出在实测结束清理；保留本节必要结果。

### 复用边界

- **Chat 原生文字/结构读取**走 `broker/documents.py` 的 Office runtime，不是本次 `LibraryRenderer` 页图发布；读文字无需先把所有页转图片。
- **Chat `view_document`**：`api/document_visual.py` 对 PDF/PPTX 页图优先尝试 Portal 浏览器；Word/Excel，或浏览器不可用/超时，再由 `server_inputs → request_page_render → Worker process_preview → Broker LibraryRenderer` 补缺失页。因此本次批量容量优化自动覆盖服务器回退的多页生成；单页请求及直接浏览器路径不受益，缓存页直接复用。
- **Work 给用户看的预览**：`broker/service.py:_refresh_previews` 只发现变化并通知原生查看，不主动转换；显式兼容性回退经 `viewer.workspace → render_workspace_preview → _render_preview --pdf` 导出一个 PDF（表格为 sheets），按文件版本/渲染版本缓存，通常没有九张页图的逐页账本开销。
- **Work 模型 `view_workspace_file` / `review_artifact`**：`broker/workspace_view.py` 在本 Run 沙箱中使用同一个 `sandbox_tools/render_preview.py` 栅格化核心，最多选 4 页，一次 JSON/base64 回传，不经过 `LibraryRenderer` 或逐页账本发布。因此不能直接套用本次批量接口优化。已有租约内 LO listener 预热/复用，但 `view_workspace_file.py` 每次建临时目录并重新将 Office 转 PDF，调用完成清掉 PDF；重复查看同一未改文件仍转换，这与资料库已有 PDF 缓存不同。后续若优化，应在本 Run 内按内容版本复用有界 PDF 缓存并验证编辑后失效，而不是放宽资源/健康阈值。

# PPT 模板缩略图：改用 sandbox 预览流水线（2026-10-24）

## 范围与基线

- 分支 `feat/ppt-templates-backend`，本轮起点 `0d12f75`，实现及完整回归基线 `ad04b19`。后续仅修改连续性文档。未修改 `platform/portal/`，未push/PR/merge/deploy。
- 本工作树初始为 `t3/ppt-template-preview-pipeline@05e8785`；目标分支已在另一个工作树检出。按用户明确要求使用 `git switch --ignore-other-worktrees feat/ppt-templates-backend` 在本工作树继续；未修改另一工作树文件。父任务接续时应先核对共享分支指针与其工作树文件基线，不能把另一工作树旧文件误当新变更。
- 父任务关于初版Portal集成/视觉验收的描述是历史证据；本轮没有重新验收Portal，也不代表合入main或部署。

## 被否决方案与撤回内容

**本记录取代此前 Worker Office 渲染方案及其验收结论。** `710eba3`/`771d9fe`/`0d12f75` 的历史测量不作为当前实现验收：macOS单页256MiB配置约2.038s、整组RSS245.2MiB；默认192MiB必然触限，20页在256MiB也有失败。该方案不满足Worker预算，已撤销，不能通过提高默认内存恢复它。

- 平台 Dockerfile 删除 LibreOffice Impress、Poppler、CJK字体、fontconfig、procps 安装步骤。
- Worker删除Office renderer入口、85%/15%渲染/草图分配、仅为其增加的进程组RSS采样、RLIMIT_AS豁免、递归输出监测及测量脚本；普通解析恢复原有限额。保留原模板分析、POTX直接读包和受限草图生成，草图失败仍保留ready。
- 新草图对象为 `derived/{file_id}/template-sketch-v3.jpg`；与旧v2真实/草图共用路径区分，保留旧v1 WebP/v2 JPEG读取兼容。新模板立即展示草图，不同步等待Office。

## 当前链路与契约

1. API处理模板ready时，在已有用户锁内调用与资料库共用的 `preview_requests.request_preview`：文件状态、pending manifest、job_id、preview outbox在同一事务提交。已登记的pending/ready/failed不因模板补丁重试或文件作业重投再次排队；事务回滚也回滚作业。未新增JobEnvelope类型、队列或后台循环。
2. 原dispatcher/claim/ACK与 `worker.preview → Broker LibraryRenderer → render profile sandbox` 执行一次文件预览；同一文件/作业绑定、锁、超时/失败/租约释放保持原规则。POTX加入原有Office预览后缀集合。
3. sandbox完成原有最多40页JPEG后，在同一个sandbox从首张页面图缩放至约480px宽，只生成一次 `preview/template-thumbnail.jpg`。Broker复用原有总输出预算上传；内部完成DTO容许40页加1缩略图，API校验当前job、文件固定路径、MIME、hash与manifest资产集合。缩略图不加入公开pages数组。
4. 模板thumbnail endpoint沿用原公司/个人可见性；预览ready且有已验证缩略图时优先真实JPEG，否则草图。真实图URL追加 `?v=<缩略图sha256>`，ETag同步改变，保留private immutable及304。模板JSON字段不变。无草图时仍可在预览完成后获得真实图。
5. template status与preview独立；preview失败不破坏成功分析、冻结或使用模板。模板文件仍排除在资料库列表、搜索、普通文件读取和分享之外。

## 性能与资源

- **Worker/平台镜像新增Office渲染成本为零；每模板一次sandbox preview，成本与一次资料库预览相同。** Worker仅保留原有轻量分析/草图及预览调度；无LO常驻进程或新增依赖，没有提高Worker/平台Pod预算。
- 复用render profile默认768MiB、最大120秒租约/总渲染预算；页面上限40、派生上传合计32MiB、每对象取现有read cap（默认10MiB）。缩略图多一次小图缩放和JPEG磁盘对象，仍计入同一输出预算；API/Worker不解码页面，也不新增完整Office文件缓冲。
- 新查询仅已有主键文件读取；未新增列表查询、索引、迁移或数据库表。不是将复杂Office文档的成本降为零，也不是长期并发容量证明。

## 验证

从本工作树platform执行，复用Python3.12解释器并设置 `PYTHONPATH=.`；`tests/conftest.py` 在载入配置前禁用开发者.env，指向临时目录/数据库。本轮未读取开发者DB/.env。实际命令：

```bash
export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/opt/findutils/libexec/gnubin:$PATH"
PY=/Users/michael/Developer/ailab-lite/platform/.venv/bin/python
RUFF=/Users/michael/Developer/ailab-lite/platform/.venv/bin/ruff
"$RUFF" check .
"$RUFF" format --check .
PYTHONPATH=. "$PY" -m pytest -o addopts='' tests/test_template_thumbnails.py \
 tests/test_templates.py tests/test_templates_migration.py tests/test_scanner_isolation.py \
 tests/test_file_previews.py tests/test_library_render.py -q --tb=short
PYTHONPATH=. "$PY" -m pytest -o addopts='' tests -q --tb=short
AILAB_TEST_REDIS_URL=redis://127.0.0.1:26479/15 \
 AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:25479/ailab_test \
 AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:25479/ailab_migrations \
 PYTHONPATH=. "$PY" -m pytest -o addopts='' tests -q --tb=short
```

- Ruff check/format通过（329文件）；专项 **84通过、1跳过，22.61s**。跳过为未配置PG的迁移参数。PPTX/POTX真实Office/Poppler renderer测试实际执行，经Broker租约生命周期，验证2页完整预览、480px首屏缩略图上传和租约释放；进程执行由本机mock sandbox适配器承载，不能称OpenSandbox容器验收。
- SQLite全量：**1123通过、118跳过，0失败/错误，159.36s**。
- PG+Redis全量：**1208通过、33跳过，0失败/错误，202.54s**。含双库迁移、outbox/queue与Redis合同测试；剩余跳过为S3/OIDC/真实容器等外部依赖，未算作通过。
- 覆盖ready登记一次、补丁/作业重投幂等、事务回滚、URL/ETag切换、304、个人权限、预览失败仍可冻结使用、草图失败后真实图接管、平台Dockerfile无Office依赖；原模板/资料库/隔离用例保持通过。
- 临时PG16/Redis使用独占25479/26479；启动前检查端口，启动后确认data_directory、pidfile与Redis INFO process_id一致、Redis空DB。16379保持不动。全套完成后再次核对Redis进程归属，执行SHUTDOWN NOSAVE，PG按本任务data_directory执行fast stop；两端口均无监听。临时目录保留作本机诊断，不删除非本任务资源。

## 移交与未验边界

- 父任务下一步审核/集成本分支，并在真实sandbox/cgroup与目标Linux环境验收；不需要给平台镜像新增Office依赖。未构建Docker镜像、未跑真实OpenSandbox/ClamAV联合部署、未验证长期容量或真实模型产物。
- 未运行Portal/E2E或Portal build：本轮禁止修改Portal，当前工作树无Portal node_modules，未为此安装依赖或更改Portal目录。无UI变更。
- 已ready且无preview记录的旧模板不自动批量回填；其旧缩略图保持读取兼容。新ready或后续ready补丁会按同样幂等规则登记一次预览。零页/损坏/复杂模板可能预览失败，仍保留草图与可用模板。

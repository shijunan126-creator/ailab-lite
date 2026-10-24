# PPT 模板后端交付（2026-10-24）

## 范围与状态

- 分支 `feat/ppt-templates-backend`，base `main@6b6825d0c27ccd7017467586c0ac4a9d30f3fd34`；最终运行代码/回归基线 `d09043a`。本页之后只更新接续文档，不改变运行代码。
- **已在独立分支实现并本地验证，未push/PR/merge/deploy**。`platform/portal/` 无任何改动；Portal及端到端UI集成归父任务/Claude。
- [接口契约与实现决策](../../reviews/2026-10-24-ppt-templates-design.md)。下一步是父任务集成Portal分支、确认缩略图视觉边界；不自动部署。

## 交付

- 0028←0027模板表、公司/个人稀疏列表索引及file_id索引、DB种类/范围约束、旧品牌PPT转公司模板并补事务outbox分析任务，双库升/降/再升及ORM漂移检查。
- `/v1/templates`列表/上传/详情/改名/软删除/thumbnail。个人20/公司50、30MiB均可配置；权限、个人配额、公司配额豁免及资料库/分享/摘要隔离。
- Worker先扫描，再受限子进程分析主题/字体/布局/占位符/画布并生成首屏联系图；POTX直接读其main part，避免重打包突破16MiB解析输出上限；宏拒绝、failed与扫描rejected区分、不可变缓存/ETag。
- Run/消息/队列在准入时冻结 `options.template`；重试、编辑、问答续跑、分叉、队列派发复用。列表/详情只增加name/source摘要。Broker restore后总是reset `.template`，按Run绑定授权有界流式注入原文件和可选分析。
- 生成器保留masters/theme/layouts、删除示例slides；Title/Content/Two Content等按占位符匹配，富内容无适合版式时自由绘制，沿用模板主题/字体；不额外叠加brand logo。短SKILL指导已更新，manifest校验一致。
- Portal补充契约：`category=slides`支持文件和folders列表，大小写不敏感pptx/potx、继承分页/项目/回收站规则；新增稀疏索引，SQLite/PG EXPLAIN回归。SQLite小样本folders可选择原有covering index，未强迫更差计划。

## 决策、偏差与兼容

1. **缩略图保真度边界**：Pillow绘制首张slide/无slide时首layout的图片、文字、形状。不是完整Office像素级渲染，复杂主题背景、图表、母版对象/特效可能缺失。Worker无新增LibreOffice依赖；产物完整预览仍沿用Broker Office。父任务须确认这一视觉边界，不能称作完整PowerPoint截图。
2. 对外 `failed` 通过meta.template_error区分；底层FileObject仍沿用原rejected枚举，不扩散全库状态机。接口JSON与约定一致。
3. template队列不允许steer（400，包括后续改为steer），因为模板属于下一Run而非当前轮。定时任务创建/更新明确拒绝非空template（422），null/缺省忽略。
4. 注册模板软删除不回收原/派生文件；个人保留字节继续计配额，公司不计。单轮资料库模板沿用原资料库回收/版本过期规则；未引入永久字节复制或自动模板GC。
5. 老品牌PPT指针仅留在设置存储以支持downgrade/历史冻结Run；新资产种类只有logo/docx。迁移时旧PPT暂回pending直到Worker补扫描分析，部署需等待其ready再放行需要旧模板的续跑。
6. 迁移补任务topic/deadline来自有效Settings，兼容env_file自定义队列；双库测试显式覆盖非默认topic，未硬编码默认生产队列。

## 验证命令与结果

以下均从本工作树 `platform/` 执行，`PY` 为复用的Python3.12虚拟环境解释器 `/Users/michael/Developer/ailab-lite/platform/.venv/bin/python`。`PYTHONPATH=.`及cwd锁定本工作树源码；conftest指向不存在的env文件和临时数据目录，未使用开发者DB或.env。

```bash
export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/opt/findutils/libexec/gnubin:$PATH"
PY=/Users/michael/Developer/ailab-lite/platform/.venv/bin/python
# Ruff check / format --check：通过，327文件已格式化。
/Users/michael/Developer/ailab-lite/platform/.venv/bin/ruff check .
/Users/michael/Developer/ailab-lite/platform/.venv/bin/ruff format --check .
# SQLite 全量：1113 passed, 118 skipped，0 failed/error；148.17s。
PYTHONPATH=. "$PY" -m pytest -o addopts='' tests -q --tb=short
# 临时 PostgreSQL16 + Redis 全量：1198 passed, 33 skipped，0 failed/error；独占26379最终188.21s。
PYTHONPATH=. AILAB_TEST_REDIS_URL=redis://127.0.0.1:26379/15 \
 AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_test \
 AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_migrations \
 "$PY" -m pytest -o addopts='' tests -q --tb=short
# 相关五服务E2E：1 passed，0 failed/skipped，18.76s。
PYTHONPATH=. AILAB_E2E=1 AILAB_TEST_REDIS_URL=redis://127.0.0.1:26379/14 \
 AILAB_E2E_BASE_PORT=18480 AILAB_E2E_PORTAL_PORT=13400 \
 AILAB_E2E_PORTAL_DIR=/Users/michael/Developer/ailab-lite/platform/portal \
 AILAB_E2E_LOG_DIR=/tmp/ailab-ppt-e2e-logs \
 "$PY" -m pytest -o addopts='' e2e/test_ppt_templates.py -q --tb=short
```

- 最终双库迁移专项：2通过；完整套件同时包含0028品牌迁移、自定义topic、线性链升降再升、EXPLAIN、权限/limits/type/size、POTX/无slide缩略图、infected/failed、freeze/queue/retry/edit/resume/fork、Broker授权/重置/限额、真实小模板生成器、library/search/shares隔离。
- 五服务专项使用真实HTTP/outbox/Redis/Worker受限子进程/Agent/Broker与恢复快照；仅模型/沙箱是mock。验证上传ready与ETag、模板注入、下一轮无模板清除。fixture复制构建Portal并附带运行其33项测试（33通过），未修改Portal、未声称其新模板UI已验。
- 技能 `index.json --check`、build.py compile及`git diff --check`通过，未在哈希技能目录生成__pycache__。
- 历史首轮失败保留：SQLite为8失败/1098通过/118跳过/6错误；PG为8失败/1183通过/33跳过/6错误。macOS未加GNU PATH使snapshot/stat/预览失败；另发现无模板多写null options和清库fixture遗漏模板外键，均已修复。最终全量不是沿用首轮结果。

## 性能与未验边界

- 可重现：`AILAB_MEASURE_LARGE=1 PYTHONPATH=. "$PY" -c 'import tests.conftest; from tests.measure_templates import main; main()'`。
- 20页44,559字节，5次：CPU中位0.113845s、wall中位0.183472s、解析子进程最大RSS44,498,944字节、输出最大5,857字节。
- 9张随机1024×1024图片、28,405,889字节模板，1次：CPU0.148174s、wall0.195082s、解析子进程RSS81,018,880字节、输出39,227字节。未计ClamAV扫描成本、上传网络/对象存储或父服务RSS。原始文件/输出在临时目录，探针结束清理。
- 没有新运行依赖；分析和首屏图只对上传执行一次并缓存。继承192MiB/45s CPU、wall/output/ZIP/Pillow限制；大文件下载、Broker注入仍为有界流式。不是2vCPU长期并发容量证明。
- SQLite跳过包含未配置PG/Redis及外部组件；PG+Redis仍跳过33项外部S3/MinIO、真实Docker/OpenSandbox/网络策略、ClamAV、OIDC/Keycloak、公共网络等未配置契约。没有把skip当通过。
- 未跑全量旧Portal UI E2E（旧品牌PPT槽用例等待另一Agent同步）；本任务要求的相关五服务E2E已跑。未跑付费真实模型、重建后的sbx-office/Docker真实生成器联合验收、生产secure ClamAV/gVisor/OSB、长期容量或真机UI。

## 清理与部署移交

- PostgreSQL/Redis为本次initdb/启动的专用临时服务，最终使用独占127.0.0.1:15432/26379，未连接开发者数据库；启动前lsof无监听，Redis pidfile与INFO process_id一致且新DBSIZE=0。首轮16379存在另一个wildcard非本任务Redis监听，本任务特定绑定实例日志证实已自行退出，原进程未被操作；为消除端口歧义，在26379重新完整验证。最终按pidfile/INFO核准归属后关闭，15432/26379无监听。E2E的五个子服务已退出，18480–18483/13400无监听。
- 父任务集成并复核后才能另行授权部署：备份→升级0028→发布应用→**重建sbx-office镜像及技能层**→等待迁移模板ready→真实模板/无模板/品牌/续跑验收。此分支未推送、无PR、未合并、未部署。
- 残余风险：联系图保真度、软删除字节长期保留、资料库one-off过期、迁移扫描等待窗口、真实Office/安全组件组合和生产规模性能；均不可由本地mock或短样本替代验收。

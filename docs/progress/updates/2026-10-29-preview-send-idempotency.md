# 首次预览与发送幂等后端（2026-10-29）

基线 `origin/main@97793e601d90d0e9db11d60621c4f1352450166b`，工作树 `.worktrees/frontend-genui`、分支 `feat/frontend-genui` 的未提交修改。Portal 与 GenUI 后端同时修改本树，本记录仅覆盖预览/发送任务；未提交、push、部署或调用付费模型。

## 原因、测量与取舍

首次点击原本才创建预览任务，用户承担排队、独立沙箱创建/注入、独立LibreOffice profile、全文件转换、最多40页栅格化和逐资产导出。重复打开直接读持久清单。

通过个人Docker shim（默认socket不存在），用现有 `ailab-local-office:reading-97793e6` 镜像启动一次性、无网络、1 CPU/768MiB/128 PID容器，stdin注入仓库未改动的render脚本和合成样本，不读业务库。12页简单DOCX渲染6.364秒、CPU0.749秒、子进程峰值RSS221016KiB（约216MiB）、JPEG输出277652字节；40页空白PDF渲染0.142秒、CPU0.141秒、输出818680字节。该进程的getrusage子进程峰值为累计值，不能把同一进程内第二份PDF的RSS当独立样本。CPU不含Python测量父进程；不含沙箱创建、注入、对象存储、排队和网络。样本不是用户文件，不据此承诺任意文件的冷延迟。

历史#107系统reader测量为创建/准入1.84–2.39秒（包含注入）、执行0.46–0.91秒、导出0.29–0.30秒、释放0.74–1.19秒；这是不同镜像/样本的历史证据，不能和上述DOCX数字直接相加作为端到端实测。来源见[原记录](2026-10-29-document-reading-reliability.md)。

选择提前渲染与完整页面复用：API只在现有事务写metadata/outbox，不同步调用沙箱，不额外等待渲染结果；原dispatcher约每秒扫描、有界50条/5秒，原Worker单渲染槽与Broker资源准入保持。上传ready走原`request_summary`派生任务调度入口（先于摘要开关），已扫描产物走`trim_file_versions`，flush后才能安全读取ORM默认purpose；均在原提交内。现有pending、ready、failed不被自动重置。`AILAB_PREVIEW_EAGER_MAX_BYTES=20971520`，0关闭；超过阈值仍可点击后按原限额请求。图片/普通文字不新建推测任务。

冷任务总时间T包含dispatcher、排队、沙箱/渲染/导出。原先点击后等待约T+下一轮poll；现在从ready就开始，ready到点击间隔D后剩余约max(0,T-D)+下一轮poll，已ready则无需poll。立即点击、Worker繁忙或大文件不能承诺零等待，也没有减少未命中时的LibreOffice转换CPU。完整OCR页命中会免除整个重复沙箱/转换/导出，只有已知总页数、连续覆盖前min(40,总页数)、同file私有key与有效digest/MIME时才复用；部分选页继续按原渲染路径处理。普通reader归档并没有可直接复用的转换PDF。

没有选择共享LibreOffice profile/常驻解析器：目前没有证明安全且收益明确的隔离方案。没有改成PDF/progressive清单：文件库`contracts/file_preview.py`实际上只接受pages/sheets/image/text；已有kind=pdf是Run工作区合同。PDF完成校验需要修改本任务禁止触碰的`routes_internal.py`，因此本轮保持旧合同。未改该文件或Portal；`routes_conversations.py`只在send_message传递新增字段，不触碰GenUI白名单。

## 客户端合同

- 文件库预览无新增status/kind/字段，不增长pages；旧缓存继续可用。保持GET `/v1/files/{id}/preview`，pending按原1.5秒轮询，ready停止并使用现有viewer。提前排队不代表文件尚未ready；上传或发送无需等预览完成。资产继续在`derived/{file_id}/preview/`，沿用immutable缓存与ETag/304。
- POST `/v1/runs` 和 `/v1/conversations/{id}/messages` 的JSON body可传 `client_send_id`，1–64字符。UI每次逻辑发送只生成一次（建议UUID），等待附件ready后提交，丢失响应重试必须保留ID和原请求字段；不要在重试中改model默认值、附件顺序等字段。
- 同用户同ID同请求返回原Run的当前RunDetail（相同run/message id，状态可能已变化），HTTP仍201；不会创建第二个Run、run.created事件或outbox派发。同ID用于不同内容、会话或端点语义返回409；不同用户可用相同ID。缺省/null保持原来的每次创建；空串/超过64字符/非字符串422。已删除会话仍按原路由授权返回404。
- 复用Run.options保存规范化请求SHA256；为了跨副本唯一约束和精确索引查询，0037只新增nullable `runs.client_send_id` 和 `(user_id,client_send_id)`唯一索引，SQLite普通ALTER，无重建被引用表；旧Run为null，支持downgrade。用户锁内先查重再检查附件、额度和会话忙碌；SQLite仍限单API进程，PG沿用用户行锁。客户端可以用原响应id归并本地乐观消息，响应格式不变。

## 成本与边界

无常驻进程/新服务/模型调用。提前渲染会为可能从不打开的≤20MiB文件付出原有渲染CPU/临时磁盘和持久预览空间；每文件一次，最多40页、现有累计32MiB输出限制、现有单渲染槽、Broker默认768MiB/120秒租约及资源准入保持。样本持久空间分别约271KiB/800KiB，不能外推图像丰富文件。API增加一条outbox与小清单，不读取原文件；原有摘要策略未改变。完整页面命中不复制资产。新增发送ID每Run最多64字符、请求摘要64字符和一个唯一索引；查重是索引点查询。日志只记录低基数阶段名称/耗时，不记录文件内容、凭据或签名URL。

Broker日志新增acquire（含inject）、exec、export、release、total；Worker日志新增slot等待和total，保留日志用于真实部署后定位，未宣称跨队列端到端追踪。2vCPU目标没有本轮真实负载验收；提前预览可能增加文件队列积压，现有并发控制保护执行资源但不是吞吐承诺。

## 集中验证

- 新worktree原无`.venv`，通过仓库constraints安装Python开发依赖；本地Office mock所需`vendor/zcode-office-runtime/runner.mjs`也原不存在，按README执行`npm ci --ignore-scripts && npm run build`，不跑额外Office测试。两类构建产物均被git忽略。
- 按指定顺序统一执行Ruff check与format --check，全树441文件通过；后续只修改迁移集成测试的head/链断言并格式化该文件。
- 专项一次70项：最终69通过、1跳过（PG）。最初1个参数化测试复用了相同用户级ID，正确触发409；改成每种逻辑发送独立ID，仅重跑该失败项1通过。覆盖ready无点击入队、事务产物交付/重复交付、阈值/关闭/删除/pending/终态幂等、完整OCR页复用和部分/私有资产拒绝、旧清单/ETag、并发/丢响应发送去重、内容/会话绑定、用户隔离、0037 up/down与SQLite精确lookup EXPLAIN。
- 使用GNU coreutils/findutils PATH执行完整SQLite套件一次，1662项/212.05秒：初次1501通过、143跳过、18失败。17项来自缺Office reader构建产物，1项来自旧集成测试硬编码head=0036；补齐构建、将集成测试扩展到0037后仅重跑18个失败项，18通过。最终逐项结果为1519通过/143跳过，**没有重新跑第二次全套**。新0037精确查询走`uq_runs_user_client_send`，模型/迁移漂移及有外键数据的迁移链回滚通过。PostgreSQL迁移/EXPLAIN测试已编写但本轮无专用测试URL，明确跳过，未宣称实测PG。
- 最后差异审查和`git diff --check`通过。未改Portal、GenUI专属文件或`routes_internal.py`；并行工程师的修改保留。未做Portal构建/浏览器、真实付费模型、生产部署或2vCPU容量验收。原始临时日志在仓库外，不随项目保存。

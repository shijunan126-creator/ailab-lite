# 2026-10-24 PPT 模板集成测试与干净部署验收

## 授权与代码基线

主工作树 `feat/ppt-templates-portal`，起点与部署源码固定为 `b2c8394047279c634802a8ef9591d8bf275fac56`，本地 merge-base `main@6b6825d`；未 fetch，不声称远端最新。用户授权全套验证、后端根因修复、小提交、保全配置后清空旧测试部署并从空库部署、真实模型和模板验收、记录资源与清理测试数据。禁止修改 `platform/portal/`、push、PR、merge；三份未跟踪 `.DS_Store` 保持原状。本次新授权覆盖旧记录中的未授权部署/必须备份要求，仅针对本次测试部署。

个人配置、账号、地址、部署脚本、原始日志和硬件诊断均仅留仓库外本机交接；本文只保留产品基线、测试结论和可公开的验收边界。

## Part 1 当前验证

- Ruff check 与 format --check 通过，329 文件；`git diff --check` 通过。
- 固定 `b2c8394` 的完整 SQLite：**1123 passed / 118 skipped / 0 failed**。完整 PostgreSQL16（UTF8/C locale）+ Redis：**1208 passed / 33 skipped / 0 failed**。使用独立 initdb、非默认端口和空 Redis，未用开发库；PG、Redis均已停止，相关监听关闭。
- 本轮最终完整五服务 E2E：**88 passed / 0 failed / 0 skipped，260.24秒**。真实 HTTP/outbox/Redis/Worker/Portal production build，模型和 sandbox 为 mock；不作为真实 Office 或付费模型部署验收。
- Portal `npm test` **33 passed**，隔离副本 `npm run build` 通过。运行库现有 E2E fixture，不修改 Portal 源码、tsconfig 或开发 `.next`。
- `kubectl kustomize` 成功渲染 `platform/deploy/overlays/{dev,lite}` 与根目录 `deploy/overlays/lite`。这是静态渲染；未对新 K3S 集群 apply 或重新验收控制器/CNI/gVisor 联合运行。

SQLite/PG日志因项目默认 `-q` 与命令 `-q` 叠加不显示总结，已按全部进度行核对1123/118与1208/33，均抵达100%，退出成功；没有用旧记录计数替代。SQLite跳过未配置PG/Redis及外部组件；PG+Redis33项仍需外部S3、真实OSB/Docker/网络、ClamAV、OIDC等显式契约环境，不把跳过记为通过。

### E2E 适配与历史失败

原生模型 `select` 已变为按钮/listbox；原生确认框已变为 `alertdialog`；个人技能位于“更多技能”菜单；旧品牌 PPT 槽已由模板注册表取代，品牌槽保留 Word 模板。只修改 `platform/e2e/`，保留模型可见列表/权重/默认项/选择持久化、确认删除成功和失败、品牌分析与设置持久化、技能使用和隐藏断言。模型选择按可见选项点击，不通过localStorage或API绕过UI；确认后再验证原有业务结果。

首轮因系统性旧选择器中断，不能计为全套通过；第一次适配后 **86通过/2失败**，分别为测试误加无关的记忆重载确认、仍使用旧个人技能快捷入口。纠正后完整重跑88项通过，不放宽产品断言。此处仅说明旧E2E适配；部署新增确认的Portal缺陷见下文，不能由88项通过覆盖。

## Part 2/3 最终结果

固定 `b2c8394` 已部署并保持运行，**未合入 main**。本轮仓库提交 `e905790` 仅修改E2E/文档，部署镜像仍来自b2c8394，后续文档提交不改变产品。保全配置后删除旧测试部署容器、镜像、卷、数据库、存储、源码快照、旧备份及构建/下载缓存；没有备份旧业务数据。保留模型/密钥、运行设置、管理员启动配置、端口与网络访问设置，只更新镜像/源码引用和本地构建同步入口。

外部存储中断使已拉取依赖层出现全零程序；恢复后普通pull仍复用坏缓存，因此停专用容器运行时、清空其存储、重新冷构建三类镜像并拉取依赖。完整过程、诊断和配置校验只在本机交接，不把个人环境细节放入Git。最终VM快照833文件、平台164文件、Portal78文件、Office技能147文件hash全部匹配固定源码。

### 已实测

- 空库升级 **0028**，`quick_check=ok`，外键错误0；初始仅2个bootstrap用户，业务对象全零。九容器运行：七项已配置healthcheck均healthy，SearXNG健康端点/Prometheus readiness均200。新管理员真实浏览器登录通过；原有网络地址本机访问200，未宣称外部设备验收。
- 公司、个人PPT模板都观察到草图→真实首屏 **480×262 JPEG**，分别约 **6.71/6.70秒**；真实图URL和ETag改变，已经看图。渲染发生在有资源限额的真实Docker/runsc sandbox，不在Worker装Office；平台镜像保持无新增Office成本。**API切换通过；页面自动切换失败，见缺陷。**
- 真实模型有模板任务生成2页PPT：12个母版/版式XML与模板canonical XML逐个一致，保留11×6原尺寸，两页使用模板原生版式，不含模板示例页/示例文字。运行中steer写入checkpoint并出现在PPT内容中。
- 排队的无模板任务由终态自动派发，再生成2页独立PPT；Run `template=null`，未沿用原模板版式。两次均实际deliver_file并下载核验，非mock模型。模型13条计费调用，prompt114511、completion3682、reasoning1363；这是有限样本，不是普遍遵循率或现金费用测量。
- PPTX和DOCX资料库预览经真实Office流水线ready，浏览器查看器图片实际加载；未见旧spinner卡住回归。指定接收者分享、接收通知、标记已读、撤销后拒绝访问通过。
- 内置preview打开新登录页；snapshot连续两次原始错误 `Preview automation snapshot timed out after 15000ms.`，按仓库路线使用Playwright完成上述登录/Office预览/缩略图交互。未保存凭据、cookie、storage-state或部署浏览器trace。

### 已确认的 Portal 缺陷：真实缩略图不自动替换草图

复现（公司、个人均实际发生）：

1. 管理员打开“管理→品牌→PPT模板”，上传一份PPT；或在工作模式选择PPT技能，打开模板菜单上传个人PPT。
2. 等待pending结束，页面显示草图。继续观察当前页面/打开的模板菜单，不刷新。
3. API `GET /v1/templates/{id}` 已返回ready和带 `?v=<hash>` 的新thumbnail_url，独立取图是渲染首屏；页面img.src仍为旧的无版本URL，额外等待6秒也不变。
4. 公司页刷新、个人菜单关闭后重开，img.src立即更新为带版本URL并显示真实图。

根因：`platform/portal/components/admin/brand.tsx:247` 与 `components/chat/template-picker.tsx:55` 只按status=pending轮询。模板ready早于异步preview完成；取得ready草图后停止跟踪，后来的真实URL到不了组件状态。后端不得延迟ready或把preview失败变成模板不可用来掩盖UI生命周期问题。

本任务禁止修改Portal，故只报告精确复现。公司/个人两次结果均为 `live_switch_to_rendered=false / refresh_shows_rendered=true / observed_sketch=true`；本机保存DOM/API观察及对应截图。**“无需刷新自动切换”验收未通过，不能标记整个模板UI外部验收完成。** 下一步交Portal负责人修复preview生命周期跟踪，补真实图切换回归，随后复验。没有push/PR/merge。

### 清理与资源

清理前确认只有本轮测试数据（3用户/1对话/2运行/7文件/4模板），没有其他新项目或会话。结束后移除测试工作沙箱及测试数据卷，使用相同已核验镜像重新从空库启动；最终0028/quick_check ok/FK0、2个bootstrap用户、项目/对话/运行/文件/模板全零，再次浏览器管理员登录与空列表通过。测试账号、产物、模板、队列和账本均已移除，系统继续运行；保留当前必需的扫描定义缓存。临时PG/Redis/E2E端口已关闭，测试失败trace在诊断后删除。

- 镜像Docker CLI显示：**platform535MB、Portal949MB、sbx-office1.38GB**；三类均ARM64全量冷构建。最终7镜像，Docker镜像统计4.094GB；9容器、6活跃卷、构建缓存0。共享层、内容压缩大小与宿主实际占用不是同一口径，不相加。
- 宿主目录 `du` 清理前 **18.4924GiB**，清理/guest trim后 **5.9955GiB**，实际减少 **12.4969GiB**；同时保存df前后值，不用逻辑镜像删除量代替宿主回收量。
- 容器外采集`docker top`进程RSS求和，5次/8秒中位值如下。采样器不进入业务容器；属于重启后的短时空闲样本，不是峰值/CPU/混合并发或长期容量证明。

| 服务 | 空闲RSS MiB | 内存限额 MiB |
|---|---:|---:|
| API | 130.64 | 384 |
| Agent | 93.62 | 256 |
| Broker | 95.75 | 384 |
| Worker | 91.44 | 384 |
| Portal | 84.92 | 384 |
| Redis | 9.09 | 128 |
| ClamAV | 988.33 | 1536 |
| Prometheus | 74.08 | 192 |
| SearXNG | 139.95 | 512 |

五应用合计496.37MiB，全部九服务RSS求和1707.82MiB；多进程共享页可能重复计数，不等于整台VM实际RSS。

## 验收边界与下一步

历史UI发布已在部署验证过的fork/retry/edit、页码引用、memory、调度到期、低阈值压缩与手机视口不在本轮重复执行；对应历史范围见[UI发布](2026-10-24-release-ui-features.md)、[工作区集成](2026-10-24-workspace-features-integration.md)。工作区原来只有临时栈证据，故本轮新增部署queue/steer/share/inbox/Office与模板真实链路。

K3S按文档完成静态渲染与资源唯一标识检查：dev34、lite32、sandbox6对象，相关部署shell语法检查通过。没有新建K3S集群或apply，没有宣称本次重验OSB控制器/CNI隔离；真实办公运行使用现有Docker/runsc路径。未重跑外部S3/Keycloak等跳过契约、长期混合容量、多架构发布、物理手机或外部设备网络测试。

剩余明确阻塞为Portal缩略图自动更新；外部存储断连的硬件/链路原因尚未消除，恢复后正常运行不等于稳定性证明，诊断与下一步硬件交叉验证仅留个人交接。父任务下一步处理Portal缺陷并复验，再自行决定push/PR/merge；本任务无此授权，不继续其他路线图项。

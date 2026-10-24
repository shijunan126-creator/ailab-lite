# PPT 模板后端契约

基线 main@6b6825d；分支 feat/ppt-templates-backend。仅后端及镜像技能；不修改 Portal，不推送、部署。

- 模板默认关闭。三种来源：全员可见的公司模板、本人模板、单轮资料库文件；仅 Work 可用。
- `/v1/templates` 提供公司/个人分组列表、multipart `upload`、详情、改名、软删除和缩略图。字段严格按产品契约；缩略图地址不带 `/api`。
- 0028 接 0027，独立 templates 表；个人/公司上限 20/50，文件默认 30 MiB，由 AILAB 设置控制。公司不计个人配额，个人计费；均不进入资料库、分享、摘要。
- 上传仅接受 pptx/potx；内容检查和分析在受限 Worker 子进程完成，先扫描后分析，拒绝宏内容。分析失败对外为 failed（底层 FileObject 沿用 rejected 状态，以 meta 区分扫描拒绝与分析失败）。
- 创建 Run/队列时授权并冻结 source/id/file_id/name 于 options.template；重试/编辑/恢复复用，不再依赖可变模板列表。软删除只隐藏注册项，不清理字节。
- 排队模板不得作为 steer 注入当前轮；只能派发新轮。定时任务不支持 template，显式拒绝。
- Broker 在恢复工作区之后清空 .template，再注入已绑定本 Run 的文件和分析；无模板同样重置，防止跨轮残留。
- 生成器保留模板 masters/theme/layouts，移除示例 slides；优先按 placeholder 匹配，否则主题色/字体自由布局；模板优先于品牌且不额外加品牌 logo。
- 部署需要迁移 0028 和重建 sbx-office；本分支不部署。CPU/RSS/延迟及双库 EXPLAIN 的当前证据见本轮进度记录，未测试的边界不得视为通过。

## 实现决策与边界

- `category=slides` 同时支持 `/v1/files` 与 `/v1/files/folders`；按大小写不敏感的 `.pptx/.potx` 后缀过滤，保留原分页/项目/回收站权限。0028 增加两项稀疏 slides 索引；SQLite 小样本可能选择已有 folder covering index，允许这一有索引的等价计划。
- POTX 由受限 Worker 注册其 main content type 后直接读包，不重打包，避免30MiB输入挤占16MiB子进程输出限制。Broker保留原字节，生成器在沙箱中将POTX main type规范化为PPTX。
- 旧品牌有效PPT文件转为 `purpose=template` 并由迁移 outbox 重新扫描/分析、补缩略图；迁移不解析文件。旧品牌指针仅用于降级与历史冻结Run兼容，不再是新品牌资产种类。
- **缩略图（替代被否决的 Worker Office 渲染方案）**：Worker 仅在原有限额内生成轻量草图。API 在模板 ready 的同一事务调用资料库共用的 `preview_requests.request_preview`，写入 pending manifest/job_id 和 outbox；用户锁串行化，已登记的 pending/ready/failed 预览均不因模板补丁或文件作业重投而再次排队。作业消费/claim/ACK/超时/失败沿用既有链路。
- 真实页面由原 `worker.preview → Broker LibraryRenderer → render profile sandbox` 产生，PPTX/POTX 使用同一 Office 预览（最多40页、总派生输出32MiB、120秒总wall、现有sandbox内存/租约预算）。sandbox 内从首张页面 JPEG 缩放到约480px宽，额外生成一次 `derived/{file_id}/preview/template-thumbnail.jpg`；不在 Worker/API 解码或渲染 Office。40页加一个缩略图允许最多41个内部完成资产，API仍校验文件归属、固定路径、MIME、hash及manifest一致性。
- API 仅在当前预览 ready 且缩略图完成时选择真实图，否则保留草图；模板 status 不依赖 preview。真实图 URL 追加 `?v=<缩略图sha256>`，ETag 同时变化，避免浏览器复用 immutable 草图；新草图为独立 `template-sketch-v3.jpg`，旧v1 WebP/v2 JPEG仍可读。模板 JSON 不新增字段，模板可见性及库/搜索/分享排除规则不变。
- 平台镜像不含 LibreOffice/Poppler/字体/procps；撤销仅为 Office 渲染加入的 Worker RSS watchdog/隔离豁免。新增 Office 渲染成本在 Worker/平台镜像为零；每模板一次 sandbox preview，与一次资料库预览成本相同，加一次小图缩放和JPEG存储。源文件/已生成页面不在平台进程新增完整缓冲。真实容器部署及长期容量仍由父任务后续验收，详见[跟进记录](../progress/updates/2026-10-24-ppt-template-thumbnail-rendering.md)。
- 注册模板软删除不回收原文件/派生文件；它仍可能被已冻结Run使用。个人模板的保留字节继续计入配额。单轮资料库模板沿用资料库文件现有回收/版本过期语义。
- 定时任务创建/更新显式拒绝非空 template（422）；null/缺省忽略。模板队列不允许 steer，防止将下一轮模板错误应用到当前轮。

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
- **缩略图视觉边界**：受限子进程以 Pillow 轻量绘制首张 slide（无 slide 时首个 layout）的图片、文本和形状，不引入 Worker LibreOffice；主题背景、复杂效果/图表/母版对象不保证像素级还原。它是模板选择联系图，非产物预览/视觉验收。生成文件的完整预览仍走原 Broker Office 渲染。此项属于本契约的保真度边界，需要产品/父任务确认是否接受。
- 注册模板软删除不回收原文件/派生文件；它仍可能被已冻结Run使用。个人模板的保留字节继续计入配额。单轮资料库模板沿用资料库文件现有回收/版本过期语义。
- 定时任务创建/更新显式拒绝非空 template（422）；null/缺省忽略。模板队列不允许 steer，防止将下一轮模板错误应用到当前轮。

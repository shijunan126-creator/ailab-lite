# 2026-11-08 技能查看、GenUI 导出与换行、提取文字、资料库、输入框、网页图片组件

分支 `feat/ui-polish-media`，base `origin/main@f40afdca`。用户一次提出的七项问题和一项新需求，一起修完。

## 问题与根因

| 问题 | 根因 | 处理 |
|---|---|---|
| 「新建」技能里直接带了一份填好的示例 | 新建复用了编辑器，并预填了 TEMPLATE | 改成空白表单：名称、什么时候用、怎么做。把整份 SKILL.md 粘进「怎么做」时，自动拆出名称和说明；保存时由表单生成 SKILL.md |
| 已有技能只能看到 SKILL.md，看不到目录 | 只读接口只返回 SKILL.md；API 不能解析不受信 ZIP | 新增 `GET /v1/skills/{name}/files` 和 `/files/{path}`。内置技能直接读镜像目录。ZIP 技能第一次查看时由 Worker 的 `skill_view` 任务解包一次，文件按内容哈希缓存在 `derived/{file_id}/skill/`（可回收缓存，30 天）。Portal 用只读的 `SkillViewer` 显示目录树 |
| 内置技能也有「另存为个人技能」 | 行菜单没有区分 | 内置技能行不再有菜单；广场技能保留「修改」 |
| GenUI 下载/复制为图片时，单行文字折行（“19/0”“50 G/B”） | modern-screenshot 会把每个元素的计算宽度写成固定像素；宽度带小数时，克隆里文字放不下就折行。100% 缩放下不出现，`zoom:1.1` 和 WebKit 能复现 | `holdLines`：截图前先量出所有单行文本节点，只在克隆里给它们 `nowrap`/`pre`；多行文字不受影响。高亮 EntityCard 的外框在 WebKit 导出中错位，改成内阴影 |
| 流程图文字被省略号截断 | 节点宽度固定，标签用 `fit` 截断 | 新增 `wrap`：中文逐字断行，英文和数字按词断，最多 4 行（连线标签 3 行）。节点高度和层间距跟行数走，宽度用 canvas 实测。图表坐标轴仍用 `fit`，这是常规做法 |
| 提取文字没有图标；打开后还要再点「提取」 | 弹窗先要求选页码 | 打开即识别：图片直接识别；PDF 先查页数，不超过 20 页就整份识别，更长的识别前 20 页，并给出页码框。结果直接显示，可「复制全部」「下载」；失败时「重试」。菜单加了 ScanText 图标 |
| 资料库隐藏右侧操作后，大小和时间挤在左边，右边空一列 | 操作列一直占着 132–148px | 操作列宽度为 0，悬停/聚焦/菜单打开时，操作按钮带渐变浮在大小和时间上方。触屏设备保持固定列。星标移到文件名后 |
| 空消息 Shift+Enter 再退格，回不到原来的单行，也没有「发消息」 | pre-wrap 的 contenteditable 里换行是 `\n` 文本；末尾的 `\n` 只是空末行的占位，却被读成了内容 | `readParts` 像处理末尾 `<br>` 一样丢掉根节点最后一段文本末尾的一个 `\n` |

## 新增：网页图片组件

- GenUI 新增 `Image`（单张图，可带说明和来源）、`Gallery`（2–12 张：三列，多出的显示 +N，窄屏两列；点开后可逐张浏览）、`ImageText`（图文并排：sm 用于列表条目，md 默认，lg 窄屏时图片在上）。`EntityCard` 增加 `image`。都是 catalog v2 的可选新增。
- 安全：页面若自动加载模型自己写的图片网址，就可能把对话内容编进 URL 外泄。所以模型拿不到原始图片 URL：`web_search`（新增 `kind: "images"`）和 `read_url`（新增 `images`）只返回 `/v1/web-images/<编码网址>.<HMAC>`。API 验签后，用公网专用客户端取图（逐跳检查地址、不带 cookie、10 秒、8 MiB），只按文件头认 jpeg/png/gif/webp/avif。取到的图存入派生缓存（与预览、OCR 共用预算，30 天，空间不够时先清最早的），以 immutable/ETag 返回。Portal 只加载匹配签名格式的 src，其余显示占位、不发请求。
- 配置：新增 `AILAB_WEB_IMAGE_KEY`。secure 档在开启联网检索时，如果它不足 32 字符就拒绝启动；dev/test 用公开的固定 key。`scripts/k8s_secrets.sh` 会创建 `platform-web-image-key`。
- 请求前缀：工具 schema 只在描述里固定地多了一个枚举，同一 URL 签出的 token 不变，不按轮或按运行变化。GenUI 索引多了一行，Chat 系统提示（延迟加载模式）为 2000 token，正好在预算上限。

## 验证（代码基线：本分支工作树，base `f40afdca`）

- Ruff check/format 通过；Portal `tsc` 无错误，`npm test` 45 个文件 / 266 项通过，`npm run build` 通过。
- 新单测（各项在改动前都会失败）：
  - Portal `tests/ui-polish.test.tsx`：wrap、readParts、技能表单 SKILL.md 往返、签名路径过滤、Gallery +N 与大图浏览、模型自写网址不加载、复制文本。
  - `tests/text-extraction.test.tsx`：打开即识别、长 PDF 分批、重试、页码解析。
  - Python `tests/test_skill_view.py`（5 项）、`tests/test_web_images.py`（17 项）。
- 浏览器：
  - 导出折行：用 Chromium `zoom:1.1` 和 WebKit 复现，并验证已修复。两个示例对话（KV 缓存流程、系统架构流程）的流程图换行已截图核对。
  - 输入框：Chromium 和 WebKit 中都恢复为 21px 单行，并显示占位文字。
  - 图片组件：在 760px 深/浅色、375px 宽度和大图浏览中核对过。
  - 技能页、资料库、提取文字、输入框：在生产构建 + 真实服务栈上用临时 Playwright 脚本截图，1280/390 宽 × 深/浅色，4 组全部通过；脚本用后已删除。截图发现并修复了三个问题：
    - 资料库操作按钮用 `pointer-events: none` 隐藏，点击会落到日期单元格上，E2E 因此失败；
    - 新建技能的输入框没写 `type="text"`，拿不到全局样式；
    - 技能查看器的长行被截断，文件夹默认全展开。
- E2E（Redis 16379）：涉及的 8 个文件全部通过，包括 `test_memory_skills`、`test_platform_skills_ui`、`test_ocr_request_ui`、`test_file_context`、`test_library_ui`、`test_genui`、`test_skill_admin`。完整 E2E 交给 CI。
- Python：PostgreSQL 16 + Redis 全套 2791 通过 / 70 跳过；SQLite 全套在本轮早些时候跑过，没有失败（汇总行没留下）。

## 没覆盖到 / 风险

- 没有用真实模型测图片搜索：token 是整段 base64 网址，模型要原样照抄 100–400 字符，可能抄错（抄错只会显示占位，不会外泄）。若真实使用中常见，改为由服务端保存的短 ID。
- 大图 `img_src` 有时超过 8 MiB 或被站点拒绝，查看时不会自动改用缩略图。
- 网页图片与文档预览、OCR 共用派生缓存预算，图片多时会先挤掉预览。
- 渲染浏览器路径（crawler）的 `read_url` 不返回图片。
- 配置真实 key 后，用开发 key 签出的旧 token 会失效。

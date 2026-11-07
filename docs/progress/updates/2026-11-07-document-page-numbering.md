# 文件输入：view_document 每种格式只有一套页码

用户在 PR #161 上问：调用中途或下一轮打开预览，浏览器截图和服务端渲染会不会页码不一致。复核后用户同意按"每种格式一套编号"修复，CI 暂不处理。分支 `feat/document-input-lifecycle`，基线 `ea7cd684`。

## 结论与修复

- **同一次调用内**本来就不会混用：来源在请求时由 `representation_revision` 绑定，预览中途打开、关闭或切换主题都会让整次调用回到服务端，并删掉已截的浏览器页。
- **DOCX 跨轮会错位**：Word 没有固定分页，浏览器排版引擎和 LibreOffice 各自分页；`page_count` 又总取服务端的页数。改为 Word 页图只走服务端（`document_visual.browser_capable`：仅 PDF/PPTX 用浏览器截图）。请求时也不再按预览状态分出不同的 operation 身份。
- **PPTX 有隐藏页时错位**：LibreOffice 导出 PDF 默认跳过隐藏幻灯片，浏览器预览和幻灯片文字按幻灯片序号计数（含隐藏页）。现在冷启动 `soffice --convert-to` 和常驻 listener（`uno_convert.py`，`FilterData` 需 `uno.invoke` 保持类型）都打开了 `ExportHiddenSlides`，第 N 页始终对应第 N 张幻灯片。资料库预览和 Work 产物预览走同一个渲染器，也会显示隐藏页。
- **Excel 不受影响**：只走服务端，工作表到页码的映射来自同一次渲染。

## PR CI 的两个根因（用户随后要求合并并部署）

- **e2e 卡在 `npm ci`**：本 PR 把 `jszip` 加为 Portal 直接依赖，`package.json` 和锁文件根条目写了 `3.10.1`，但锁文件实际解析的是 main 已有的传递依赖 `3.10.2`，两边不一致。改为直接依赖 `3.10.2`（本地 Portal 测试和 build 用的就是它，pptx-renderer 要求 `^3.10.1`，只保留一份）；`npm install --package-lock-only` 确认锁文件已同步。
- **python (postgres) 偶发 501**：本 PR 让服务关闭时 `close_storage()` 清空 `get_storage` 的 `lru_cache`。下一次启动时，请求线程和后台循环同时首次取 storage，`lru_cache` 不保证创建是原子的，竞争中输的一方会拿到一个不入缓存、也不会被关闭的第二个实例；测试给这个实例打了补丁，路由用的却是缓存里的那个。改成加锁的单例，`close_storage` 在锁内取走实例再关闭。新回归 `test_concurrent_first_use_after_close_shares_one_storage` 在旧实现上失败。

- **推送后 e2e 仍失败（Portal 单测依赖时钟）**：`office-client-calls` 的 js 调用测试断言 `timeout_ms` 恰好是 100，但实现会扣掉认领往返耗时，只把截止前剩余的预算交给引擎，慢的 CI 机器上得到 99。实现是对的，测试固定 `Date.now` 后确定通过；e2e 的其他报错都是同一个会话夹具在 Portal 单测失败后 setup 出错。

## 验证（基线 `ea7cd684` 加本轮改动）

- 真实 LibreOffice 26.8（本机 Homebrew）：3 张幻灯片、第 2 张隐藏，默认导出 2 页，新参数导出 3 页。sandbox 镜像是 trixie 自带的 LibreOffice，JSON 过滤参数需要 ≥7.4，满足。
- listener 路径：本机 LibreOffice 自带的 Python 会被 macOS 杀掉（137），所以合并后在 `main@2bf86144` 构建的 sbx-office 镜像里按 Broker 预热方式起 listener 实测（无网络）：同一份 3 页含 1 张隐藏页的 PPT，旧 `uno_convert.py` 导出 2 页，新代码 3 页；`render_preview.soffice` 在 `AILAB_LO_LISTENER=1 AILAB_LO_STRICT=1` 下也是 3 页。真实 Docker 沙箱默认走这条路径，严格模式下不会退回冷启动。
- 新回归：`test_only_intrinsic_page_numbers_use_the_open_preview`（pptx 走浏览器；docx/xlsx 即使开着预览也走服务端，且不登记浏览器绑定）；冷启动和 listener 两条转换路径的隐藏页参数。
- Ruff check/format 通过。PostgreSQL 16 + Redis 完整套件 2739 通过 / 69 跳过 / 0 失败。
- E2E（含 Portal 测试和 build）193 通过 / 15 跳过 / 1 失败：`test_chat_upload_native_select_quote_send_and_reveal[docx-对话]` 断言 Chat 看 Word 页会走浏览器截图，这正是本轮去掉的行为；期望改为只有 PDF/PPTX 走浏览器后，该用例 8 个参数全部重跑通过。opt-in 的迟到截图验收（需要真实 Docker）改用 PDF 作浏览器截图对象，本机没有 Docker，没有跑。

## 没覆盖到 / 风险

- **旧缓存不会自动重渲**（合并后只读核查本机测试库：18 个 PPTX、114 张幻灯片，没有隐藏页，所以现有缓存不受影响）：`RenderedPage` 的版本仍是 `poppler-1280-v1`，文件元数据里的预览也没有渲染器版本。已经渲染过、并且含隐藏页的 PPTX 会继续用旧页图（少了隐藏页），直到文件新版本或缓存被淘汰。PR 尚未合并，本机是可重建测试环境，所以这次没有引入渲染版本迁移；如果要上生产，需要先补上按格式划分的渲染版本。
- Portal 浏览器路径没有实测。

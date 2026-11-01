# GenUI 存图、表格复制（2026-11-01）

分支 `feat/genui-image`，base `origin/main@4104d5a`。只改 Portal，无后端、无迁移。

## 需求与设计

- 用户：GenUI 生成的界面与图表要能复制为图片或下载为图片；入口自然隐藏、不打扰、按钮少。追加：表格复制为表格（粘到 Excel/文档是表格，粘到纯文本/Markdown 编辑器是 Markdown），Markdown 表格同样；每个代码块可单独复制。
- 对象各管各的复制，最内层优先：
  - **GenUI 块**：右上角悬停浮出两个图标（复制为图片、下载图片）。角落是卡片/指标块时内缩 8px 浮在框内，否则贴齐块角落。触屏轻触块显示，点块外隐藏；键盘可达。
  - **表格**（Markdown 表格、GenUI `Table` 与 `Compare`）：悬停浮出一个“复制表格”。剪贴板同时写 `text/html`（带边框的表格）和 `text/plain`（Markdown；Markdown 表格复制原文，数字列 `---:`）。
  - **代码块**：已有的每块“复制代码”保留（Markdown 代码块、GenUI `Code`/`Diff`/`JsonView`）。
  - 只有表格、文字、代码、控件的块不给图片操作；HtmlArtifact（独立 frame，页面画不到）也不给。指针在块内的表格或代码上时，块的图片操作隐藏，只显示该对象自己的复制。
- 图片：按页面所见（主题、当前 Tab、滑块、表格滚动位置），四周留 20px 页面底色；倍率 max(2, DPR) 封顶 3，并按 16M 像素/16000 边长限制降倍。文件名取块内第一个标题。
- 非安全上下文（局域网 http）没有图片剪贴板：只显示下载；文字/表格复制走 copy 事件同时写两种格式。

## 成本

- 依赖 `modern-screenshot@4.7.0`（ADR 0027），首次点击才加载，约 21 KB gzip，不在对话页入口 chunk 内（已查 client-reference-manifest）。
- 画图在用户浏览器内完成，无服务端 CPU/内存/磁盘；实测 Chromium 一个“标题 + 三指标 + 折线 + 7 列表格”块 0.3–0.5 s。

## 本轮检查（Claude，代码基线为本分支工作区）

- Codex（基线 `3881e14` + 本轮 fixture/E2E）：独立 Python 3.12 `.venv`；`ruff check` + `ruff format --check`（`ailab_platform/llm.py e2e/test_genui.py`）通过；`pytest tests -q -k "genui or llm"` 84 通过；`AILAB_E2E=1 .venv/bin/pytest e2e/test_genui.py -q` 首轮 7 通过、1 失败（测试误用原始数值 1280，实际按可见格式复制 1,280），修正断言后仅重跑该用例 1 通过；`cd portal && npm test` 128 通过。未改 Portal 产品代码，未跑全套 Python/E2E；新用例验证真实 hover、PNG 下载/剪贴板及 HTML/Markdown/代码复制。
- `npm test`（含新增 `tests/genui-image.test.tsx` 9 项）与 `npm run build` 通过。
- 生产构建 + Playwright（Chromium），临时展示页已删除：浅/深色悬停位置、复制图片读回 PNG、下载文件名、块内表格悬停只显示表格复制、Markdown/GenUI/Compare 表格剪贴板读回 HTML + Markdown、代码复制、图片中不含任何操作按钮；手机 390 宽触屏：点表格只显示表格复制、点指标只显示块操作、点外部全部隐藏；无页面错误。

## 合并与部署

- PR #135 CI 9 项（含 E2E、SQLite/PG 全套）通过，squash 合并为 `main@8890338`。
- 部署完整 `main@8890338` 到 AILab-local：后端与 Portal 镜像 `release-8890338`，迁移 0043→0045，服务均 healthy，Portal 200；按约定未做部署后验收。

## 未测边界

- Safari/Firefox 真机复制图片（依赖 ClipboardItem Promise）、iOS 下载行为；Excel/Word 实际粘贴只验证了剪贴板内容，未在 Office 里粘贴。
- GenUI 表格超过显示上限的行不会被复制（复制可见内容）。

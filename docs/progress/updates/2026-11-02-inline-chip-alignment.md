# 行内气泡对齐

- 范围：用户截图中的来源气泡偏高，并追加要求检查、一起修复同类位置；已本地实施/验证；用户随后授权PR与合并，不部署。
- 基线：`fix/source-pill-alignment`，HEAD `c033664c4d9d5837283193f44fbd9ce2ede306e9` 加本轮3个样式文件工作区差异。进入时已有文档修改与本地文件保留。
- 结论：来源气泡的裁切行内块使用底边基线，正像素对齐继续抬高；网页来源、文件引用、普通网址、输入技能标签、对比表标题徽章统一为 `vertical-align: middle`，尺寸和截断规则不变。数字引用保持角标。仅CSS，无新增请求、脚本、依赖或后台成本。
- 检查：最终 `npm test` 138 passed；`npm run build` 通过；`ruff check .` / `ruff format --check .` 通过；`git diff --check` 通过。`PATH=<GNU coreutils/findutils> python -m pytest tests -q` 全套1985 passed / 164 skipped，exit 0（复用用户cache中的Python3.12 venv）；计数取pytest进度字符，项目默认-q与命令-q叠加不输出最终计数。警告为httpx弃用与SQLite表达式索引反射边界。
- 浏览器：Playwright Chromium 加载真实样式的隔离行内夹具，1280/375宽、浅/深色四种组合；五类对齐样式、长来源截断、无水平溢出通过，已人工查看桌面浅色/手机深色截图。未跑完整会话交互、Safari/Firefox、真实模型或双库/E2E；截图与运行日志仅存仓库外临时目录。
- 发布：[PR #147](https://github.com/shijunan126-creator/ailab-lite/pull/147) 已提交；下一步全部CI通过后合并，未授权部署。

- 发布整合：分支 `fix/inline-chip-alignment-pr` 从 `origin/main@188afca` 建立，仅带本轮5处CSS及相关交接；原工作树已有部署文档和本地文件保留，不混入PR。产品差异与已测工作区一致，不重复本地全套，最终由PR CI验证。

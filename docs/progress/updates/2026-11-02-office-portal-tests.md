# Office Portal 测试（2026-11-02）

基线 `feat/office-suite-portal@7d226ab`，共同 base `origin/main@bf0af4f`。范围仅 Portal 测试与真实前端缺陷修复，无 push/PR/部署。接口依据 [04 合同](../../architecture/design/office-suite/04-viewer-and-file-contract.md#前端实现带来的修订2026-11-02以此为准)。

## 自动检查

新增 Vitest 覆盖 channel/source/nonce/长度，load_document 路径围栏、字节 token 单次消费及 transfer、文件/工作区引用与 ask、未知调用、启动失败回退；索引 pending 轮询与取消、native/大小/失败回退；引用最大8条/去重/按 key 清理；outbox 可选 doc_refs；截图按本页 view 认领、超限缩窄、关闭错误及原 JS 路径；标准字体 url 部分替换。

首轮 `npm test` 34 文件、159 passed。新增测试的 TypeScript 导入/参数类型问题已修正，相应3文件18项重跑通过；`npx tsc --noEmit` 与带 `OFFICE_RUNTIME_SRC` 的 `npm run build` 通过。后续截图回归发现256 KiB控制消息上限丢弃合法图片响应，独立缺陷修复及最终检查待本轮收尾补齐。

## 浏览器与边界

使用未提交 `/office-test` fetch shim 与 runtime 四个 fixture；真实鼠标选区用 Playwright Chromium `page.mouse`（按 frame 元素 boundingBox 的页面坐标）。手工页须提供 DOCX blocks 并使用 fixture 真正的 Revenue sheet；这些只修正手工页，不进入产品提交。最终矩阵、交互证据、清理与未测边界待收尾记录。原始日志和截图均在 `/tmp`，不入库。

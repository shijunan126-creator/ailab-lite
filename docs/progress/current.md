# 文件输入：提示词与调用逻辑终审修复完成（2026-11-07）

- 最新授权：PR前复核Chat/Work提示词与调用逻辑，做全部修复并可用真实模型测试；仅本地实现、验证与提交，未push/PR/合并/部署。分支`feat/document-input-lifecycle`，基线`7d446f9c`，本地base `origin/main@fdc0a05f`，本轮未fetch。上一轮真实模型复核见[workflow记录](updates/2026-11-07-real-model-document-workflows.md)。
- 本轮修复：V2指南只写本格式事实、去掉先目录/重复workflow/测试语料措辞；view结果返回可回填的`region_ref`；Agent本地字段级参数反馈；Work copy路径反馈；多页OCR每图每派发独立执行身份；`file_work.finish`按本operation job kind围栏（修复47d9c757回归）。根因与对比见[终审修复](updates/2026-11-07-document-prompt-review.md)。
- 验证：Ruff通过；完整SQLite Python套件退出码0（OCR身份最终修复前），修复后OCR/文档相关组122通过；真实DeepSeek Flash 30轮/134次调用，`navigation_only`先行调用归零，PPT/Work Word/两页OCR 0工具失败，追问暖缓存96.97%–99.16%。未跑PostgreSQL/Redis双库、E2E、Portal build/浏览器。
- 剩余边界：小PDF视觉unknown时模型可能主动看页；Excel工作表→页映射未知；回答仍可能偏长或外推；未做多模型/真实业务文档评估。
- 下一步：用户决定是否提PR；PR正文按AGENTS §11引用两份dated update并保留上述边界。CI需在最终head跑双库/E2E。本轮独立VM与临时产物已清理。

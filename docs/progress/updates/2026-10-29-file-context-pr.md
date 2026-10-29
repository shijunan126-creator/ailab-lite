# 文件上下文改造提交与合并

用户授权本批push、PR和merge，覆盖旧记录中的历史限制。分支`feat/file-context-office-runtime`，起始HEAD/base及当前主线均为`e71a60f8b4e0ea139b12be80d14cb38a627a2db7`。本轮不切换/覆盖主目录，不发布前端HLD，不移动旧Memory原始日志或个人部署文件。

PR包含文件/附件身份与生命周期、固定ZCode Office reader和内部skills、Chat/Work准入与按需读取、RapidOCR独立执行与计量、增量摘要来源及85%动态窗口策略、附件标签预览/网页恢复和fork交叉链路修复。原始解析性能对照及实际模型/CPU OCR证据与未验NPU/容量边界随设计和验收文档提交。

准备过程中补齐干净CI环境的Office runner构建：Python双库job固定Node24.11.1并运行上游15项，浏览器job构建相同reader；不提交生成的runner.mjs或node_modules。Docker context排除本机.local/.worktrees，PDF fixture按binary保留原始字节。HANDOFF标题层级与证据尾部空白已整理，未改第三方原始源码。

本轮新检查：上游15项、CI脚本28项、Ruff427文件、服务导入与skills manifest一致性通过。此前集中267核心与重叠57最终回归、7真实浏览器和Portal42/build作为历史证据；PR的CI将针对提交后源运行完整门禁。真实NPU、500轮10代/满窗质量及数百人吞吐依然未验。

后续PR地址、检查与合并状态由current记录，远端门禁未完成前不宣称已合并。本机已有体验版不自动重建成合并版本。

# 当前工作

## 办公内容、OCR与等待可靠性（2026-10-29）

用户已授权修复并验证，未授权部署/push/PR/merge。独立工作树`.worktrees/document-reading-reliability`、分支`fix/document-reading-reliability`，基于本地origin/main@202e48a（包含已合入的#106），保留主目录、原文件方案工作树与全部已有修改；未fetch。

已集中修复原生占位/OCR读取选择、缓存及在途复用、持久等待/恢复/取消/准入、四格式视觉覆盖与Chat/Work输入边界、含图Excel兼容，并补自动等待的Portal/SSE合同。最终SQLite1502/103跳过、PG1550/55跳过、真实浏览器128、Portal45/build、Office15、真实离线四格式渲染与两并发系统reader、原指定PDF真实CPU OCR、Ruff425/导入均通过。唯一结果与未测边界见[本轮记录](updates/2026-10-29-document-reading-reliability.md)，决定见[ADR 0026](../architecture/adr/0026-document-visual-coverage-and-file-waits.md)。下一步：用户审阅本地结果后决定是否发布；不自动部署/push/PR/merge。验证临时资源已清理，现有个人部署未更新。前端视觉重构HLD仍暂停，本轮只修文件处理状态。

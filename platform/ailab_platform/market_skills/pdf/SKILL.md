---
name: pdf
title: PDF 工具
description: 处理 PDF：合并、拆分、提取页面文本；适合整理扫描件、会议材料和多份附件
---

# 什么时候用

用户要合并多份 PDF、拆出某几页、读取 PDF 正文，或整理会议/报告附件时。

# 工作流程

1. 确认输入文件都在 `/workspace/inputs/` 或 `/workspace/downloads/`，不要编造路径。
2. 按任务运行：

```bash
# 合并
python /workspace/.ailab/skills/pdf/scripts/pdf_tool.py merge \
  --inputs /workspace/inputs/a.pdf /workspace/inputs/b.pdf \
  --output /workspace/outputs/merged.pdf

# 拆分：按页输出多个文件，或用 --pages 选页
python /workspace/.ailab/skills/pdf/scripts/pdf_tool.py split \
  --input /workspace/inputs/a.pdf --output-dir /workspace/outputs/pages

# 提取文本
python /workspace/.ailab/skills/pdf/scripts/pdf_tool.py extract \
  --input /workspace/inputs/a.pdf --output /workspace/outputs/a.txt
```

3. 用 `deliver_file` 交付结果；如果只提取文本，直接把关键内容总结给用户，并附上文本文件。

# 规则

- 不修改原文件，输出写到 `/workspace/outputs/`；
- 保留页码信息，引用时写清第几页；
- 扫描件没有文本层时，先说明限制，不要编造内容；
- 加密 PDF 若打不开，告诉用户需要密码，不要尝试破解；
- 拆分文件名用有意义的页码/标题，不要覆盖已有输出。

# 检查

- 输出页数符合预期；
- 合并顺序与用户要求一致；
- 文本提取没有明显乱码；
- 交付前确认文件存在。

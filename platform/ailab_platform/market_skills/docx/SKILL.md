---
name: docx
title: Word 文档
description: 生成排版规范的 Word 文档（.docx）：封面、标题层级、正文、要点、表格、图片和分页；支持按 JSON 规格稳定生成
---

# 什么时候用

用户要写报告、方案、通知、合同之外的正式文档，或要把已有内容整理成 Word 时。

# 工作流程

1. 先确认：文档标题、给谁看、篇幅、是否需要封面/目录、有没有模板或必须包含的章节。
2. 把结构写到 `/workspace/work/doc.json`，不要直接把正文塞进脚本参数。
3. 运行：

```bash
python /workspace/.ailab/skills/docx/scripts/build_docx.py /workspace/work/doc.json /workspace/outputs/<文件名>.docx
```

4. 用 `deliver_file` 交付，再告诉用户文档结构；需要改时修改 JSON 后重新生成，不要从头写脚本。

# 规格格式

```json
{
  "title": "文档标题",
  "subtitle": "副标题（可选）",
  "author": "作者（可选）",
  "date": "2026 年 10 月（可选）",
  "sections": [
    {
      "heading": "一、背景",
      "level": 1,
      "paragraphs": ["第一段。", "第二段。"],
      "bullets": ["要点一", "要点二"],
      "table": {"columns": ["项目", "说明"], "rows": [["A", "..."], ["B", "..."]]},
      "image": "/workspace/assets/example.png",
      "caption": "图 1 说明（可选）",
      "page_break_before": false
    }
  ]
}
```

- `level`：1/2/3，默认 1；
- `paragraphs` 和 `bullets` 可同时存在；
- `table` 的 `rows` 每行长度要与 `columns` 一致；
- `image` 用实际存在的路径；没有图片不要编造；
- 需要新起一页时设置 `page_break_before: true`。

# 写作要求

- 标题写结论，不要写“相关情况”；
- 每段只讲一件事；
- 数字、时间、负责人要具体；
- 不编造事实、数据或引用；
- 正式文档少用表情符号和网络用语。

# 检查

- 输出文件存在且能被 Word/WPS 打开；
- 表格没有错列；
- 图片没有超出页面；
- 目录/页码只在用户明确要求时添加。

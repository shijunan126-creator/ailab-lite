"""Safe workspace-view failure vocabulary; never forward renderer diagnostics."""

VIEW_ERRORS = {
    "invalid_workspace_path": "路径必须是工作区内的普通文件，不能使用符号链接或内部目录",
    "invalid_page_selection": "请提供一至四个不重复的正整数页码",
    "page_out_of_range": "请求页码超出文件总页数，请先确认页数再选择",
    "unsupported_file_type": "仅支持 PNG、JPEG、PDF、DOCX、PPTX、XLSX",
    "input_limit": "文件超过 20 MiB 或不是普通文件",
    "archive_limit": "文档解压大小或压缩比超过安全限制",
    "pixel_limit": "图片超过 1600 万像素限制",
    "image_size_limit": "单页图片超过 512 KiB，请降低页面复杂度",
    "output_limit": "本批图片超过 1200 KiB，请减少查看页数",
    "file_not_found": "工作文件不存在，请确认文件已生成",
    "view_timeout": "渲染超过时间限制，请减少页数或降低文档复杂度",
    "conversion_failed": "文档转换失败，请检查文件是否损坏或重新生成",
    "invalid_view_output": "渲染结果不完整或格式无效，请重新生成文件后重试",
    "view_failed": "渲染失败，请检查文件格式或重新生成",
    "view_unavailable": "沙箱或文件传输暂不可用，请稍后重试",
}

"""Official skill marketplace: a small, curated catalog shipped with the platform.

Phase 1 is intentionally read-only: the catalog is code, not user content. Installing copies the package into the
user's personal skill library, where the normal Worker scan, review policy, versioning and deletion rules apply.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

MARKET_ROOT = Path(__file__).with_name("market_skills")

CATALOG: tuple[dict, ...] = (
    {
        "slug": "pptx",
        "name": "pptx",
        "title": "演示文稿（PPT）",
        "description": "做演示文稿 / PPT / 汇报材料（.pptx）：多种版式、配色主题、图标、原生图表、配图；支持多轮修改。",
        "category": "演示文稿",
        "version": "1.0.0",
        "license": "NOASSERTION",
        "author": "AILab",
        "builtin": True,
        "requires": ["python-pptx", "matplotlib", "pandas", "libreoffice"],
    },
    {
        "slug": "xlsx",
        "name": "xlsx",
        "title": "表格（Excel）",
        "description": "做 Excel 表格 / 报表 / 数据分析（.xlsx）：表头样式、数字格式、公式、合计行、原生图表；支持多轮修改。",
        "category": "表格与数据",
        "version": "1.0.0",
        "license": "NOASSERTION",
        "author": "AILab",
        "builtin": True,
        "requires": ["openpyxl", "pandas", "libreoffice"],
    },
    {
        "slug": "docx",
        "name": "docx",
        "title": "Word 文档",
        "description": "生成排版规范的 Word 文档（.docx）：封面、标题层级、正文、要点、表格、图片和分页；支持按 JSON 规格稳定生成。",
        "category": "文档",
        "version": "1.0.0",
        "license": "NOASSERTION",
        "author": "AILab",
        "builtin": False,
        "requires": ["python-docx"],
    },
    {
        "slug": "pdf",
        "name": "pdf",
        "title": "PDF 工具",
        "description": "处理 PDF：合并、拆分、提取页面文本；适合整理扫描件、会议材料和多份附件。",
        "category": "PDF",
        "version": "1.0.0",
        "license": "NOASSERTION",
        "author": "AILab",
        "builtin": False,
        "requires": ["pypdf"],
    },
)


def listing() -> list[dict]:
    return [dict(item) for item in CATALOG]


def find(slug: str) -> dict | None:
    return next((dict(item) for item in CATALOG if item["slug"] == slug), None)


def package_zip(slug: str) -> bytes:
    """Build the installable ZIP from the checked-in package directory."""
    entry = find(slug)
    if entry is None:
        raise KeyError(slug)
    root = MARKET_ROOT / slug
    if not root.is_dir():
        raise FileNotFoundError(f"market package missing: {slug}")
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError(f"market package has no SKILL.md: {slug}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                archive.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()

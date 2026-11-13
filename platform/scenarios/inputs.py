"""Small Office inputs built in memory, so a scenario states its data where it asserts on it."""

from __future__ import annotations

import io

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def quarterly_report() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("2026 年第三季度经营简报", level=1)
    doc.add_paragraph("一、收入：第三季度营业收入 1,280 万元，同比增长 12%，主要来自华东区新签客户。")
    doc.add_paragraph("二、利润：第三季度净利润 37.5 万元，较上季度下降 8%，原因是一次性的服务器迁移费用 21 万元。")
    doc.add_paragraph("三、风险：两家大客户的合同将在 12 月到期，续约谈判尚未开始。")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def regional_budget() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Budget"
    sheet.append(["Region", "Revenue", "Cost"])
    sheet.append(["North", 120, 80])
    sheet.append(["South", 95, 60])
    sheet.append(["Total", "=SUM(B2:B3)", "=SUM(C2:C3)"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    cells = [cell.text for table in doc.tables for row in table.rows for cell in row.cells]
    return "\n".join([p.text for p in doc.paragraphs] + cells)


def receipt_png() -> bytes:
    """A scanned-looking receipt: its text exists only as pixels."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=44)
    for y, line in enumerate(("RECEIPT NO. 2026-0457", "DATE 2026-11-03", "TOTAL 3280.50 CNY")):
        draw.text((60, 60 + y * 90), line, fill="black", font=font)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()

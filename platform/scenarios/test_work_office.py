"""Work: the four Office skills producing and editing real files in the sandbox."""

import io

from scenarios.conftest import contracts
from scenarios.inputs import XLSX, docx_text, regional_budget


def test_word_report_from_scratch(api):
    _, turn = api.start(
        "写一份 Word 周报《第46周项目周报》：一段本周进展，一个三列表格（任务、负责人、状态）至少三行，一段下周计划。交付 docx。",
        mode="work",
    )
    contracts(turn)
    _, data = api.artifact(turn, ".docx")
    from docx import Document

    doc = Document(io.BytesIO(data))
    assert "第46周项目周报" in docx_text(data)
    shapes = [(len(table.columns), len(table.rows)) for table in doc.tables]
    assert any(columns == 3 and rows >= 4 for columns, rows in shapes), shapes


def test_spreadsheet_edit_keeps_formulas(api):
    file = api.upload("budget.xlsx", regional_budget(), XLSX)
    _, turn = api.start("把 North 的收入从 120 改成 175，其余数据和公式保持不变，交付修改后的 xlsx。", mode="work", file_ids=[file["id"]])
    contracts(turn)
    _, data = api.artifact(turn, ".xlsx")
    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(data))["Budget"]
    assert sheet["A2"].value == "North" and sheet["B2"].value == 175
    assert str(sheet["B4"].value).replace(" ", "").upper() == "=SUM(B2:B3)" and sheet["C2"].value == 80


def test_slide_deck(api):
    _, turn = api.start("做一份 4 页的 PPT，主题《城市骑行安全》：封面、两页要点、一页总结。交付 pptx。", mode="work")
    contracts(turn)
    _, data = api.artifact(turn, ".pptx")
    from pptx import Presentation

    deck = Presentation(io.BytesIO(data))
    assert len(deck.slides) >= 4
    text = "".join(shape.text_frame.text for shape in deck.slides[0].shapes if shape.has_text_frame)
    assert "骑行" in text, text


def test_chinese_pdf_report_embeds_its_fonts(api):
    _, turn = api.start("生成一页 PDF 报告《季度回顾》：标题、两段中文正文、一个三行的小表格。交付 pdf。", mode="work")
    contracts(turn)
    _, data = api.artifact(turn, ".pdf")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    assert "季度回顾" in "".join(page.extract_text() for page in reader.pages)
    for page in reader.pages:
        for font in (page.get("/Resources", {}).get("/Font") or {}).values():
            font = font.get_object()
            if font.get("/Subtype") == "/Type3":
                continue  # glyphs are drawn by procedures inside the PDF itself: embedded by definition
            descendant = font["/DescendantFonts"][0].get_object() if "/DescendantFonts" in font else font
            descriptor = descendant.get("/FontDescriptor")
            # a CJK font that is not embedded renders as boxes on any machine without it
            assert descriptor is None or any(k in descriptor.get_object() for k in ("/FontFile", "/FontFile2", "/FontFile3")), font

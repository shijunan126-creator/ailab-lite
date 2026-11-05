"""Deterministic synthetic acceptance corpus; generated binaries stay outside the repository."""

import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FIXED = datetime(2026, 1, 1, tzinfo=UTC)
IMAGE_LINES = ("SYSTEM FLOW", "Browser -> API -> Worker", "扫描页测试：独立选择文字与图像", "Invoice TOTAL: 1280.50", "Version B")


def bitmap():
    fonts = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    font_path = next((p for p in fonts if Path(p).is_file()), None)
    if not font_path:
        raise RuntimeError("Chinese acceptance font unavailable; do not silently substitute missing glyphs")
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 34)
    for i, line in enumerate(IMAGE_LINES):
        draw.text((40, 40 + i * 90), line, font=font, fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    image.close()
    return output.getvalue()


def stable_zip(data):
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as dest,
    ):
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            payload = source.read(name)
            if name == "docProps/core.xml":
                payload = re.sub(
                    rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)",
                    rb"\g<1>2026-01-01T00:00:00Z\g<2>",
                    payload,
                )
            dest.writestr(info, payload)
    return output.getvalue()


def pdf(pages, picture, *, native=True, mixed=False):
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    writer.add_metadata({"/CreationDate": "D:20260101000000Z", "/Producer": "AILab acceptance corpus v1"})
    image = Image.open(io.BytesIO(picture)).convert("RGB")
    raster = DecodedStreamObject()
    raster.set_data(image.tobytes())
    raster.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(image.width),
            NameObject("/Height"): NumberObject(image.height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image.close()
    image_ref = writer._add_object(raster.flate_encode())
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for i in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
                NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): image_ref}),
            }
        )
        content = b""
        if native:
            title = f"Page {i + 1}: {'Header only' if mixed else 'Native text and stable locator; searchable amount 1280.50.'}"
            content += f"BT /F1 16 Tf 40 740 Td ({title}) Tj ET\n".encode()
        if mixed or not native:
            content += b"q 500 0 0 300 40 350 cm /Im0 Do Q\n"
        else:
            content += b"40 600 m 540 600 l S\n"  # table border alone must not imply missing visual semantics
        stream = DecodedStreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/MediaBox")] = ArrayObject([NumberObject(0), NumberObject(0), NumberObject(612), NumberObject(792)])
    result = io.BytesIO()
    writer.write(result)
    writer.close()
    return result.getvalue()


def word(picture, *, mixed):
    from docx import Document
    from docx.shared import Inches

    doc = Document()
    doc.core_properties.created = doc.core_properties.modified = FIXED
    for i in range(12 if mixed else 20):
        doc.add_paragraph(f"Paragraph {i + 1}: Native searchable invoice 1280.50; no semantic outline is supplied.")
        if mixed:
            doc.add_picture(io.BytesIO(picture), width=Inches(5))
            if i < 11:
                doc.add_page_break()
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Region", "Revenue"
    table.cell(1, 0).text, table.cell(1, 1).text = "North", "1280.50"
    output = io.BytesIO()
    doc.save(output)
    return stable_zip(output.getvalue())


def slides(picture):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_CONNECTOR
    from pptx.util import Inches

    doc = Presentation()
    doc.core_properties.created = doc.core_properties.modified = FIXED
    for i in range(12):
        slide = doc.slides.add_slide(doc.slide_layouts[6])
        slide.shapes.add_textbox(Inches(0.2), Inches(0.1), Inches(8), Inches(0.6)).text = f"Slide {i + 1} invoice 1280.50"
        if i % 3 == 0:
            slide.shapes.add_picture(io.BytesIO(picture), Inches(0.3), Inches(1), width=Inches(7))
        if i % 3 == 1:
            slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1), Inches(2), Inches(5), Inches(3))
    output = io.BytesIO()
    doc.save(output)
    return stable_zip(output.getvalue())


def workbook(picture, *, large):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.drawing.image import Image as SheetImage

    doc = Workbook()
    doc.properties.created = doc.properties.modified = FIXED.replace(tzinfo=None)
    sheet = doc.active
    sheet.title = "Revenue"
    sheet.append(["Region", "Revenue", "Formula"])
    for i in range(1, 16667 if large else 31):
        sheet.append([f"Region {i}", i * 12.5, f"=B{i + 1}*2"])
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=12), titles_from_data=True)
    sheet.add_chart(chart, "E2")
    visual = doc.create_sheet("Scanned")
    visual.add_image(SheetImage(io.BytesIO(picture)), "A1")
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    return stable_zip(output.getvalue())


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    image = bitmap()
    data = {
        "native-small.pdf": pdf(3, image),
        "native-long-500.pdf": pdf(500, image),
        "scan-40.pdf": pdf(40, image, native=False),
        "header-scan-mixed.pdf": pdf(10, image, mixed=True),
        "no-outline.docx": word(image, mixed=False),
        "mixed-12.docx": word(image, mixed=True),
        "mixed-12.pptx": slides(image),
        "mixed.xlsx": workbook(image, large=False),
        "large-50001-cells.xlsx": workbook(image, large=True),
        "scan.png": image,
    }
    manifest = {"corpus_version": 1, "files": []}
    for name, content in data.items():
        (directory / name).write_bytes(content)
        manifest["files"].append({"name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    import sys

    generate(sys.argv[1])

"""Executed without network in the real Office image; assertions inspect content, not exit codes."""

import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from docx import Document
from openpyxl import Workbook, load_workbook
from pptx import Presentation
from pypdf import PdfReader
from reportlab.pdfgen import canvas

STARTED = time.monotonic()
FORMAT = sys.argv[1]
OUT = Path("/workspace/outputs")
INPUTS = Path("/workspace/inputs")
OUT.mkdir(exist_ok=True)
INPUTS.mkdir(exist_ok=True)
PDF = "/opt/skills/platform.pdf/scripts/pdf.py"


def run(*args):
    subprocess.run(args, check=True, timeout=120, capture_output=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


original = INPUTS / ("original." + FORMAT)
created = OUT / ("created." + FORMAT)
edited = OUT / ("edited." + FORMAT)
if FORMAT == "docx":
    # Creation follows the full skill's native editable Node route.
    run(
        "node",
        "-e",
        "const {Document,Packer,Paragraph,TextRun}=require('docx');"
        "Packer.toBuffer(new Document({sections:[{children:[new Paragraph({children:[new TextRun('Original report')]})]}]}))"
        ".then(b=>require('fs').writeFileSync('/workspace/outputs/created.docx',b));",
    )
    shutil.copyfile(created, original)
    before = digest(original)
    original.chmod(0o444)
    unpacked = OUT / "unpacked"
    with zipfile.ZipFile(original) as archive:
        archive.extractall(unpacked)
    sys.path.insert(0, "/opt/skills/platform.docx")
    from scripts.document import Document as EditDocument

    document = EditDocument(unpacked, author="AILab")
    editor = document["word/document.xml"]
    node = editor.get_node(tag="w:t", contains="Original report")
    editor.replace_node(node, "<w:t>Edited report</w:t>")
    document.save()
    shutil.make_archive(str(OUT / "edited"), "zip", unpacked)
    (OUT / "edited.zip").rename(edited)
    assert "Edited report" in "\n".join(p.text for p in Document(edited).paragraphs)
    assert "Original report" in "\n".join(p.text for p in Document(original).paragraphs)
elif FORMAT == "xlsx":
    book = Workbook()
    sheet = book.active
    sheet["A1"], sheet["A2"], sheet["A3"] = 2, 3, "=SUM(A1:A2)"
    book.save(created)
    shutil.copyfile(created, original)
    before = digest(original)
    original.chmod(0o444)
    book = load_workbook(original)
    book.active["A1"] = 7
    book.save(edited)
    run("python", "/opt/skills/platform.xlsx/xlsx.py", "recalc", str(edited))
    assert load_workbook(edited).active["A3"].value.startswith("=")
    assert load_workbook(edited, data_only=True).active["A3"].value == 10
    assert load_workbook(original).active["A1"].value == 2
    run("python", "/opt/skills/platform.xlsx/xlsx.py", "audit", str(edited))
elif FORMAT == "pptx":
    run(
        "node",
        "-e",
        "const Pptx=require('pptxgenjs');const p=new Pptx();const s=p.addSlide();"
        "s.addText('Original slide',{x:1,y:1,w:5,h:1});"
        "s.addChart(p.ChartType.bar,[{name:'Revenue',labels:['A','B'],values:[2,3]}],{x:1,y:2,w:5,h:3});"
        "p.writeFile({fileName:'/workspace/outputs/created.pptx'});",
    )
    shutil.copyfile(created, original)
    before = digest(original)
    original.chmod(0o444)
    deck = Presentation(original)
    next(s for s in deck.slides[0].shapes if s.has_text_frame).text = "Edited slide"
    deck.save(edited)
    assert any(s.has_chart for s in Presentation(edited).slides[0].shapes)
    assert any(s.text == "Edited slide" for s in Presentation(edited).slides[0].shapes if s.has_text_frame)
    assert any(s.text == "Original slide" for s in Presentation(original).slides[0].shapes if s.has_text_frame)
elif FORMAT == "pdf":
    page = canvas.Canvas(str(created))
    page.drawString(72, 720, "Original report")
    page.showPage()
    page.save()
    shutil.copyfile(created, original)
    before = digest(original)
    original.chmod(0o444)
    run("python", PDF, "pages.merge", str(original), str(original), "-o", str(edited))
    assert len(PdfReader(edited).pages) == 2
    assert "Original report" in PdfReader(edited).pages[0].extract_text()
    run("pdftoppm", "-f", "1", "-l", "1", "-scale-to", "640", "-png", str(edited), str(OUT / "converted"))
    from PIL import Image

    with Image.open(next(OUT.glob("converted*.png"))) as image:
        assert image.width > 100 and image.height > 100
else:
    raise AssertionError(FORMAT)
assert digest(original) == before
if FORMAT != "pdf":
    converted = OUT / "converted.pdf"
    run("python", PDF, "convert.office", str(edited), "-o", str(converted))
    assert len(PdfReader(converted).pages) >= 1
# The pinned parser must also read the actual generated native format.
run("office-read", str(edited), str(OUT / "readback"))
manifest = json.loads((OUT / "readback/manifest.json").read_text())
assert manifest["total_units"] > 0 and manifest["status"] == "ready"
# Execute the same bounded server-side review helper that Broker injects into a lease.
run("python", "/workspace/.ailab/review-test/view_workspace_file.py", "/workspace", str(edited), str(OUT / "review.json"), "[1]", "review")
review = json.loads((OUT / "review.json").read_text())
assert review["quality_gate"] == "pending_model_judgment"
assert review["structural"]["sha256"] == digest(edited)
assert review["page_count"] >= 1 and review["coverage"]["reviewed_pages"] == [1]
assert len(review["images"]) == 1
assert digest(original) == before
print(
    json.dumps(
        {
            "format": FORMAT,
            "original_unchanged": True,
            "created_bytes": created.stat().st_size,
            "edited_bytes": edited.stat().st_size,
            "readback_units": manifest["total_units"],
            "seconds": round(time.monotonic() - STARTED, 3),
            "cgroup_peak_bytes": int(Path("/sys/fs/cgroup/memory.peak").read_text()),
            "cpu_stat": Path("/sys/fs/cgroup/cpu.stat").read_text(),
        }
    )
)

"""Real, offline production routes. Enable with AILAB_TEST_OFFICE_IMAGE after building the image."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

IMAGE = os.environ.get("AILAB_TEST_OFFICE_IMAGE")
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def office_image():
    if not IMAGE or not shutil.which("docker"):
        pytest.skip("AILAB_TEST_OFFICE_IMAGE and Docker are required")
    if subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode:
        pytest.skip("Docker daemon unavailable")
    subprocess.run(["docker", "image", "inspect", IMAGE], check=True, capture_output=True, timeout=15)
    return IMAGE


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx", "pdf"])
def test_real_create_edit_convert_and_preserve_original(office_image, fmt):
    name = "c3-office-p2-" + uuid4().hex[:12]
    helpers = ROOT / "platform/ailab_platform/sandbox_tools"
    files = {
        name: (helpers / name).read_text()
        for name in ("view_workspace_file.py", "render_preview.py", "work_view_listener.py", "uno_convert.py", "artifact_checks.py")
    }
    bootstrap = (
        "import os\nfrom pathlib import Path\nfiles="
        + repr(files)
        + "\nbase=Path('/workspace/.ailab/review-test');base.mkdir(parents=True)\n"
        + "for name, content in files.items(): (base/name).write_text(content)\n"
        + "os.environ['AILAB_LO_RSS_LIMIT']='536870912'\n"
        + "os.environ['AILAB_LO_CLIENT']=str(base/'uno_convert.py')\n"
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--memory",
                "1536m",
                "--cpus",
                "1",
                "--pids-limit",
                "256",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-i",
                office_image,
                "python",
                "-",
                fmt,
            ],
            input=bootstrap.encode() + (ROOT / "platform/tests/fixtures/office_production.py").read_bytes(),
            capture_output=True,
            timeout=240,
        )
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors="replace")[-8000:]
        report = json.loads(result.stdout.splitlines()[-1])
        print(json.dumps(report))
        assert report["original_unchanged"] and report["readback_units"] > 0
        assert report["created_bytes"] > 100 and report["edited_bytes"] > 100
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)


def test_real_offline_html_and_latex(office_image):
    program = r"""
import json
import subprocess
import sys
import unicodedata
from pathlib import Path
from pypdf import PdfReader
out=Path('/workspace/outputs')
pdf_py='/opt/skills/platform.pdf/scripts/pdf.py'
html=out/'poster.html'
html.write_text('<html><head><meta charset="utf-8"></head><body><h1 style="font-family:Inter">Offline poster</h1>'
                '<p style="font-family:\'Noto Sans SC\'">中文正文</p></body></html>')
# The documented commands, not just the script behind them.
subprocess.run(['python',pdf_py,'convert.html',str(html),'-o',str(out/'poster.pdf')],check=True,timeout=100)
text = unicodedata.normalize('NFKC', PdfReader(out/'poster.pdf').pages[0].extract_text())
assert 'Offline poster' in text and '中文正文' in text, repr(text)
fonts = subprocess.run(['pdffonts',str(out/'poster.pdf')],capture_output=True,text=True,check=True).stdout
assert 'Inter' in fonts and 'NotoSansCJK' in fonts, fonts
blueprint={'document_meta':{'title':'Offline blueprint'},'art_direction':{'palette_mode':'minimal','background_svg':'none'},
           'pages':[{'components':[{'type':'Hero_Typography','heading':'Offline blueprint','subheading':'蓝图'}]}]}
(out/'answer.md').write_text('```json\n'+json.dumps(blueprint,ensure_ascii=False)+'\n```')
subprocess.run(['python',pdf_py,'convert.blueprint',str(out/'answer.md'),'-o',str(out/'blueprint.pdf')],check=True,timeout=100)
text = unicodedata.normalize('NFKC', PdfReader(out/'blueprint.pdf').pages[0].extract_text())
assert 'Offline blueprint' in text, repr(text)
# Every cover role resolves to a font the image ships, not a fontconfig guess.
sys.path.insert(0,'/opt/skills/platform.pdf/scripts')
import cover_render
picks = cover_render.detect_fonts()
assert not any(name.startswith('FC_') for name in picks.values()) and len(set(picks.values())) == 4, picks
tex=out/'paper.tex'
tex.write_text(r'\documentclass{article}\begin{document}Offline academic $a^2+b^2=c^2$.\end{document}')
subprocess.run(['python','/opt/skills/platform.pdf/scripts/pdf.py','convert.latex',str(tex)],check=True,timeout=150)
text = PdfReader(out/'paper.pdf').pages[0].extract_text()
assert 'Offline academic' in unicodedata.normalize('NFKC', text), repr(text)
"""
    name = "c3-office-p2-" + uuid4().hex[:12]
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--memory",
                "1536m",
                "--cpus",
                "1",
                "--pids-limit",
                "256",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-i",
                office_image,
                "python",
                "-",
            ],
            input=program.encode(),
            capture_output=True,
            timeout=280,
        )
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors="replace")[-8000:]
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)

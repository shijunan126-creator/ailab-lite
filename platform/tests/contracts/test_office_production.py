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
        assert report["original_unchanged"] and report["readback_units"] > 0
        assert report["created_bytes"] > 100 and report["edited_bytes"] > 100
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)


def test_real_offline_html_and_latex(office_image):
    program = r"""
import subprocess
from pathlib import Path
from pypdf import PdfReader
out=Path('/workspace/outputs')
html=out/'poster.html'
html.write_text('<html><body><h1>Offline poster</h1><p>Production review</p></body></html>')
subprocess.run(['node','/opt/skills/platform.pdf/scripts/html2pdf-next.js',str(html),'-o',str(out/'poster.pdf')],check=True,timeout=100)
assert 'Offline poster' in PdfReader(out/'poster.pdf').pages[0].extract_text()
tex=out/'paper.tex'
tex.write_text(r'\documentclass{article}\begin{document}Offline academic $a^2+b^2=c^2$.\end{document}')
subprocess.run(['python','/opt/skills/platform.pdf/scripts/pdf.py','convert.latex',str(tex)],check=True,timeout=150)
assert 'Offline academic' in PdfReader(out/'paper.pdf').pages[0].extract_text()
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

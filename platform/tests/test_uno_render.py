from pathlib import Path
from types import SimpleNamespace

import pytest

from ailab_platform.sandbox_tools import render_preview


@pytest.mark.parametrize("listener_fails", [False, True])
def test_listener_conversion_and_cold_fallback(tmp_path, monkeypatch, listener_fails):
    source = tmp_path / "document.docx"
    source.write_bytes(b"input")
    calls = []
    monkeypatch.setenv("AILAB_LO_LISTENER", "1")
    monkeypatch.setenv("AILAB_LO_PROFILE", "/tmp/listener-profile")

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "/usr/bin/python3" and listener_fails:
            raise render_preview.subprocess.CalledProcessError(1, command)
        target = Path(command[3]) if command[0] == "/usr/bin/python3" else Path(command[command.index("--outdir") + 1]) / "document.pdf"
        target.write_bytes(b"pdf")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(render_preview.subprocess, "run", run)
    assert render_preview.soffice(source, "pdf", tmp_path).read_bytes() == b"pdf"
    assert calls[0][1] == "/workspace/.ailab/uno_convert.py"
    assert len(calls) == (2 if listener_fails else 1)
    if listener_fails:
        assert calls[1][0] == "soffice"
        assert "/tmp/listener-profile" not in " ".join(calls[1])


def test_strict_render_does_not_hide_a_failed_listener(tmp_path, monkeypatch):
    source = tmp_path / "document.docx"
    source.write_bytes(b"input")
    monkeypatch.setenv("AILAB_LO_LISTENER", "1")
    monkeypatch.setenv("AILAB_LO_STRICT", "1")
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise render_preview.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(render_preview.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="listener failed"):
        render_preview.soffice(source, "pdf", tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize(("name", "pdf_filter"), [("deck.pptx", render_preview.PRESENTATION_PDF), ("report.docx", "pdf")])
def test_cold_conversion_keeps_hidden_slides_numbered(tmp_path, monkeypatch, name, pdf_filter):
    source = tmp_path / name
    source.write_bytes(b"input")
    monkeypatch.delenv("AILAB_LO_LISTENER", raising=False)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        (Path(command[command.index("--outdir") + 1]) / f"{source.stem}.pdf").write_bytes(b"pdf")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(render_preview.subprocess, "run", run)
    render_preview.soffice(source, "pdf", tmp_path)
    assert calls[0][calls[0].index("--convert-to") + 1] == pdf_filter


@pytest.mark.parametrize("presentation", [True, False])
def test_listener_conversion_keeps_hidden_slides_numbered(tmp_path, monkeypatch, presentation):
    import sys
    from types import ModuleType

    from ailab_platform.sandbox_tools import uno_convert

    class Any:
        def __init__(self, kind, value):
            self.kind, self.value = kind, value

    class PropertyValue:
        Name = Value = None

    stored = []

    class Document:
        def supportsService(self, name):
            return presentation and name == "com.sun.star.presentation.PresentationDocument"

        def close(self, deliver):
            pass

    class Desktop:
        def loadComponentFromURL(self, *args):
            return Document()

    class Manager:
        def createInstanceWithContext(self, name, context):
            return SimpleNamespace(
                resolve=lambda url: SimpleNamespace(ServiceManager=SimpleNamespace(createInstanceWithContext=lambda *a: Desktop()))
            )

    uno = ModuleType("uno")
    uno.Any = Any
    uno.getComponentContext = lambda: SimpleNamespace(ServiceManager=Manager())
    uno.invoke = lambda target, method, args: stored.append((method, args))
    beans = ModuleType("com.sun.star.beans")
    beans.PropertyValue = PropertyValue
    for module in ("com", "com.sun", "com.sun.star"):
        monkeypatch.setitem(sys.modules, module, ModuleType(module))
    monkeypatch.setitem(sys.modules, "com.sun.star.beans", beans)
    monkeypatch.setitem(sys.modules, "uno", uno)
    monkeypatch.setattr(sys, "argv", ["uno_convert.py", str(tmp_path / "in"), str(tmp_path / "out.pdf"), ""])
    uno_convert.main()
    [(method, (_, options))] = stored
    values = {item.Name: item.Value for item in options.value}
    assert method == "storeToURL"
    assert values["FilterName"] == ("impress_pdf_Export" if presentation else "writer_pdf_Export")
    hidden = values.get("FilterData")
    assert ([(item.Name, item.Value) for item in hidden.value] if hidden else None) == (
        [("ExportHiddenSlides", True)] if presentation else None
    )

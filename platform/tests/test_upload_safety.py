"""Safety-only inspection must stay bounded and produce no content derivatives."""

import hashlib
import struct
import zipfile

import pytest

from ailab_platform.worker.isolate import IsolationError, execute
from ailab_platform.worker.safety import directory_guard, inspect


def package(tmp_path, files, name="input.docx"):
    output = tmp_path / name
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for key, body in files.items():
            archive.writestr(key, body)
    return output


def test_real_child_reports_identity_without_extracting_content(tmp_path):
    from docx import Document

    source = tmp_path / "input.docx"
    doc = Document()
    doc.add_paragraph("正文不在安全检查期间提取")
    doc.save(source)
    result = execute("safety", source, tmp_path / "output", filename=source.name)
    assert result == {
        "kind": "docx",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "bytes": source.stat().st_size,
        "archive_checked": True,
    }
    assert sorted(p.name for p in (tmp_path / "output").iterdir()) == ["job.json", "result.json", "stderr.log"]


def test_zip_directory_count_rejected_before_full_directory_allocation(tmp_path, monkeypatch):
    source = package(tmp_path, {str(i): b"" for i in range(10001)}, "many.zip")
    monkeypatch.setattr(zipfile, "ZipFile", lambda *_: (_ for _ in ()).throw(AssertionError("ZipFile allocated too soon")))
    with pytest.raises(ValueError, match="archive_limit"):
        inspect(source, source.name)


def test_zip_expansion_is_streamed_and_counts_actual_bytes(tmp_path, monkeypatch):
    from ailab_platform.worker import safety

    source = package(tmp_path, {"file": b"A" * 4096}, "compressed.zip")
    monkeypatch.setattr(safety, "MAX_EXPANDED", 2048)
    with pytest.raises(ValueError, match="archive_limit"):
        inspect(source, source.name)


def test_ooxml_signature_and_package_entry_must_match_extension(tmp_path):
    source = package(tmp_path, {"[Content_Types].xml": b"types", "xl/workbook.xml": b"workbook"})
    with pytest.raises(ValueError, match="file_type_mismatch"):
        inspect(source, "pretend.docx")
    source.write_bytes(b"not a PDF")
    with pytest.raises(IsolationError, match="file_type_mismatch"):
        execute("safety", source, tmp_path / "output", filename="pretend.pdf")


@pytest.mark.parametrize("name", ["../private", "/absolute", "windows\\private"])
def test_unsafe_archive_paths_are_rejected_without_extracting(tmp_path, name):
    source = package(tmp_path, {name: b"no extraction"}, "unsafe.zip")
    with pytest.raises(ValueError, match="archive_unsafe_path"):
        directory_guard(source)
    assert not (tmp_path / "private").exists()


def test_fake_end_record_inside_zip_comment_is_not_trusted(tmp_path):
    source = package(tmp_path, {"file": b"data"}, "comment.zip")
    with zipfile.ZipFile(source, "a") as archive:
        archive.comment = b"comment with fake PK\x05\x06 signature and no valid record"
    assert directory_guard(source) == {"file"}


def test_duplicate_members_and_invalid_crc_fail_explicitly(tmp_path):
    source = package(tmp_path, {"file": b"data"}, "corrupt.zip")
    body = bytearray(source.read_bytes())
    central = body.index(b"PK\x01\x02")
    struct.pack_into("<L", body, central + 16, 12345)
    source.write_bytes(body)
    with pytest.raises(zipfile.BadZipFile):
        inspect(source, source.name)
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("file", "one")
        with pytest.warns(UserWarning, match="Duplicate"):
            archive.writestr("file", "two")
    with pytest.raises(ValueError, match="archive_unsafe_path"):
        directory_guard(source)


def test_safety_only_upload_has_no_parser_prewarm_or_derivative_writes(tmp_path):
    from ailab_platform.storage import LocalStorage
    from ailab_platform.worker.pipeline import process_file

    payload = b"plain input, never extract during upload"
    updates = []

    class Platform:
        def get_file(self, *args, **kwargs):
            return {
                "status": "pending",
                "filename": "input.txt",
                "content_type": "text/plain",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "purpose": "library",
                "meta": {"input_lifecycle": 2},
            }

        def download_file(self, fid, target, **kwargs):
            target.write_bytes(payload)

        def patch_file(self, fid, **kwargs):
            updates.append(kwargs)

        def prewarm_render(self):
            raise AssertionError("ordinary upload must not prewarm")

        def reuse_document(self, *args):
            raise AssertionError("ordinary upload must not copy derived data")

    storage = LocalStorage(tmp_path / "objects")
    result = process_file("file", Platform(), storage)
    assert result["safety_ready"] is True
    assert len(updates) == 1 and updates[0]["status"] == "ready"
    meta = updates[0]["meta"]
    assert meta["scan"]["verdict"] == "clean"
    assert set(meta["representations"]) == {"native_text", "page_images", "ocr_text"}
    assert all(x["status"] == "absent" for x in meta["representations"].values())
    assert not (tmp_path / "objects/derived").exists()
    storage.close()


@pytest.mark.parametrize("mismatch", ["digest", "size"])
def test_safety_ready_requires_exact_uploaded_identity(tmp_path, mismatch):
    from ailab_platform.storage import LocalStorage
    from ailab_platform.worker.pipeline import process_file

    payload = b"safe text"
    updates = []

    class Platform:
        def get_file(self, *args, **kwargs):
            return {
                "status": "pending",
                "filename": "identity.txt",
                "size": len(payload) + (mismatch == "size"),
                "sha256": "wrong" if mismatch == "digest" else hashlib.sha256(payload).hexdigest(),
                "meta": {"input_lifecycle": 2},
            }

        def download_file(self, fid, target, **kwargs):
            target.write_bytes(payload)

        def patch_file(self, fid, **kwargs):
            updates.append(kwargs)

    storage = LocalStorage(tmp_path / "store")
    assert process_file("file", Platform(), storage)["rejected"] == "file_integrity_mismatch"
    assert updates[-1]["status"] == "rejected"
    storage.close()


def test_plain_native_rejects_over_limit_and_keeps_unicode_byte_ranges(tmp_path):
    from ailab_platform.native_text import read_unit
    from ailab_platform.storage import LocalStorage
    from ailab_platform.worker.plain_text import prepare

    source, output = tmp_path / "source.txt", tmp_path / "output"
    output.mkdir()
    body = "测试范围与分页\n" * 2000
    source.write_bytes(body.encode("utf-16"))
    manifest = prepare(source, output)
    store = LocalStorage(output)
    assert "".join(read_unit(store, "text.txt", unit, {}).decode() for unit in manifest["units"]) == body
    store.close()
    source.write_text("x" * 200001)
    (output / "manifest.json").unlink()
    with pytest.raises(ValueError, match="document_text_limit"):
        prepare(source, output)
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize(
    "filename,actual,mime,expected",
    [
        ("download", "pdf", "application/octet-stream", "pdf"),
        ("pretend.txt", "png", "text/plain", None),
        ("note.txt", "binary", "text/plain", "text"),
    ],
)
def test_native_kind_uses_verified_type_without_treating_an_image_as_text(filename, actual, mime, expected):
    from ailab_platform.api.native_preparation import native_kind
    from ailab_platform.models import FileObject

    file = FileObject(filename=filename, content_type=mime, meta={"safety": {"kind": actual}})
    assert native_kind(file) == expected

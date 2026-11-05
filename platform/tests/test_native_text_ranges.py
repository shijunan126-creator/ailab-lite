"""Canonical native text has one asset, exact UTF-8 ranges and independent legacy compatibility."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ailab_platform.native_text import NativeCacheError, read_unit
from ailab_platform.storage import LocalStorage


def test_canonical_utf8_ranges_reuse_one_buffer_and_verify_each_digest(tmp_path):
    store = LocalStorage(tmp_path)
    text = "页眉\n\n".encode() + "扫描文字与原生文字独立".encode()
    store.put_bytes("text.txt", text)
    units = [
        {"byte_offset": 0, "byte_size": len("页眉".encode()), "sha256": hashlib.sha256("页眉".encode()).hexdigest()},
        {
            "byte_offset": len("页眉\n\n".encode()),
            "byte_size": len("扫描文字与原生文字独立".encode()),
            "sha256": hashlib.sha256("扫描文字与原生文字独立".encode()).hexdigest(),
        },
    ]
    cache = {}
    assert read_unit(store, "text.txt", units[0], cache).decode() == "页眉"
    assert read_unit(store, "text.txt", units[1], cache).decode() == "扫描文字与原生文字独立"
    assert len(cache) == 1
    for change in [{"byte_offset": -1}, {"byte_size": 2000}, {"sha256": "0" * 64}, {"byte_offset": True}]:
        with pytest.raises(NativeCacheError):
            read_unit(store, "text.txt", {**units[0], **change}, cache)
    store.close()


def test_legacy_blocks_and_oversized_legacy_source_fail_explicitly(tmp_path):
    store = LocalStorage(tmp_path)
    store.put_bytes("000000.txt", b"legacy")
    assert read_unit(store, "000000.txt", {"digest": hashlib.sha256(b"legacy").hexdigest()}) == b"legacy"
    store.put_bytes("large.txt", b"x" * 100001)
    with pytest.raises(NativeCacheError, match="document_text_limit"):
        read_unit(store, "large.txt", {"digest": "0" * 64})
    store.close()


def runner():
    value = Path(__file__).resolve().parents[2] / "vendor/office-suite/runtime/runner.mjs"
    if not shutil.which("node") or not value.is_file():
        pytest.skip("built Office runtime and Node required")
    return value


def pdf_bytes(pages=3):
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
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
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 40 700 Td (Page {i + 1}: 1280.50) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


def parse(tmp_path, data, suffix, profile="chat"):
    source, output = tmp_path / ("source." + suffix), tmp_path / profile
    source.write_bytes(data)
    result = subprocess.run(["node", str(runner()), str(source), str(output), suffix, profile], capture_output=True, timeout=60)
    return result, output


def test_chat_runner_emits_only_two_canonical_files_and_legacy_remains_available(tmp_path):
    result, output = parse(tmp_path, pdf_bytes(), "pdf")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in output.iterdir()) == ["manifest.json", "text.txt"]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["version"] == 2 and manifest["parser"].endswith("ailab4")
    body = (output / "text.txt").read_bytes()
    assert manifest["text_bytes"] == len(body)
    assert len(manifest["units"]) == 3
    for unit in manifest["units"]:
        part = body[unit["byte_offset"] : unit["byte_offset"] + unit["byte_size"]]
        assert hashlib.sha256(part).hexdigest() == unit["sha256"]
        assert f"Page {unit['unit_number']}" in part.decode()
    legacy, old = parse(tmp_path, pdf_bytes(), "pdf", "legacy")
    assert legacy.returncode == 0, legacy.stderr
    assert json.loads((old / "manifest.json").read_text())["parser"].endswith("ailab3")
    assert (old / "000000.txt").exists()


def test_chat_has_no_physical_total_page_admission_limit(tmp_path):
    result, output = parse(tmp_path, pdf_bytes(500), "pdf")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["total_units"] == 500
    assert manifest["status"] == "ready"
    assert len(manifest["units"]) == 500
    assert len(list(output.iterdir())) == 2


def test_chat_text_limit_rejects_without_publishing_partial_manifest(tmp_path):
    import io

    from docx import Document

    doc = Document()
    for _ in range(210):
        doc.add_paragraph("text " * 220)
    data = io.BytesIO()
    doc.save(data)
    result, output = parse(tmp_path, data.getvalue(), "docx")
    assert result.returncode == 2
    assert b'"error":"document_text_limit"' in result.stderr
    assert not (output / "manifest.json").exists()


def test_chat_native_manifest_keeps_cell_count_and_geometry_independent(tmp_path):
    import io

    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference

    book = Workbook()
    sheet = book.active
    sheet.append(["Header", "Value"])
    sheet.append(["North", 42])
    sheet.append(["South", "=B2*2"])
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=3), titles_from_data=True)
    sheet.add_chart(chart, "E1")
    data = io.BytesIO()
    book.save(data)
    book.close()
    result, output = parse(tmp_path, data.getvalue(), "xlsx")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["cells"] == 6
    assert manifest["visual_units"]
    assert manifest["units"][0]["quality"] == "native_unverified"


def test_packed_native_index_and_read_keep_source_identity_and_unicode(tmp_path, monkeypatch):
    from ailab_platform.api.document_sources import index_document
    from ailab_platform.api.routes_library_tools import Read, read_document_blocks
    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.models import FileObject, User, new_id
    from ailab_platform.storage import get_storage

    init_db()
    body = "原生正文独立于图片\n\n第二块扫描覆盖未知\n\n".encode()
    parts = ["原生正文独立于图片", "第二块扫描覆盖未知"]
    offset = 0
    units = []
    for i, part in enumerate(parts):
        data = part.encode()
        units.append(
            {
                "ordinal": i,
                "anchor": f"p:{i + 1}",
                "unit_number": i + 1,
                "name": "text.txt",
                "chars": len(part),
                "sha256": hashlib.sha256(data).hexdigest(),
                "byte_offset": offset,
                "byte_size": len(data),
                "quality": "native_unverified",
                "visual": True,
            }
        )
        offset += len(data) + 2
    with SessionLocal() as db:
        user = db.query(User).first()
        file = FileObject(
            id=new_id("fil"), user_id=user.id, filename="packed.docx", storage_key="original", sha256="source", status="ready"
        )
        db.add(file)
        db.flush()
        prefix = f"derived/{file.id}/office/generation/"
        key = prefix + "manifest.json"
        manifest = {
            "version": 2,
            "parser": "office-a60ba6a-ailab4",
            "status": "ready",
            "kind": "docx",
            "units": units,
            "total_units": 2,
            "visual_units": [{"anchor": "p:1", "unit_number": 1}],
        }
        storage = get_storage()
        storage.put_bytes(prefix + "text.txt", body)
        storage.put_bytes(key, json.dumps(manifest).encode())
        file.meta = {"document_manifest_key": key, "extract": {"kind": "docx", "parser": manifest["parser"]}}
        index_document(db, file, key)
        db.commit()
        result = read_document_blocks(db, file, Read(file_id=file.id))
        assert result["text"] == "".join(parts)
        assert len(result["coverage"]) == 2
        assert all(item["method"] == "native" for item in result["coverage"])
        assert result["visual"]["status"] == "present"


def test_native_representation_stages_two_assets_then_publishes_atomically(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from ailab_platform.api import derived_assets
    from ailab_platform.api.main import app
    from ailab_platform.api.native_preparation import request_native
    from ailab_platform.config import settings
    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.models import DerivedAsset, DerivedBudget, FileObject, OutboxMessage, User, new_id
    from ailab_platform.storage import get_storage

    init_db()
    storage = get_storage()
    with SessionLocal() as db:
        user = db.scalar(select(User))
        file = FileObject(
            id=new_id("fil"),
            user_id=user.id,
            filename="native.pdf",
            storage_key="test-original",
            sha256="source",
            status="ready",
            meta={"scan": {"verdict": "clean"}},
        )
        db.add(file)
        db.flush()
        state = request_native(db, file)
        again = request_native(db, file)
        assert again["job_id"] == state["job_id"]
        db.commit()
        db.get(DerivedBudget, 1).backfill_complete = True
        job = db.get(OutboxMessage, state["job_id"])
        job.claim_token, job.claimed_until = "attempt", datetime.now(UTC) + timedelta(minutes=3)
        db.commit()
        fid, job_id = file.id, job.id
    headers = {"X-Service-Token": settings.service_token, "X-Run-Attempt": "attempt"}
    with TestClient(app) as client:
        start = client.post(f"/internal/files/{fid}/native/start", json={"job_id": job_id}, headers=headers)
        assert start.status_code == 200, start.text
        assert start.json()["job_id"] == "attempt"
        body = b"Native independent coverage"
        unit = {
            "ordinal": 0,
            "anchor": "page:1",
            "unit_number": 1,
            "name": "text.txt",
            "chars": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "byte_offset": 0,
            "byte_size": len(body),
            "quality": "native_unverified",
            "visual": True,
        }
        manifest = {
            "version": 2,
            "parser": "office-a60ba6a-ailab4",
            "status": "ready",
            "kind": "pdf",
            "units": [unit],
            "chars": len(body),
            "total_units": 1,
            "visual_units": [{"anchor": "page:1", "unit_number": 1}],
        }
        prefix = f"derived/{fid}/office/{job_id}-attempt/"
        payloads = {prefix + "manifest.json": json.dumps(manifest).encode(), prefix + "text.txt": body}
        grants = client.post(
            "/internal/derived-assets/reserve-batch",
            headers=headers,
            json={
                "items": [
                    {"key": key, "version": "source", "max_bytes": len(data), "kind": "native", "job_id": job_id}
                    for key, data in payloads.items()
                ]
            },
        ).json()["items"]
        receipts = [{"key": g["key"], "token": g["token"]} for g in grants]
        for key, data in payloads.items():
            storage.put_bytes(key, data)
        finish = client.post("/internal/derived-assets/finish-batch", headers=headers, json={"items": receipts})
        assert finish.status_code == 200, finish.text
        bad = client.post(
            f"/internal/files/{fid}/native/complete",
            headers=headers,
            json={"job_id": job_id, "manifest_key": prefix + "manifest.json", "receipts": receipts[:1]},
        )
        assert bad.status_code == 422
        with SessionLocal() as db:
            assert {r.state for r in db.scalars(select(DerivedAsset).where(DerivedAsset.file_id == fid))} == {"staged"}
            assert db.get(FileObject, fid).status == "ready"
        complete = client.post(
            f"/internal/files/{fid}/native/complete",
            headers=headers,
            json={"job_id": job_id, "manifest_key": prefix + "manifest.json", "receipts": receipts},
        )
        assert complete.status_code == 200, complete.text
        assert complete.json()["status"] == "ready"
        replay = client.post(
            f"/internal/files/{fid}/native/complete",
            headers=headers,
            json={"job_id": job_id, "manifest_key": prefix + "manifest.json", "receipts": receipts},
        )
        assert replay.status_code == 200 and replay.json() == complete.json()
        with SessionLocal() as db:
            file = db.get(FileObject, fid)
            assert file.status == "ready"
            assert request_native(db, file)["status"] == "ready"
            assert {r.state for r in db.scalars(select(DerivedAsset).where(DerivedAsset.file_id == fid))} == {"ready"}
            assert db.get(DerivedBudget, 1).reserved_bytes == 0
            for key in payloads:
                storage.delete(key)
                derived_assets.forget_deleted(db, key)
            db.commit()


def test_native_bundle_unpacks_exactly_two_files_and_rejects_extra_or_corrupt_members(tmp_path):
    import io
    import tarfile

    from ailab_platform.worker.isolate import IsolationError
    from ailab_platform.worker.native_preparation import unpack

    data = b"Native text"
    manifest = {
        "version": 2,
        "parser": "office-a60ba6a-ailab4",
        "kind": "pdf",
        "status": "ready",
        "chars": len(data),
        "total_units": 1,
        "units": [{"ordinal": 0, "name": "text.txt", "byte_offset": 0, "byte_size": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    }
    storage = LocalStorage(tmp_path / "storage")

    def archive(extra=False, corrupt=False):
        bundle = io.BytesIO()
        with tarfile.open(fileobj=bundle, mode="w") as tar:
            files = {"manifest.json": json.dumps(manifest).encode(), "text.txt": b"Corrupt text" if corrupt else data}
            if extra:
                files["../escape"] = b"bad"
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        content = bundle.getvalue()
        storage.put_bytes("bundle.tar", content)
        return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    output = tmp_path / "good"
    output.mkdir()
    assert unpack(storage, "bundle.tar", output, archive())["status"] == "ready"
    with pytest.raises(IsolationError, match="invalid_native_bundle"):
        unpack(storage, "bundle.tar", output, archive(extra=True))
    with pytest.raises(ValueError, match="document_block_changed"):
        unpack(storage, "bundle.tar", output, archive(corrupt=True))
    assert not (tmp_path / "escape").exists()
    storage.close()

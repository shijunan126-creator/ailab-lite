"""The one document catalog: mode fences, latest transport, and the frozen per-run copy."""

import asyncio
import copy
import hashlib

import pytest
from sqlalchemy import select

from ailab_platform.agent.loop import _tools_for_run
from ailab_platform.agent.subtasks import ALLOWED, allowed
from ailab_platform.agent.tools import ToolContext, ToolError, execute_tool, tools_for_chat, tools_for_turn
from ailab_platform.db import SessionLocal
from ailab_platform.domain.document_reading import CursorError, Position, decode_cursor, encode_cursor
from ailab_platform.domain.document_tools import CHAT_DOCUMENT_TOOLS
from ailab_platform.models import DocumentBlock, FileDerivation, FileObject, OutboxMessage, Run, new_id
from ailab_platform.storage import get_storage
from tests.test_ledger_protocol import SERVICE, api  # noqa: F401
from tests.test_runtime_protocol import owned


def names(tools):
    return {t["function"]["name"] for t in tools}


def configure(api, *, mode="chat", ready=True):  # noqa: F811
    rid, _, headers = owned(api)
    with SessionLocal() as db:
        run = db.get(Run, rid)
        run.options = {**(run.options or {}), "mode": mode}
        file = FileObject(
            id=new_id("fil"),
            user_id=run.user_id,
            filename="catalog-v2-" + rid + ".txt",
            storage_key="original/" + rid,
            sha256="a" * 64,
            size=100,
            content_type="text/plain",
            status="ready",
            meta={"input_lifecycle": 2, "scan": {"verdict": "clean"}},
        )
        db.add(file)
        db.flush()
        if ready:
            key = f"derived/{file.id}/office/catalog/text.txt"
            text = ("target " * 43).encode()
            get_storage().put_bytes(key, text)
            index = FileDerivation(
                file_id=file.id,
                kind="native",
                fingerprint=rid,
                parser_version="office-a60ba6a-ailab4",
                status="ready",
                manifest_key=key.replace("text.txt", "manifest.json"),
            )
            db.add(index)
            db.flush()
            db.add(
                DocumentBlock(
                    derivation_id=index.id,
                    ordinal=0,
                    unit_number=1,
                    anchor="p:1",
                    text_key=key,
                    digest=hashlib.sha256(text).hexdigest(),
                    chars=len(text),
                    byte_offset=0,
                    byte_size=len(text),
                )
            )
            file.meta = {
                **file.meta,
                "extract": {"kind": "text", "units": 1},
                "representations": {
                    "native_text": {"status": "ready", "version": file.sha256, "derivation_id": index.id},
                },
            }
        db.commit()
        return rid, file.id, headers


def test_each_mode_has_one_catalog_and_a_run_keeps_its_frozen_copy():
    work, chat = copy.deepcopy(tools_for_turn()), copy.deepcopy(tools_for_chat())
    assert not CHAT_DOCUMENT_TOOLS & names(work)
    assert CHAT_DOCUMENT_TOOLS <= names(chat)
    assert {"bash", "copy_library_file", "resolve_attachment", "view_workspace_file"} <= names(work)
    assert tools_for_turn() == work and tools_for_chat() == chat
    assert _tools_for_run({"options": {"mode": "work"}, "_fixed_catalog": work}) is work
    assert {"copy_library_file", "resolve_attachment", "view_workspace_file"} <= allowed()
    assert "read_library_file" not in ALLOWED


@pytest.mark.parametrize("name", sorted(CHAT_DOCUMENT_TOOLS))
def test_work_execution_rejects_chat_document_calls_before_clients(name):
    ctx = ToolContext(mode="work")
    with pytest.raises(ToolError, match="not available in this mode"):
        asyncio.run(execute_tool(name, {"file_id": "forged"}, "lease", None, ctx))


def test_issued_cursor_signature_cannot_be_forged_or_reused_with_another_key():
    key = b"api-owned-test-key"
    token = encode_cursor("scope", Position(2, 3), key=key)
    assert decode_cursor(token, "scope", key=key) == Position(2, 3)
    with pytest.raises(CursorError, match="invalid_cursor"):
        decode_cursor(encode_cursor("scope", Position(999, 0)), "scope", key=key)
    with pytest.raises(CursorError, match="invalid_cursor"):
        decode_cursor(token, "scope", key=b"rotated-key")
    with pytest.raises(CursorError, match="cursor_stale"):
        decode_cursor(token, "changed", key=key)


def test_native_routes_require_current_attempt_mode_and_source_and_page_without_ocr(api):  # noqa: F811
    rid, fid, headers = configure(api)
    url = f"/internal/runs/{rid}/documents/"
    assert api.post(url + "read_document", headers=SERVICE, json={"file_id": fid}).status_code == 409
    first = api.post(url + "search_document", headers=headers, json={"file_id": fid, "query": "target"})
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["hits"]) == 20 and page["next_cursor"]
    assert page["coverage"]["ocr"]["status"] == "not_searched"
    second = api.post(url + "search_document", headers=headers, json={"file_id": fid, "query": "target", "cursor": page["next_cursor"]})
    assert second.status_code == 200 and len(second.json()["hits"]) == 20
    stale = api.post(url + "search_document", headers=headers, json={"file_id": fid, "query": "changed", "cursor": page["next_cursor"]})
    assert stale.status_code == 409 and stale.json()["detail"] == "cursor_stale"
    read = api.post(url + "read_document", headers=headers, json={"file_id": fid})
    assert read.status_code == 200 and read.json()["text"] == "target " * 43
    assert api.post(url + "read_document", headers=headers, json={"file_id": fid, "text_source": "ocr"}).status_code == 422
    explicit = api.post(
        url + "read_document", headers=headers, json={"file_id": fid, "text_source": "ocr", "selector": {"kind": "pages", "pages": [1]}}
    )
    assert explicit.status_code == 503 and explicit.json()["detail"] == "ocr_executor_unavailable"
    with SessionLocal() as db:
        jobs = db.scalars(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == fid)).all()
        assert not jobs  # read/search/explicit unavailable OCR never starts a legacy OCR job
        file = db.get(FileObject, fid)
        from datetime import UTC, datetime

        file.deleted_at = datetime.now(UTC)
        db.commit()
    assert api.post(url + "read_document", headers=headers, json={"file_id": fid}).status_code == 404


def test_new_endpoints_cannot_bypass_the_mode_fence(api):  # noqa: F811
    rid, fid, headers = configure(api, mode="work")
    for operation in ("read_document", "search_document"):
        result = api.post(
            f"/internal/runs/{rid}/documents/{operation}",
            headers=headers,
            json={"file_id": fid, **({"query": "target"} if operation == "search_document" else {})},
        )
        assert result.status_code == 422 and result.json()["detail"] == "document_tool_not_available_for_catalog"


def test_retired_tool_routes_stay_available_to_deployed_agents(api):  # noqa: F811
    """Old Agents still deploy against these internal routes; the model never sees their tool names any more."""
    rid, fid, headers = configure(api, mode="work")
    for action in ("read", "extract-text", "image-reference"):
        result = api.post(f"/internal/runs/{rid}/library/{action}", headers=headers, json={"file_id": fid})
        assert result.status_code in {200, 422, 503}, result.text
    assert api.post(f"/internal/runs/{rid}/document-view", headers=headers, json={"op": "get_view"}).status_code == 200


def test_new_text_route_stages_one_lazy_native_job_and_waits_without_changing_original(api):  # noqa: F811
    rid, fid, headers = configure(api, ready=False)
    for _ in range(2):
        result = api.post(f"/internal/runs/{rid}/documents/read_document", headers=headers, json={"file_id": fid})
        assert result.status_code == 200, result.text
        assert result.json()["pending"] and result.json()["action"] == "native-text"
    with SessionLocal() as db:
        jobs = db.scalars(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == fid)).all()
        assert len(jobs) == 1 and jobs[0].payload["kind"] == "native"
        assert db.get(FileObject, fid).status == "ready"


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("name", ["platform.pdf", "platform.docx", "platform.xlsx", "platform.pptx"])
def test_generated_guides_preserve_locators_and_use_mode_operations(name, mode):
    from ailab_platform.agent.skill_modules import read_module

    result = read_module(name, mode)
    viewer = read_module(name, mode, "viewer/SKILL.md")
    assert read_module(name, mode) == result
    retired = ("read_library_file", "view_attachment", "extract_text", "document_view")
    for guide in (result, viewer):
        assert not any(op in guide["skill"] for op in retired)
        assert hashlib.sha256(guide["skill"].encode()).hexdigest() == guide["hash"]
        assert ("office-read" in guide["skill"]) == (mode == "work")
    assert ("read_document" in result["skill"]) == (mode == "chat")
    assert "## Anchors" in viewer["skill"] and "## Notes" in viewer["skill"]
    if name == "platform.xlsx":
        assert "not calculated in file" in viewer["skill"] and "## Values and formulas" in viewer["skill"]


def test_v2_attachment_guidance_never_rewrites_user_prompt_or_source_text():
    from ailab_platform.agent.prompts import user_prompt

    original = "read_library_file 与 view_attachment 是源码中的旧函数名；原文件在 /workspace/inputs/original.txt"
    result = user_prompt(original, [{"file_id": "f", "filename": "source.txt", "text": original}], mode="chat")
    assert result.startswith(original)
    assert result.split("<!-- AILAB_ATTACHMENT_START -->\n\n```\n\n")[1].split("\n\n```\n\n")[0] == original


@pytest.mark.parametrize("page", [2147483648, 10**100])
def test_page_protocol_rejects_integer_overflow_without_a_physical_page_admission_limit(page):
    from pydantic import ValidationError

    from ailab_platform.contracts.document_input import ReadDocument

    with pytest.raises(ValidationError):
        ReadDocument(file_id="f", selector={"kind": "pages", "pages": [page]})
    assert ReadDocument(file_id="f", selector={"kind": "pages", "pages": [100001]}).selector.pages == [100001]


def test_generated_document_guides_are_reproducible_without_repacking_old_skill_assets(tmp_path):
    import runpy
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    builder = runpy.run_path(str(root / "vendor/office-suite/build.py"))
    output = tmp_path / "guides.json"
    builder["build_document_guides"](output)
    assert output.read_bytes() == (root / "platform/ailab_platform/agent/office_guides_v2.json").read_bytes()


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        (
            "view_document",
            {
                "file_id": "f",
                "target": {"kind": "region", "page": 2, "image_id": "c", "revision": "r", "x": 0, "y": 0, "width": 1, "height": 1},
            },
            "target.region: region requires exactly one page or image_id",
        ),
        (
            "view_document",
            {"file_id": "f", "target": {"kind": "region", "page": 2, "x": 0, "y": 0, "width": 1, "height": 1}},
            "target.region.revision",
        ),
        ("read_document", {"file_id": "f", "text_source": "ocr"}, "OCR requires explicit pages or region"),
        ("read_document", {"file_id": "f", "selector": {"kind": "sheet", "sheet": "S", "range": "a1"}}, "selector.sheet.range"),
    ],
)
def test_malformed_document_calls_name_the_fields_to_fix(name, arguments, expected):
    from ailab_platform.agent.document_tools import argument_error

    error = argument_error(name, arguments)
    assert error["error"] == "invalid_document_arguments"
    assert any(problem.startswith(expected) for problem in error["fix"]), error
    assert "a1" not in str(error)  # never echo argument values back
    assert argument_error("view_document", {"file_id": "f", "target": {"kind": "pages", "pages": [1]}}) is None


def test_v2_guides_state_each_rule_once_and_only_their_own_format_facts():
    from ailab_platform.agent.skill_modules import read_module

    for fmt in ("pdf", "docx", "xlsx", "pptx"):
        navigation = read_module(f"platform.{fmt}", "chat")["skill"]
        viewer = read_module(f"platform.{fmt}", "chat", "viewer/SKILL.md")["skill"]
        # Attachments already carry file_id/navigation; the guide must not demand extra round trips.
        assert "resolve_attachment" not in navigation and "直接读取或查看该范围" in navigation
        # The operating workflow lives in the navigation guide, never repeated inside the viewer resource.
        assert navigation.splitlines()[2] not in viewer and "## Workflow" not in viewer
        assert ("工作表序号不是页号" in navigation) == (fmt == "xlsx")
        assert "the verified locator" not in viewer and "workspace" not in viewer


def test_revision_failures_tell_the_model_how_to_obtain_a_region_identity():
    from ailab_platform.agent.document_tools import failure

    result = failure("ocr_image_revision_changed")
    assert not result["ok"] and "region_ref" in result["fix"][0]
    assert "fix" not in failure("document_queue_full")


def test_copy_outside_inputs_returns_the_destination_rule_not_a_generic_failure():
    from ailab_platform.agent.tools import ToolContext, execute_tool, tools_for_turn
    from ailab_platform.clients import ClientError

    class Platform:
        async def library(self, run_id, action, payload):
            return {"file_id": payload["file_id"]}

    class Broker:
        async def copy_library_file(self, lease_id, file_id, path):
            raise ClientError("POST", "/library-file", 422, '{"detail":"path must stay under /workspace/inputs"}')

    ctx = ToolContext("r", Platform(), mode="work")
    result = asyncio.run(execute_tool("copy_library_file", {"file_id": "f", "path": "/workspace/work/a.docx"}, "lease", Broker(), ctx))
    assert result["ok"] is False and "/workspace/inputs/" in result["error"]
    copy = next(t for t in tools_for_turn() if t["function"]["name"] == "copy_library_file")
    assert "/workspace/inputs/" in copy["function"]["description"]

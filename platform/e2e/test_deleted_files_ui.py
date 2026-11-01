"""A file deleted from the library after a turn stays named in that turn but no longer opens or downloads."""

import json
import subprocess
import sys

import pytest

from .conftest import PLATFORM

pytestmark = pytest.mark.e2e


def _seed(stack):
    code = """
import json
from ailab_platform.db import SessionLocal
from ailab_platform.library import assign_file_version
from ailab_platform.models import Artifact, Conversation, FileObject, Run, User, new_id
from ailab_platform.storage import get_storage
with SessionLocal() as db:
    user = db.query(User).filter_by(username="e2e-user").one()
    files = {}
    for name in ("删除的附件.txt", "删除的产物.txt"):
        row = FileObject(id=new_id("fil"), user_id=user.id, filename=name, content_type="text/plain", size=4, status="ready",
            storage_key=f"e2e/{name}")
        assign_file_version(db, row)
        db.add(row); db.flush()
        get_storage().put_bytes(row.storage_key, b"data")
        files[name] = row.id
    conv = Conversation(user_id=user.id, title="删除文件验收")
    db.add(conv); db.flush()
    run = Run(user_id=user.id, conversation_id=conv.id, prompt="看附件", model="test-fast", status="succeeded", summary="已完成。",
        input_file_ids=[files["删除的附件.txt"]])
    db.add(run); db.flush()
    conv.active_run_id = run.id
    db.add(Artifact(run_id=run.id, file_id=files["删除的产物.txt"]))
    db.commit()
    print(json.dumps({"id": conv.id, "files": list(files.values())}))
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("width", [1280, 390])
def test_deleted_attachment_and_artifact_render_as_gone(page, stack, width):
    from .conftest import login

    page.set_viewport_size({"width": width, "height": 844})
    seeded = _seed(stack)
    login(page, stack)
    for fid in seeded["files"]:
        assert page.request.delete(f"{stack.portal}/api/v1/files/{fid}").status == 204
    page.goto(f"{stack.portal}/c/{seeded['id']}")
    chip = page.locator(".attach-chip.gone", has_text="删除的附件.txt")
    chip.wait_for()
    assert chip.evaluate("el => el.tagName") == "SPAN"  # no longer a button that opens the file
    card = page.locator(".file-card.gone", has_text="删除的产物.txt")
    card.wait_for()
    assert card.locator("button, a").count() == 0  # no preview, download or file menu
    assert page.locator(".attach-chip.open").count() == 0

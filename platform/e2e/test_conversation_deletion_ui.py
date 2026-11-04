"""Back navigation must not retain cached content after a conversation is deleted elsewhere."""

import json
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from .conftest import PLATFORM, login

pytestmark = pytest.mark.e2e


def _seed(stack):
    code = """
import json
from uuid import uuid4
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, Run, User
with SessionLocal() as db:
    user = db.query(User).filter_by(username="e2e-user").one()
    rows = []
    tag = uuid4().hex[:8]
    for i in range(2):
        conv = Conversation(user_id=user.id, title=f"删除缓存验证-{tag}-{i}")
        db.add(conv); db.flush()
        run = Run(user_id=user.id, conversation_id=conv.id, prompt=f"原问题-{tag}-{i}", model="test-fast",
                  status="succeeded", summary=f"保密正文-{tag}-{i}")
        db.add(run); db.flush()
        conv.active_run_id = run.id
        rows.append(dict(id=conv.id, title=conv.title, body=run.summary))
    db.commit()
    print(json.dumps(rows))
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("width", [1280, 390])
def test_back_to_deleted_cached_conversation_has_no_body(page, stack, width):
    first, second = _seed(stack)
    page.set_viewport_size({"width": width, "height": 844})
    login(page, stack, path=f"/c/{first['id']}")
    expect(page.get_by_text(first["body"], exact=True)).to_be_visible()
    # A real Next Link keeps the same JS cache while changing conversations.
    link = page.locator(f'a[href="/c/{second["id"]}"]').first
    if not link.is_visible():
        page.get_by_role("button", name="展开侧栏", exact=True).click()
    link.click()
    expect(page.get_by_text(second["body"], exact=True)).to_be_visible()
    assert page.request.delete(f"{stack.portal}/api/v1/conversations/{first['id']}").status == 204
    page.go_back()
    expect(page.get_by_text("对话已删除或不存在", exact=True)).to_be_visible()
    expect(page.get_by_text(first["body"], exact=True)).to_have_count(0)
    assert page.locator(".turn").count() == 0

"""Real Portal/Worker flow; OCR/model quality is deliberately outside this fixture."""

import io
import os
import uuid
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import expect

from .test_library_ui import _upload

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("width", [1280, 390])
def test_text_extraction_dialog_and_unconfigured_service(logged_in, stack, tmp_path, width):
    page = logged_in
    page.set_viewport_size({"width": width, "height": 900})
    data = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(data, "PNG")
    name = "extract-" + uuid.uuid4().hex[:6] + ".png"
    _upload(page, stack, tmp_path, name, data.getvalue())
    row = page.locator(".lib-row", has_text=name)
    row.get_by_role("button", name=f"更多操作 {name}", exact=True).click()
    page.get_by_role("menuitem", name="提取文字", exact=True).click()
    dialog = page.get_by_role("dialog", name="提取文字", exact=True)
    dialog.get_by_role("button", name="提取", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text("文字识别服务尚未配置")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    shots = Path(os.environ.get("AILAB_E2E_SHOTS", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shots / f"file-extraction-{width}.png"), animations="disabled")
    dialog.get_by_role("button", name="关闭", exact=True).click()
    expect(dialog).not_to_be_visible()

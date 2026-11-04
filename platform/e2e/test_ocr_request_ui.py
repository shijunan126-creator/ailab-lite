"""Real-browser request binding; OCR responses are controlled at the HTTP edge."""

import json
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

pytestmark = pytest.mark.e2e


def test_text_extraction_binds_polling_and_download_to_request(logged_in, stack, tmp_path):
    page = logged_in
    name = f"ocr-binding-{uuid.uuid4().hex[:8]}.png"
    path = tmp_path / name
    Image.new("RGB", (400, 200), "white").save(path)
    page.goto(stack.portal + "/files")
    page.set_input_files("input[type=file]", str(path))
    row = page.locator(".lib-row", has_text=name)
    row.get_by_role("link", name=f"下载 {name}", exact=True).wait_for(timeout=20000)
    fid = next(f["id"] for f in page.request.get(stack.portal + "/api/v1/files").json() if f["filename"] == name)
    polls = []
    posts = []

    def extraction(route):
        if route.request.method == "POST":
            posts.append(route.request.url)
            if len(posts) == 1:
                route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "另一组页码正在提取，请完成后重试"}))
            else:
                route.fulfill(content_type="application/json", body=json.dumps({"request_id": "selected-request", "ocr": "done"}))
            return
        polls.append(parse_qs(urlparse(route.request.url).query))
        route.fulfill(
            content_type="application/json",
            body=json.dumps({"status": "done", "download_url": f"/v1/files/{fid}/extracted-text?request_id=selected-request"}),
        )

    page.route(f"**/api/v1/files/{fid}/extract-text*", extraction)
    # Let the actual download route stream a fixture; Chromium's intercepted
    # attachment responses can be canceled before Playwright saves the body.
    key = f"derived/{fid}/ocr/selected-request/result.txt"
    target = Path(stack.env["AILAB_STORAGE_ROOT"]) / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("selected page text")
    patched = page.request.patch(
        stack.api + f"/internal/files/{fid}",
        headers={"X-Service-Token": stack.service_token},
        params={"job": "processing"},
        data={"meta": {"ocr": "done", "ocr_generation": "selected-request", "ocr_text_key": key}},
    )
    assert patched.ok, patched.text()
    try:
        row.get_by_role("button", name=f"更多操作 {name}", exact=True).click()
        page.get_by_role("menuitem", name="提取文字", exact=True).click()
        dialog = page.get_by_role("dialog", name="提取文字")
        dialog.get_by_role("button", name="提取", exact=True).click()
        dialog.get_by_role("alert").filter(has_text="另一组页码正在提取").wait_for()
        assert not polls
        dialog.get_by_role("button", name="提取", exact=True).click()
        link = dialog.get_by_role("link", name="下载文字", exact=True)
        link.wait_for()
        assert polls == [{"request_id": ["selected-request"]}]
        with page.expect_download() as downloaded:
            link.click()
        assert downloaded.value.path().read_text() == "selected page text"
    finally:
        page.unroute_all(behavior="ignoreErrors")
        assert page.request.delete(stack.portal + f"/api/v1/files/{fid}").status == 204

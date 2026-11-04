"""Real uploads, bounded Worker indices, native frame references and Agent client calls."""

import re
import time
import uuid

import httpx
import pytest

from .conftest import PLATFORM
from .test_portal import _send, _start_conversation, _wait_for_turns

pytestmark = pytest.mark.e2e
FIXTURES = PLATFORM.parent / "vendor/office-suite/runtime/fixtures"


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_ppt_table_reference_actions(logged_in, stack, tmp_path, empty, direct):
    from playwright.sync_api import expect
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    shape = slide.shapes.add_table(2, 1 if empty else 2, Inches(1), Inches(1), Inches(4), Inches(2))
    if not empty:
        for row, cells in enumerate([["收入", "利润"], ["100", "50"]]):
            for col, value in enumerate(cells):
                shape.table.cell(row, col).text = value
    source = tmp_path / f"table-{uuid.uuid4().hex[:8]}.pptx"
    presentation.save(source)
    page = logged_in
    page.set_input_files(".composer-box input[type=file]", str(source))
    _start_conversation(page, "原生收件验收", "test-office-vision")
    _wait_for_turns(page, 1)
    page.locator(".turn .attach-chip.open").first.click()
    file = next(item for item in page.request.get(stack.portal + "/api/v1/files").json() if item["filename"] == source.name)
    _index(page, stack, file["id"])
    frame = _frame(page, source.name)
    table = frame.locator('.slide-frame[data-at="slide:1"] table')
    expect(table).to_be_visible(timeout=25000)
    # One click inside the actual table selects the shape, including a blank single-column table.
    table.click(position={"x": 5, "y": 5})
    if direct:
        frame.get_by_role("button", name="提问", exact=True).click()
        question = frame.get_by_role("textbox", name="就这里提问…")
        question.fill("修改这张表")
        question.press("Enter")
    else:
        frame.get_by_role("button", name=re.compile("^添加(到对话)?$")).click()
        expect(page.locator(".composer-box .quote-chip")).to_be_visible()
        _send(page, "修改这张表")
    _wait_for_turns(page, 2)
    cid = page.url.rsplit("/", 1)[-1]
    turn = page.request.get(f"{stack.portal}/api/v1/conversations/{cid}").json()["turns"][-1]
    assert len(turn["doc_refs"]) == 1
    ref = turn["doc_refs"][0]
    assert ref["at"] == f"slide:1/shape:{shape.shape_id}"
    assert ref["quote"] == ("" if empty else "收入 | 利润\n100 | 50")


def _index(page, stack, file_id):
    end = time.monotonic() + 30
    value = None
    while time.monotonic() < end:
        response = page.request.get(f"{stack.portal}/api/v1/files/{file_id}/document")
        assert response.ok, response.text()
        value = response.json()
        assert value.get("status") not in {"failed", "unsupported"}, f"Native document index failed for {file_id}: {value}"
        if value.get("status") == "ready":
            assert value["native"], value
            return value
        page.wait_for_timeout(150)
    raise AssertionError(value)


def _frame(page, name):
    frame = page.frame_locator(f'iframe.office-frame[aria-label="{name}"]')
    end = time.monotonic() + 30
    while not frame.locator(".canvas").is_visible():
        failure = page.locator(".viewer-state").filter(has_text="无法预览")
        assert not failure.count(), f"Native viewer failed for {name}: {failure.all_inner_texts()}"
        assert time.monotonic() < end, f"Native viewer did not open for {name}"
        page.wait_for_timeout(150)
    return frame


def _select(page, frame, kind, question=None):
    """Select real text with the mouse, then add it to the composer, or ask about it right there."""
    from playwright.sync_api import expect

    if kind == "xlsx":
        expect(frame.get_by_role("tab", name="Revenue", exact=True)).to_be_visible(timeout=20000)
        canvas = frame.locator(".xlsx-grid")
        expect(canvas).to_be_visible(timeout=20000)
        box = canvas.bounding_box()
        assert box
        # The first occupied range in the real Revenue worksheet, using actual pointer events.
        page.mouse.move(box["x"] + 80, box["y"] + 45)
        page.mouse.down()
        page.mouse.move(box["x"] + 280, box["y"] + 90, steps=12)
        page.mouse.up()
    else:
        selector = {"docx": '[data-at="p:2"]', "pdf": ".textLayer span", "pptx": ".pptx-slide svg text"}[kind]
        text = frame.get_by_text("Product Launch", exact=True).first if kind == "pptx" else frame.locator(selector).first
        expect(text).to_be_visible(timeout=25000)
        if kind == "pdf":
            text.click(click_count=3)
            selection = frame.locator("body").evaluate("() => document.getSelection()?.toString()")
            assert selection and selection.strip(), "real PDF text selection was empty"
        box = text.bounding_box()
        assert box
        if kind != "pdf":
            page.mouse.move(box["x"] + 2, box["y"] + min(box["height"] / 2, 8))
            page.mouse.down()
            page.mouse.move(box["x"] + min(box["width"] - 2, 160), box["y"] + min(box["height"] / 2, 8), steps=12)
            page.mouse.up()
    if question:
        ask = frame.get_by_role("button", name="提问", exact=True)
        expect(ask).to_be_visible(timeout=10000)
        ask.click()
        box = frame.get_by_role("textbox", name="就这里提问…")
        expect(box).to_be_focused()
        box.fill(question)
        box.press("Enter")
        # sent through the outbox as a reference, not left in the composer
        expect(box).to_be_hidden(timeout=10000)
        expect(page.locator(".composer-box .quote-chip")).to_have_count(0)
        return
    add = frame.get_by_role("button", name=re.compile("^添加(到对话)?$"))
    expect(add).to_be_visible(timeout=10000)
    add.click()
    expect(page.locator(".composer-box .quote-chip")).to_be_visible()


@pytest.mark.parametrize("mode", ["对话", "工作"])
@pytest.mark.parametrize("kind,name", [("docx", "report.docx"), ("xlsx", "budget.xlsx"), ("pptx", "launch.pptx"), ("pdf", "cjk.pdf")])
def test_chat_upload_native_select_quote_send_and_reveal(logged_in, stack, kind, name, mode):
    from playwright.sync_api import expect

    page = logged_in
    page.get_by_role("radio", name=mode, exact=True).click()
    page.set_input_files(".composer-box input[type=file]", str(FIXTURES / name))
    _start_conversation(page, "原生收件验收", "test-office-vision")
    _wait_for_turns(page, 1)
    chip = page.locator(".turn .attach-chip.open").first
    filename = chip.inner_text().strip()
    chip.click()
    source = page.request.get(stack.portal + "/api/v1/files").json()
    file = next(item for item in source if item["filename"] == filename)
    _index(page, stack, file["id"])
    frame = _frame(page, filename)
    _select(page, frame, kind)
    quote = page.locator(".composer-box .quote-chip-text").inner_text()
    assert quote.strip()
    _send(page, "请解释这段引用")
    _wait_for_turns(page, 2)
    sent = page.locator(".turn .quote-chip").last
    expect(sent).to_contain_text(quote)
    if kind == "xlsx":
        frame.get_by_role("tab", name="Notes", exact=True).click()
    sent.click()
    revealed = _frame(page, filename)
    if kind == "xlsx":
        # Switching away before clicking the quote proves that reveal restores its worksheet.
        expect(revealed.get_by_role("tab", name="Revenue", exact=True)).to_have_attribute("aria-selected", "true")
    else:
        end = time.monotonic() + 10
        while not revealed.locator("body").evaluate(
            "() => Boolean(CSS.highlights.get('office-reveal')?.size) || "
            "document.querySelector('.office-revealed, .shape-revealed, .revealed') !== null"
        ):
            assert time.monotonic() < end
            page.wait_for_timeout(100)
    if kind in {"docx", "xlsx", "pptx", "pdf"}:
        results = []
        page.on(
            "response",
            lambda response: results.append(response) if "/client-calls/" in response.url and response.url.endswith("/result") else None,
        )
        _send(page, "原生控制 " + file["id"] + " " + kind)
        _wait_for_turns(page, 3)
        assert results and all(r.ok for r in results)
        assert "unavailable" not in page.locator(".turn").last.inner_text()
        expect(page.locator(".turn").last).to_contain_text("原生截图结果")
        expect(page.locator(".turn").last).to_contain_text('"status": "ready"')
        expect(page.locator(".turn").last).to_contain_text("capture_id")


@pytest.mark.parametrize("mode", ["对话", "工作"])
@pytest.mark.parametrize("kind,name", [("docx", "report.docx"), ("xlsx", "budget.xlsx"), ("pptx", "launch.pptx"), ("pdf", "cjk.pdf")])
def test_native_selection_direct_ask(logged_in, stack, kind, name, mode):
    """Ask in the viewer: the question and its exact reference go out as one message, and the model sees both."""
    from playwright.sync_api import expect

    page = logged_in
    page.get_by_role("radio", name=mode, exact=True).click()
    page.set_input_files(".composer-box input[type=file]", str(FIXTURES / name))
    _start_conversation(page, "原生收件验收", "test-office-vision")
    _wait_for_turns(page, 1)
    chip = page.locator(".turn .attach-chip.open").first
    filename = chip.inner_text().strip()
    chip.click()
    file = next(item for item in page.request.get(stack.portal + "/api/v1/files").json() if item["filename"] == filename)
    _index(page, stack, file["id"])
    frame = _frame(page, filename)
    _select(page, frame, kind, question="就这里直接提问：这段说的是什么？")
    _wait_for_turns(page, 2)
    turn = page.locator(".turn").last
    expect(turn).to_contain_text("就这里直接提问：这段说的是什么？")
    expect(turn.locator(".quote-chip")).to_have_count(1)
    # the scripted model answers with the reference block it received: the file and the selected location
    expect(turn).to_contain_text(f"直接提问引用：{filename} (", timeout=30000)


def test_live_work_file_native_agent_reveal_and_capture(logged_in, stack):
    from playwright.sync_api import expect

    page = logged_in
    results = []
    view_updates = []
    page.on(
        "response",
        lambda response: (
            view_updates.append(response) if response.request.method == "PUT" and response.url.endswith("/view") and response.ok else None
        ),
    )
    page.on(
        "response",
        lambda response: results.append(response) if "/client-calls/" in response.url and response.url.endswith("/result") else None,
    )
    page.get_by_role("radio", name="工作", exact=True).click()
    _start_conversation(page, "原生工作区验收", "test-office-vision")
    try:
        frame = _frame(page, "draft.docx")
        expect(frame.locator('[data-at="p:2"]')).to_be_visible(timeout=25000)
        expect(frame.locator('[data-at="p:2"].office-revealed')).to_be_visible(timeout=10000)
        end = time.monotonic() + 15
        while not view_updates:
            assert time.monotonic() < end
            page.wait_for_timeout(150)
        expect(_frame(page, "draft.docx").locator('[data-at="p:2"].office-revealed')).to_be_visible(timeout=10000)
    finally:
        httpx.post(stack.agent + "/e2e/office-release", timeout=5).raise_for_status()
    _wait_for_turns(page, 1)
    assert results and all(r.ok for r in results)
    assert "unavailable" not in page.locator(".turn").last.inner_text()
    expect(page.locator(".turn").last).to_contain_text("原生截图结果")
    expect(page.locator(".turn").last).to_contain_text('"status": "ready"')
    expect(page.locator(".turn").last).to_contain_text("capture_id")


@pytest.mark.parametrize("kind,name", [("docx", "report.docx"), ("xlsx", "budget.xlsx"), ("pptx", "launch.pptx"), ("pdf", "cjk.pdf")])
def test_library_native_view_does_not_request_server_preview(logged_in, stack, tmp_path, kind, name):
    from playwright.sync_api import expect

    from .test_library_ui import _open, _upload

    page = logged_in
    requests = []
    page.on(
        "request",
        lambda request: requests.append(request.url) if re.search(r"/files/[^/]+/preview(?:[/?]|$)|/view$", request.url) else None,
    )
    data = (FIXTURES / name).read_bytes()
    name = f"native-{uuid.uuid4().hex[:8]}.{kind}"
    _upload(page, stack, tmp_path, name, data)
    file = next(item for item in page.request.get(stack.portal + "/api/v1/files").json() if item["filename"] == name)
    viewer = _open(page, name)
    frame = _frame(page, name)
    if kind == "xlsx":
        expect(frame.get_by_role("tab", name="Revenue", exact=True)).to_be_visible()
    elif kind == "docx":
        text = frame.locator('[data-at="p:2"]')
        expect(text).to_be_visible()
        text.click(click_count=3)
        expect(frame.get_by_role("button", name="提问", exact=True)).to_have_count(0)
        expect(frame.get_by_role("button", name=re.compile("^添加(到对话)?$"))).to_have_count(0)
    elif kind == "pptx":
        expect(frame.get_by_text("Product Launch", exact=True).first).to_be_visible()
    else:
        expect(frame.locator(".textLayer span").first).to_be_visible()
    assert not requests, requests
    # No HTTP request is not enough: readiness must also avoid background preview jobs.
    metadata = page.request.get(stack.portal + f"/api/v1/files/{file['id']}")
    assert metadata.ok, metadata.text()
    assert not metadata.json().get("meta", {}).get("preview_job_id")
    expect(viewer.locator(".document-scroll img")).to_have_count(0)
    viewer.get_by_role("button", name="关闭预览", exact=True).click()


def test_work_native_unavailable_renders_workspace_only_on_fallback(logged_in, stack):
    from playwright.sync_api import expect

    page = logged_in
    # Force only the browser's admission decision. The workspace snapshot, render,
    # cache and asset delivery still traverse the actual API/Broker/sandbox chain.
    page.route("**/api/v1/runs/*/workspace/document?*", lambda route: route.fulfill(json={"status": "unsupported", "native": False}))
    calls = []
    page.on("response", lambda response: calls.append(response) if "/workspace/preview?" in response.url else None)
    page.get_by_role("radio", name="工作", exact=True).click()
    _start_conversation(page, "原生表格回退验收", "test-office-vision")
    try:
        pane = page.get_by_role("complementary", name="侧栏")
        expect(pane.locator(".sheet-grid td", has_text="华东")).to_be_visible(timeout=30000)
        assert len(calls) == 1 and calls[0].ok
        first = calls[0].json()
        # A second explicit request takes a fresh source snapshot but reuses the
        # same immutable server assets, instead of exporting the workbook again.
        cached = page.request.get(calls[0].url)
        assert cached.ok and cached.json() == first
        expect(pane.get_by_role("tab", name="fallback.xlsx")).to_be_visible()
    finally:
        httpx.post(stack.agent + "/e2e/office-release", timeout=5).raise_for_status()
    _wait_for_turns(page, 1)


@pytest.mark.parametrize("surface", ["library", "share", "workspace"])
def test_native_renderer_failure_after_index_uses_authorized_fallback(logged_in, stack, tmp_path, surface):
    """A valid index followed by corrupt browser bytes must exercise the actual frame error bridge.

    The original remains intact in API/Broker storage: only its browser download is corrupted.
    The fallback therefore reads/render the real authorized source, rather than the injected bytes.
    """
    from playwright.sync_api import expect

    from .test_library_ui import _open, _upload

    page = logged_in
    native_downloads, fallback_requests = [], []

    def corrupt(route):
        native_downloads.append(route.request.url)
        response = route.fetch()
        assert response.ok, response.text()
        # Keep length and version headers intact: this is a renderer failure,
        # not a source-version/partial-download error (which must not export).
        route.fulfill(response=response, body=b"x" * len(response.body()))

    page.on(
        "request",
        lambda req: (
            fallback_requests.append(req.url)
            if re.search(r"/(?:workspace/)?preview\?", req.url) or re.search(r"/files/[^/]+/preview$", req.url)
            else None
        ),
    )
    if surface == "workspace":
        page.route("**/api/v1/runs/*/workspace/content?*", corrupt)
        page.get_by_role("radio", name="工作", exact=True).click()
        _start_conversation(page, "原生表格回退验收", "test-office-vision")
        try:
            pane = page.get_by_role("complementary", name="侧栏")
            expect(pane.locator(".sheet-grid td", has_text="华东")).to_be_visible(timeout=30000)
            assert native_downloads
            assert len(fallback_requests) == 1, fallback_requests
            expect(pane.locator("iframe.office-frame")).to_have_count(0)
        finally:
            httpx.post(stack.agent + "/e2e/office-release", timeout=5).raise_for_status()
        _wait_for_turns(page, 1)
        return
    name = f"renderer-failure-{surface}-{uuid.uuid4().hex[:8]}.docx"
    _upload(page, stack, tmp_path, name, (FIXTURES / "report.docx").read_bytes())
    file = next(item for item in page.request.get(stack.portal + "/api/v1/files").json() if item["filename"] == name)
    _index(page, stack, file["id"])
    if surface == "share":
        share = page.request.post(stack.portal + "/api/v1/shares", data={"kind": "file", "file_id": file["id"], "audience": "everyone"})
        assert share.ok, share.text()
        sid = share.json()["id"]
        page.route(f"**/api/v1/shares/{sid}/files/{file['id']}/content", corrupt)
        page.goto(stack.portal + f"/s/{sid}")
    else:
        page.route(f"**/api/v1/files/{file['id']}/content", corrupt)
        _open(page, name)
    image = page.locator(".document-scroll img").first
    expect(image).to_be_visible(timeout=90000)
    page.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=image.element_handle())
    assert native_downloads
    assert fallback_requests
    expect(page.locator("iframe.office-frame")).to_have_count(0)


@pytest.mark.parametrize("failure", ["access", "size"])
def test_native_content_access_failure_does_not_start_server_render(logged_in, stack, tmp_path, failure):
    from playwright.sync_api import expect

    from .test_library_ui import _open, _upload

    page = logged_in
    name = f"denied-content-{uuid.uuid4().hex[:8]}.docx"
    _upload(page, stack, tmp_path, name, (FIXTURES / "report.docx").read_bytes())
    file = next(item for item in page.request.get(stack.portal + "/api/v1/files").json() if item["filename"] == name)
    _index(page, stack, file["id"])
    requests = []
    page.on("request", lambda req: requests.append(req.url) if f"/files/{file['id']}/preview" in req.url else None)
    if failure == "access":
        page.route(f"**/api/v1/files/{file['id']}/content", lambda route: route.fulfill(status=403, json={"detail": "access revoked"}))
    else:
        page.route(
            f"**/api/v1/files/{file['id']}/content", lambda route: route.fulfill(body=b"truncated", content_type="application/octet-stream")
        )
    viewer = _open(page, name)
    expect(viewer.get_by_role("alert")).to_contain_text(
        "文件不存在或无权访问" if failure == "access" else "文件已更新，请重新打开", timeout=30000
    )
    expect(viewer.locator("iframe.office-frame")).to_have_count(0)
    assert not requests, requests

"""Reader tabs retain independent documents without repeat fetches or route changes."""

import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

from .conftest import PLATFORM, login
from .test_conversation_ui import seed

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("mobile", [False, True])
def test_independent_reader_tabs_collapse_and_reopen(page, stack, mobile):
    conv = seed(stack, active=False)
    code = """
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
with SessionLocal() as db:
    run = db.get(Run, RUN)
    run.summary = '[第一篇](https://example.com/one) [第二篇](https://example.com/two)'
    run.result = {"sources": [{"title": "第一篇", "url": "https://example.com/one", "site": "example.com"},
                              {"title": "第二篇", "url": "https://example.com/two", "site": "example.com"}]}
    run.result = {**run.result, "previews": [{"filename": "source.xlsx", "kind": "sheets", "data": "1/sheets.json"}]}
    db.commit()
""".replace("RUN", repr(conv["run"]))
    subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, check=True)
    calls = []

    def respond(route):
        parsed = urlsplit(route.request.url)
        url = parse_qs(parsed.query)["url"][0]
        calls.append((parsed.path, url))
        if parsed.path.endswith("/render"):
            route.fulfill(content_type="text/html", body=f'<p>{url}</p><script>top.location="/wrong"</script>')

    page.route("**/api/v1/reader**", respond)
    login(page, stack, path="/c/" + conv["id"])
    location = page.url
    page.get_by_role("button", name="来源（2）", exact=True).click()
    page.locator(".src-item").filter(has_text="第一篇").click()
    page.get_by_role("tab", name="第一篇", exact=True).wait_for()
    page.get_by_role("button", name="关闭 source.xlsx", exact=True).click()
    assert page.get_by_role("tab", name="第一篇", exact=True).get_attribute("aria-selected") == "true"
    page.get_by_role("link", name="第二篇", exact=True).first.click()
    page.get_by_role("tab", name="第二篇", exact=True).wait_for()
    if mobile:
        page.get_by_role("button", name="收起侧栏", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("tab", name="第一篇", exact=True).click()
    assert page.get_by_role("tab", name="第一篇", exact=True).get_attribute("aria-selected") == "true"
    # collapsing keeps every tab; the composer pill brings the pane back as it was
    page.get_by_role("button", name="收起预览", exact=True).click()
    page.locator(".preview-pane").wait_for(state="detached")
    page.get_by_role("button", name="打开预览（4）", exact=True).click()
    assert page.get_by_role("tab", name="第一篇", exact=True).get_attribute("aria-selected") == "true"
    page.get_by_role("button", name="关闭 source.xlsx", exact=True).click()
    page.get_by_role("button", name="关闭 第二篇", exact=True).click()
    assert page.get_by_role("tab", name="第一篇", exact=True).get_attribute("aria-selected") == "true"
    page.get_by_role("button", name="关闭 来源", exact=True).click()
    assert page.get_by_role("tab", name="第一篇", exact=True).get_attribute("aria-selected") == "true"
    assert page.url == location
    assert all(path.endswith("/render") for path, _ in calls), calls
    assert page.locator(".reader-frame").first.get_attribute("sandbox") == "allow-popups"
    page.screenshot(path=str(stack.log_dir / f"reader-tabs-{mobile}.png"))


@pytest.mark.parametrize("mobile", [False, True])
def test_long_reader_tab_displays_title_from_first_character(page, stack, mobile):
    conv = seed(stack, active=False)
    title = "法国社会动员：马克龙收紧对示威活动的管制与相关报道详情" * 3
    code = """
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
with SessionLocal() as db:
    run = db.get(Run, RUN)
    run.result = {"sources": [{"title": TITLE + str(i), "url": "https://example.com/article-" + str(i), "site": "example.com"}
                              for i in range(5)]}
    db.commit()
""".replace("RUN", repr(conv["run"])).replace("TITLE", repr(title))
    subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, check=True)
    page.route("**/api/v1/reader/render?**", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body="<p>静态预览</p>"))
    login(page, stack, path="/c/" + conv["id"])
    page.get_by_role("button", name="来源（5）", exact=True).click()
    for i in range(5):
        if i:
            page.locator(".preview-tabs").get_by_role("tab", name="来源", exact=True).click()
        page.locator(".src-item").filter(has_text=title + str(i)).click()
    if mobile:
        page.get_by_role("button", name="收起侧栏", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
    for i in (0, 4, 1):
        tab = page.locator(".preview-tabs").get_by_role("tab", name=title + str(i), exact=True)
        tab.click()
        assert tab.evaluate("""el => {
            const first = document.createRange();
            first.setStart(el.firstChild, 0); first.setEnd(el.firstChild, 1);
            const glyph = first.getBoundingClientRect(), box = el.getBoundingClientRect();
            const strip = el.closest('.preview-tabs').getBoundingClientRect();
            return glyph.left >= Math.max(box.left, strip.left) && glyph.right <= Math.min(box.right, strip.right)
                && getComputedStyle(el).textAlign === 'left' && el.scrollLeft === 0;
        }""")
    page.screenshot(path=str(stack.log_dir / f"reader-title-start-{mobile}.png"))

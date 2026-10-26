"""Reader tabs retain independent documents without repeat fetches or route changes."""

import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

from .conftest import PLATFORM, login
from .test_conversation_ui import seed

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("mobile", [False, True])
def test_independent_reader_tabs_and_modes(page, stack, mobile):
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
        else:
            route.fulfill(json={"url": url, "title": url, "blocks": [{"type": "p", "text": "正文 " + url}], "truncated": False})

    page.route("**/api/v1/reader**", respond)
    login(page, stack, path="/c/" + conv["id"])
    location = page.url
    page.get_by_role("button", name="来源（2）", exact=True).click()
    page.locator(".src-item").filter(has_text="第一篇").click()
    page.get_by_role("tab", name="第一篇", exact=True).wait_for()
    page.get_by_role("button", name="关闭 source.xlsx", exact=True).click()
    assert page.get_by_role("tab", name="第一篇", exact=True).get_attribute("aria-selected") == "true"
    for theme in ("light", "dark"):
        page.emulate_media(color_scheme=theme)
        for name in ("原版", "正文"):
            button = page.get_by_role("tab", name=name, exact=True)
            button.hover()
            page.wait_for_timeout(160)
            assert button.evaluate(r"""e => {
                const s = getComputedStyle(e);
                const lum = value => {
                    const c = value.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => {
                        v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
                    });
                    return .2126*c[0] + .7152*c[1] + .0722*c[2];
                };
                const a = lum(s.color), b = lum(s.backgroundColor);
                return (Math.max(a,b)+.05)/(Math.min(a,b)+.05) >= 4.5;
            }""")
    page.get_by_role("tab", name="正文", exact=True).click()
    page.get_by_text("正文 https://example.com/one", exact=True).wait_for()
    page.get_by_role("tab", name="原版", exact=True).click()
    page.get_by_role("tab", name="正文", exact=True).click()
    page.get_by_role("link", name="第二篇", exact=True).first.click()
    page.get_by_role("tab", name="第二篇", exact=True).wait_for()
    if mobile:
        page.get_by_role("button", name="收起侧栏", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("tab", name="第一篇", exact=True).click()
    page.get_by_text("正文 https://example.com/one", exact=True).wait_for()
    assert page.get_by_role("tab", name="正文", exact=True).get_attribute("aria-selected") == "true"
    page.get_by_role("button", name="关闭 第二篇", exact=True).click()
    assert page.get_by_role("tab", name="第一篇", exact=True).get_attribute("aria-selected") == "true"
    page.get_by_role("button", name="关闭 来源", exact=True).click()
    assert page.get_by_role("tab", name="第一篇", exact=True).get_attribute("aria-selected") == "true"
    assert page.url == location
    assert len(calls) == 3, calls  # original one + extracted one + original two
    assert page.locator(".reader-frame").first.get_attribute("sandbox") == "allow-popups"
    page.screenshot(path=str(stack.log_dir / f"reader-tabs-{mobile}.png"))

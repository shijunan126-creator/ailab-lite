"""Responsive GenUI/composer regression and screenshots, using synthetic data only."""

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import select_model, wait_models

pytestmark = pytest.mark.e2e
OUT = Path("/tmp/ailab-visual")


def test_visual(logged_in, tmp_path):
    page = logged_in
    OUT.mkdir(exist_ok=True)
    wait_models(page)
    select_model(page, "test-fast")
    page.fill("textarea", "[[genui-demo]] 看看请求情况")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    page.locator(".genui-block[data-state='ok']").nth(2).wait_for(timeout=60000)
    page.wait_for_timeout(500)
    conv = page.url
    note = tmp_path / "季度销售数据汇总表.xlsx"
    note.write_bytes(b"x" * 2000)
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        for w, h in ((1440, 900), (390, 844), (320, 640)):
            page.set_viewport_size({"width": w, "height": h})
            page.goto(conv)
            page.locator(".genui-block[data-state='ok']").nth(2).wait_for(timeout=30000)
            page.wait_for_timeout(400)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(OUT / f"conv-{scheme}-{w}.png"), animations="disabled")
            page.fill("textarea", "这是一段比较长的输入，用来看输入框如何随文字增长：" * 6)
            page.set_input_files(".composer-box input[type=file]", str(note))
            page.wait_for_timeout(600)
            # a conversation's mode is fixed: its composer has no mode switch, only add, model and send
            assert page.get_by_role("radiogroup", name="模式").count() == 0
            for control in (page.get_by_role("button", name="添加附件", exact=True), page.get_by_role("button", name="模型", exact=True)):
                assert control.evaluate("""el => {
                    const r = el.getBoundingClientRect();
                    return r.left >= 0 && r.right <= innerWidth &&
                        document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('button') === el;
                }""")
            page.get_by_role("button", name="模型", exact=True).click()
            page.get_by_role("option").filter(has_text="Test Fast").click()
            page.locator(".composer-box").screenshot(path=str(OUT / f"composer-{scheme}-{w}.png"), animations="disabled")
            page.fill("textarea", "")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(page.url.split("/c/")[0] + "/")
        wait_models(page)
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / f"home-{scheme}.png"), animations="disabled")


def _escapes(page) -> list[str]:
    """Text boxes inside answers whose content is wider than the box (clipped or overflowing), as class names."""
    return page.evaluate("""() => [...document.querySelectorAll('.genui-block *')].filter(el => {
        if (el.closest('.gu-table, svg, .sr-only')) return false;
        const r = el.getBoundingClientRect(), p = el.parentElement.getBoundingClientRect();
        return r.width > 0 && (el.scrollWidth > el.clientWidth + 1 || r.right > p.right + 1 || r.left < p.left - 1);
    }).map(el => el.className.baseVal ?? el.className)""")


def test_genui_controls_recompute_in_place_and_send_their_values(logged_in):
    page = logged_in
    wait_models(page)
    select_model(page, "test-fast")
    page.fill("textarea", "[[genui-interactive]] 算一下费用")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    block = page.locator(".genui-block[data-state='ok']")
    block.wait_for(timeout=60000)
    total = block.locator(".gu-metric-number")
    expect(total).to_have_text("980")  # 20 * 49
    block.get_by_role("radio", name="专业版", exact=True).click()
    block.get_by_role("slider", name="人数").fill("60")
    block.get_by_role("switch").click()
    expect(total).to_have_text("5,049")  # 60 * 99 * 0.85, no new message
    expect(page.locator(".turn")).to_have_count(1)
    # pointer reads values out of the chart
    plot = block.locator(".gu-plot svg")
    box = plot.bounding_box()
    page.mouse.move(box["x"] + box["width"] - 14, box["y"] + box["height"] / 2)
    expect(block.locator(".gu-tip")).to_contain_text("10月")
    expect(block.locator(".gu-tip")).to_contain_text("26")
    block.get_by_role("button", name="新增").click()  # legend hides a series
    expect(block.get_by_role("button", name="新增")).to_have_attribute("aria-pressed", "false")
    block.get_by_role("button", name="按这个方案出报价单").click()
    expect(page.locator(".msg.user .bubble").last).to_have_text("按专业版、60 人出一份报价单")


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_genui_long_text_wraps_inside_its_box(logged_in, width):
    page = logged_in
    page.set_viewport_size({"width": width, "height": 900})
    page.reload()  # the phone layout (sidebar as a drawer) is chosen on load
    wait_models(page)
    select_model(page, "test-fast")
    page.fill("textarea", "[[genui-stress]] 长文本")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    page.locator(".genui-block[data-state='ok']").wait_for(timeout=60000)
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert _escapes(page) == []

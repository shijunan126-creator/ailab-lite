"""Real crawler + egress proxy (images/crawler, ADR 0021). Opt in with a disposable deployment:

AILAB_TEST_CRAWLER_URL=http://127.0.0.1:8090 AILAB_TEST_CRAWLER_TOKEN=... pytest tests/contracts/test_crawler.py
"""

from __future__ import annotations

import os

import httpx
import pytest

URL = os.environ.get("AILAB_TEST_CRAWLER_URL", "")
TOKEN = os.environ.get("AILAB_TEST_CRAWLER_TOKEN", "")
pytestmark = pytest.mark.skipif(not (URL and TOKEN), reason="AILAB_TEST_CRAWLER_URL / AILAB_TEST_CRAWLER_TOKEN not set")


def _read(url: str, token: str = TOKEN) -> httpx.Response:
    return httpx.post(
        f"{URL.rstrip('/')}/read", json={"url": url, "max_chars": 4000}, headers={"Authorization": f"Bearer {token}"}, timeout=60
    )


def test_public_page_comes_back_as_markdown():
    r = _read("https://example.com/")
    assert r.status_code == 200, r.text
    page = r.json()
    # Pruning may omit the heading; metadata and the retained article link identify this page.
    assert page["title"] == "Example Domain"
    assert page["status"] == 200 and page["url"] == "https://example.com/"
    assert "https://iana.org/help/example-domains" in page["markdown"]


def test_requests_without_the_token_are_refused():
    assert _read("https://example.com/", token="wrong").status_code == 401


@pytest.mark.parametrize("target", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/"])
def test_private_destinations_are_unreachable_even_when_asked_directly(target):
    # The Agent never sends these; the proxy must stop them anyway, because a page can redirect or load them.
    r = _read(target)
    assert r.status_code != 200, r.text

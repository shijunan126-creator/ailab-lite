"""Contracts against the installed Crawl4AI API; no browser or external network required."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import server
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


class ConfigurationTests(unittest.TestCase):
    def test_actual_package_accepts_the_browser_and_run_settings(self):
        with patch.object(server, "PROXY", "http://proxy.test:4750"):
            browser = server.browser_config()
        run = server.run_config()
        self.assertEqual(browser.proxy_config.server, "http://proxy.test:4750")
        self.assertFalse(browser.accept_downloads)
        self.assertFalse(browser.ignore_https_errors)
        self.assertEqual(run.cache_mode, server.CacheMode.BYPASS)
        self.assertEqual(run.page_timeout, server.PAGE_TIMEOUT_MS)


class ResponseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.result = SimpleNamespace(
            success=True,
            status_code=200,
            error_message=None,
            response_headers={"Content-Type": "text/html"},
            markdown=SimpleNamespace(
                fit_markdown="rendered article",
                raw_markdown="navigation and rendered article",
            ),
            redirected_url=None,
            url="https://example.com/",
            metadata={"title": "Title"},
            links={},
        )
        result = self.result

        class Crawler:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def arun(self, *args, **kwargs):
                return result

        self.patches = [
            patch.object(server, "AsyncWebCrawler", Crawler),
            patch.object(server, "TOKEN", "test-token"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        app = web.Application()
        app.router.add_post("/read", server.read)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def read(self):
        return await self.client.post(
            "/read",
            json={"url": "https://example.com/"},
            headers={"Authorization": "Bearer test-token"},
        )

    async def test_http_errors_are_not_returned_as_readable_articles(self):
        for status in (403, 404, 429, 500, 503):
            with self.subTest(status=status):
                self.result.status_code = status
                response = await self.read()
                self.assertEqual(response.status, 502)
                self.assertEqual((await response.json())["status"], status)

    async def test_success_uses_the_pruned_markdown(self):
        response = await self.read()
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["markdown"], "rendered article")

    async def test_missing_bearer_is_refused_before_rendering(self):
        response = await self.client.post("/read", json={"url": "https://example.com/"})
        self.assertEqual(response.status, 401)

    async def test_deadline_includes_browser_startup(self):
        closed = []

        class SlowStartup:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                await asyncio.Event().wait()
                return self

            async def __aexit__(self, *args):
                closed.append(True)

            async def arun(self, *args, **kwargs):
                return self_result

        self_result = self.result
        # The tiny total budget keeps this deterministic without a real browser.
        with (
            patch.object(server, "AsyncWebCrawler", SlowStartup),
            patch.object(server, "READ_TIMEOUT_SECONDS", 0.03, create=True),
        ):
            response = await asyncio.wait_for(self.read(), 0.5)
        self.assertEqual(response.status, 504)
        self.assertEqual(closed, [True])

    async def test_challenge_and_empty_pages_are_not_articles(self):
        for title, text in (
            ("Just a moment...", "Checking your browser"),
            ("Title", ""),
        ):
            self.result.metadata = {"title": title}
            self.result.markdown = SimpleNamespace(fit_markdown=text, raw_markdown=text)
            response = await self.read()
            self.assertEqual(response.status, 502)

    async def test_article_about_captcha_is_preserved(self):
        self.result.metadata = {"title": "CAPTCHA and access denied explained"}
        response = await self.read()
        self.assertEqual(response.status, 200)

    async def test_health_refuses_exhausted_process_budget(self):
        def read_counter(path):
            return "256" if str(path).endswith("pids.max") else "257"

        with patch.object(server.Path, "read_text", read_counter):
            response = await server.health(None)
        self.assertEqual(response.status, 503)


if __name__ == "__main__":
    unittest.main()

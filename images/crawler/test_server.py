"""Installed package contracts and Linux worker lifecycle regression tests."""

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import render
import server
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


class ConfigurationTests(unittest.TestCase):
    def test_default_concurrency_fits_the_one_gib_deployment(self):
        self.assertEqual(server.MAX_PAGES, 1)

    def test_actual_package_accepts_browser_and_run_settings(self):
        with patch.object(render, "PROXY", "http://proxy.test:4750"):
            browser = render.browser_config()
        run = render.run_config()
        self.assertEqual(browser.proxy_config.server, "http://proxy.test:4750")
        self.assertFalse(browser.accept_downloads)
        self.assertFalse(browser.ignore_https_errors)
        self.assertEqual(run.cache_mode, render.CacheMode.BYPASS)
        self.assertEqual(run.page_timeout, render.PAGE_TIMEOUT_MS)


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.result = SimpleNamespace(
            success=True,
            status_code=200,
            error_message=None,
            response_headers={"Content-Type": "text/html"},
            markdown=SimpleNamespace(fit_markdown="rendered article", raw_markdown="navigation and rendered article"),
            redirected_url=None,
            url="https://example.com/",
            metadata={"title": "Title"},
            links={},
        )

    def extract(self):
        return render.extract_result(self.result, 4000, time.monotonic())

    def test_http_errors_are_not_articles(self):
        for status in (403, 404, 429, 500, 503):
            self.result.status_code = status
            result = self.extract()
            self.assertEqual(result["status"], 502)
            self.assertEqual(result["body"]["status"], status)

    def test_success_uses_pruned_markdown(self):
        self.assertEqual(self.extract()["body"]["markdown"], "rendered article")

    def test_challenge_and_empty_pages_are_not_articles(self):
        for title, text in (("Just a moment...", "Checking your browser"), ("Title", "")):
            self.result.metadata = {"title": title}
            self.result.markdown = SimpleNamespace(fit_markdown=text, raw_markdown=text)
            self.assertEqual(self.extract()["status"], 502)

    def test_article_about_captcha_is_preserved(self):
        self.result.metadata = {"title": "CAPTCHA and access denied explained"}
        self.assertEqual(self.extract()["status"], 200)

    def test_html_is_opt_in_and_bounded_without_truncating_a_snapshot(self):
        self.result.html = "<h1>正文</h1>"
        self.assertNotIn("html", self.extract()["body"])
        result = render.extract_result(self.result, 4000, time.monotonic(), include_html=True)
        self.assertEqual(result["body"]["html"], self.result.html)
        with patch.object(render, "MAX_SNAPSHOT_BYTES", 10):
            self.assertEqual(render.extract_result(self.result, 4000, time.monotonic(), include_html=True)["status"], 502)


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patch = patch.object(server, "TOKEN", "test-token")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        app = web.Application()
        app.router.add_post("/read", server.read)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def read(self):
        return await self.client.post("/read", json={"url": "https://example.com/"}, headers={"Authorization": "Bearer test-token"})

    async def test_missing_bearer_is_refused_before_launch(self):
        with patch.object(server, "worker_command", side_effect=AssertionError("must not launch")):
            response = await self.client.post("/read", json={"url": "https://example.com/"})
        self.assertEqual(response.status, 401)

    async def test_capacity_is_refused_before_launch(self):
        async with server.slots:
            with patch.object(server, "worker_command", side_effect=AssertionError("must not launch")):
                response = await self.read()
        self.assertEqual(response.status, 503)

    async def test_success_returns_result_and_reaps_worker(self):
        command = [
            sys.executable,
            "-c",
            "import sys,json;sys.stdin.readline();"
            'print(json.dumps({"status":200,"body":{"markdown":"article"}}),flush=True);sys.stdin.read()',
        ]
        with patch.object(server, "worker_command", lambda: command):
            response = await self.read()
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["markdown"], "article")

    @unittest.skipUnless(sys.platform == "linux", "Linux process tree supervisor")
    async def test_timeout_kills_detached_orphan_without_browser_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child-pid"
            orphan = (
                "import os,time;from pathlib import Path;\n"
                "if os.fork(): os._exit(0)\n"
                f"os.setsid();Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)"
            )
            command = [
                sys.executable,
                "-c",
                "import ctypes,subprocess,sys,time;ctypes.CDLL(None).prctl(36,1,0,0,0);"
                f"subprocess.run([sys.executable,'-c',{orphan!r}]);time.sleep(60)",
            ]
            with patch.object(server, "worker_command", lambda: command), patch.object(server, "READ_TIMEOUT_SECONDS", 0.3):
                response = await asyncio.wait_for(self.read(), 3)
            self.assertEqual(response.status, 504)
            self.assertTrue(marker.exists(), "fault must actually create the child")
            child = int(marker.read_text())
            # The container init may need an event-loop turn to reap the killed orphan.
            for _ in range(50):
                if not Path(f"/proc/{child}").exists():
                    break
                await asyncio.sleep(0.01)
            self.assertFalse(Path(f"/proc/{child}").exists(), f"child {child} leaked")

    @unittest.skipUnless(sys.platform == "linux", "Linux process tree supervisor")
    async def test_cancelled_request_reaps_its_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "worker-pid"
            command = [
                sys.executable,
                "-c",
                f"import os,time;from pathlib import Path;Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)",
            ]
            with patch.object(server, "worker_command", lambda: command):
                task = asyncio.create_task(server.render_page("https://example.com/", 4000))
                for _ in range(100):
                    if marker.exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(marker.exists())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            self.assertFalse(Path(f"/proc/{int(marker.read_text())}").exists())

    async def test_health_refuses_exhausted_process_budget(self):
        def read_counter(path):
            return "256" if str(path).endswith("pids.max") else "257"

        with patch.object(server.Path, "read_text", read_counter):
            response = await server.health(None)
        self.assertEqual(response.status, 503)


if __name__ == "__main__":
    unittest.main()

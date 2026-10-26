# 2026-10-26 read_url via Crawl4AI

Branch `feat/crawl4ai-reader`, base `origin/main@2233870`. Decision: [ADR 0021](../../architecture/adr/0021-isolated-browser-rendering-for-read-url.md).

## Done

- `images/crawler`: Crawl4AI 0.7.4 + Chromium behind `POST /read` (bearer token, fresh browser per request, ≤2 concurrent, pruned Markdown, ≤50 links, non-text refused).
- `ailab_platform/egress.py`: CONNECT/HTTP proxy reusing `web.public_addresses`; the crawler's only route out.
- `web.read_url`: `AILAB_WEB_READ_PROVIDER=static|crawl4ai`; crawler errors are tool errors, no fallback; metric `ailab_web_read_seconds{provider,outcome}`. Secure profile checks for URL/token.
- Removed `GET /v1/reader` JSON, `web.read_page`, `ailab_platform/reader.py` and their tests; `/v1/reader/render` kept.
- k8s base: `deploy/base/crawler` (crawler + egress, NetworkPolicies, limits), config, `platform-crawler-token` secret in `k8s_secrets.sh`, crawler image in `deploy.sh`.
- Tests written: `test_web_crawler.py`, `test_egress.py`, `faults/test_crawler_down.py`, `contracts/test_crawler.py` (opt-in), deployment policy test.

## Not done / validation

- Per user instruction no tests, builds or image builds were run; only `ruff format`. CI on the PR is the first run.
- The Crawl4AI version pin, its license terms (Apache-2.0 plus an attribution request in recent releases) and the BrowserConfig/CrawlerRunConfig argument names are from memory and must be checked when the image is first built.
- Not deployed. The personal Compose deployment needs the crawler and egress containers and `AILAB_WEB_READ_PROVIDER=crawl4ai`; until then it keeps the static reader.

# 2026-10-26 read_url: Trafilatura first, Crawl4AI when weak

Branch `feat/crawl4ai-reader`, base `origin/main@2233870`. Decision: [ADR 0021](../../architecture/adr/0021-isolated-browser-rendering-for-read-url.md).

## Done

- First version rendered every page in the crawler. Per user review (Crawl4AI too heavy for every read) it is now two tiers:
  httpx + Trafilatura 2.3.1 → Markdown; only short text, app shells or 403/429/503 go to the crawler.
- `images/crawler`: Crawl4AI 0.7.4 + Chromium behind `POST /read` (bearer token, fresh browser per request, ≤2 concurrent).
- `ailab_platform/egress.py`: CONNECT/HTTP proxy reusing `web.public_addresses`; the crawler's only route out.
- Render failure with static text → static text + `note`; without → tool error. Metric `ailab_web_read_seconds{path,outcome}`.
- Removed: `GET /v1/reader` JSON, `web.read_page`, `ailab_platform/reader.py`, the old flat-text `extract_html` and the
  short-lived `web_read_provider` switch. `/v1/reader/render` kept.
- k8s base: `deploy/base/crawler` (crawler + egress, NetworkPolicies, limits), `AILAB_CRAWLER_URL`, `platform-crawler-token`
  secret, crawler image in `deploy.sh`. `constraints.txt` gained Trafilatura and its dependencies (and the missing rapidfuzz pin)
  without changing existing pins.
- Tests written: `test_web_crawler.py`, `test_web.py` (extract_markdown), `test_egress.py`, `faults/test_crawler_down.py`,
  `contracts/test_crawler.py` (opt-in), deployment policy test.

## Not done / validation

- Per user instruction no tests, builds or image builds were run; only `ruff format` and dependency locking. CI on the PR is
  the first run. Assertions on Trafilatura's output for tiny fixtures may need adjusting.
- Crawl4AI pin, its license terms (Apache-2.0 plus an attribution request) and its config argument names are from memory;
  check on the first image build.
- Not deployed. The personal Compose deployment needs the crawler and egress containers and `AILAB_CRAWLER_URL`; without them
  it still gets the Trafilatura tier.

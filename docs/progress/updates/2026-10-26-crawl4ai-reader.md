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

## Follow-up validation and merge authorization

User explicitly authorized both PRs to be pushed, made green and squash-merged; no deployment. Original head `5dbe7bf3408bd4730eb046eeddd32ad08d6dda4e` passed all eight jobs in [CI 40310854393](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40310854393). The branch now includes `main@ddaf756`; this section supersedes the earlier no-validation handoff.

- Installed the exact crawler requirements in an isolated disposable Python 3.12 environment; `BrowserConfig` and `CrawlerRunConfig` accept the actual server arguments in Crawl4AI 0.7.4.
- A real native Chromium + this branch's egress proxy exposed a cause-level bug: Crawl4AI reports `success=True` for HTTP error pages. The server now rejects status >=400 instead of delivering the proxy's 403 page as article content. No private target was reached: the proxy refused all three destinations, but the old server misrepresented that refusal.
- Added four tests against the installed package API, auth, Markdown output and HTTP errors (five statuses). The unchanged old server fails the new TLS assertion and all five HTTP-error subcases; the fixed server passes all four tests. The Dockerfile runs these tests at build time. TLS validation is explicit and browser init logging is disabled.
- Real crawler+proxy contract: **5 passed**, covering public HTTPS Markdown/title/link, bad bearer, loopback, metadata and RFC1918 refusal. The public assertion checks the retained article link and metadata because the pruning algorithm drops the heading; no product extraction change was made for the fixture.
- Cluster policy originally blocked resolution of `platform-egress`; allow only kube-system/kube-dns TCP+UDP 53 in addition to the proxy connection. Updated the policy regression and ADR; real NetworkPolicy enforcement is not tested here.
- The installed wheel's license contains Apache-2.0 plus a mandatory Attribution Requirement. Full license and required NOTICE are included in the image, with the exact credit in root README. Sources: [versioned LICENSE](https://github.com/unclecode/crawl4ai/blob/v0.7.4/LICENSE), [PyPI 0.7.4](https://pypi.org/project/crawl4ai/0.7.4/). This corrects the earlier “attribution request” description.
- Current local web/extraction/fault/egress/deployment tests: **39 passed**. Ruff checks and formatting include crawler sources and pass. Tested baseline: `5dbe7bf` + `ddaf756` + the follow-up diff in this commit. Full dual-DB, storage and Portal/E2E acceptance will use the new exact-head CI, not the original run.
- Residual: no Linux image build, Kubernetes rollout/NetworkPolicy enforcement or target 2-vCPU/8-GB resource measurement. Dependencies beneath the two top-level pins remain unpinned. Native contract success is not container isolation proof. Two concurrent browsers are not proven to fit 1 GiB; the ADR no longer claims they do. No production deployment or personal configuration was changed.

Next: exact-head eight-check CI, blocking-review and mergeability checks, then `gh pr merge 94 --squash --match-head-commit <head>`.

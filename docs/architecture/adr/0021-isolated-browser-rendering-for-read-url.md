# ADR 0021 · Isolated browser rendering for read_url

Status: proposed (2026-10-26). Implemented on `feat/crawl4ai-reader`; not deployed, not accepted against a real crawler.

## Context

`read_url` fetched a page with the Agent's bounded public-only httpx client and flattened the HTML to text (ADR 0007). Pages that render their content with JavaScript came back empty, and the flat text kept navigation, footers and share widgets around the article, so the model read more noise and less of what it was asked about. The user asked for Crawl4AI, which renders the page in headless Chromium and returns the main content as Markdown (headings, lists, tables, code kept).

That changes the threat model ADR 0007 relied on: "read only, no page scripts". A browser runs the page's JavaScript, resolves names itself, follows redirects and loads subresources from any host the page names. The Agent's URL check sees only the first URL.

## Decision

1. **Rendering happens in its own container, never in the Agent.** `images/crawler` is python + pinned `crawl4ai` + Playwright Chromium behind one endpoint, `POST /read {url, max_chars}`, with a bearer token shared only with the Agent. It holds no platform credentials, no service-account token, runs non-root with a read-only root filesystem, 1 CPU / 1 GiB. It is infrastructure like ClamAV/SearXNG, not a sixth business service; the official all-in-one Crawl4AI server (LLM endpoints, playground) is not used.
2. **A fresh browser per request**, at most 2 at once (busy → 503). No cookies or storage survive between pages and memory returns when the read ends. Downloads are off; non-text responses are refused ("use download_url for files").
3. **Every connection the browser makes goes through `platform-egress`** (`ailab_platform/egress.py`): CONNECT to 443 or absolute-form HTTP to 80, each destination resolved by the proxy with `web.public_addresses` — the same "every DNS answer public, connect to the checked address" rule as the Agent's own fetches. NetworkPolicy is the second layer: the crawler may reach only the proxy (it needs no DNS), the proxy may reach only DNS and public 80/443 (RFC1918, CGNAT, loopback and link-local excluded). The proxy never logs destinations.
4. **The Agent keeps every existing control before calling the crawler**: provenance (`net_tools.check_source`), the per-run request budget (one read = one request), `_validate_url`, and a DNS check of the first host. GitHub issues/PRs keep the REST path.
5. **`AILAB_WEB_READ_PROVIDER=static|crawl4ai`**, validated; `static` (the old path) stays for development and tests. A crawler failure (down, timeout, busy, render error) is a defined tool error. It never falls back to the static fetch, so a broken crawler is visible instead of silently returning worse content. Secure profile refuses `crawl4ai` without a crawler URL and a token of at least 24 characters.
6. **Output keeps the old shape** (`url, title, text, content_type, truncated, links`), so the model contract does not change. `text` is Crawl4AI's pruned Markdown, falling back to the full Markdown only when pruning leaves nothing.

The JSON `GET /v1/reader` (extracted blocks for the old 正文 view, unused by the Portal since PR #87) and `ailab_platform/reader.py` are removed. `GET /v1/reader/render` (the Portal's original-page iframe) is unchanged and stays script-free.

## Consequences

- A read now costs a browser start plus a render: roughly 2–6 s instead of 0.3–2 s, and up to ~400–600 MiB RSS while a page renders (Chromium + renderer), ~60 MiB idle (Python server only). Two concurrent reads stay inside the 1 GiB limit. Image ≈ 1.5–2 GB (Chromium and its system libraries). These are estimates; they have not been measured on the target 2 vCPU / 8 GB machine.
- Residual: page JavaScript can make further requests to public hosts. The browser only ever receives the URL, never conversation data, so this is not a new exfiltration channel beyond the URL itself (already bounded by provenance). Per-page subresource bytes are bounded by time (20 s page timeout, 120 s proxy tunnel deadline), not by a byte cap.
- IPv6: the proxy's address rule covers IPv6 forms; the NetworkPolicy's second layer is IPv4-only, matching the validated single-stack lite node.
- Not yet accepted: real crawler + proxy contract (`tests/contracts/test_crawler.py`), target-machine latency/RSS, the lite overlay deployment, and the personal Compose deployment, which needs the two new containers added before release.

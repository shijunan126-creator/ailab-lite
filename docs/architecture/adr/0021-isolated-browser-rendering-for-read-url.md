# ADR 0021 · read_url: Trafilatura first, isolated browser rendering when that is weak

Status: proposed (2026-10-26). Implemented on `feat/crawl4ai-reader`; not deployed, not accepted against a real crawler.

## Context

`read_url` fetched a page with the Agent's bounded public-only httpx client and flattened the HTML to text (ADR 0007). The flat text kept navigation, footers and share widgets around the article, and pages that render their content with JavaScript came back empty.

Most pages carry their article in the HTML the server sends; a good extractor fixes those without a browser. Only app shells (React/Vue SPAs), bot walls (Cloudflare challenges) and content loaded by script need one, and a browser is heavy (Chromium, hundreds of MiB per render) and changes the threat model ADR 0007 relied on, "read only, no page scripts": it runs the page's JavaScript, resolves names itself, follows redirects and loads subresources from any host the page names. The Agent's URL check sees only the first URL.

## Decision

0. **Two tiers.** The Agent fetches with its existing bounded client and extracts the main content with Trafilatura as Markdown (tables and formatting kept, chrome dropped), in a thread. That result is used unless it is weak: under 200 characters, an app-shell page under 1500 characters (empty `#root`/`#app`/`#__next` mount point, "enable JavaScript", Cloudflare challenge markers), or the fetch was answered 403/429/503. Only then is the page sent to the crawler below. If rendering fails and the plain fetch had text, that text is returned with a note that it may be incomplete; if it had none, the crawler's error is the tool error. With no crawler configured (`AILAB_CRAWLER_URL` empty) weak pages return what the fetch found.
1. **Rendering happens in its own container, never in the Agent.** `images/crawler` is python + pinned `crawl4ai` + Playwright Chromium behind one endpoint, `POST /read {url, max_chars}`, with a bearer token shared only with the Agent. It holds no platform credentials, no service-account token, runs non-root with a read-only root filesystem, 1 CPU / 1 GiB. It is infrastructure like ClamAV/SearXNG, not a sixth business service; the official all-in-one Crawl4AI server (LLM endpoints, playground) is not used.
2. **A fresh browser per request**, at most 2 at once (busy → 503). No cookies or storage survive between pages and memory returns when the read ends. Downloads are off; non-text responses are refused ("use download_url for files").
3. **Every connection the browser makes goes through `platform-egress`** (`ailab_platform/egress.py`): CONNECT to 443 or absolute-form HTTP to 80, each destination resolved by the proxy with `web.public_addresses` — the same "every DNS answer public, connect to the checked address" rule as the Agent's own fetches. NetworkPolicy is the second layer: the crawler may reach only the proxy (it needs no DNS), the proxy may reach only DNS and public 80/443 (RFC1918, CGNAT, loopback and link-local excluded). The proxy never logs destinations.
4. **The Agent keeps every existing control before calling the crawler**: provenance (`net_tools.check_source`), the per-run request budget (one read = one request), `_validate_url`, and a DNS check of the first host. GitHub issues/PRs keep the REST path.
5. **Failures are visible.** Crawler down, timeout, busy or render error are defined tool errors, or — when the plain fetch had text — that text with an explicit `note`. The metric `ailab_web_read_seconds{path=static|rendered|static_fallback,outcome}` shows how often rendering is needed and fails. Secure profile refuses a crawler URL without a token of at least 24 characters.
6. **Output keeps the old shape** (`url, title, text, content_type, truncated, links`, plus `note` when partial), so the model contract does not change. The old flat-text extractor (`extract_html`) is removed.

The JSON `GET /v1/reader` (extracted blocks for the old 正文 view, unused by the Portal since PR #87) and `ailab_platform/reader.py` are removed. `GET /v1/reader/render` (the Portal's original-page iframe) is unchanged and stays script-free.

## Consequences

- Most reads stay on the fast path: one fetch plus Trafilatura (~50–300 ms CPU for a typical page, a few tens of MiB transient in the Agent; Trafilatura and lxml add ~30 MiB to the Agent's resident size). Parsing untrusted HTML in the Agent is not new (the old extractor did it); it runs with the same byte cap.
- A rendered read costs a browser start plus a render: roughly 2–6 s, up to ~400–600 MiB RSS in the crawler while a page renders, ~60 MiB idle. Two concurrent renders stay inside the 1 GiB limit. Crawler image ≈ 1.5–2 GB. These are estimates; they have not been measured on the target 2 vCPU / 8 GB machine, nor has the share of pages that need rendering.
- Residual: page JavaScript can make further requests to public hosts. The browser only ever receives the URL, never conversation data, so this is not a new exfiltration channel beyond the URL itself (already bounded by provenance). Per-page subresource bytes are bounded by time (20 s page timeout, 120 s proxy tunnel deadline), not by a byte cap.
- IPv6: the proxy's address rule covers IPv6 forms; the NetworkPolicy's second layer is IPv4-only, matching the validated single-stack lite node.
- Not yet accepted: real crawler + proxy contract (`tests/contracts/test_crawler.py`), target-machine latency/RSS, the lite overlay deployment, and the personal Compose deployment, which needs the two new containers added before release.

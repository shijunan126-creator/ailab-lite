"""Safe external web research for the Agent.

The sandbox has no egress. This module runs in the Agent process (outside the
sandbox): it searches with a private SearXNG instance and turns public pages
into Markdown: its own bounded fetch + Trafilatura, and only when that comes
back weak, the isolated crawler's browser rendering (ADR 0021). The model only
sees the text and source URLs; the sandbox never gets a network route.

Security rules:
* only http(s), default ports 80/443, no credentials in URLs;
* every hostname is resolved and every address must be globally routable
  (rejects loopback, RFC1918, link-local, CGNAT, cloud metadata, and IPv6 forms
  that embed one of those: mapped, NAT64, 6to4, Teredo);
* the connection goes to the address that was checked, never a second lookup,
  so a DNS answer that changes between check and connect cannot reach inside;
* redirects are followed manually and validated again;
* responses are bounded by time and bytes, and only text-like content types
  are accepted;
* no cookies, no auth headers, no environment proxy.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import re
import socket
import ssl
import tempfile
import time
from collections.abc import AsyncIterator
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx
import trafilatura

from . import metrics
from .config import settings

USER_AGENT = "AILab-lite/0.1 (safe web research; https://github.com/shijunan126-creator/ailab-lite)"
MAX_REDIRECTS = 3
MAX_LINKS = 40
TEXT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
}


class WebError(RuntimeError):
    pass


class WebStatusError(WebError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class WebFetchError(WebError):
    """A transport failure that a separate browser connection may recover from."""


def enabled() -> bool:
    return settings.web_search_provider in ("searxng", "mock")


NAT64 = ipaddress.ip_network("64:ff9b::/96")


def is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not ip.is_global or ip.is_multicast:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = [ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None]
        if ip in NAT64:
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        return all(v4 is None or is_public(v4) for v4 in embedded)
    return True


async def public_addresses(host: str, port: int) -> list[str]:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise WebError(f"cannot resolve host: {host}") from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise WebError(f"cannot resolve host: {host}")
    for address in addresses:
        # Every answer must be public: picking the good one of a mixed set lets a later lookup pick the other.
        if not is_public(ipaddress.ip_address(address.split("%")[0])):
            raise WebError(f"refusing non-public address: {host} -> {address}")
    return addresses


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Resolves and checks the host itself, then connects to that address. TLS still verifies the hostname."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        last: Exception | None = None
        for address in await public_addresses(host, port):
            try:
                return await self._inner.connect_tcp(address, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise last or WebError(f"cannot connect to {host}")

    async def connect_unix_socket(self, *args, **kwargs):
        raise WebError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def public_client(timeout: float, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    """An HTTP client that can only reach public addresses: no proxy, no cookies kept across tools, no redirects."""
    transport = httpx.AsyncHTTPTransport(retries=0)
    # httpx has no public hook for the network backend; replacing the pool keeps its request mapping intact.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(), max_connections=4, network_backend=_PublicOnlyBackend(), http1=True, http2=False
    )
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        follow_redirects=False,
        trust_env=False,
    )


@contextlib.asynccontextmanager
async def open_public(client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None) -> AsyncIterator[httpx.Response]:
    """GET a public URL, following at most MAX_REDIRECTS redirects, each one validated like the first."""
    current = _validate_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            async with client.stream("GET", current, headers=headers) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise WebError("redirect without location")
                    current = _validate_url(urljoin(current, location))
                    continue
                if resp.status_code >= 400:
                    raise WebStatusError(resp.status_code, f"HTTP {resp.status_code} from {current}")
                yield resp
                return
        except WebError:
            raise
        except httpx.HTTPError as exc:
            cause = exc
            while cause is not None:
                if isinstance(cause, ssl.SSLCertVerificationError):
                    raise WebError(f"TLS certificate verification failed for {current}") from exc
                cause = cause.__cause__ or cause.__context__
            raise WebFetchError(f"could not fetch {current}: {type(exc).__name__}: {exc}") from exc
    raise WebError("too many redirects")


async def _read_bounded(resp: httpx.Response, limit: int, deadline: float | None = None) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        if deadline is not None and time.monotonic() >= deadline:
            # The read timeout only bounds silence between chunks; a peer trickling bytes
            # forever needs a wall-clock budget too (the same trap the internal download closes).
            raise WebFetchError("response did not finish within the transfer deadline")
        if total + len(chunk) >= limit:
            chunks.append(chunk[: limit - total])
            return b"".join(chunks), True
        total += len(chunk)
        chunks.append(chunk)
    return b"".join(chunks), False


async def get_json(url: str, max_bytes: int, headers: dict[str, str] | None = None):
    async with public_client(settings.web_fetch_timeout_seconds, {"Accept": "application/json", **(headers or {})}) as client:
        async with open_public(client, url) as resp:
            body, truncated = await _read_bounded(resp, max_bytes, deadline=time.monotonic() + settings.web_transfer_deadline_seconds)
    if truncated:
        raise WebError(f"response from {url} is larger than {max_bytes} bytes")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise WebError(f"invalid JSON from {url}") from exc


@contextlib.asynccontextmanager
async def download(url: str, max_bytes: int) -> AsyncIterator[dict]:
    """Stream a public resource to a private temp file (one copy on disk, one chunk in memory); removed on exit.

    The bytes are never parsed here: they go to the sandbox, where untrusted content is opened.
    """
    with tempfile.TemporaryDirectory(prefix="ailab-fetch-") as tmp:
        target = Path(tmp) / "body"
        async with public_client(settings.web_download_timeout_seconds) as client:
            async with open_public(client, url) as resp:
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise WebError(f"download is {declared} bytes; the limit is {max_bytes}")
                digest, size = hashlib.sha256(), 0
                deadline = time.monotonic() + settings.web_transfer_deadline_seconds
                with target.open("wb") as out:
                    async for chunk in resp.aiter_bytes():
                        if time.monotonic() >= deadline:
                            # The read timeout bounds silence between chunks, not total duration;
                            # a server trickling bytes must not hold the fetch open forever.
                            raise WebError("download did not finish within the transfer deadline")
                        size += len(chunk)
                        if size > max_bytes:
                            raise WebError(f"download exceeds {max_bytes} bytes")
                        digest.update(chunk)
                        out.write(chunk)
                final_url = str(resp.url)
                ctype = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip().lower()
                disposition = resp.headers.get("content-disposition") or ""
        yield {
            "path": target,
            "url": final_url,
            "size": size,
            "sha256": digest.hexdigest(),
            "content_type": ctype,
            "disposition": disposition,
        }


def _validate_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw.strip())
    except ValueError as exc:
        raise WebError(f"invalid URL: {raw[:120]}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebError("only public http(s) URLs are allowed")
    if parsed.username or parsed.password:
        raise WebError("URLs with embedded credentials are not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise WebError("private hostnames are not allowed")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise WebError("invalid port") from exc
    if port not in (80, 443):
        raise WebError("only ports 80 and 443 are allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not is_public(ip):
        raise WebError(f"refusing non-public address: {host}")
    # Remove fragment. Keep the original path/query; urlsplit already decoded nothing.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _Links(HTMLParser):
    """The page's outgoing links: read_url returns them so the model may follow one (net_tools provenance)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "a" and len(self.links) < MAX_LINKS * 4 and (href := dict(attrs).get("href")):
            self.links.append(href)


def _links(html: str, base: str) -> list[str]:
    parser = _Links()
    with contextlib.suppress(Exception):  # malformed HTML only costs the links
        parser.feed(html)
    out: list[str] = []
    for href in parser.links:
        url = urljoin(base, href.strip()).split("#")[0]
        if url.startswith(("http://", "https://")) and len(url) <= 2000 and url not in out:
            out.append(url)
        if len(out) >= MAX_LINKS:
            break
    return out


def extract_markdown(html: str, base: str = "") -> tuple[str, str, list[str]]:
    """Title, main content as Markdown (navigation, footers and boilerplate dropped by Trafilatura) and links.
    CPU-bound: callers run it in a thread."""
    try:
        text = trafilatura.extract(
            html,
            url=base or None,
            output_format="markdown",
            include_tables=True,
            include_formatting=True,
            include_links=False,
            include_images=False,
            include_comments=False,
            favor_recall=True,
        )
        meta = trafilatura.extract_metadata(html, default_url=base or None)
    except Exception as exc:  # malformed HTML must not fail the whole tool
        raise WebError(f"could not parse HTML: {type(exc).__name__}") from exc
    title = (meta.title if meta and meta.title else "") or ""
    if not title and (m := re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)):
        title = unescape(m[1])
    return _clean(title)[:300], _markdown(text or ""), _links(html, base)


def _markdown(text: str) -> str:
    # Keep indentation and table spacing, which _clean would flatten.
    return re.sub(r"\n{3,}", "\n\n", text.replace("\xa0", " ").replace("\u200b", "")).strip()


# A static fetch is good enough for most pages. These signs mean the content needs a browser: too
# little text, an app shell waiting for its scripts, or a bot wall answering instead of the site.
MIN_TEXT = 200
SCRIPT_SHELL = re.compile(
    r"<div[^>]+id=[\"'](?:root|app|__next|__nuxt|q-app)[\"'][^>]*>\s*</div>|enable javascript|启用\s*javascript|"
    r"cf-chl|challenge-platform|just a moment\.\.\.",
    re.I,
)
RENDER_STATUSES = {403, 408, 412, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
CHALLENGE_TITLE = re.compile(
    r"^(?:just a moment[.!…]*|attention required[!！]?(?:\s*\|\s*cloudflare)?|access denied|verify (?:that )?you are human)[.!…\s]*$",
    re.I,
)


def _weakness(html: str, text: str) -> str | None:
    if not text or (len(text) < MIN_TEXT and re.search(r"<script\b", html, re.I)):
        return "short"
    if len(text) < 1500 and SCRIPT_SHELL.search(html):
        return "script"
    return None


async def search(query: str, count: int | None = None) -> list[dict]:
    query = query.strip()[:300]
    if not query:
        raise WebError("query is required")
    n = max(1, min(int(count or settings.web_max_results), 8))
    provider = settings.web_search_provider
    if provider == "mock":
        return [
            {
                "title": f"{query} — result {i + 1}",
                "url": f"https://example.com/{i + 1}",
                "snippet": f"Mock result for {query}",
                "engine": "mock",
            }
            for i in range(n)
        ]
    if provider != "searxng":
        raise WebError("web search is not configured")

    base = settings.searxng_url.rstrip("/")
    params = {"q": query, "format": "json", "language": "zh-CN", "safesearch": "1"}
    try:
        async with httpx.AsyncClient(
            timeout=settings.web_search_timeout_seconds,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            trust_env=False,
        ) as client:
            resp = await client.get(f"{base}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise WebError(f"search service unavailable: {type(exc).__name__}: {exc}") from exc

    out: list[dict] = []
    for item in data.get("results") or []:
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        out.append(
            {
                "title": str(item.get("title") or url)[:300],
                "url": url[:2000],
                "snippet": str(item.get("content") or "")[:500],
                "engine": str(item.get("engine") or "")[:50],
            }
        )
        if len(out) >= n:
            break
    return out


GITHUB_ISSUE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(?:issues|pull)/(\d+)/?$")
GITHUB_API_BYTES = 4 * 1024 * 1024
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


async def read_github_issue(owner: str, repo: str, number: str, limit: int) -> dict:
    """An issue or PR with its comments, from the public REST API (no token: public data only)."""
    base = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    issue = await get_json(base, GITHUB_API_BYTES, GITHUB_HEADERS)
    parts = [
        f"# {issue.get('title') or ''}",
        f"{(issue.get('user') or {}).get('login', '?')} · {issue.get('state', '')} · {issue.get('created_at', '')}",
        str(issue.get("body") or ""),
    ]
    used, truncated, page = sum(map(len, parts)), False, 1
    while int(issue.get("comments") or 0) and not truncated and page <= 5:
        comments = await get_json(f"{base}/comments?per_page=100&page={page}", GITHUB_API_BYTES, GITHUB_HEADERS)
        if not isinstance(comments, list) or not comments:
            break
        for c in comments:
            who = (c.get("user") or {}).get("login", "?")
            text = f"\n---\n{who} · {c.get('created_at', '')} · {c.get('html_url', '')}\n{c.get('body') or ''}"
            if used + len(text) > limit:
                truncated = True
                break
            parts.append(text)
            used += len(text)
        if len(comments) < 100:
            break
        page += 1
    text = _clean("\n".join(parts))
    if len(text) > limit:
        text, truncated = text[:limit].rstrip() + "\n...[truncated]", True
    return {
        "url": issue.get("html_url") or f"https://github.com/{owner}/{repo}/issues/{number}",
        "title": str(issue.get("title") or "")[:300],
        "text": text,
        "content_type": "application/vnd.github+json",
        "truncated": truncated,
        "comments": int(issue.get("comments") or 0),
        "links": [],
    }


async def fetch_html(url: str) -> dict:
    """One bounded public fetch of a text-like page: raw decoded text, nothing extracted. The
    Agent's read_url and the Portal's original-page view share it — same caps, one fetch per click."""
    current = _validate_url(url)
    headers = {
        "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    async with public_client(settings.web_fetch_timeout_seconds, headers) as client:
        async with open_public(client, current) as resp:
            final = str(resp.url)
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype and ctype not in TEXT_TYPES:
                raise WebError(f"unsupported content type: {ctype}; use download_url for files")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > settings.web_max_bytes:
                raise WebError(f"page is too large ({declared} bytes)")
            body, truncated = await _read_bounded(
                resp, settings.web_max_bytes, deadline=time.monotonic() + settings.web_transfer_deadline_seconds
            )
            encoding = resp.encoding or "utf-8"
    text = body.decode(encoding, "replace")
    title = ""
    if m := re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S):
        title = _clean(unescape(m[1]))[:300]
    return {
        "url": final,
        "title": title,
        "text": text,
        "content_type": ctype or "text/html",
        "truncated": truncated,
    }


async def read_url(url: str, max_chars: int | None = None) -> dict:
    """httpx + Trafilatura first; a weak result (or a bot wall) is rendered by the isolated crawler (ADR 0021)."""
    current = _validate_url(url)
    limit = max(1000, min(int(max_chars or settings.web_max_chars), 60_000))
    issue = GITHUB_ISSUE.match(current)
    if issue:
        return await read_github_issue(*issue.groups(), limit)
    started = time.monotonic()
    path, outcome = "static", "error"
    try:
        try:
            page, weak = await _read_static(current, limit)
        except WebStatusError as exc:
            if exc.status not in RENDER_STATUSES or not settings.crawler_url:
                raise
            page, weak = None, "blocked"
        except WebFetchError:
            if not settings.crawler_url:
                raise
            page, weak = None, "transport"
        if weak is None or not settings.crawler_url:
            if page is None or not page["text"]:
                raise WebError("page had no readable text")
            outcome = "ok"
            return page
        path = "rendered"
        try:
            rendered = await _read_rendered(current, limit)
        except WebError:
            if page is None or not page["text"]:
                raise
            # The browser could not help; what the plain fetch found is still worth returning, marked as partial.
            path = "static_fallback"
            outcome = "ok"
            return {**page, "note": "这页需要浏览器渲染但渲染失败，以下是未运行脚本时取到的内容，可能不完整"}
        outcome = "ok"
        return rendered
    finally:
        metrics.observe_web_read(path, outcome, time.monotonic() - started)


async def _read_static(current: str, limit: int) -> tuple[dict, str | None]:
    """The page and, for HTML, why it may need a browser (None when the static text is good)."""
    page = await fetch_html(current)
    final, ctype, raw, truncated = page["url"], page["content_type"], page["text"], page["truncated"]
    weak = None
    if ctype in ("text/html", "application/xhtml+xml") or raw.lstrip().startswith("<"):
        title, text, links = await asyncio.to_thread(extract_markdown, raw, final)
        weak = _weakness(raw, text)
        if CHALLENGE_TITLE.fullmatch(title.strip()) or CHALLENGE_TITLE.fullmatch(text.strip()):
            # An access wall is not useful partial article content if rendering fails.
            text, weak = "", "challenge"
    else:
        title, links, text = page["title"], [], raw
        if ctype == "application/json":
            with contextlib.suppress(ValueError):
                text = json.dumps(json.loads(raw), ensure_ascii=False, indent=1)
        text = _markdown(text)
    if len(text) > limit:
        text, truncated = text[:limit].rstrip() + "\n...[truncated]", True
    page = {
        "url": final,
        "title": title or urlsplit(final).netloc,
        "text": text,
        "content_type": ctype,
        "truncated": truncated,
        "links": links,
    }
    return page, weak


def _crawler_client() -> httpx.AsyncClient:
    """Internal service call: no environment proxy, no redirects, bounded connect and total time."""
    return httpx.AsyncClient(timeout=httpx.Timeout(settings.crawler_timeout_seconds, connect=3), trust_env=False, follow_redirects=False)


async def _read_rendered(url: str, limit: int) -> dict:
    """The page as the isolated crawler renders it: main content as Markdown. The URL is checked here
    first (scheme, port, every DNS answer public); what the page loads afterwards is held to the same
    rule by the egress proxy the crawler must use."""
    try:
        # HTTPX's read timeout bounds silence between chunks, not the whole response.
        # Include DNS so a stalled resolver cannot keep the Agent's tool slot forever.
        async with asyncio.timeout(settings.crawler_timeout_seconds), _crawler_client() as client:
            parsed = urlsplit(url)
            await public_addresses(parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80))
            resp = await client.post(
                settings.crawler_url.rstrip("/") + "/read",
                json={"url": url, "max_chars": limit},
                headers={"Authorization": f"Bearer {settings.crawler_token}"},
            )
            try:
                data = resp.json()
            except ValueError:
                data = {}
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise WebError("page reader timed out; try again later or use another source") from exc
    except httpx.HTTPError as exc:
        raise WebError(f"page reader unavailable: {type(exc).__name__}") from exc
    if resp.status_code == 503:
        raise WebError("page reader is busy; try again shortly")
    if resp.status_code >= 400:
        raise WebError(str(data.get("error") or f"page reader returned HTTP {resp.status_code}")[:300])
    final = str(data.get("url") or url)
    text = _markdown(str(data.get("markdown") or ""))
    if not text:
        raise WebError("page had no readable text")
    truncated = bool(data.get("truncated"))
    if len(text) > limit:
        text, truncated = text[:limit].rstrip(), True
    if truncated:
        text += "\n...[truncated]"
    links = [str(link) for link in data.get("links") or [] if str(link).startswith(("http://", "https://"))][:MAX_LINKS]
    return {
        "url": final,
        "title": str(data.get("title") or "")[:300] or urlsplit(final).netloc,
        "text": text,
        "content_type": str(data.get("content_type") or "text/html"),
        "truncated": truncated,
        "links": links,
    }

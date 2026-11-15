"""Safe external web research for the Agent.

The sandbox has no egress. This module runs in the Agent process (outside the
sandbox): it searches with a private SearXNG instance and turns public pages
into Markdown with one bounded fetch plus Trafilatura. Pages that only render
with JavaScript are salvaged from their own structured data (JSON-LD,
__NEXT_DATA__, meta description) instead of running a browser; blocked pages
fall back to a Wayback snapshot (ADR 0045). The model only sees the text and
source URLs; the sandbox never gets a network route.

Security rules:
* only http(s), default ports 80/443, no credentials in URLs;
* every hostname is resolved and every address must be globally routable
  (rejects loopback, RFC1918, link-local, CGNAT, cloud metadata, and IPv6 forms
  that embed one of those: mapped, NAT64, 6to4, Teredo);
* the connection goes to the address that was checked: libcurl's RESOLVE option
  maps host:port to the checked IP and no second DNS lookup happens;
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
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpcore
import httpx
import trafilatura
from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import CertificateVerifyError, RequestException, Timeout

from . import metrics
from .config import settings

USER_AGENT = "AILab-lite/0.1 (safe web research; https://github.com/shijunan126-creator/ailab-lite)"
BROWSER_ACCEPT = "text/markdown, text/html;q=0.9, */*;q=0.8"
HTML_ACCEPT = "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.1"
BROWSER_HEADERS = {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"}
MAX_REDIRECTS = 3
MAX_LINKS = 12
MAX_PAGE_IMAGES = 6
ALLOWED_PORTS = (80, 443)
DNS_TIMEOUT_SECONDS = 3.0
WAYBACK_MIN_SECONDS = 3.0
WAYBACK_AVAILABLE = "https://archive.org/wayback/available"
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"
TIMEOUT_MESSAGE = "该站读取超时，请换其他来源"
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
    """A transport failure (timeouts are WebError with a caller-facing message)."""


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
        async with asyncio.timeout(DNS_TIMEOUT_SECONDS):
            infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except TimeoutError as exc:
        raise WebError(f"DNS lookup for {host} timed out; try another source") from exc
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
    if port not in ALLOWED_PORTS:
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


class _References(HTMLParser):
    """One pass over the page for what it points at: outgoing links (read_url returns them so the model may follow
    one, net_tools provenance) and its pictures (registered by the Agent before returning to the model)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.shared: list[tuple[str, str]] = []  # og:image / twitter:image: the picture the page chose to represent it
        self.images: list[tuple[str, str]] = []
        self.shared_alt = ""
        self.description = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "a":
            if len(self.links) < MAX_LINKS * 4 and (href := dict(attrs).get("href")):
                self.links.append(href)
        elif tag == "meta":
            values = dict(attrs)
            name = (values.get("property") or values.get("name") or "").strip().lower()
            content = (values.get("content") or "").strip()
            if name in SHARED_IMAGE_META and content and len(self.shared) < 4:
                self.shared.append((content, ""))
            elif name in ("og:image:alt", "twitter:image:alt") and not self.shared_alt:
                self.shared_alt = content
            elif name in ("description", "og:description") and content and not self.description:
                self.description = content
        elif tag == "img" and len(self.images) < MAX_PAGE_IMAGES * 10:
            values = dict(attrs)
            if any(_attr_px(values.get(side)) <= 64 for side in ("width", "height")):
                return  # tracking pixels, icons, spacers
            src = (values.get("src") or "").strip()
            if not src or src.startswith("data:"):  # lazy loaders keep a placeholder in src and the picture here
                src = next((v.strip() for k in LAZY_SRC if (v := values.get(k)) and not v.strip().startswith("data:")), "")
            if src:
                self.images.append((src, (values.get("alt") or "").strip()))


SHARED_IMAGE_META = {"og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"}
LAZY_SRC = ("data-src", "data-original", "data-lazy-src", "data-actualsrc")
# Path words that mark chrome rather than content.
DECORATION = {
    "logo", "logos", "icon", "icons", "favicon", "avatar", "avatars", "sprite", "sprites", "pixel", "spacer", "blank",
    "badge", "badges", "emoji", "placeholder", "loading", "spinner", "1x1", "beacon", "tracking",
}  # fmt: skip


def _attr_px(value: str | None) -> int:
    match = re.match(r"\s*(\d+)\s*(?:px)?\s*$", value or "")
    return int(match[1]) if match else 10_000


def image_reference(raw: str, base: str = "") -> str | None:
    """A validated public picture URL found in a search result or page; None for what must not be shown:
    non-http(s), data:, credentials, odd ports, private literal addresses, SVG (scriptable) and icons."""
    raw = raw.strip()
    if not raw or raw.startswith("data:"):
        return None
    try:
        url = _validate_url(urljoin(base, raw) if base else ("https:" + raw if raw.startswith("//") else raw))
    except (WebError, ValueError):
        return None
    path = urlsplit(url).path.lower()
    if path.endswith((".svg", ".svgz", ".ico")) or DECORATION & set(re.split(r"[^a-z0-9]+", path)):
        return None
    return url if len(url.encode()) <= 4096 else None


def _references(html: str, base: str) -> tuple[list[str], list[dict], str]:
    parser = _References()
    with contextlib.suppress(Exception):  # malformed HTML only costs the links and pictures
        parser.feed(html)
    links: list[str] = []
    for href in parser.links:
        url = urljoin(base, href.strip()).split("#")[0]
        if url.startswith(("http://", "https://")) and len(url) <= 2000 and url not in links:
            links.append(url)
        if len(links) >= MAX_LINKS:
            break
    images: list[dict] = []
    shared = [(src, parser.shared_alt) for src, _ in parser.shared]
    for src, alt in shared + parser.images:
        ref = image_reference(src, base)
        if ref and all(item["image"] != ref for item in images):
            images.append({"image": ref, "page_url": base, "alt": _clean(alt)[:120]})
        if len(images) >= MAX_PAGE_IMAGES:
            break
    return links, images, _clean(parser.description)[:1000]


MARKDOWN_LINK = re.compile(r"\]\(\s*<?(https?://[^)\s>]+)>?\s*\)|<(https?://[^>\s]+)>")
TEXT_KEYS = {"articlebody", "body", "content", "text", "summary", "excerpt", "description"}


def _json_strings(value, keys: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str) and key.lower() in keys:
                found.append(child)
            else:
                found.extend(_json_strings(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(_json_strings(child, keys))
    return found


def _plain_markup(text: str) -> str:
    return _clean(unescape(re.sub(r"<[^>]+>", " ", text)))


def _structured_text(html: str, description: str) -> str:
    """Article text a server-rendered HTML page carries for its own scripts; no JS engine is involved."""
    article: list[str] = []
    for match in re.finditer(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.I | re.S):
        with contextlib.suppress(ValueError, TypeError):
            article.extend(_json_strings(json.loads(match[1]), {"articlebody"}))
    if article:
        return _plain_markup(max(article, key=len))

    next_data: list[str] = []
    for match in re.finditer(r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", html, re.I | re.S):
        with contextlib.suppress(ValueError, TypeError):
            next_data.extend(_json_strings(json.loads(match[1]), TEXT_KEYS))
    next_data = [_plain_markup(item) for item in next_data]
    if best := next((item for item in sorted(next_data, key=len, reverse=True) if len(item) >= 200), ""):
        return best

    return description if len(description) >= 80 else ""


def _content_links(text: str, base: str) -> list[str]:
    links: list[str] = []
    for match in MARKDOWN_LINK.finditer(text):
        url = urljoin(base, match[1] or match[2]).split("#")[0]
        if url.startswith(("http://", "https://")) and len(url) <= 2000 and url not in links:
            links.append(url)
        if len(links) >= MAX_LINKS:
            break
    return links


def _extract_document(html: str, base: str, recover: bool = False) -> dict:
    """Title, main Markdown, links, pictures and weakness for one HTML page. CPU-bound: callers run it in a thread."""
    try:
        text = trafilatura.extract(
            html,
            url=base or None,
            output_format="markdown",
            include_tables=True,
            include_formatting=True,
            include_links=True,
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
    text = _markdown(text or "")
    page_links, images, description = _references(html, base)
    links = _content_links(text, base) or page_links
    weak = _weakness(html, text)
    challenged = bool(CHALLENGE_TITLE.fullmatch(_clean(title).strip()) or CHALLENGE_TITLE.fullmatch(text.strip()))
    recovered = _structured_text(html, description) if recover and weak in ("short", "script") else ""
    return {
        "title": _clean(title)[:300],
        "text": text,
        "links": links[:MAX_LINKS],
        "images": images,
        "weak": weak,
        "challenged": challenged,
        "recovered": recovered,
    }


def extract_markdown(html: str, base: str = "") -> tuple[str, str, list[str], list[dict]]:
    """Title, main content as Markdown (navigation, footers and boilerplate dropped by Trafilatura), links and
    picture address candidates. CPU-bound: callers run it in a thread."""
    page = _extract_document(html, base)
    return page["title"], page["text"], page["links"], page["images"]


def _markdown(text: str) -> str:
    # Keep indentation and table spacing, which _clean would flatten.
    return re.sub(r"\n{3,}", "\n\n", text.replace("\xa0", " ").replace("\u200b", "")).strip()


# A static fetch is good enough for most pages. These signs mean the server sent an app shell, a bot wall,
# or too little text: salvage the page's own structured data, then try a Wayback snapshot.
MIN_TEXT = 200
SCRIPT_SHELL = re.compile(
    r"<div[^>]+id=[\"'](?:root|app|__next|__nuxt|q-app)[\"'][^>]*>\s*</div>|enable javascript|启用\s*javascript|"
    r"cf-chl|challenge-platform|just a moment\.\.\.",
    re.I,
)
CHALLENGE_TITLE = re.compile(
    r"^(?:just a moment[.!…]*|attention required[!！]?(?:\s*\|\s*cloudflare)?|access denied|verify (?:that )?you are human)[.!…\s]*$",
    re.I,
)


def _weakness(html: str, text: str) -> str | None:
    if not text:
        return "script" if SCRIPT_SHELL.search(html) else "short"
    if len(text) < MIN_TEXT and re.search(r"<script\b", html, re.I):
        return "short"
    if len(text) < 1500 and SCRIPT_SHELL.search(html):
        return "script"
    return None


MOCK_IMAGE_BASE = "https://example.com/ailab-mock-images/"


async def search(query: str, count: int | None = None, kind: str = "web") -> list[dict]:
    query = query.strip()[:300]
    if not query:
        raise WebError("query is required")
    if kind not in ("web", "images"):
        raise WebError("kind must be web or images")
    n = max(1, min(int(count or settings.web_max_results), 8))
    provider = settings.web_search_provider
    if provider == "mock" and kind == "images":
        # The API serves these without a network (api/routes_web_images.py), so the mock stack shows real pictures.
        return [
            {
                "title": f"{query} — image {i + 1}",
                "image": f"{MOCK_IMAGE_BASE}{i + 1}.png",
                "page_url": f"https://example.com/{i + 1}",
                "width": 480,
                "height": 320,
            }
            for i in range(n)
        ]
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
    if kind == "images":
        params["categories"] = "images"
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
        raise WebError(f"search service unavailable: {type(exc).__name__}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise WebError("search service returned an invalid response")
    # SearXNG reports upstream failures in a successful HTTP response. Do not
    # turn an outage into evidence that the query has no matching sources.
    if data.get("error") or (not data["results"] and data.get("unresponsive_engines")):
        raise WebError("search provider engines failed or timed out; retry later (not a successful empty search)")

    if kind == "images":
        return _image_results(data["results"], n)
    out: list[dict] = []
    for item in data["results"]:
        if not isinstance(item, dict):
            raise WebError("search service returned an invalid result")
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


def _image_results(results: list, n: int) -> list[dict]:
    """SearXNG image address candidates: the picture (img_src, else its thumbnail) and the page showing it."""
    out: list[dict] = []
    for item in results:
        if not isinstance(item, dict):
            raise WebError("search service returned an invalid result")
        if str(item.get("img_format") or "").lower().startswith("svg"):
            continue
        ref = image_reference(str(item.get("img_src") or "")) or image_reference(str(item.get("thumbnail_src") or ""))
        if not ref or any(hit["image"] == ref for hit in out):
            continue
        page = str(item.get("url") or "").strip()
        hit = {
            "title": str(item.get("title") or "")[:300],
            "image": ref,
            "thumbnail_url": image_reference(str(item.get("thumbnail_src") or "")) or "",
        }
        if page.startswith(("http://", "https://")) and len(page) <= 2000:
            hit["page_url"] = page
        if size := re.match(r"\s*(\d{1,5})\s*[x×]\s*(\d{1,5})", str(item.get("resolution") or "")):
            hit["width"], hit["height"] = int(size[1]), int(size[2])
        out.append(hit)
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


# Extraction runs on its own two threads. A cancelled await cannot stop a running thread, so a semaphore released on
# cancellation (excerpt and run deadlines) would let abandoned extractions pile up past any bound: each holds a parsed
# tree and competes for the agent's small CPU quota, which starves the event loop (model connects time out) and memory.
# Queued jobs whose caller was cancelled are dropped before they start.
_EXTRACT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="web-extract")


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise WebError(TIMEOUT_MESSAGE)
    return left


def _resolve_entries(host: str, port: int, addresses: list[str]) -> list[bytes]:
    """One RESOLVE entry carrying every checked address. libcurl keeps only the last entry for a host:port, so one
    entry per address would pin the connection to whichever came last (often an IPv6 address with no route here).
    IPv4 first: the containers this runs in usually have no IPv6 route, and libcurl tries the list in order."""
    ordered = sorted(addresses, key=lambda address: ":" in address)
    targets = ",".join(f"[{address}]" if ":" in address else address for address in ordered)
    return [f"{host}:{port}:{targets}".encode()]


async def _fetch_page(url: str, headers: dict[str, str], deadline: float, max_bytes: int | None = None) -> dict:
    """One HTTP GET pinned to the addresses public_addresses checked; libcurl's RESOLVE means no second lookup."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    limit = max_bytes or settings.web_max_bytes
    remaining = _remaining(deadline)
    try:
        async with asyncio.timeout(remaining):
            addresses = await public_addresses(host, port)
    except TimeoutError as exc:
        raise WebError(TIMEOUT_MESSAGE) from exc
    remaining = _remaining(deadline)
    options = {CurlOpt.NOPROXY: b"*"}
    if not _is_ip_literal(host):  # an already-checked literal connects directly; RESOLVE syntax cannot carry IPv6 hosts
        options[CurlOpt.RESOLVE] = _resolve_entries(host, port, addresses)
    try:
        async with AsyncSession(
            impersonate="chrome",
            trust_env=False,
            discard_cookies=True,
            allow_redirects=False,
            timeout=remaining,
            curl_options=options,
        ) as session:
            response = await session.get(url, headers=headers, stream=True, allow_redirects=False)
            try:
                ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise WebError(f"page is too large ({declared} bytes)")
                body, total, truncated = bytearray(), 0, False
                async for chunk in response.aiter_content():
                    if time.monotonic() >= deadline:
                        raise WebError(TIMEOUT_MESSAGE)
                    if total + len(chunk) > limit:
                        body.extend(chunk[: limit - total])
                        truncated = True
                        break
                    body.extend(chunk)
                    total += len(chunk)
                return {
                    "url": response.url,
                    "status": response.status_code,
                    "content_type": ctype,
                    "location": response.headers.get("location"),
                    "text": body.decode(response.encoding or "utf-8", "replace"),
                    "truncated": truncated,
                }
            finally:
                with contextlib.suppress(Exception):
                    await response.aclose()
    except WebError:
        raise
    except CertificateVerifyError as exc:
        raise WebError(f"TLS certificate verification failed for {url}") from exc
    except Timeout as exc:
        raise WebError(TIMEOUT_MESSAGE) from exc
    except RequestException as exc:
        if time.monotonic() >= deadline:
            raise WebError(TIMEOUT_MESSAGE) from exc
        raise WebFetchError(f"could not fetch {url}: {type(exc).__name__}") from exc


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return True


async def _fetch_follow(url: str, headers: dict[str, str], deadline: float) -> dict:
    current = _validate_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        _remaining(deadline)
        page = await _fetch_page(current, headers, deadline)
        if page["status"] in (301, 302, 303, 307, 308):
            location = page.get("location")
            if not location:
                raise WebError("redirect without location")
            current = _validate_url(urljoin(current, location))
            continue
        return page
    raise WebError("too many redirects")


async def fetch_html(url: str) -> dict:
    """One bounded public fetch of a text-like page: raw decoded text, nothing extracted. The
    Agent's read_url and the Portal's original-page view share it — same caps, one fetch per click."""
    current = _validate_url(url)
    deadline = time.monotonic() + settings.web_read_deadline_seconds
    page = await _fetch_follow(current, {"Accept": HTML_ACCEPT, **BROWSER_HEADERS}, deadline)
    if page["status"] >= 400:
        raise WebStatusError(page["status"], f"HTTP {page['status']} from {page['url']}")
    ctype = page["content_type"]
    if ctype and ctype not in TEXT_TYPES:
        raise WebError(f"unsupported content type: {ctype}; use download_url for files")
    text = page["text"]
    title = ""
    if m := re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S):
        title = _clean(unescape(m[1]))[:300]
    return {
        "url": page["url"],
        "title": title,
        "text": text,
        "content_type": ctype or "text/html",
        "truncated": page["truncated"],
    }


async def _extract_bounded(html: str, base: str) -> dict:
    return await asyncio.get_running_loop().run_in_executor(_EXTRACT_POOL, partial(_extract_document, html, base, recover=True))


async def _page_from(fetched: dict) -> dict:
    """Turn a fetched body into the read_url result shape; HTML extraction runs in a bounded thread."""
    final, ctype, raw, truncated = fetched["url"], fetched["content_type"], fetched["text"], fetched["truncated"]
    if ctype in ("text/html", "application/xhtml+xml") or (not ctype and raw.lstrip().startswith("<")):
        document = await _extract_bounded(raw, final)
        page = {
            "url": final,
            "title": document["title"] or urlsplit(final).netloc,
            "text": document["text"],
            "content_type": ctype or "text/html",
            "truncated": truncated,
            "links": document["links"],
            "challenged": document["challenged"],
            "weak": document["weak"],
            "recovered": document["recovered"],
            **({"images": document["images"]} if document["images"] else {}),
        }
        return page
    text = raw
    if ctype == "application/json":
        with contextlib.suppress(ValueError):
            text = json.dumps(json.loads(raw), ensure_ascii=False, indent=1)
    text = _markdown(text)
    heading = re.match(r"#\s+(.+)", text)
    return {
        "url": final,
        "title": (_clean(heading[1])[:300] if heading else urlsplit(final).netloc),
        "text": text,
        "content_type": ctype or "text/plain",
        "truncated": truncated,
        "links": [],
        "challenged": False,
        "weak": None,
        "recovered": "",
    }


def _window(page: dict, start: int, limit: int) -> dict:
    """Return text[start:start+limit]; next_start is present only when more text may follow."""
    full = page.get("text") or ""
    if start >= len(full):
        page["text"] = ""
        page["truncated"] = bool(page.get("truncated"))
        page.pop("next_start", None)
        return page
    end = start + limit
    page["text"] = full[start:end]
    page["truncated"] = end < len(full) or bool(page.get("truncated"))
    if end < len(full):
        page["next_start"] = end
    else:
        page.pop("next_start", None)
    return page


async def _wayback(url: str, deadline: float) -> dict | None:
    """The closest Wayback snapshot as a normal page, or None when there is no usable snapshot in time."""
    if deadline - time.monotonic() < WAYBACK_MIN_SECONDS:
        return None
    try:
        async with asyncio.timeout(deadline - time.monotonic()):
            data = await get_json(f"{WAYBACK_AVAILABLE}?url={quote(url, safe='')}", 256 * 1024)
    except (WebError, TimeoutError):
        return None
    closest = (data.get("archived_snapshots") or {}).get("closest") if isinstance(data, dict) else None
    stamp = str((closest or {}).get("timestamp") or "")
    if not closest or not closest.get("available") or not re.fullmatch(r"\d{14}", stamp):
        return None
    try:
        _remaining(deadline)
        fetched = await _fetch_follow(
            _validate_url(WAYBACK_SNAPSHOT.format(stamp=stamp, url=url)),
            {"Accept": HTML_ACCEPT, **BROWSER_HEADERS},
            deadline,
        )
        if fetched["status"] >= 400 or (fetched["content_type"] and fetched["content_type"] not in TEXT_TYPES):
            return None
        page = await _page_from(fetched)
    except WebError:
        return None
    if not page["text"] or page["challenged"]:
        return None
    archived_at = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    page.pop("weak", None)
    page.pop("recovered", None)
    page["archived_at"] = archived_at
    page["note"] = f"这是 Wayback Machine 存档快照（{archived_at}），可能不是最新内容。"
    return page


async def read_url(url: str, max_chars: int | None = None, start: int = 0) -> dict:
    """Read a public page as Markdown with browser-like headers, structured-data recovery and a Wayback fallback."""
    current = _validate_url(url)
    limit = max(1000, min(int(max_chars or settings.web_max_chars), 60_000))
    start = max(0, int(start or 0))
    issue = GITHUB_ISSUE.match(current)
    if issue:
        return _window(await read_github_issue(*issue.groups(), start + limit), start, limit)
    deadline = time.monotonic() + settings.web_read_deadline_seconds
    started, path, outcome = time.monotonic(), "static", "error"
    try:
        fetched = await _fetch_follow(current, {"Accept": BROWSER_ACCEPT, **BROWSER_HEADERS}, deadline)
        if fetched["status"] == 403:  # some sites (openai.com) reject Markdown negotiation but serve HTML
            fetched = await _fetch_follow(current, {"Accept": HTML_ACCEPT, **BROWSER_HEADERS}, deadline)
        path = "markdown" if fetched["content_type"] == "text/markdown" else "static"
        status = fetched["status"]
        if status in (401, 403, 429):
            archived = await _wayback(current, deadline)
            if archived:
                path, outcome = "wayback", "ok"
                return _window(archived, start, limit)
            raise WebStatusError(status, f"HTTP {status} from {fetched['url']}；网站拒绝读取或需要验证，没有可用存档，请换其他来源")
        if status >= 400:
            raise WebStatusError(status, f"HTTP {status} from {fetched['url']}；请换其他来源")
        if fetched["content_type"] and fetched["content_type"] not in TEXT_TYPES:
            raise WebError(f"unsupported content type: {fetched['content_type']}; use download_url for files")
        page = await _page_from(fetched)
        if page["challenged"]:
            archived = await _wayback(current, deadline)
            if archived:
                path, outcome = "wayback", "ok"
                return _window(archived, start, limit)
            raise WebError("该网站要求人机验证，没有可用存档，请换其他来源")
        if page["weak"] in ("short", "script"):
            if page["recovered"]:
                page["text"] = page["recovered"]
                page["truncated"] = False
                page["note"] = "页面没有可直接抽取的正文，以下内容取自页面自带的结构化数据，可能不完整。"
                path = "recovered"
            elif page["weak"] == "script":
                archived = await _wayback(current, deadline)
                if archived:
                    path, outcome = "wayback", "ok"
                    return _window(archived, start, limit)
            if not page["text"]:
                raise WebError("页面正文由脚本加载，未运行脚本时没有可读内容，请换其他来源")
        page.pop("weak", None)
        page.pop("recovered", None)
        if not page["text"]:
            raise WebError("页面没有可读正文，请换其他来源")
        outcome = "ok"
        return _window(page, start, limit)
    except WebFetchError as exc:
        raise WebError(f"{exc}；请换其他来源") from exc
    finally:
        metrics.observe_web_read(path, outcome, time.monotonic() - started)

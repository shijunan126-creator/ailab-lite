"""Safe external web research for the Agent.

The sandbox has no egress. This module runs in the Agent process (outside the
sandbox): it searches with a private SearXNG instance and turns public pages
into plain text. The model only sees the text and source URLs; the sandbox
never gets a network route.

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
import tempfile
import time
from collections.abc import AsyncIterator
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx

from . import reader
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
SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "canvas", "template", "form", "nav", "footer", "aside", "button"}
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "main",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "br",
    "blockquote",
    "pre",
}


class WebError(RuntimeError):
    pass


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
                    raise WebError(f"HTTP {resp.status_code} from {current}")
                yield resp
                return
        except WebError:
            raise
        except httpx.HTTPError as exc:
            raise WebError(f"could not fetch {current}: {type(exc).__name__}: {exc}") from exc
    raise WebError("too many redirects")


async def _read_bounded(resp: httpx.Response, limit: int, deadline: float | None = None) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        if deadline is not None and time.monotonic() >= deadline:
            # The read timeout only bounds silence between chunks; a peer trickling bytes
            # forever needs a wall-clock budget too (the same trap the internal download closes).
            raise WebError("response did not finish within the transfer deadline")
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


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_title = False
        self.title: list[str] = []
        self.body: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in ("a", "img") and len(self.links) < MAX_LINKS * 4:
            href = dict(attrs).get("href" if tag == "a" else "src")
            if href:
                self.links.append(href)
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if tag == "title":
            self.in_title = True
        if tag in BLOCK_TAGS and not self.skip:
            self.body.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self.in_title = False
        if tag in BLOCK_TAGS and not self.skip:
            self.body.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.in_title:
            self.title.append(data)
        else:
            self.body.append(data)


def extract_html(html: str, base: str = "") -> tuple[str, str, list[str]]:
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # malformed HTML must not fail the whole tool
        raise WebError(f"could not parse HTML: {exc}") from exc
    title = _clean(" ".join(parser.title))[:300]
    body = _clean("".join(parser.body))
    links: list[str] = []
    for href in parser.links:
        url = urljoin(base, href.strip()).split("#")[0] if base else href.strip()
        if url.startswith(("http://", "https://")) and len(url) <= 2000 and url not in links:
            links.append(url)
        if len(links) >= MAX_LINKS:
            break
    return title, body, links


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
    Agent's read_url and the Portal's reader share it — same caps, one fetch per click."""
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
    current = _validate_url(url)
    limit = max(1000, min(int(max_chars or settings.web_max_chars), 60_000))
    issue = GITHUB_ISSUE.match(current)
    if issue:
        return await read_github_issue(*issue.groups(), limit)
    page = await fetch_html(current)
    final, ctype, text, truncated = page["url"], page["content_type"], page["text"], page["truncated"]
    title, links = page["title"], []
    if ctype in ("text/html", "application/xhtml+xml") or text.lstrip().startswith("<"):
        title, text, links = extract_html(text, final)
    elif ctype == "application/json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
        except ValueError:
            pass
    text = _clean(text)
    if not text:
        raise WebError("page had no readable text")
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n...[truncated]"
        truncated = True
    return {
        "url": final,
        "title": title or urlsplit(final).netloc,
        "text": text,
        "content_type": ctype,
        "truncated": truncated,
        "links": links,
    }


async def read_page(url: str) -> dict:
    """The side-pane reader's view of one public page: the main content as structured blocks
    (see ailab_platform.reader) — the article without the site chrome around it."""
    current = _validate_url(url)
    if issue := GITHUB_ISSUE.match(current):
        page = await read_github_issue(*issue.groups(), 20_000)
        return {
            "url": page["url"],
            "title": page["title"],
            "blocks": reader.markdown_to_blocks(page["text"]),
            "truncated": page["truncated"],
        }
    page = await fetch_html(current)
    ctype, raw = page["content_type"], page["text"]
    if ctype in ("text/html", "application/xhtml+xml") or raw.lstrip().startswith("<"):
        blocks, limited = reader.extract_article(raw, page["url"])
        page["truncated"] = page["truncated"] or limited
    elif ctype == "application/json":
        try:
            blocks = [{"type": "pre", "text": json.dumps(json.loads(raw), ensure_ascii=False, indent=1)[:20_000], "lt": 0}]
        except ValueError:
            blocks = [{"type": "pre", "text": raw[:20_000], "lt": 0}]
    else:
        blocks = reader.markdown_to_blocks(raw)
    return {
        "url": page["url"],
        "title": page["title"] or urlsplit(page["url"]).netloc,
        "blocks": blocks,
        "truncated": page["truncated"],
    }

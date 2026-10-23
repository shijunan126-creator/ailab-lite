"""Safe external web research for the Agent.

The sandbox has no egress. This module runs in the Agent process (outside the
sandbox): it searches with a private SearXNG instance and turns public pages
into plain text. The model only sees the text and source URLs; the sandbox
never gets a network route.

Security rules:
* only http(s), default ports 80/443, no credentials in URLs;
* every hostname is resolved and every address must be globally routable
  (rejects loopback, RFC1918, link-local, cloud metadata, etc.);
* redirects are followed manually and validated again;
* responses are bounded by time and bytes, and only text-like content types
  are accepted;
* no cookies, no auth headers, no environment proxy.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .config import settings

USER_AGENT = "AILab-lite/0.1 (safe web research; https://github.com/shijunan126-creator/ailab-lite)"
MAX_REDIRECTS = 3
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
    if ip is not None and not ip.is_global:
        raise WebError(f"refusing non-public address: {host}")
    # Remove fragment. Keep the original path/query; urlsplit already decoded nothing.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


async def _assert_public(host: str, port: int) -> None:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebError(f"cannot resolve host: {host}") from exc
    if not infos:
        raise WebError(f"cannot resolve host: {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise WebError(f"refusing non-public address: {host} -> {ip}")


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

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
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


def extract_html(html: str) -> tuple[str, str]:
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # malformed HTML must not fail the whole tool
        raise WebError(f"could not parse HTML: {exc}") from exc
    title = _clean(" ".join(parser.title))[:300]
    body = _clean("".join(parser.body))
    return title, body


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


async def read_url(url: str, max_chars: int | None = None) -> dict:
    current = _validate_url(url)
    limit = max(1000, min(int(max_chars or settings.web_max_chars), 60_000))
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    timeout = httpx.Timeout(settings.web_fetch_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers, trust_env=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlsplit(current)
            await _assert_public(parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80))
            try:
                async with client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise WebError("redirect without location")
                        current = _validate_url(urljoin(current, location))
                        continue
                    if resp.status_code >= 400:
                        raise WebError(f"HTTP {resp.status_code} from {current}")
                    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if ctype and ctype not in TEXT_TYPES:
                        raise WebError(f"unsupported content type: {ctype}")
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > settings.web_max_bytes:
                        raise WebError(f"page is too large ({declared} bytes)")
                    chunks: list[bytes] = []
                    total = 0
                    truncated = False
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > settings.web_max_bytes:
                            keep = settings.web_max_bytes - (total - len(chunk))
                            if keep > 0:
                                chunks.append(chunk[:keep])
                            truncated = True
                            break
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    encoding = resp.encoding or "utf-8"
                    text = body.decode(encoding, "replace")
                    title = ""
                    if ctype in ("text/html", "application/xhtml+xml", "") or text.lstrip().startswith("<"):
                        title, text = extract_html(text)
                    elif ctype == "application/json":
                        try:
                            text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
                        except Exception:
                            pass
                    text = _clean(text)
                    if not text:
                        raise WebError("page had no readable text")
                    if len(text) > limit:
                        text = text[:limit].rstrip() + "\n...[truncated]"
                        truncated = True
                    return {
                        "url": current,
                        "title": title or parsed.netloc,
                        "text": text,
                        "content_type": ctype or "text/html",
                        "truncated": truncated,
                    }
            except WebError:
                raise
            except Exception as exc:
                raise WebError(f"could not fetch {current}: {type(exc).__name__}: {exc}") from exc
    raise WebError("too many redirects")

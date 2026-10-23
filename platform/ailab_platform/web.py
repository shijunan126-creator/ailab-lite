"""Safe external web acquisition for the Agent.

The sandbox has no egress. This module runs in the Agent process (outside the
sandbox): it searches with a private SearXNG instance, reads public pages,
downloads bounded public files, and parses common document formats. The model
only sees text and paths; the sandbox never gets a network route.

Security rules:
* only http(s), default ports 80/443, no credentials in URLs;
* every hostname is resolved, every address must be globally routable, and the
  TCP connection is pinned to one validated address (TLS still verifies the
  original hostname);
* redirects are followed manually and validated again;
* responses are bounded by time and bytes;
* downloads are scanned by the same ClamAV adapter used for uploads;
* no cookies, no auth headers, no environment proxy.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import mimetypes
import re
import socket
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpcore
import httpx

from .config import settings

USER_AGENT = "AILab-lite/0.1 (safe web acquisition; https://github.com/shijunan126-creator/ailab-lite)"
MAX_REDIRECTS = 3
TEXT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
}
DOCUMENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
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
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


async def _public_ips(host: str, port: int) -> list[str]:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebError(f"cannot resolve host: {host}") from exc
    if not infos:
        raise WebError(f"cannot resolve host: {host}")
    addresses: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise WebError(f"refusing non-public address: {host} -> {ip}")
        if str(ip) not in addresses:
            addresses.append(str(ip))
    return addresses


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Resolve once, validate, then connect to exactly that address.

    httpcore still uses the original hostname for the Host header and TLS SNI;
    only the TCP destination is pinned. This closes the gap between validation
    and a second DNS lookup by the HTTP client.
    """

    def __init__(self, ip: str) -> None:
        self._ip = ip
        self._delegate = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return await self._delegate.connect_tcp(self._ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self._delegate.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


def _pinned_transport(ip: str) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport()
    # httpx's default transport does not expose httpcore's network_backend.
    # Replace the pool while keeping httpx's TLS/HTTP handling.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        network_backend=_PinnedBackend(ip),
        http1=True,
        http2=False,
    )
    return transport


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
    except Exception as exc:
        raise WebError(f"could not parse HTML: {exc}") from exc
    title = _clean(" ".join(parser.title))[:300]
    body = _clean("".join(parser.body))
    return title, body


async def _fetch(url: str, max_bytes: int, accept: str) -> dict:
    current = _validate_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    timeout = httpx.Timeout(settings.web_fetch_timeout_seconds)
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlsplit(current)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ips = await _public_ips(host, port)
        transport = _pinned_transport(ips[0])
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                headers=headers,
                trust_env=False,
            ) as client:
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
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise WebError(f"response is too large ({declared} bytes)")
                    chunks: list[bytes] = []
                    total = 0
                    truncated = False
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            keep = max_bytes - (total - len(chunk))
                            if keep > 0:
                                chunks.append(chunk[:keep])
                            truncated = True
                            break
                        chunks.append(chunk)
                    return {
                        "url": current,
                        "content_type": ctype,
                        "data": b"".join(chunks),
                        "truncated": truncated,
                        "content_disposition": resp.headers.get("content-disposition") or "",
                    }
        except WebError:
            raise
        except Exception as exc:
            raise WebError(f"could not fetch {current}: {type(exc).__name__}: {exc}") from exc
    raise WebError("too many redirects")


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
    limit = max(1000, min(int(max_chars or settings.web_max_chars), 60_000))
    fetched = await _fetch(
        url,
        settings.web_max_bytes,
        "text/html,application/xhtml+xml,text/plain,application/json,text/csv;q=0.9,*/*;q=0.1",
    )
    ctype = fetched["content_type"]
    if ctype and ctype not in TEXT_TYPES:
        raise WebError(f"unsupported content type: {ctype}")
    text = fetched["data"].decode("utf-8", "replace")
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
    truncated = fetched["truncated"]
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n...[truncated]"
        truncated = True
    return {
        "url": fetched["url"],
        "title": title or (urlsplit(fetched["url"]).hostname or ""),
        "text": text,
        "content_type": ctype or "text/html",
        "truncated": truncated,
    }


def _safe_filename(url: str, content_type: str, disposition: str) -> str:
    name = ""
    match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, re.I)
    if match:
        name = unquote(match.group(1).strip().strip('"'))
    if not name:
        match = re.search(r"filename\s*=\s*\"?([^\";]+)", disposition, re.I)
        if match:
            name = match.group(1).strip()
    if not name:
        name = unquote(PurePosixPath(urlsplit(url).path).name)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")[:120]
    if not name:
        name = "download"
    if not Path(name).suffix:
        ext = mimetypes.guess_extension(content_type or "") or ".bin"
        name += ext
    return name


async def download(url: str, max_bytes: int | None = None) -> dict:
    limit = max(1, min(int(max_bytes or settings.web_download_max_bytes), settings.web_download_max_bytes))
    fetched = await _fetch(url, limit, "*/*")
    if fetched["truncated"]:
        raise WebError(f"file exceeds the {limit}-byte download limit")
    data = fetched["data"]
    filename = _safe_filename(fetched["url"], fetched["content_type"], fetched["content_disposition"])
    return {
        "url": fetched["url"],
        "content_type": fetched["content_type"] or "application/octet-stream",
        "data": data,
        "filename": filename,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _decode_text(data: bytes, max_chars: int) -> str:
    return data.decode("utf-8", "replace")[:max_chars]


def _pdf_text(data: bytes, max_chars: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            parts.append(text)
            total += len(text)
            if total >= max_chars:
                break
    return _clean("\n\n".join(parts))[:max_chars]


def _docx_text(data: bytes, max_chars: int) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return _clean("\n".join(parts))[:max_chars]


def _pptx_text(data: bytes, max_chars: int) -> str:
    from pptx import Presentation

    presentation = Presentation(io.BytesIO(data))
    parts: list[str] = []
    for index, slide in enumerate(presentation.slides, 1):
        parts.append(f"## Slide {index}")
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = shape.text.strip()
                if text:
                    parts.append(text)
    return _clean("\n".join(parts))[:max_chars]


def _xlsx_text(data: bytes, max_chars: int) -> str:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    total = 0
    try:
        for sheet in workbook.worksheets:
            parts.append(f"## {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if not any(values):
                    continue
                line = " | ".join(values)
                parts.append(line)
                total += len(line)
                if total >= max_chars:
                    return _clean("\n".join(parts))[:max_chars]
    finally:
        workbook.close()
    return _clean("\n".join(parts))[:max_chars]


def extract_document(data: bytes, filename: str, content_type: str = "") -> tuple[str, bool]:
    """Extract bounded plain text from a downloaded document.

    Returns (text, truncated). Images and unknown binary formats return empty
    text; the caller still gets the saved file path.
    """
    suffix = Path(filename).suffix.lower()
    limit = settings.web_max_chars
    try:
        if suffix == ".pdf" or content_type == "application/pdf":
            text = _pdf_text(data, limit)
        elif suffix == ".docx":
            text = _docx_text(data, limit)
        elif suffix == ".pptx":
            text = _pptx_text(data, limit)
        elif suffix in {".xlsx", ".xlsm"}:
            text = _xlsx_text(data, limit)
        elif suffix in {".html", ".htm"}:
            _, text = extract_html(_decode_text(data, limit * 2))
        elif suffix == ".json":
            try:
                text = json.dumps(json.loads(data), ensure_ascii=False, indent=1)
            except Exception:
                text = _decode_text(data, limit)
        elif suffix in {".txt", ".md", ".csv", ".xml", ".yaml", ".yml", ".log", ".py", ".js", ".ts", ".java", ".go", ".rs"}:
            text = _decode_text(data, limit)
        else:
            text = ""
    except Exception as exc:
        raise WebError(f"could not extract {filename}: {type(exc).__name__}: {exc}") from exc
    text = _clean(text)
    truncated = len(text) > limit
    return text[:limit], truncated

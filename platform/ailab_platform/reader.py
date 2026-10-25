"""Main-content extraction for the Portal's side-pane reader.

`web.read_url` gives the model a page's flat text, chrome included — fine for reading, bad for
showing: language selectors, share bars and footers bury the article. This module turns the same
HTML into the blocks a reader view renders: it drops site chrome (by tag, class and ARIA role),
picks the article region by scoring containers, and keeps structure — headings, lists, images,
code. stdlib only, one pass.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

# A subtree that is chrome or machinery wherever it appears, whatever its class says.
DROP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "frame",
    "frameset",
    "applet",
    "form",
    "button",
    "select",
    "option",
    "optgroup",
    "input",
    "textarea",
    "label",
    "fieldset",
    "datalist",
    "video",
    "audio",
    "object",
    "embed",
    "dialog",
    "nav",
    "header",
    "footer",
    "aside",
    "title",
    "head",
}
VOID_TAGS = {"img", "br", "hr", "meta", "link", "base", "col", "source", "wbr", "area", "param", "track", "input"}
# A class/id naming the surrounding machinery rather than the content: dropped with its subtree.
# Word boundaries (start/end/-/_/space) keep this from matching inside ordinary words ("update", "download").
NOISE_HINT = re.compile(
    r"(?:^|[-_\s])(?:nav|navbar|topbar|menu|submenu|dropdown|sidebar|aside|breadcrumb|crumb|share|social|"
    r"comment|reply|related|recommend|banner|promo|ad|ads|advert|popup|modal|dialog|mask|wechat|weixin|"
    r"qrcode|qr|contact|hotline|subscribe|newsletter|login|register|signin|signup|search|pagination|pager|"
    r"copyright|beian|icp|lang|language|region|locale|service|customer|toolbar|footer|header)(?:[-_\s]|$)",
    re.I,
)
ROLE_DROP = {"navigation", "banner", "complementary", "contentinfo", "search", "menu", "menubar", "dialog", "alertdialog", "form"}
# A container whose class/id claims it is the content: the article-region candidate.
CONTENT_HINT = re.compile(r"(?:^|[-_\s])(?:article|content|main|detail|post|body|text|summary|richtext|zhengwen)(?:[-_\s]|$)", re.I)

HARD_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "li",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "table",
    "figure",
    "figcaption",
    "body",
    "html",
    "center",
}
COLLECT_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "figcaption", "summary", "caption"}
REGION_TAGS = {"div", "section", "article", "main", "table", "blockquote", "center"}
# Elements HTML closes implicitly: <li>a<li>b never sends </li>, so the collector is closed when the
# next same-type tag opens (the endtag walk below covers the rest).
IMPLIED_TAGS = {"p", "li", "dt", "dd", "td", "th"}

MAX_CHARS = 60_000
MAX_BLOCKS = 400
MAX_PRE = 5_000
MIN_REGION = 250  # a container smaller than this is never the article


def _tidy(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _Blocks(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.blocks: list[dict] = []
        self.spans: list[tuple[int, int, str, bool]] = []
        self.chars = 0
        self.seen = 0
        self.skip = 0
        self.link_depth = 0
        self.row_start: int | None = None
        self.root = {"type": "p", "buf": [], "links": 0}
        self.stack: list[dict] = []  # open collectors: {"type", "buf", "links", "ordered"?}
        self.lists: list[str] = []  # open ul/ol, nearest last: a li's ordered comes from here
        self.open: list[tuple[str, bool, tuple[int, str, bool] | None]] = []  # (tag, dropping, region)

    # -- emission ---------------------------------------------------------

    def _top(self) -> dict:
        return self.stack[-1] if self.stack else self.root

    def _emit(self, block: dict) -> None:
        if len(self.blocks) >= MAX_BLOCKS or self.chars >= MAX_CHARS:
            return
        self.blocks.append(block)
        self.chars += len(block.get("text") or "")

    def _flush_root(self) -> None:
        if not self.root["buf"]:
            return
        buf, self.root["buf"] = self.root["buf"], []
        links, self.root["links"] = self.root["links"], 0
        text = _tidy("".join(buf))
        if text:
            self._emit({"type": "p", "text": text, "lt": links})

    def _close_collector(self) -> None:
        c = self.stack.pop()
        buf, c["buf"] = c["buf"], []
        links = c["links"]
        if c["type"] == "pre":
            text = re.sub(r"\n{3,}", "\n\n", "".join(buf)).strip()[:MAX_PRE]
            if text:
                self._emit({"type": "pre", "text": text, "lt": links})
            return
        text = _tidy("".join(buf))
        if text:
            block = {"type": c["type"], "text": text, "lt": links}
            if c.get("ordered"):
                block["ordered"] = True
            self._emit(block)

    def _close_row(self) -> None:
        if self.row_start is None:
            return
        cells = [b["text"] for b in self.blocks[self.row_start :] if b.get("text")]
        self.blocks = self.blocks[: self.row_start]
        # Spans opened inside the row point at merged-away blocks; spans still open (recorded only
        # at close) keep valid indices because the merged row lands exactly at row_start.
        self.spans = [s for s in self.spans if s[0] < self.row_start]
        self.row_start = None
        if cells:
            self._emit({"type": "p", "text": " ｜ ".join(cells), "lt": 0})

    # -- parser events ----------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in VOID_TAGS:
            self._void(tag, attrs)
            return
        a = dict(attrs)
        dropping = self.skip > 0
        if not dropping:
            if tag in DROP_TAGS:
                dropping = True
            else:
                named = f"{a.get('class', '')} {a.get('id', '')}"
                dropping = bool(
                    NOISE_HINT.search(named)
                    or a.get("role", "").lower() in ROLE_DROP
                    or a.get("aria-hidden") == "true"
                    or a.get("hidden") is not None
                )
        if dropping:
            self.skip += 1
            self.open.append((tag, True, None))
            return
        if tag in IMPLIED_TAGS and self.stack and self.stack[-1]["type"] == tag:
            self._close_collector()
        if tag == "tr":
            self._close_row()
            self._flush_root()
            self.row_start = len(self.blocks)
            self.open.append((tag, False, None))
            return
        if tag in HARD_TAGS:
            self._flush_root()
        if tag == "a":
            self.link_depth += 1
        if tag in COLLECT_TAGS:
            self.stack.append({"type": tag, "buf": [], "links": 0, "ordered": bool(self.lists and self.lists[-1] == "ol")})
            self.open.append((tag, False, None))
            return
        region = None
        if tag in ("ul", "ol"):
            self.lists.append(tag)
        elif tag in REGION_TAGS:
            hinted = tag in ("article", "main") or bool(CONTENT_HINT.search(f"{a.get('class', '')} {a.get('id', '')}"))
            region = (len(self.blocks), tag, hinted)
        self.open.append((tag, False, region))

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in VOID_TAGS:
            self._void(tag, attrs)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS or not self.open:
            return
        # Pop to this tag, closing everything implied-closed along the way (<li> without </li>...).
        while self.open:
            otag, dropping, region = self.open.pop()
            if dropping:
                self.skip = max(0, self.skip - 1)
            else:
                if otag in COLLECT_TAGS and self.stack and self.stack[-1]["type"] == otag:
                    self._close_collector()
                if otag == "a" and self.link_depth:
                    self.link_depth -= 1
                if otag in ("ul", "ol") and self.lists:
                    self.lists.pop()
                if otag in HARD_TAGS:
                    self._flush_root()
                if otag == "tr" or otag == "table":
                    self._close_row()
                if region is not None:
                    start, rtag, hinted = region
                    self.spans.append((start, len(self.blocks), rtag, hinted))
            if otag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        self.seen += len(data)
        if self.seen > 3 * MAX_CHARS:  # emitted text is capped by _emit; this bounds one never-closed tag's buffer
            return
        top = self._top()
        top["buf"].append(data)
        if self.link_depth:
            top["links"] += len(data)

    # -- void elements ------------------------------------------------------

    def _void(self, tag: str, attrs) -> None:
        if self.skip:
            return
        if tag == "br":
            self._top()["buf"].append("\n")
        elif tag == "img":
            a = dict(attrs)
            src = (a.get("src") or "").strip()
            if not src:
                return
            if not src.startswith(("http://", "https://", "data:image/")):
                src = urljoin(self.base, src) if self.base else src
            if not src.startswith(("http://", "https://", "data:image/")) or (src.startswith("data:") and len(src) > 20_000):
                return
            self._flush_root()
            self._emit({"type": "img", "src": src[:2000], "alt": (a.get("alt") or "").strip()[:300], "lt": 0})


def _pick_region(blocks: list[dict], spans: list[tuple[int, int, str, bool]]) -> tuple[int, int] | None:
    """The highest-text article-ish container, if it plausibly is the article and not a wrapper."""
    total = sum(len(b.get("text", "")) for b in blocks)
    best: tuple[int, int, int] | None = None  # (size, start, end)
    for start, end, _tag, hinted in spans:
        if end <= start or not hinted:
            continue
        size = sum(len(b.get("text", "")) for b in blocks[start:end])
        if size < MIN_REGION:
            continue
        if best is None or size > best[0]:
            best = (size, start, end)
    if best and best[0] >= max(MIN_REGION, 0.15 * total):
        return best[1], best[2]
    return None


def extract_blocks(html: str, base: str = "") -> list[dict]:
    """A page as reader blocks: headings, paragraphs, list items, images and code, chrome dropped."""
    parser = _Blocks(base)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed HTML must not fail the whole reader
        return []
    parser._close_row()
    parser._flush_root()
    while parser.stack:
        parser._close_collector()
    for _tag, dropping, region in parser.open:  # unclosed containers at EOF
        if dropping:
            parser.skip = max(0, parser.skip - 1)
        elif region is not None:
            start, rtag, hinted = region
            parser.spans.append((start, len(parser.blocks), rtag, hinted))
    blocks = parser.blocks
    region = _pick_region(blocks, parser.spans)
    if region:
        blocks = blocks[region[0] : region[1]]
    out: list[dict] = []
    for b in blocks:
        text = b.get("text") or ""
        if b["type"] != "img" and len(text) < 2:
            continue  # stray close-icons, bullets, separators
        # A short block that is almost entirely a link is a menu, not content.
        if text and len(text) < 200 and b.get("lt", 0) / len(text) > 0.65:
            continue
        if out and text and out[-1].get("text") == text and b["type"] != "img":
            continue  # menus repeated across the page
        out.append(b)
    return out


def markdown_to_blocks(text: str) -> list[dict]:
    """Reader blocks for plain text / markdown / issue bodies: headings, list items and paragraphs."""
    out: list[dict] = []
    code = False
    for line in text.splitlines()[:600]:
        if line.startswith("```"):
            code = not code
            continue
        if not line.strip() or line.strip() in ("---", "***"):
            continue
        if code:
            if out and out[-1]["type"] == "pre":
                out[-1]["text"] += "\n" + line[:500]
            else:
                out.append({"type": "pre", "text": line[:500], "lt": 0})
            continue
        if heading := re.match(r"(#{1,6})\s+(.*)", line):
            out.append({"type": f"h{min(len(heading[1]), 6)}", "text": _tidy(heading[2])[:500], "lt": 0})
        elif re.match(r"\s*[-*+]\s+", line):
            out.append({"type": "li", "text": _tidy(re.sub(r"^\s*[-*+]\s+", "", line))[:1000], "lt": 0})
        elif re.match(r"\s*\d+[.)]\s+", line):
            out.append({"type": "li", "text": _tidy(re.sub(r"^\s*\d+[.)]\s+", "", line))[:1000], "lt": 0, "ordered": True})
        else:
            out.append({"type": "p", "text": _tidy(line)[:2000], "lt": 0})
        if len(out) >= MAX_BLOCKS:
            break
    return out

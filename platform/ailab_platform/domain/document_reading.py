"""Bounded text pagination; no storage, authorization, ORM or implicit OCR."""

import base64
import binascii
import hashlib
import hmac
import json
import unicodedata
from dataclasses import dataclass

MAX_BLOCKS = 100
MAX_HITS = 20
MAX_TEXT = 8000
SNIPPET = 200


class CursorError(ValueError):
    pass


@dataclass(frozen=True)
class Position:
    ordinal: int = 0
    offset: int = 0


@dataclass(frozen=True)
class TextBlock:
    ordinal: int
    locator: str
    text: str
    source: str = "native"
    block_id: str | None = None


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold()


def context_digest(*, operation, file_id, version, representation, selector=None, query=None):
    # This is a compatibility binding, never a permission grant. The API must
    # reauthorize the source before consuming any caller-provided cursor.
    body = [operation, file_id, version, representation, selector, query]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def encode_cursor(context, position, *, key=None):
    body = json.dumps([1, context, position.ordinal, position.offset], separators=(",", ":")).encode()
    if key is not None:
        body += b"." + hmac.digest(key, body, "sha256").hex().encode()
    return base64.urlsafe_b64encode(body).decode().rstrip("=")


def decode_cursor(cursor, context, *, key=None):
    if cursor is None:
        return Position()
    if not isinstance(cursor, str) or len(cursor) > 2048:
        raise CursorError("invalid_cursor")
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        if key is not None:
            raw, signature = raw.rsplit(b".", 1)
            expected = hmac.digest(key, raw, "sha256").hex().encode()
            if not hmac.compare_digest(signature, expected):
                raise CursorError("invalid_cursor")
        body = json.loads(raw)
    except (ValueError, binascii.Error, UnicodeError):
        raise CursorError("invalid_cursor") from None
    if not isinstance(body, list) or len(body) != 4 or body[0] != 1:
        raise CursorError("invalid_cursor")
    if body[1] != context:
        raise CursorError("cursor_stale")
    if any(type(n) is not int or n < 0 or n > 2147483647 for n in body[2:]):
        raise CursorError("invalid_cursor")
    return Position(*body[2:])


def identity(block):
    return {"block_id": block.block_id, "locator": block.locator, "text_source": block.source}


def read_batch(blocks, position=Position()):
    """Caller supplies ordered blocks >= ordinal, at most one lookahead consumed."""
    text, coverage, next_position = "", [], None
    checked = 0
    previous = -1
    for block in blocks:
        if block.ordinal < position.ordinal or block.ordinal <= previous:
            raise ValueError("unordered_document_blocks")
        previous = block.ordinal
        if checked == MAX_BLOCKS or len(text) == MAX_TEXT:
            next_position = Position(block.ordinal)
            break
        checked += 1
        start = position.offset if block.ordinal == position.ordinal else 0
        if start > len(block.text):
            raise CursorError("invalid_cursor")
        remaining = MAX_TEXT - len(text)
        # Keep complete blocks when they fit on a fresh page; only split a block
        # that cannot fit in a single result, with a persistent intra-block offset.
        if text and len(block.text) - start > remaining:
            next_position = Position(block.ordinal, start)
            break
        piece = block.text[start : start + remaining]
        text += piece
        coverage.append({**identity(block), "start": start, "end": start + len(piece)})
        if start + len(piece) < len(block.text):
            next_position = Position(block.ordinal, start + len(piece))
            break
    return {"text": text, "coverage": coverage, "checked_blocks": checked, "next_position": next_position}


def search_batch(blocks, query, position=Position()):
    """NFKC/casefold literal search. Offsets explicitly address normalized text.

    Snippets are navigation evidence, not exact source quotations. Exact quoting
    goes through read using the returned source locator and block identity.
    """
    needle = normalize(query)
    if not needle or not needle.strip() or len(query) > 200:
        raise ValueError("invalid_query")
    hits, checked, next_position, previous = [], 0, None, -1
    for block in blocks:
        if block.ordinal < position.ordinal or block.ordinal <= previous:
            raise ValueError("unordered_document_blocks")
        previous = block.ordinal
        if checked == MAX_BLOCKS or len(hits) == MAX_HITS:
            next_position = Position(block.ordinal)
            break
        checked += 1
        text = normalize(block.text)
        start = position.offset if block.ordinal == position.ordinal else 0
        if start > len(text):
            raise CursorError("invalid_cursor")
        while (found := text.find(needle, start)) >= 0:
            end = found + len(needle)
            excerpt = max(0, found - (SNIPPET - min(SNIPPET, len(needle))) // 2)
            hits.append(
                {
                    **identity(block),
                    "normalized_start": found,
                    "normalized_end": end,
                    "snippet": text[excerpt : excerpt + SNIPPET],
                    "normalization": "NFKC_casefold",
                }
            )
            start = end
            if len(hits) == MAX_HITS:
                # Preserve unreturned matches in this block. A final empty page
                # is preferable to silently claiming the rest has been searched.
                next_position = Position(block.ordinal, start)
                break
        if next_position:
            break
    return {"hits": hits, "checked_blocks": checked, "next_position": next_position}

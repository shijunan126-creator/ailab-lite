"""Bounded native-cache decoding shared by consumers, without HTTP or business DB access."""

import hashlib
import re

CANONICAL_TEXT_BYTES = 1024**2
LEGACY_BLOCK_BYTES = 100000
PARSER_V3 = "office-a60ba6a-ailab3"
PARSER_V4 = "office-a60ba6a-ailab4"


class NativeCacheError(ValueError):
    pass


def read_unit(storage, key, unit, cache=None):
    offset, size = unit.get("byte_offset"), unit.get("byte_size")
    ranged = offset is not None or size is not None
    if ranged and (type(offset) is not int or type(size) is not int or offset < 0 or size < 0 or offset + size > CANONICAL_TEXT_BYTES):
        raise NativeCacheError("invalid_document_range")
    buffers = {} if cache is None else cache
    if ranged and key in buffers:
        body = buffers[key]
    else:
        body = bytearray()
        limit = CANONICAL_TEXT_BYTES if ranged else LEGACY_BLOCK_BYTES
        with storage.open_stream(key) as chunks:
            for chunk in chunks:
                if len(body) + len(chunk) > limit:
                    raise NativeCacheError("document_text_limit")
                body.extend(chunk)
        if ranged:
            buffers[key] = body
    if ranged and offset + size > len(body):
        raise NativeCacheError("invalid_document_range")
    result = bytes(memoryview(body)[offset : offset + size]) if ranged else bytes(body)
    expected = unit.get("sha256") or unit.get("digest")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or hashlib.sha256(result).hexdigest() != expected:
        raise NativeCacheError("document_block_changed")
    return result

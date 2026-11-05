"""Stream plain text into the same two-object native contract without silent truncation."""

import codecs
import hashlib
import json

from ..native_text import PARSER_V4


def prepare(source, output):
    with source.open("rb") as stream:
        signature = stream.read(4)
    encoding = "utf-16" if signature.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
    # Non-UTF8 files get one explicit decoding fallback; no replacement characters.
    candidates = [encoding] if encoding == "utf-16" else [encoding, "gb18030"]
    for encoding in candidates:
        units, chars, offset, pending = [], 0, 0, ""
        try:
            with source.open("r", encoding=encoding, newline="") as stream, (output / "text.txt").open("wb") as target:
                while True:
                    part = stream.read(8192)
                    chars += len(part)
                    if chars > 200000:
                        raise ValueError("document_text_limit")
                    pending += part
                    while len(pending) >= 8000 or (not part and pending):
                        text, pending = pending[:8000], pending[8000:]
                        data = text.encode("utf-8")
                        target.write(data)
                        units.append(
                            {
                                "ordinal": len(units),
                                "anchor": f"block:{len(units) + 1}",
                                "unit_number": len(units) + 1,
                                "name": "text.txt",
                                "byte_offset": offset,
                                "byte_size": len(data),
                                "chars": len(text),
                                "sha256": hashlib.sha256(data).hexdigest(),
                                "quality": "native_unverified",
                                "visual": False,
                            }
                        )
                        offset += len(data)
                    if not part:
                        break
            manifest = {
                "version": 2,
                "parser": PARSER_V4,
                "kind": "text",
                "title": None,
                "total_units": len(units),
                "status": "ready",
                "coverage": [u["anchor"] for u in units],
                "units": units,
                "chars": chars,
                "text_bytes": offset,
                "cells": None,
                "visual_units": [],
                "outline": [],
                "encoding": encoding,
            }
            (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
            return manifest
        except UnicodeDecodeError:
            continue
    raise ValueError("text_encoding_unsupported")

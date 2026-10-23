"""Names identify siblings; file contents continue to use immutable object IDs."""

import hashlib
import unicodedata
from pathlib import PurePath


def name_key(value: str) -> str:
    value = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    return hashlib.sha256(value.encode()).hexdigest()


def numbered_name(value: str, number: int, *, limit: int = 200) -> str:
    suffix = PurePath(value).suffix[:40]
    stem = value[: -len(suffix)] if suffix else value
    ending = f" ({number}){suffix}"
    return stem[: limit - len(ending)] + ending

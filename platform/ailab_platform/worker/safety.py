"""Bounded upload inspection, run only in the Worker's disposable subprocess.

No body extraction, thumbnail, renderer, OCR or native index is produced here.
"""

import hashlib
import struct
import zipfile
from pathlib import Path

MAX_ENTRIES = 10000
MAX_EXPANDED = 100 * 1024**2
MAX_DIRECTORY = 8 * 1024**2
OOXML = {".docx": ("word/document.xml", "docx"), ".pptx": ("ppt/presentation.xml", "pptx"), ".xlsx": ("xl/workbook.xml", "xlsx")}


def directory_guard(source):
    """Count central entries before ZipFile allocates its complete directory.

    ZIP64 is unnecessary under upload/expanded limits. Explicitly reject it,
    split archives and inconsistent directories instead of interpreting offsets.
    """
    with source.open("rb") as stream:
        length = source.stat().st_size
        stream.seek(max(0, length - 65557))
        tail = stream.read(65557)
        # A comment may contain the EOCD signature, so select by exact end length.
        at = len(tail)
        while True:
            at = tail.rfind(b"PK\x05\x06", 0, at)
            if at < 0:
                raise ValueError("archive_corrupt")
            if at + 22 <= len(tail):
                end = struct.unpack_from("<4s4H2LH", tail, at)
                if at + 22 + end[-1] == len(tail):
                    break
        _, disk, central_disk, disk_entries, entries, size, offset, _ = end
        if disk or central_disk or entries != disk_entries or entries == 65535 or size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
            raise ValueError("archive_structure_unsupported")
        if entries > MAX_ENTRIES or size > MAX_DIRECTORY:
            raise ValueError("archive_limit")
        if offset + size != length - len(tail) + at:
            raise ValueError("archive_corrupt")
        stream.seek(offset)
        consumed, count, expanded, names = 0, 0, 0, set()
        while consumed < size:
            fixed = stream.read(46)
            if len(fixed) != 46 or fixed[:4] != b"PK\x01\x02":
                raise ValueError("archive_corrupt")
            fields = struct.unpack("<4s6H3L5H2L", fixed)
            flags, compressed, raw = fields[3], fields[8], fields[9]
            name_size, extra_size, comment_size = fields[10:13]
            consumed += 46 + name_size + extra_size + comment_size
            count += 1
            expanded += raw
            if count > MAX_ENTRIES or expanded > MAX_EXPANDED or consumed > size or name_size > 1024:
                raise ValueError("archive_limit")
            if flags & 1 or compressed == 0xFFFFFFFF or raw == 0xFFFFFFFF:
                raise ValueError("archive_structure_unsupported")
            name = stream.read(name_size).decode("utf-8" if flags & 2048 else "cp437")
            if name in names or name.startswith(("/", "\\")) or "\\" in name or ".." in name.split("/"):
                raise ValueError("archive_unsafe_path")
            names.add(name)
            stream.seek(extra_size + comment_size, 1)
        if count != entries:
            raise ValueError("archive_corrupt")
    return names


def inspect(source: Path, filename: str):
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as stream:
        header = stream.read(1024)
        digest.update(header)
        size += len(header)
        while chunk := stream.read(65536):
            digest.update(chunk)
            size += len(chunk)
    suffix = Path(filename).suffix.lower()
    kind = "binary"
    package = header.startswith((b"PK\x03\x04", b"PK\x05\x06"))
    if package:
        names = directory_guard(source)
        kind = "zip"
        if suffix in OOXML:
            required, kind = OOXML[suffix]
            if required not in names or "[Content_Types].xml" not in names:
                raise ValueError("file_type_mismatch")
        # Verify actual expanded bytes and CRC, not attacker-controlled totals.
        expanded = 0
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                with archive.open(info) as member:
                    while chunk := member.read(65536):
                        expanded += len(chunk)
                        if expanded > MAX_EXPANDED:
                            raise ValueError("archive_limit")
    elif b"%PDF-" in header:
        kind = "pdf"
    elif header.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = "png"
    elif header.startswith(b"\xff\xd8\xff"):
        kind = "jpeg"
    elif header.startswith((b"GIF87a", b"GIF89a")):
        kind = "gif"
    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        kind = "webp"
    if (suffix in OOXML and kind != OOXML[suffix][1]) or (suffix == ".pdf" and kind != "pdf"):
        raise ValueError("file_type_mismatch")
    return {"kind": kind, "sha256": digest.hexdigest(), "bytes": size, "archive_checked": package}

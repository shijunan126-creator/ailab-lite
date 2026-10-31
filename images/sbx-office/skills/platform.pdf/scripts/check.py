"""Check a PDF is readable and report its page count and page boxes."""

import sys

from pypdf import PdfReader


def main(path):
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        raise ValueError("encrypted PDF cannot be inspected")
    if not reader.pages:
        raise ValueError("PDF has no pages")
    for number, page in enumerate(reader.pages, 1):
        box = page.mediabox
        if box.width <= 0 or box.height <= 0:
            raise ValueError(f"page {number} has an invalid page box")
    print(f"OK: {len(reader.pages)} pages; verify order and layout against the request")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: check.py document.pdf")
    try:
        main(sys.argv[1])
    except Exception as exc:
        print(f"ERROR check failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

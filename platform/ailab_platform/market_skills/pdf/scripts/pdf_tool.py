#!/usr/bin/env python3
"""Small PDF utility: merge, split, extract text. Uses pypdf only."""

from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def _pages(spec: str | None, count: int) -> list[int]:
    if not spec:
        return list(range(count))
    selected: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            selected.extend(range(int(start) - 1, int(end)))
        else:
            selected.append(int(part) - 1)
    out = []
    for page in selected:
        if page < 0 or page >= count:
            raise SystemExit(f"page out of range: {page + 1} (document has {count})")
        out.append(page)
    return out


def merge(inputs: list[Path], output: Path) -> None:
    writer = PdfWriter()
    for path in inputs:
        reader = PdfReader(str(path))
        for page in reader.pages:
            writer.add_page(page)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        writer.write(stream)


def split(source: Path, output_dir: Path, pages: str | None) -> list[Path]:
    reader = PdfReader(str(source))
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    stem = source.stem
    for index in _pages(pages, len(reader.pages)):
        writer = PdfWriter()
        writer.add_page(reader.pages[index])
        target = output_dir / f"{stem}-{index + 1}.pdf"
        with target.open("wb") as stream:
            writer.write(stream)
        written.append(target)
    return written


def extract(source: Path, output: Path, pages: str | None) -> None:
    reader = PdfReader(str(source))
    chunks = []
    for index in _pages(pages, len(reader.pages)):
        text = reader.pages[index].extract_text() or ""
        chunks.append(f"\n--- 第 {index + 1} 页 ---\n{text.strip()}\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(chunks).strip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    merge_parser = sub.add_parser("merge")
    merge_parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)

    split_parser = sub.add_parser("split")
    split_parser.add_argument("--input", type=Path, required=True)
    split_parser.add_argument("--output-dir", type=Path, required=True)
    split_parser.add_argument("--pages")

    extract_parser = sub.add_parser("extract")
    extract_parser.add_argument("--input", type=Path, required=True)
    extract_parser.add_argument("--output", type=Path, required=True)
    extract_parser.add_argument("--pages")

    args = parser.parse_args()
    if args.command == "merge":
        merge(args.inputs, args.output)
        print(args.output)
    elif args.command == "split":
        for path in split(args.input, args.output_dir, args.pages):
            print(path)
    else:
        extract(args.input, args.output, args.pages)
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

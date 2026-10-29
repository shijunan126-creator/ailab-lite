"""Render a preview of an office file. Runs INSIDE the sandbox (copied there by the Broker).

    python render_preview.py <file> <out_dir>

Prints one JSON line: {"kind": "pages", "pages": [names...]} for slides / documents / PDFs (JPEG per page)
or {"kind": "sheets", "data": "sheets.json"} for spreadsheets, or {"error": "..."}.
Only standard tools of the sandbox image are used: LibreOffice, pdftoppm, openpyxl.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PAGES = {".pptx", ".potx", ".ppt", ".odp", ".docx", ".doc", ".odt", ".pdf", ".rtf"}
SHEETS = {".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv"}
MAX_PAGES = 40
MAX_ROWS, MAX_COLS = 200, 40
MAX_SHEETS = 20
MAX_CELL_CHARS = 2000
MAX_SHEET_CHARS = 1_000_000


def check_archive(source: Path) -> None:
    if source.suffix.lower() not in {".docx", ".xlsx", ".xlsm", ".pptx", ".potx", ".odt", ".ods", ".odp", ".odf", ".zip"}:
        return
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 2048 or sum(entry.file_size for entry in entries) > 128 * 1024 * 1024:
            raise ValueError("archive_limit")
        if any(entry.file_size > max(1, entry.compress_size) * 200 for entry in entries):
            raise ValueError("archive_limit")


def soffice(src: Path, fmt: str, outdir: Path) -> Path:
    profile = Path(tempfile.mkdtemp(prefix="lo-profile-"))  # a private profile: parallel runs do not collide
    try:
        subprocess.run(
            ["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", fmt, "--outdir", str(outdir), str(src)],
            check=True,
            capture_output=True,
            timeout=90,
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    out = outdir / f"{src.stem}.{fmt.split(':')[0]}"
    if not out.exists():
        raise RuntimeError(f"LibreOffice produced no {fmt} for {src.name}")
    return out


def pdf_document(src: Path, out: Path) -> dict:
    if src.suffix.lower() == ".pdf":
        target = out / "document.pdf"
        shutil.copyfile(src, target)
    else:
        converted = soffice(src, "pdf", out)
        target = out / "document.pdf"
        if converted != target:
            converted.replace(target)
    count = None
    try:
        info = subprocess.run(["pdfinfo", str(target)], capture_output=True, text=True, timeout=15, check=True)
        match = re.search(r"^Pages:\s+(\d+)", info.stdout, re.MULTILINE)
        count = int(match[1]) if match else None
    except Exception:
        pass
    return {"kind": "pdf", "pdf": "document.pdf", "page_count": count}


def pages(src: Path, out: Path, selected: list[int] | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        pdf = src if src.suffix.lower() == ".pdf" else soffice(src, "pdf", Path(tmp))
        info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=15, check=True)
        match = re.search(r"^Pages:\s+(\d+)", info.stdout, re.MULTILINE)
        count = int(match[1]) if match else None
        ranges = [(n, n) for n in selected] if selected else [(1, MAX_PAGES)]
        for first, last in ranges:
            if count is not None and first > count:
                raise ValueError("page_out_of_range")
            subprocess.run(
                [
                    "pdftoppm",
                    "-jpeg",
                    "-jpegopt",
                    "quality=82",
                    "-scale-to",
                    "1280",
                    "-f",
                    str(first),
                    "-l",
                    str(last),
                    str(pdf),
                    str(out / "p"),
                ],
                check=True,
                capture_output=True,
                timeout=90,
            )
    names = sorted((p.name for p in out.glob("p-*.jpg")), key=lambda n: int(n[2:-4]))
    return {"kind": "pages", "pages": names, "page_count": count}


def template_thumbnail(out: Path, result: dict) -> None:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 16_000_000
    with Image.open(out / result["pages"][0]) as image:
        width = min(480, image.width)
        size = (width, max(1, round(image.height * width / image.width)))
        image.convert("RGB").resize(size, Image.Resampling.LANCZOS).save(out / "template-thumbnail.jpg", "JPEG", quality=82)


def _color(c) -> str | None:
    try:
        if c is not None and c.type == "rgb" and isinstance(c.rgb, str) and len(c.rgb) == 8 and c.rgb[:2] != "00":
            return c.rgb[2:]
    except Exception:
        pass
    return None


def _display(v, fmt: str) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        f = fmt or ""
        if "%" in f:
            decimals = len(f.split(".")[1].split("%")[0]) if "." in f else 0
            return f"{v * 100:.{decimals}f}%"
        prefix = "¥" if "¥" in f else "$" if "$" in f else ""
        if "#,##0" in f or prefix:
            decimals = len(f.split(".")[1].rstrip(");-_ ")) if "." in f.split(";")[0] else 0
            decimals = min(decimals, 4)
            return f"{prefix}{v:,.{decimals}f}"
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d") if "h" not in (fmt or "") else v.strftime("%Y-%m-%d %H:%M")
    return str(v)


def sheets(src: Path, out: Path, *, recalculate: bool = True) -> dict:
    if src.suffix.lower() in {".csv", ".tsv"}:
        rows = []
        count = 0
        with src.open(encoding="utf-8-sig", errors="replace", newline="") as stream:
            csv.field_size_limit(200_000)
            for row in csv.reader(stream, delimiter="\t" if src.suffix.lower() == ".tsv" else ","):
                count += 1
                if count > MAX_ROWS:
                    break
                rows.append([{"v": v[:2000]} for v in row[:MAX_COLS]])
        payload = {
            "sheets": [
                {
                    "name": src.stem,
                    "rows": rows,
                    "widths": [],
                    "merged": [],
                    "charts": 0,
                    "truncated": count > MAX_ROWS,
                    "size": [count, max(map(len, rows), default=0)],
                }
            ],
            "errors": [],
        }
        (out / "sheets.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return {"kind": "sheets", "data": "sheets.json", "errors": []}
    from openpyxl import load_workbook

    with tempfile.TemporaryDirectory() as tmp:
        native = src.suffix.lower() in (".xlsx", ".xlsm")
        if recalculate and shutil.which("soffice"):
            # LibreOffice recalculates on load and stores the results, so formula cells get values (openpyxl alone cannot)
            recalced = soffice(src, "xlsx:Calc MS Excel 2007 XML", Path(tmp))
        elif native:
            recalced = src  # no LibreOffice (a dev machine): formulas are shown as written, without values
        else:
            raise RuntimeError("LibreOffice is needed to preview this kind of spreadsheet")
        values = load_workbook(recalced, data_only=True, read_only=not recalculate)
        styles = load_workbook(src if native else recalced, read_only=not recalculate)
        result = []
        errors = []
        char_budget = MAX_SHEET_CHARS
        for ws in values.worksheets[:MAX_SHEETS]:
            if ws.max_row is None or ws.max_column is None:
                ws.calculate_dimension(force=True)
            st = styles[ws.title] if ws.title in styles.sheetnames else ws
            nrows, ncols = min(ws.max_row, MAX_ROWS), min(ws.max_column, MAX_COLS)
            rows = []
            for value_row, style_row in zip(
                ws.iter_rows(max_row=nrows, max_col=ncols), st.iter_rows(max_row=nrows, max_col=ncols), strict=True
            ):
                row = []
                for cell, sc in zip(value_row, style_row, strict=True):
                    text = _display(cell.value, sc.number_format)[:MAX_CELL_CHARS]
                    if not text and isinstance(sc.value, str) and sc.value.startswith("=") and recalced is src:
                        text = sc.value[:MAX_CELL_CHARS]
                    if text.startswith("#") and text.rstrip("!?0/") in ("#DIV", "#REF", "#VALUE", "#NAME", "#N/A", "#NUM", "#NULL"):
                        errors.append(f"{ws.title}!{cell.coordinate} {text}")
                    text = text[: max(0, char_budget)]
                    char_budget -= len(text)
                    item = {"v": text}
                    if sc.font is not None and sc.font.bold:
                        item["b"] = 1
                    if sc.fill is not None and sc.fill.fill_type == "solid" and (bg := _color(sc.fill.fgColor)):
                        item["bg"] = bg
                    if sc.font is not None and (fg := _color(sc.font.color)):
                        item["fg"] = fg
                    if isinstance(cell.value, int | float) and not isinstance(cell.value, bool):
                        item["n"] = 1
                    if isinstance(sc.value, str) and sc.value.startswith("="):
                        item["f"] = sc.value[:200]
                    row.append(item)
                rows.append(row)
            # charts and formatting stretch max_row far past the data: drop the empty tail
            while rows and not any(c["v"] or c.get("bg") for c in rows[-1]):
                rows.pop()
            widths = []
            for c in range(1, ncols + 1):
                from openpyxl.utils import get_column_letter

                dim = getattr(st, "column_dimensions", {}).get(get_column_letter(c))
                widths.append(round(dim.width, 1) if dim is not None and dim.width else None)
            result.append(
                {
                    "name": ws.title,
                    "rows": rows,
                    "widths": widths,
                    "merged": [str(m) for m in st.merged_cells.ranges][:200] if hasattr(st, "merged_cells") else [],
                    "charts": len(getattr(st, "_charts", [])),
                    "truncated": ws.max_row > MAX_ROWS or ws.max_column > MAX_COLS or char_budget <= 0,
                    "size": [ws.max_row, ws.max_column],
                }
            )
        values.close()
        styles.close()
    (out / "sheets.json").write_text(json.dumps({"sheets": result, "errors": errors[:50]}, ensure_ascii=False), encoding="utf-8")
    return {"kind": "sheets", "data": "sheets.json", "errors": errors[:20]}


def main() -> int:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    try:
        check_archive(src)
        if "--pdf" in sys.argv[3:]:
            if ext in PAGES:
                result = pdf_document(src, out)
            elif ext in SHEETS:
                result = sheets(src, out)
            else:
                result = {"error": f"no PDF preview for {ext} files"}
        elif ext in PAGES or (ext in SHEETS and ("--pages" in sys.argv[3:] or "--visual-pages" in sys.argv[3:])):
            selected = None
            if "--pages" in sys.argv[3:]:
                selected = [int(n) for n in sys.argv[sys.argv.index("--pages") + 1].split(",")]
                if not 1 <= len(selected) <= 20 or any(n < 1 or n > 100000 for n in selected):
                    raise ValueError("invalid_page_selection")
            result = pages(src, out, selected)
            if "--template-thumbnail" in sys.argv[3:]:
                template_thumbnail(out, result)
                result["thumbnail"] = "template-thumbnail.jpg"
        elif ext in SHEETS:
            result = sheets(src, out)
        else:
            result = {"error": f"no preview for {ext} files"}
    except subprocess.TimeoutExpired:
        result = {"error": "rendering timed out"}
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

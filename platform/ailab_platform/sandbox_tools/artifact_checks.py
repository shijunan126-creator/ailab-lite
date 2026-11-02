"""Bounded structural evidence inside the sandbox; never a visual quality verdict."""

import hashlib
import zipfile
from pathlib import Path


def check(source: Path) -> dict:
    # This helper executes in the Office image; service startup does not require sandbox libraries.
    from defusedxml import ElementTree as ET

    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {"sha256": digest, "bytes": source.stat().st_size, "format": source.suffix[1:], "issues": []}
    if source.suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(source)
        result.update(pages=len(reader.pages), encrypted=reader.is_encrypted)
        result["limitations"] = ["Text/content correctness and typography require model review."]
        return result
    with zipfile.ZipFile(source) as archive:
        required = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}[source.suffix]
        names = archive.namelist()
        if required not in names or "[Content_Types].xml" not in names:
            raise ValueError("invalid_office_structure")
        formulas = errors = paragraphs = slides = 0
        for name in names:
            if not name.endswith(".xml"):
                continue
            with archive.open(name) as stream:
                for _, element in ET.iterparse(stream, events=("end",)):
                    tag = element.tag.rsplit("}", 1)[-1]
                    formulas += int(tag == "f" and name.startswith("xl/worksheets/"))
                    errors += int(tag == "c" and element.get("t") == "e")
                    paragraphs += int(tag == "p" and name == required and source.suffix == ".docx")
                    slides += int(tag == "sldId")
                    element.clear()
        result.update(formulas=formulas, formula_errors=errors, paragraphs=paragraphs, slides=slides, zip_entries=len(names))
        if errors:
            result["issues"].append("cached_formula_errors")
    result["limitations"] = [
        "Formula caches may be stale; recalculate before review.",
        "Structure alone cannot prove content or visual quality.",
    ]
    return result

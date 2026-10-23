"""Worker side of file OCR: PDF page analysis in the limited parser, then the API job.

No model credential and no business database access here. Inference and billing live in the
API (api/routes_ocr.py). Every transient failure raises, which leaves the queue message unacked;
the API keeps the job pending, so redelivery resumes it and never repeats a finished page.
"""

from ..config import settings
from .isolate import execute


def process_ocr(file_id, platform):
    """Drive one queued OCR job towards a terminal state. Raising means "retry this message later"."""
    from .preview import process_preview

    state = platform.ocr(file_id, prepare=True)
    if state["ocr"] == "running":
        raise RuntimeError("OCR is already running for this file")
    if state["ocr"] != "pending":
        return state
    if state.get("preview_job_id"):
        # The API recorded this preview job as pending; render it through the existing Broker path.
        process_preview(file_id, state["preview_job_id"], platform)
        meta = platform.get_file(file_id, job="preview").get("meta") or {}
        if (meta.get("preview") or {}).get("status") == "pending":
            raise RuntimeError("OCR preview is still rendering")
    result = platform.ocr(file_id)
    if result["ocr"] == "running":
        raise RuntimeError("OCR is already running for this file")
    return result


def low_text_pages(source, output, *, max_pages, min_chars):
    """Runs inside the parser subprocess. Stops counting once more than max_pages qualify, so a long
    text-only document costs no more than its own extraction."""
    from pypdf import PdfReader

    reader = PdfReader(source)
    low = []
    for index, page in enumerate(reader.pages):
        if len((page.extract_text() or "").strip()) < min_chars:
            low.append(index)
            if len(low) > max_pages:
                break
    return {"pages": len(reader.pages), "low_text_pages": low}


def analyze_pdf(source, output):
    return execute("ocr_pdf", source, output, max_pages=settings.ocr_max_pages, min_chars=settings.ocr_min_page_chars)

"""Drive authorized RapidOCR jobs; API alone writes progress and unbilled page metering.

Transient failures leave the queue message unacknowledged so a later delivery resumes
committed pages. No model credential or business database access belongs to the Worker.
"""

from ..config import settings
from .isolate import execute


def process_ocr(file_id, platform):
    """Drive one queued OCR job towards a terminal state. Raising means "retry this message later"."""
    state = platform.ocr(file_id, prepare=True)
    if state["ocr"] == "running":
        raise RuntimeError("OCR is already running for this file")
    if state["ocr"] != "pending":
        return state
    if state.get("preview_job_id"):
        # Preview owns a separate durable claim and the render lane. Do not run
        # it under this OCR attempt or bypass global fairness with a nested call.
        raise RuntimeError("OCR preview is still rendering")
    import time

    deadline = time.monotonic() + min(540, settings.ocr_deadline_seconds)
    for _ in range(21):
        if time.monotonic() >= deadline:
            raise RuntimeError("OCR progress saved; continue on redelivery")
        result = platform.ocr(file_id)
        if result["ocr"] == "pending" and result.get("completed_page"):
            continue
        if result["ocr"] in {"running", "pending"}:
            raise RuntimeError("OCR is still processing")
        return result
    raise RuntimeError("OCR batch progress saved")


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

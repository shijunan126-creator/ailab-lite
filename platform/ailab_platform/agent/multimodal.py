"""Controlled image parts shared by OCR and conversation input builders.

Only normalized bytes fetched through an authorized file binding enter here;
model- or user-supplied URLs are never fetched by the model gateway.
"""

import base64

from ..config import settings

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def image_part(data: bytes, content_type: str) -> dict:
    if content_type not in IMAGE_TYPES:
        raise ValueError("unsupported_image")
    if not data or len(data) > settings.ocr_page_max_bytes:
        raise ValueError("image_size_limit")
    # "high" keeps small print legible; vision_input_tokens reserves for its cost.
    url = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
    return {"type": "image_url", "image_url": {"url": url, "detail": "high"}}


def has_image_part(messages: list[dict]) -> bool:
    """True when a request carries an image: candidates must then be restricted to vision models."""
    return any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"]) for m in messages)


def attachment_content(
    file: dict, *, vision: bool, image: bytes | None = None, content_type: str = "image/webp", text: str = ""
) -> list[dict]:
    label = f"附件《{file['filename']}》" + (f"渲染第{file['page']}页" if file.get("page") else "")
    if vision and image is not None:
        return [{"type": "text", "text": label}, image_part(image, content_type)]
    if text.strip():
        limitation = "（仅文字提取，尚未理解图片内容、图形关系或布局）" if (file.get("content_type") or "").startswith("image/") else ""
        return [{"type": "text", "text": f"{label}{limitation}\n{text}"}]
    if (file.get("content_type") or "").startswith("image/"):
        ocr = (file.get("meta") or {}).get("ocr", "unavailable")
        return [{"type": "text", "text": f"{label}：图片未能读取（OCR {ocr}）。"}]
    return [{"type": "text", "text": f"{label}：无法提取文字内容。"}]


async def input_content(platform, run_id: str, file: dict, spec, *, text: str = "") -> list[dict]:
    """AgentLoop hook: build parts before estimating/reserving the model call.

    Return parts, not a new user message, to preserve attachment turn ordering.
    Callers must require vision in ModelPolicy.candidates whenever parts contain
    an image, so a fallback cannot send them to a text-only model.
    """
    image = None
    if spec.supports({"vision"}):
        from ..clients import ClientError

        try:
            image = (
                (await platform.get_file_page_image(file["id"], run_id, file["page"]))
                if file.get("page")
                else (await platform.get_file_image(file["id"], run_id))
            )
        except (ClientError, ValueError):
            pass
    return attachment_content(
        file, vision=spec.supports({"vision"}), image=image, content_type="image/jpeg" if file.get("page") else "image/webp", text=text
    )

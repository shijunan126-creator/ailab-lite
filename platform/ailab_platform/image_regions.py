"""Bounded image regions, imported only by sandbox or Worker parser subprocesses."""

import hashlib
import io
import math

from PIL import Image, ImageOps

MAX_PIXELS = 16_000_000
MAX_OUTPUT = 2 * 1024**2
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def prepare(source, root, region=None):
    with Image.open(source) as opened:
        if opened.format not in {"PNG", "JPEG", "WEBP"} or opened.width * opened.height > MAX_PIXELS:
            raise ValueError("image_pixel_limit")
        ImageOps.exif_transpose(opened, in_place=True)
        working, actual_region = opened, None
        try:
            if region:
                rotation = region.get("rotation", 0)
                values = [region[k] for k in ("x", "y", "width", "height")]
                if rotation not in {0, 90, 180, 270} or any(type(v) not in {int, float} or not math.isfinite(v) for v in values):
                    raise ValueError("invalid_region")
                x, y, width, height = values
                if not (0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0 and x + width <= 1 and y + height <= 1):
                    raise ValueError("invalid_region")
                if rotation:
                    working = opened.rotate(-rotation, expand=True)
                frame_width, frame_height = working.size
                bounds = (
                    math.floor(x * frame_width),
                    math.floor(y * frame_height),
                    math.ceil((x + width) * frame_width),
                    math.ceil((y + height) * frame_height),
                )
                actual_x, actual_y = bounds[0] / frame_width, bounds[1] / frame_height
                actual_region = {
                    "x": actual_x,
                    "y": actual_y,
                    "width": min(1 - actual_x, (bounds[2] - bounds[0]) / frame_width),
                    "height": min(1 - actual_y, (bounds[3] - bounds[1]) / frame_height),
                    "rotation": rotation,
                }
                if bounds != (0, 0, frame_width, frame_height):
                    cropped = working.crop(bounds)
                    if working is not opened:
                        working.close()
                    working = cropped
            if working.width * working.height > MAX_PIXELS:
                raise ValueError("image_pixel_limit")

            def encode(rgb):
                with io.BytesIO() as output:
                    rgb.save(output, "JPEG", quality=90, optimize=False, progressive=False)
                    return output.getvalue()

            # Flatten alpha as displayed on white, without retaining duplicate
            # full RGBA/EXIF buffers for the common already-oriented image.
            if working.mode in {"RGBA", "LA"} or "transparency" in working.info:
                rgba = working if working.mode == "RGBA" else working.convert("RGBA")
                try:
                    with Image.new("RGB", working.size, "white") as rgb, rgba.getchannel("A") as alpha:
                        rgb.paste(rgba, mask=alpha)
                        payload = encode(rgb)
                finally:
                    if rgba is not working:
                        rgba.close()
            elif working.mode == "RGB":
                payload = encode(working)
            else:
                with working.convert("RGB") as rgb:
                    payload = encode(rgb)
            if len(payload) > MAX_OUTPUT:
                raise ValueError("image_size_limit")
            (root / "image.jpg").write_bytes(payload)
            return {
                "image_sha256": hashlib.sha256(payload).hexdigest(),
                "width": working.width,
                "height": working.height,
                "actual_region": actual_region,
            }
        finally:
            if working is not opened:
                working.close()

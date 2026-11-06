"""Normalize one selected image inside its existing Work sandbox; no network or credentials."""

import hashlib
import io
import json
import math
import os
import stat
import sys
from pathlib import Path

from PIL import Image, ImageOps

MAX_INPUT = 20 * 1024**2
MAX_PIXELS = 16_000_000
MAX_OUTPUT = 2 * 1024**2
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def snapshot(path, target):
    relative = os.path.relpath(path, "/workspace")
    parts = relative.split(os.sep)
    if any(part in {"", ".", ".."} for part in parts) or relative.startswith(".ailab/"):
        raise ValueError("invalid_workspace_path")
    directory = os.open("/workspace", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "rb") as source, target.open("wb") as output:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INPUT:
                raise ValueError("image_input_limit")
            digest, size = hashlib.sha256(), 0
            while chunk := source.read(65536):
                size += len(chunk)
                if size > MAX_INPUT:
                    raise ValueError("image_input_limit")
                digest.update(chunk)
                output.write(chunk)
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("workspace_source_changed")
            return digest.hexdigest()
    finally:
        os.close(directory)


def main():
    path, directory, raw_region = sys.argv[1:4]
    root = Path(directory)
    source = root / "source"
    region = json.loads(raw_region)
    digest = snapshot(path, source)
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
            (root / "image.json").write_text(
                json.dumps(
                    {
                        "source_sha256": digest,
                        "image_sha256": hashlib.sha256(payload).hexdigest(),
                        "width": working.width,
                        "height": working.height,
                        "actual_region": actual_region,
                    }
                )
            )
        finally:
            if working is not opened:
                working.close()


try:
    main()
except Exception as exc:
    reason = (
        str(exc)
        if str(exc)
        in {
            "invalid_workspace_path",
            "image_input_limit",
            "image_pixel_limit",
            "image_size_limit",
            "workspace_source_changed",
            "invalid_region",
        }
        else "image_preparation_failed"
    )
    print(json.dumps({"error": reason}), file=sys.stderr)
    sys.exit(2)

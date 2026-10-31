"""Bounded visual rendering inside the current run sandbox."""

import base64
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from render_preview import check_archive, pages

MAX_INPUT = 20 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_IMAGE = 512 * 1024
MAX_TOTAL = 1200 * 1024
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def copy_source(root: str, path: str, target: Path) -> None:
    relative = os.path.relpath(path, root)
    parts = relative.split(os.sep)
    if relative == "." or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid_workspace_path")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    directory = os.open(root, flags | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        fd = os.open(parts[-1], flags, dir_fd=directory)
        with os.fdopen(fd, "rb") as source, target.open("wb") as dest:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT:
                raise ValueError("input_limit")
            remaining = MAX_INPUT + 1
            while remaining:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                dest.write(chunk)
                remaining -= len(chunk)
            if not remaining:
                raise ValueError("input_limit")
    finally:
        os.close(directory)


def normalize(path: Path) -> bytes:
    with Image.open(path) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("pixel_limit")
        image = ImageOps.exif_transpose(image)
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        else:
            rgb = image.convert("RGB")
        output = io.BytesIO()
        rgb.save(output, "JPEG", quality=75, optimize=False, progressive=False, subsampling=2)
        data = output.getvalue()
        if len(data) > MAX_IMAGE:
            raise ValueError("image_size_limit")
        return data


def main() -> None:
    root, path, output, selection = sys.argv[1:]
    selected = json.loads(selection)
    suffix = Path(path).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".pdf", ".docx", ".pptx", ".xlsx"}:
        raise ValueError("unsupported_file_type")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 4 or any(type(n) is not int or not 1 <= n <= 100000 for n in selected):
        raise ValueError("invalid_page_selection")
    if len(set(selected)) != len(selected):
        raise ValueError("invalid_page_selection")
    with tempfile.TemporaryDirectory(prefix="ailab-view-") as tmp:
        work = Path(tmp)
        source = work / ("input" + suffix)
        copy_source(root, path, source)
        check_archive(source)
        if suffix in {".png", ".jpg", ".jpeg"}:
            if selected != [1]:
                raise ValueError("page_out_of_range")
            names = [source]
        else:
            rendered = work / "pages"
            rendered.mkdir()
            result = pages(source, rendered, selected)
            names = [rendered / name for name in result["pages"]]
        images = [normalize(name) for name in names]
        if sum(map(len, images)) > MAX_TOTAL:
            raise ValueError("output_limit")
        payload = {"pages": selected, "images": [base64.b64encode(image).decode("ascii") for image in images]}
        Path(output).write_text(json.dumps(payload, separators=(",", ":")), encoding="ascii")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"view failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

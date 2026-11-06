"""Normalize one selected image inside its existing Work sandbox; no network or credentials."""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

MAX_INPUT = 20 * 1024**2


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
    from image_regions import prepare

    result = prepare(source, root, region)
    (root / "image.json").write_text(json.dumps({"source_sha256": digest, **result}))


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

"""Snapshot an authorized ordinary workspace file before parsing or streaming it."""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path


def snapshot(root, path, output):
    root, path, output = Path(root), Path(path), Path(output)
    relative = path.relative_to(root)
    if not relative.parts or any(p in {"..", ".ailab"} for p in relative.parts):
        raise ValueError("invalid_workspace_path")
    # Open every component relative to its parent, without following symlinks (including the final file).
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 20 * 1024**2:
                raise ValueError("input_limit")
            digest, size = hashlib.sha256(), 0
            with output.open("xb") as target:
                while chunk := source.read(65536):
                    size += len(chunk)
                    if size > 20 * 1024**2:
                        raise ValueError("input_limit")
                    digest.update(chunk)
                    target.write(chunk)
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("version_changed")
        return {"version": digest.hexdigest(), "bytes": size}
    finally:
        os.close(directory)


if __name__ == "__main__":
    print(json.dumps(snapshot(*sys.argv[1:])))

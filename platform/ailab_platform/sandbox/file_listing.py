"""Bounded structured enumeration, shared with the program executed inside real sandboxes."""

LIST_PROGRAM = r"""import hashlib, json, os, stat, sys, time
root = sys.argv[1]
hashing = sys.argv[2] == "hash"
limit = int(sys.argv[3])
byte_limit = int(sys.argv[4])
files, total, visited = [], 0, 0
deadline = time.monotonic() + 45
try:
    if os.path.islink(root):
        raise ValueError("symlink listing root")
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > 10000 or time.monotonic() > deadline:
                    raise ValueError("file enumeration limit exceeded")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(entry.path)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("non-regular output")
                if len(files) >= limit:
                    raise ValueError("file count limit exceeded")
                path = entry.path
                row = {"path": path, "size": info.st_size}
                if hashing:
                    total += info.st_size
                    if total > byte_limit:
                        raise ValueError("output hash byte limit exceeded")
                    digest, count = hashlib.sha256(), 0
                    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
                        for chunk in iter(lambda: source.read(1048576), b""):
                            count += len(chunk)
                            if count > info.st_size or time.monotonic() > deadline:
                                raise ValueError("output changed or hashing deadline exceeded")
                            digest.update(chunk)
                    after = os.stat(path, follow_symlinks=False)
                    before_fp = (info.st_ino, info.st_size, info.st_mtime_ns)
                    if count != info.st_size or (after.st_ino, after.st_size, after.st_mtime_ns) != before_fp:
                        raise ValueError("output changed during hashing")
                    row["sha256"] = digest.hexdigest()
                files.append(row)
    result = json.dumps({"files": sorted(files, key=lambda row: row["path"]), "complete": True}, ensure_ascii=True)
    if len(result) > 120000:
        raise ValueError("file manifest byte limit exceeded")
    print(result)
except (OSError, ValueError) as exc:
    print(json.dumps({"complete": False, "error": str(exc)}))
"""


def parse_listing(stdout):
    import json

    from .base import SandboxError

    try:
        result = json.loads(stdout)
        if result.get("complete") is not True:
            raise ValueError(result.get("error", "incomplete file enumeration"))
        return result["files"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SandboxError("文件枚举未完成: " + str(exc)) from exc

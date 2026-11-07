"""Fetch the exact APT-resolved closure with bounded downloads and SHA256 checks.

The authenticated snapshot index remains authoritative. A current Debian mirror
may supply identical package bytes; it never chooses versions or dependencies.
"""

import hashlib
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class DownloadError(RuntimeError):
    pass


def matches(path, size, digest):
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == digest


def package(line):
    uri, filename, size, checksum = shlex.split(line)
    url = urllib.parse.urlsplit(uri)
    if (
        url.scheme != "https"
        or url.netloc != "snapshot.debian.org"
        or not url.path.startswith("/archive/debian/")
        or "/pool/" not in url.path
        or Path(filename).name != filename
        or not filename.endswith(".deb")
        or not checksum.startswith("SHA256:")
    ):
        raise DownloadError("invalid snapshot package identity")
    digest = checksum.removeprefix("SHA256:")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise DownloadError("invalid snapshot package digest")
    size = int(size)
    if not 0 < size <= 200 * 1024**2:
        raise DownloadError("package size exceeds build budget")
    mirror = "https://deb.debian.org/debian/pool/" + url.path.split("/pool/", 1)[1]
    return filename, size, digest, (mirror, uri)


def fetch(item, directory, deadline):
    filename, size, digest, candidates = item
    target = directory / filename
    if matches(target, size, digest):
        return
    temporary = target.with_suffix(".download")
    for uri in candidates:
        temporary.unlink(missing_ok=True)
        try:
            if time.monotonic() >= deadline:
                raise DownloadError("snapshot acquisition deadline")
            request = urllib.request.Request(
                uri, headers={"User-Agent": "AILab-pinned-package-build/1"}
            )
            with (
                urllib.request.urlopen(
                    request, timeout=min(20, deadline - time.monotonic())
                ) as response,
                temporary.open("xb") as out,
            ):
                downloaded = 0
                while chunk := response.read(65536):
                    downloaded += len(chunk)
                    if downloaded > size or time.monotonic() >= deadline:
                        raise DownloadError("package transfer budget exceeded")
                    out.write(chunk)
            if not matches(temporary, size, digest):
                raise DownloadError("snapshot package integrity mismatch: " + filename)
            os.replace(temporary, target)
            print("Verified", filename, size, flush=True)
            return
        except DownloadError:
            raise  # Never turn a digest or budget violation into a mirror retry.
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(
                "Unavailable",
                urllib.parse.urlsplit(uri).netloc,
                filename,
                type(exc).__name__,
                flush=True,
            )
        finally:
            temporary.unlink(missing_ok=True)
    raise DownloadError("pinned package unavailable: " + filename)


def main(packages):
    result = subprocess.run(
        [
            "apt-get",
            "-o",
            "Acquire::ForceHash=SHA256",
            "--print-uris",
            "--yes",
            "--no-install-recommends",
            "install",
            *packages,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    items = [
        package(line) for line in result.stdout.splitlines() if line.startswith("'")
    ]
    if len(items) > 1000 or sum(item[1] for item in items) > 2 * 1024**3:
        raise DownloadError("APT closure exceeds build budget")
    directory = Path("/var/cache/apt/archives")
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 1200
    for item in items:
        fetch(item, directory, deadline)


if __name__ == "__main__":
    main(sys.argv[1:])

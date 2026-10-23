"""Public GitHub archive acquisition with a deliberately small tar extractor."""

from __future__ import annotations

import io
import re
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from . import web
from .config import settings

_REPO = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$", re.I)
_SHORT = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$")


def parse_github(url: str) -> tuple[str, str]:
    value = url.strip()
    match = _REPO.match(value) or _SHORT.match(value)
    if not match:
        raise web.WebError("expected a public GitHub URL such as https://github.com/owner/repo")
    owner, repo = match.group(1), match.group(2)
    if not owner or not repo or repo.startswith("-"):
        raise web.WebError("invalid GitHub owner/repo")
    return owner, repo


def _safe_root(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-")[:120]
    return value or "repo"


def _extract(data: bytes, root_name: str) -> tuple[bytes, int, int]:
    max_files = settings.web_repo_max_files
    max_bytes = settings.web_repo_max_uncompressed_bytes
    files: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > max_files:
            raise web.WebError(f"repository has too many entries ({len(members)} > {max_files})")
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise web.WebError(f"unsafe archive path: {member.name[:200]}")
            if member.issym() or member.islnk():
                raise web.WebError(f"repository archive contains a link: {member.name[:200]}")
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                raise web.WebError(f"repository archive contains a special file: {member.name[:200]}")
            if member.isdir():
                continue
            if not member.isfile():
                continue
            relative = PurePosixPath(*name.parts[1:]) if len(name.parts) > 1 else name
            if not relative.parts:
                continue
            total += member.size
            if total > max_bytes:
                raise web.WebError(f"repository is too large after extraction ({total} > {max_bytes} bytes)")
            source = archive.extractfile(member)
            if source is None:
                continue
            files[relative.as_posix()] = source.read()
    if not files:
        raise web.WebError("repository archive was empty")

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for relative, content in sorted(files.items()):
            info = tarfile.TarInfo(f"{root_name}/{relative}")
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue(), len(files), total


async def fetch(url: str, ref: str | None = None) -> dict:
    owner, repo = parse_github(url)
    clean_ref = (ref or "").strip()
    if clean_ref and not re.fullmatch(r"[A-Za-z0-9._/@-]{1,200}", clean_ref):
        raise web.WebError("invalid Git ref")
    archive_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{quote(clean_ref, safe='') if clean_ref else 'HEAD'}"
    downloaded = await web.download(archive_url, settings.web_repo_max_bytes)
    root_name = _safe_root(f"{owner}-{repo}")
    archive, files, size = _extract(downloaded["data"], root_name)
    return {
        "owner": owner,
        "repo": repo,
        "ref": clean_ref or "HEAD",
        "root_name": root_name,
        "path": f"/workspace/inputs/repos/{root_name}",
        "archive": archive,
        "files": files,
        "uncompressed_bytes": size,
        "archive_bytes": len(archive),
        "sha256": downloaded["sha256"],
    }


def extract_command(archive_path: str, target_dir: str) -> str:
    return (
        "mkdir -p /workspace/inputs/repos && "
        f"tar -xzf {shlex.quote(archive_path)} -C /workspace/inputs/repos && "
        f"rm -f {shlex.quote(archive_path)} && "
        f"test -d {shlex.quote(target_dir)}"
    )


# Path is intentionally exported for callers that want to validate a name
# without importing the private helper.
def path_for(root_name: str) -> Path:
    return Path("/workspace/inputs/repos") / _safe_root(root_name)

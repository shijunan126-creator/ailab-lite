from __future__ import annotations

import io
import tarfile

import pytest

from ailab_platform import repo, web


def _tar(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


def test_parse_github_urls() -> None:
    assert repo.parse_github("https://github.com/deepseek-ai/deepseek-harness") == ("deepseek-ai", "deepseek-harness")
    assert repo.parse_github("https://github.com/deepseek-ai/deepseek-harness.git") == ("deepseek-ai", "deepseek-harness")
    assert repo.parse_github("deepseek-ai/deepseek-harness") == ("deepseek-ai", "deepseek-harness")
    with pytest.raises(web.WebError):
        repo.parse_github("https://gitlab.com/owner/repo")


def test_repo_archive_is_rebuilt_without_links_or_parent_paths() -> None:
    archive, files, size = repo._extract(
        _tar(
            {
                "demo-main/README.md": b"# demo\n",
                "demo-main/src/app.py": b"print('ok')\n",
            }
        ),
        "demo",
    )
    assert files == 2
    assert size == len(b"# demo\n") + len(b"print('ok')\n")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as rebuilt:
        names = rebuilt.getnames()
        assert "demo/README.md" in names
        assert "demo/src/app.py" in names
        assert all(not name.startswith("/") for name in names)


def test_repo_archive_rejects_path_traversal() -> None:
    with pytest.raises(web.WebError):
        repo._extract(_tar({"demo-main/../evil": b"x"}), "demo")


def test_repo_archive_rejects_symlinks() -> None:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        info = tarfile.TarInfo("demo-main/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    with pytest.raises(web.WebError):
        repo._extract(out.getvalue(), "demo")


def test_download_filename_is_sanitized() -> None:
    assert web._safe_filename("https://example.com/a/b.pdf", "application/pdf", "") == "b.pdf"
    assert web._safe_filename("https://example.com/", "application/pdf", 'filename="../../x.pdf"') == "x.pdf"
    assert web._safe_filename("https://example.com/", "application/octet-stream", "") == "download.bin"

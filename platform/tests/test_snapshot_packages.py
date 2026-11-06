"""Pinned package transport preserves the signed snapshot identities on mirror failures."""

import hashlib
import importlib.util
import io
import time
import urllib.error
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("snapshot_fetch", Path(__file__).resolve().parents[2] / "images/sbx-office/fetch_snapshot.py")
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


def item(data=b"package"):
    digest = hashlib.sha256(data).hexdigest()
    return fetcher.package(
        "'https://snapshot.debian.org/archive/debian/20261101T000000Z/pool/main/p/pkg/pkg_1_arm64.deb' "
        f"pkg_1_arm64.deb {len(data)} SHA256:{digest}"
    )


def test_primary_mirror_bytes_are_identical_and_cached_without_network(tmp_path, monkeypatch):
    calls = []

    def download(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(b"package")

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", download)
    fetcher.fetch(item(), tmp_path, time.monotonic() + 10)
    fetcher.fetch(item(), tmp_path, time.monotonic() + 10)
    assert calls == ["https://deb.debian.org/debian/pool/main/p/pkg/pkg_1_arm64.deb"]
    assert (tmp_path / "pkg_1_arm64.deb").read_bytes() == b"package"
    assert list(tmp_path.glob("*.download")) == []


def test_unavailable_mirror_falls_back_to_exact_snapshot(tmp_path, monkeypatch):
    calls = []

    def download(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 404, "not on mirror", {}, None)
        return io.BytesIO(b"package")

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", download)
    fetcher.fetch(item(), tmp_path, time.monotonic() + 10)
    assert len(calls) == 2 and calls[-1] == item()[-1][-1]


def test_hash_mismatch_cannot_publish_or_retry_and_corrupt_cache_is_replaced(tmp_path, monkeypatch):
    (tmp_path / "pkg_1_arm64.deb").write_bytes(b"corrupt")
    calls = []

    def download(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(b"invalid")

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", download)
    with pytest.raises(fetcher.DownloadError, match="integrity mismatch"):
        fetcher.fetch(item(), tmp_path, time.monotonic() + 10)
    assert len(calls) == 1 and not list(tmp_path.glob("*.download"))
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"package"))
    fetcher.fetch(item(), tmp_path, time.monotonic() + 10)
    assert (tmp_path / "pkg_1_arm64.deb").read_bytes() == b"package"


def test_expired_deadline_does_not_start_a_download(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *a, **k: pytest.fail("unexpected download"))
    with pytest.raises(fetcher.DownloadError, match="deadline"):
        fetcher.fetch(item(), tmp_path, time.monotonic() - 1)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("substitution", ["http://snapshot.debian.org", "https://attacker.example"])
def test_only_signed_snapshot_package_identities_are_accepted(substitution):
    line = "'https://snapshot.debian.org/archive/debian/20261101T000000Z/pool/main/p/p.deb' p.deb 1 SHA256:" + "a" * 64
    with pytest.raises(fetcher.DownloadError):
        fetcher.package(line.replace("https://snapshot.debian.org", substitution))

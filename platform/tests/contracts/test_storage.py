"""One contract for local objects and an optional real S3 endpoint."""

import io
import os
import uuid

import httpx
import pytest

from ailab_platform.config import settings
from ailab_platform.storage import LocalStorage, S3Storage


@pytest.fixture(params=["local", "s3"])
def storage(request, tmp_path, monkeypatch):
    if request.param == "local":
        yield LocalStorage(tmp_path)
        return
    endpoint = os.environ.get("AILAB_TEST_S3_URL")
    access = os.environ.get("AILAB_TEST_S3_ACCESS_KEY")
    secret = os.environ.get("AILAB_TEST_S3_SECRET_KEY")
    if not all((endpoint, access, secret)):
        pytest.skip("AILAB_TEST_S3_URL / ACCESS_KEY / SECRET_KEY not set")
    for name, value in (
        ("s3_endpoint", endpoint),
        ("s3_access_key", access),
        ("s3_secret_key", secret),
        ("s3_bucket", "contract-" + uuid.uuid4().hex),
    ):
        monkeypatch.setattr(settings, name, value)
    store = S3Storage()
    store.client.create_bucket(Bucket=store.bucket)
    try:
        yield store
    finally:
        page = store.list_prefix("")
        while page.items:
            for item in page:
                store.delete(item.key)
            page = store.list_prefix("")
        store.client.delete_bucket(Bucket=store.bucket)
        store.client.close()


def test_stream_info_and_cap_is_atomic(storage):
    storage.put_stream("test/data.txt", iter([b"one", b"two"]), "text/plain", max_bytes=6)
    info = storage.info("test/data.txt")
    assert info.size == 6 and info.etag and info.content_type == "text/plain"
    with storage.open_stream("test/data.txt", 2) as chunks:
        assert list(chunks) == [b"on", b"et", b"wo"]
    with pytest.raises(ValueError, match="exceeds"):
        storage.put_stream("test/data.txt", io.BytesIO(b"too much"), max_bytes=2)
    assert storage.get_bytes("test/data.txt") == b"onetwo"
    assert [i.key for i in storage.list_prefix("")] == ["test/data.txt"]


def test_copy_freezes_and_retries(storage):
    storage.put_bytes("signed/x", b"approved", "text/plain")
    storage.copy("signed/x", "frozen/x")
    storage.copy("signed/x", "frozen/x")
    storage.put_bytes("signed/x", b"replacement")
    assert storage.get_bytes("frozen/x") == b"approved"
    assert storage.info("frozen/x").content_type == "text/plain"
    storage.copy("frozen/x", "frozen/x")
    assert storage.get_bytes("frozen/x") == b"approved"


def test_list_pagination_and_delete_prefix(storage, monkeypatch):
    for i in range(7):
        storage.put_bytes(f"test/{i}", bytes([i]))
    storage.put_bytes("testing/keep", b"other")
    cursor = None
    keys = []
    while True:
        page = storage.list_prefix("test/", cursor=cursor, limit=2)
        assert len(page.items) <= 2
        keys.extend(item.key for item in page)
        cursor = page.cursor
        if cursor is None:
            break
    assert keys == [f"test/{i}" for i in range(7)]
    original = storage.list_prefix
    monkeypatch.setattr(storage, "list_prefix", lambda prefix, cursor=None, limit=1000: original(prefix, cursor, min(limit, 3)))
    assert storage.delete_prefix("test/") == 7
    assert storage.delete_prefix("test/") == 0
    assert storage.exists("testing/keep")


def test_early_read_and_exception_close_underlying_handle(storage, monkeypatch):
    storage.put_bytes("x", b"abcdef")
    handles = []
    if isinstance(storage, S3Storage):
        original = storage.client.get_object

        def get(**kw):
            result = original(**kw)
            handles.append(result["Body"]._raw_stream)
            return result

        monkeypatch.setattr(storage.client, "get_object", get)
    else:
        import builtins

        original = builtins.open

        def get(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == storage.root / "x" and args[1] == "rb":
                handles.append(result)
            return result

        monkeypatch.setattr(builtins, "open", get)
    with pytest.raises(RuntimeError), storage.open_stream("x", 2) as chunks:
        assert next(chunks) == b"ab"
        raise RuntimeError("client disconnected")
    assert handles and all(handle.closed for handle in handles)


def test_presigned_url_and_freeze(storage):
    signed = storage.presign_put("signed/x")
    if isinstance(storage, LocalStorage):
        assert signed is None and storage.presign_get("signed/x") is None
        return
    with httpx.Client(timeout=10) as client:
        assert client.put(signed, content=b"first").is_success
        storage.copy("signed/x", "frozen/x")
        assert client.put(signed, content=b"second").is_success
        assert client.get(storage.presign_get("frozen/x")).content == b"first"


def test_failed_source_leaves_no_object(storage):
    def source():
        yield b"partial"
        raise RuntimeError("producer failed")

    with pytest.raises(RuntimeError, match="producer"):
        storage.put_stream("test/failed", source(), max_bytes=100)
    assert not storage.exists("test/failed")


def test_copy_pins_source_before_concurrent_replacement(storage, monkeypatch):
    if not isinstance(storage, LocalStorage):
        pytest.skip("local inode replacement interleaving")
    from pathlib import Path

    storage.put_bytes("signed/x", b"a" * 100)
    original = Path.open
    replaced = False

    def opened(path, *args, **kwargs):
        nonlocal replaced
        handle = original(path, *args, **kwargs)
        if path == storage.root / "signed/x" and not replaced and args and args[0] == "rb":
            replaced = True
            storage.put_bytes("signed/x", b"changed length")
        return handle

    monkeypatch.setattr(Path, "open", opened)
    storage.copy("signed/x", "frozen/x")
    assert replaced and storage.get_bytes("frozen/x") == b"a" * 100


def test_hidden_user_filenames_remain_supported(storage):
    storage.put_bytes("uploads/user/file/.env", b"user file")
    assert storage.get_bytes("uploads/user/file/.env") == b"user file"
    assert [i.key for i in storage.list_prefix("uploads/")] == ["uploads/user/file/.env"]

from __future__ import annotations

from ailab_platform.storage import LocalStorage


def test_local_storage_streams_a_file_without_loading_it(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"abcdef" * 1000)

    storage.put_file("objects/a.bin", source, "application/octet-stream")
    assert storage.size("objects/a.bin") == 6000
    assert b"".join(storage.stream("objects/a.bin", chunk_size=7)) == source.read_bytes()
    assert storage.get_bytes("objects/a.bin") == source.read_bytes()


def test_concurrent_first_use_after_close_shares_one_storage(monkeypatch) -> None:
    # A shutdown drops the shared instance; a request and a background loop that
    # ask for it at the same moment must not each build one (the losing instance
    # is never closed and its callers diverge from everyone else's).
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from ailab_platform import storage as module

    built = []
    original = module.LocalStorage

    class SlowLocal(original):
        def __init__(self, *args, **kwargs):
            time.sleep(0.05)
            super().__init__(*args, **kwargs)
            built.append(self)

    module.close_storage()
    monkeypatch.setattr(module, "LocalStorage", SlowLocal)
    monkeypatch.setattr(module.settings, "storage_backend", "local")
    start = threading.Barrier(4)

    def first_use():
        start.wait()
        return module.get_storage()

    try:
        with ThreadPoolExecutor(4) as pool:
            instances = list(pool.map(lambda _: first_use(), range(4)))
        assert len(built) == 1 and all(item is built[0] for item in instances)
    finally:
        module.close_storage()

"""Service-side storage writes with API capacity grants and failed-write compensation.

The API alone publishes staged bytes with business metadata. This wrapper owns
only write/transfer leases; it never grants read permission or writes the DB.
"""

from contextlib import contextmanager


class AccountedWrites:
    def __init__(self, storage, platform, version, job_id):
        self.storage, self.platform = storage, platform
        self.version, self.job_id = version, job_id
        self.transferred = False

    def __getattr__(self, name):
        return getattr(self.storage, name)

    def _write(self, key, maximum, action):
        if not key.startswith(("derived/", "viewer/", "previews/")):
            return action()
        native = key.endswith(".txt") or "/office/" in key or key.endswith("viewer.json")
        grant = self.platform.reserve_derived(
            key=key, version=self.version, max_bytes=maximum, kind="native" if native else "cache", job_id=self.job_id
        )
        result = action()
        self.platform.finish_derived(key, grant["token"])
        return result

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        return self._write(key, len(data), lambda: self.storage.put_bytes(key, data, content_type))

    def put_file(self, key, path, content_type="application/octet-stream"):
        from pathlib import Path

        return self._write(key, Path(path).stat().st_size, lambda: self.storage.put_file(key, path, content_type))

    def put_stream(self, key, stream, content_type="application/octet-stream", *, max_bytes):
        return self._write(key, max_bytes, lambda: self.storage.put_stream(key, stream, content_type, max_bytes=max_bytes))

    def copy(self, source, target):
        return self._write(target, self.storage.info(source).size, lambda: self.storage.copy(source, target))

    def handoff(self):
        # The same durable writer/attempt inventory is now owned by the Worker
        # completion path; process loss is covered by staged-lease compensation.
        self.transferred = True

    def cleanup(self):
        if self.transferred:
            return
        # Publication responses can be lost. Only the API can declare a grant
        # deletable; ready/canonical bytes must survive such a lost response.
        import time

        deadline = time.monotonic() + 30
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("derived write compensation deadline")
            abandoned = self.platform.abandon_derived_writer(self.job_id)
            deleted = []
            for receipt in abandoned.get("deletable", []):
                self.storage.delete(receipt["key"])
                deleted.append(receipt)
            if not deleted:
                break
            self.platform.discard_derived_batch(deleted)


@contextmanager
def accounted_writes(storage, platform, version, job_id):
    import logging

    wrapped = AccountedWrites(storage, platform, version, job_id)
    try:
        yield wrapped
    finally:
        try:
            wrapped.cleanup()
        except Exception:
            logging.getLogger("ailab.derived_writes").warning("write compensation remains in the durable ledger", exc_info=True)

"""Local and S3 adapters for the bounded Storage port."""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import tempfile
import uuid
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .config import settings
from .ports.storage import ObjectInfo, ObjectPage, Storage, StoredObject

CHUNK_SIZE = 64 * 1024


def _safe_key(key: str) -> str:
    if not key or key.startswith("/") or "\\" in key or any(p in ("", ".", "..") for p in key.split("/")):
        raise ValueError(f"invalid storage key: {key!r}")
    if PurePosixPath(key).parts[0] == ".metadata":
        raise ValueError("storage metadata namespace is reserved")
    return key


def _prefix(prefix: str) -> str:
    return _safe_key(prefix.rstrip("/")) + ("/" if prefix.endswith("/") else "") if prefix else ""


def _chunks(source: Iterable[bytes] | BinaryIO, max_bytes: int) -> Iterator[bytes]:
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    total = 0
    chunks = iter(lambda: source.read(CHUNK_SIZE), b"") if hasattr(source, "read") else iter(source)
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise TypeError("storage streams must yield bytes")
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"object exceeds {max_bytes} bytes")
        yield chunk


class _StorageMethods:
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.put_stream(key, [data], content_type, max_bytes=len(data))

    def put_file(self, key: str, path: str | Path, content_type: str = "application/octet-stream") -> None:
        with open(path, "rb") as source:
            self.put_stream(key, source, content_type, max_bytes=os.fstat(source.fileno()).st_size)

    def get_bytes(self, key: str) -> bytes:
        with self.open_stream(key) as chunks:
            return b"".join(chunks)

    @contextmanager
    def open_stream(self, key: str, chunk_size: int = CHUNK_SIZE):
        with closing(self.stream(key, chunk_size)) as chunks:
            yield chunks

    def size(self, key: str) -> int:
        return self.info(key).size

    def delete_prefix(self, prefix: str) -> int:
        if not prefix:
            raise ValueError("refuse deleting the entire store")
        cursor = None
        removed = 0
        while True:
            page = self.list_prefix(prefix, cursor=cursor)
            for item in page:
                self.delete(item.key)
                removed += 1
            cursor = page.cursor
            if cursor is None:
                return removed

    def commit(self, staging_key: str, final_key: str) -> None:
        self.copy(staging_key, final_key)
        self.delete(staging_key)

    def presign_put(self, key: str, expires: int = 900) -> str | None:
        return None

    def presign_get(self, key: str, expires: int = 900) -> str | None:
        return None


class LocalStorage(_StorageMethods):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError(f"storage key escapes root: {key!r}")
        return path

    def _meta(self, key: str) -> Path:
        return self.root / ".metadata" / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def put_stream(self, key, source, content_type="application/octet-stream", *, max_bytes):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.root / ".metadata"
        staging.mkdir(exist_ok=True)
        tmp = staging / f"{uuid.uuid4().hex}.tmp"
        digest = hashlib.sha256()
        try:
            with open(tmp, "xb") as fh:
                for chunk in _chunks(source, max_bytes):
                    fh.write(chunk)
                    digest.update(chunk)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            stat = path.stat()
            meta = self._meta(key)
            meta.parent.mkdir(exist_ok=True)
            meta_tmp = meta.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                meta_tmp.write_text(
                    json.dumps({"size": stat.st_size, "mtime": stat.st_mtime_ns, "etag": digest.hexdigest(), "content_type": content_type})
                )
                os.replace(meta_tmp, meta)
            finally:
                meta_tmp.unlink(missing_ok=True)
        finally:
            tmp.unlink(missing_ok=True)

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def info(self, key: str) -> ObjectInfo:
        path = self._path(key)
        stat = path.stat()
        try:
            meta = json.loads(self._meta(key).read_text())
        except FileNotFoundError:
            meta = {}
        if meta.get("size") == stat.st_size and meta.get("mtime") == stat.st_mtime_ns:
            return ObjectInfo(stat.st_size, meta["etag"], meta["content_type"])
        with path.open("rb") as fh:
            digest = hashlib.file_digest(fh, "sha256").hexdigest()
        return ObjectInfo(stat.st_size, digest, meta.get("content_type", "application/octet-stream"))

    def list_prefix(self, prefix: str, cursor: str | None = None, limit: int = 1000) -> ObjectPage:
        prefix = _prefix(prefix)
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")

        # Heap retains only limit+1 keys; scandir does not allocate a whole
        # directory listing. A local page still costs one prefix-tree traversal.
        base = self._path(prefix.rstrip("/")) if prefix.endswith("/") else self._path(prefix).parent if prefix else self.root

        def candidates(directory):
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_symlink() or (directory == self.root and entry.name == ".metadata"):
                            continue
                        path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False):
                            yield from candidates(path)
                        elif entry.is_file(follow_symlinks=False):
                            key = path.relative_to(self.root).as_posix()
                            if key.startswith(prefix) and (cursor is None or key > cursor):
                                yield key
            except FileNotFoundError:
                return

        keys = heapq.nsmallest(limit + 1, candidates(base))
        items = []
        for key in keys[:limit]:
            try:
                stat = self._path(key).stat()
            except FileNotFoundError:
                continue
            items.append(StoredObject(key, stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC)))
        return ObjectPage(items, keys[limit - 1] if len(keys) > limit else None)

    def stream(self, key: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        with open(self._path(key), "rb") as fh:
            while chunk := fh.read(chunk_size):
                yield chunk

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
        self._meta(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def copy(self, src_key: str, dst_key: str) -> None:
        # Pin one inode before reading its size; replacing the signed source cannot
        # change the copy halfway through a completion.
        info = self.info(src_key)
        with self._path(src_key).open("rb") as source:
            self.put_stream(dst_key, source, info.content_type, max_bytes=os.fstat(source.fileno()).st_size)


class S3Storage(_StorageMethods):
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            region_name=settings.s3_region,
            config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
        )

    def put_stream(self, key, source, content_type="application/octet-stream", *, max_bytes):
        from boto3.s3.transfer import TransferConfig

        # Arbitrary iterators are not seekable; spool to disk, never a second full RAM copy.
        with tempfile.TemporaryFile() as spool:
            for chunk in _chunks(source, max_bytes):
                spool.write(chunk)
            spool.seek(0)
            self.client.upload_fileobj(
                spool,
                self.bucket,
                _safe_key(key),
                ExtraArgs={"ContentType": content_type},
                Config=TransferConfig(use_threads=False, multipart_chunksize=5 * 1024 * 1024),
            )

    def info(self, key: str) -> ObjectInfo:
        item = self.client.head_object(Bucket=self.bucket, Key=_safe_key(key))
        return ObjectInfo(int(item["ContentLength"]), item["ETag"].strip('"'), item.get("ContentType", "application/octet-stream"))

    def list_prefix(self, prefix: str, cursor: str | None = None, limit: int = 1000) -> ObjectPage:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        args = {"Bucket": self.bucket, "Prefix": _prefix(prefix), "MaxKeys": limit}
        if cursor:
            args["ContinuationToken"] = cursor
        page = self.client.list_objects_v2(**args)
        return ObjectPage(
            [StoredObject(i["Key"], int(i["Size"]), i["LastModified"]) for i in page.get("Contents", [])], page.get("NextContinuationToken")
        )

    def stream(self, key: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        body = self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"]
        try:
            yield from body.iter_chunks(chunk_size)
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_safe_key(key))

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.info(key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def copy(self, src_key: str, dst_key: str) -> None:
        if src_key == dst_key:
            self.info(src_key)
            return
        self.client.copy_object(Bucket=self.bucket, Key=_safe_key(dst_key), CopySource={"Bucket": self.bucket, "Key": _safe_key(src_key)})

    def presign_put(self, key: str, expires: int = 900) -> str | None:
        return self.client.generate_presigned_url("put_object", Params={"Bucket": self.bucket, "Key": _safe_key(key)}, ExpiresIn=expires)

    def presign_get(self, key: str, expires: int = 900) -> str | None:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": _safe_key(key)}, ExpiresIn=expires)


@lru_cache
def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage()
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_path)
    raise ValueError(f"unknown storage backend: {settings.storage_backend}")

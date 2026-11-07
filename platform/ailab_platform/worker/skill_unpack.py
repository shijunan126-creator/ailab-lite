"""Unpack a claimed skill in a resource-limited child and publish its manifest."""

from contextlib import contextmanager
from pathlib import Path

from ..clients import ClientError
from ..owned_temporary import directory as temporary_directory
from ..skill_drafts.files import inspect_file
from ..skillfiles import MAX_ZIP
from ..storage import get_storage
from .isolate import IsolationError, execute


@contextmanager
def _unpacked(storage, source: dict, prefix: str):
    """Yields (output dir, entries) or an error code; the archive never touches this process's parser."""
    with temporary_directory(prefix=prefix) as tmp:
        archive = Path(tmp) / "input.zip"
        size = 0
        with archive.open("wb") as out:
            for chunk in storage.stream(source["storage_key"]):
                size += len(chunk)
                if size > MAX_ZIP:
                    yield None, "input_limit"
                    return
                out.write(chunk)
        output = Path(tmp) / "output"
        try:
            result = execute("skill_unpack", archive, output, filename=source["filename"])
        except IsolationError:
            yield None, "invalid_archive"
            return
        yield output, result["files"]


def process_skill_unpack(client, draft_id: str, job_id: str, token: str) -> None:
    source = client.skill_draft_action(draft_id, "source", job_id=job_id, token=token)
    storage = get_storage()
    with _unpacked(storage, source, "ailab-skill-draft-") as (output, entries):
        if output is None:
            client.skill_draft_action(draft_id, "complete", job_id=job_id, token=token, error=entries)
            return
        files = []
        for item in entries:
            path = output / item["entry"]
            digest, size, kind = inspect_file(item["path"], path)
            key = f"skill-drafts/{source['user_id']}/{draft_id}/attempts/{token}/{digest}"
            storage.put_file(key, path)
            files.append({"path": item["path"], "size": size, "sha256": digest, "kind": kind, "change": "unchanged", "blob": key})
        client.skill_draft_action(draft_id, "complete", job_id=job_id, token=token, files=files)


def process_skill_view(client, file_id: str, job_id: str, token: str) -> None:
    """Unpack an immutable skill zip once for read-only viewing, into content-addressed derived cache."""
    from ..derived_writes import accounted_writes

    source = client.skill_view_action(file_id, "source", job_id=job_id, token=token)
    storage = get_storage()
    with _unpacked(storage, source, "ailab-skill-view-") as (output, entries):
        if output is None:
            client.skill_view_action(file_id, "complete", job_id=job_id, token=token, error=entries)
            return
        # Grants are bound to this claim; anything not published by "complete" is reclaimed on exit.
        with accounted_writes(storage, client, source["sha256"], job_id) as writer:
            files, stored = [], set()
            try:
                for item in entries:
                    path = output / item["entry"]
                    digest, size, kind = inspect_file(item["path"], path)
                    key = f"derived/{file_id}/skill/{digest}"
                    if key not in stored:  # identical files share one object and one grant
                        writer.put_file(key, path)
                        stored.add(key)
                    files.append({"path": item["path"], "size": size, "sha256": digest, "kind": kind, "blob": key})
            except ClientError:
                client.skill_view_action(file_id, "complete", job_id=job_id, token=token, error="storage_unavailable")
                return
            client.skill_view_action(file_id, "complete", job_id=job_id, token=token, files=files)
            writer.handoff()

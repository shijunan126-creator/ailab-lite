"""Unpack a claimed skill in a resource-limited child and publish its manifest."""

import hashlib
import tempfile
from pathlib import Path

from ..skill_drafts.files import classify
from ..skillfiles import MAX_ZIP
from ..storage import get_storage
from .isolate import IsolationError, execute


def process_skill_unpack(client, draft_id: str, job_id: str, token: str) -> None:
    source = client.skill_draft_action(draft_id, "source", job_id=job_id, token=token)
    storage = get_storage()
    with tempfile.TemporaryDirectory(prefix="ailab-skill-draft-") as tmp:
        archive = Path(tmp) / "input.zip"
        size = 0
        with archive.open("wb") as out:
            for chunk in storage.stream(source["storage_key"]):
                size += len(chunk)
                if size > MAX_ZIP:
                    client.skill_draft_action(draft_id, "complete", job_id=job_id, token=token, error="input_limit")
                    return
                out.write(chunk)
        output = Path(tmp) / "output"
        try:
            result = execute("skill_unpack", archive, output, filename=source["filename"])
        except IsolationError:
            client.skill_draft_action(draft_id, "complete", job_id=job_id, token=token, error="invalid_archive")
            return
        files = []
        for item in result["files"]:
            path = output / item["entry"]
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            key = f"skill-drafts/{source['user_id']}/{draft_id}/{digest}"
            storage.put_file(key, path)
            data = path.read_bytes()
            files.append(
                {
                    "path": item["path"],
                    "size": len(data),
                    "sha256": digest,
                    "kind": classify(item["path"], data),
                    "change": "unchanged",
                    "blob": key,
                }
            )
        client.skill_draft_action(draft_id, "complete", job_id=job_id, token=token, files=files)

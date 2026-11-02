"""Live-lease, owner-bound workspace snapshots; all parsing stays in the sandbox."""

import json
import shlex
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from starlette.responses import FileResponse

from ..config import settings
from ..contracts.document_view import PARSER_REVISION
from ..paths import normalize_path
from ..worker.viewer import build

_reader_slot = threading.BoundedSemaphore(1)


class SnapshotResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


def workspace(manager, run_id, user_id, path, operation, selections=None, version=None):
    if not path or ".." in path.split("/") or "\\" in path:
        raise HTTPException(422, "invalid workspace path")
    try:
        path = normalize_path(path if path.startswith("/") else "/workspace/" + path)
    except ValueError:
        raise HTTPException(422, "invalid workspace path") from None
    if ".ailab" in path.split("/"):
        raise HTTPException(422, "invalid workspace path")
    lease = next(
        (x for x in manager.active_leases() if x["run_id"] == run_id and x.get("user_id") == user_id and x["state"] == "active"), None
    )
    if not lease:
        raise HTTPException(410, "live workspace unavailable")
    lease = manager.get(lease["lease_id"])
    claimed = operation != "content"
    if claimed and not _reader_slot.acquire(blocking=False):
        raise HTTPException(503, "viewer busy")
    ident = uuid4().hex
    directory = f"/workspace/.ailab/viewer-{ident}"
    source = f"{directory}/source{Path(path).suffix.lower()}"
    helper = Path(__file__).resolve().parents[1] / "sandbox_tools" / "workspace_document.py"
    try:
        manager.put_file(lease["lease_id"], directory + "/snapshot.py", helper.read_bytes())
        command = " ".join(shlex.quote(x) for x in (directory + "/snapshot.py", "/workspace", path, source))
        result = manager.exec(lease["lease_id"], "python " + command, 10)
        if result.exit_code:
            raise HTTPException(422, "workspace file unavailable")
        identity = json.loads(result.stdout)
        if version and identity["version"] != version:
            raise HTTPException(409, "workspace version changed")
        if operation == "content":
            local = tempfile.NamedTemporaryFile(prefix="ailab-workspace-", delete=False)
            local.close()
            target = Path(local.name)
            try:
                manager._download(lease, source, target, 20 * 1024**2)
                return SnapshotResponse(
                    target,
                    media_type="application/octet-stream",
                    headers={"ETag": '"' + identity["version"] + '"'},
                )
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        cache_key = f"previews/{run_id}/viewer/{identity['version']}-{PARSER_REVISION}.json"
        if operation == "document" and manager.storage.exists(cache_key) and manager.storage.size(cache_key) <= 4 * 1024**2:
            return json.loads(manager.storage.get_bytes(cache_key))
        kind = Path(path).suffix.lower().removeprefix(".")
        if kind not in {"docx", "xlsx", "pptx", "pdf"}:
            return {"status": "unsupported", "version": identity["version"], "native": False}
        if operation == "render":
            import re

            from ..contracts.document_view import RENDER_REVISION
            from .workspace_view import view

            at = selections[0] if selections and len(selections) == 1 else ""
            if not ((kind == "pdf" and re.fullmatch(r"page:[1-9]\d*", at)) or (kind == "pptx" and re.fullmatch(r"slide:[1-9]\d*", at))):
                raise HTTPException(422, "precise render range unavailable")
            cache_key = f"previews/{run_id}/viewer/{identity['version']}-{RENDER_REVISION}-{at.replace(':', '-')}.json"
            if manager.storage.exists(cache_key):
                return json.loads(manager.storage.get_bytes(cache_key))
            rendered = view(manager, lease["lease_id"], source, [int(at.split(":")[1])], trusted_snapshot=True)
            captured = {"version": identity["version"], "at": at, "image": rendered["images"][0], "mime_type": "image/jpeg"}
            manager.storage.put_bytes(cache_key, json.dumps(captured).encode(), "application/json")
            return captured
        if not settings.viewer_reader_path.exists():
            return {"status": "failed", "version": identity["version"], "native": False, "reason": "viewer_reader_unavailable"}
        executor = helper.with_name("office_exec.py")
        manager.put_file(lease["lease_id"], directory + "/office_exec.py", executor.read_bytes())
        manager.put_file(lease["lease_id"], directory + "/reader.mjs", settings.viewer_reader_path.read_bytes())
        manager.put_file(lease["lease_id"], directory + "/request.json", json.dumps({"selections": selections or []}).encode())
        out = directory + "/viewer.json"
        command = (
            f"ln -s /opt/ailab-office/node_modules {directory}/node_modules && "
            f"python {directory}/office_exec.py 18 {192 * 1024**2} "
            f"node --max-old-space-size=96 {directory}/reader.mjs {shlex.quote(source)} {out} {directory}/request.json"
        )
        result = manager.exec(lease["lease_id"], command, 22)
        if result.exit_code:
            return {"status": "failed", "version": identity["version"], "native": False, "reason": "document_parse_failed"}
        with manager.transfer_file() as local:
            manager._download(lease, out, local, 4 * 1024**2)
            parsed = json.loads(local.read_bytes())
        if operation == "check":
            return {"version": identity["version"], "selections": parsed["selections"]}
        index = build(None, identity["version"], Path(path).name, identity["bytes"], parsed)
        index.pop("file_id", None)
        index["workspace_revision"] = identity["version"]
        manager.storage.put_bytes(cache_key, json.dumps(index).encode(), "application/json")
        return index
    finally:
        try:
            manager.exec(lease["lease_id"], f"rm -rf -- {shlex.quote(directory)}", 5)
        except Exception:
            pass
        finally:
            if claimed:
                _reader_slot.release()

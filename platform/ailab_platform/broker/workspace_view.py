"""Render untrusted working files in the run's own sandbox, never as library previews."""

import base64
import binascii
import json
import shlex
import time
from pathlib import Path
from uuid import uuid4

from ..config import settings
from ..contracts.workspace_view import VIEW_ERRORS
from ..paths import PathError, normalize_path
from .render import SCRIPT as RENDER_SCRIPT

HELPER = Path(__file__).resolve().parents[1] / "sandbox_tools" / "view_workspace_file.py"
LISTENER = HELPER.with_name("work_view_listener.py")
UNO_CLIENT = HELPER.with_name("uno_convert.py")
MAX_RESULT = 1700 * 1024
MAX_TOTAL = 1200 * 1024


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Read the bounded JPEG frame header without decoding untrusted pixels in the Broker."""
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("invalid_view_output")
    offset, dimensions = 2, None
    while offset < len(data) - 1:
        if data[offset] != 0xFF:
            raise ValueError("invalid_view_output")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xDA:
            break
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if offset + 2 > len(data):
            break
        size = int.from_bytes(data[offset : offset + 2], "big")
        if size < 2 or offset + size > len(data):
            raise ValueError("invalid_view_output")
        if marker in {0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            raise ValueError("invalid_view_output")
        if marker == 0xC0:
            if dimensions is not None or size < 8:
                raise ValueError("invalid_view_output")
            dimensions = (int.from_bytes(data[offset + 5 : offset + 7], "big"), int.from_bytes(data[offset + 3 : offset + 5], "big"))
        offset += size
    if dimensions is None or not all(1 <= side <= 1280 for side in dimensions):
        raise ValueError("invalid_view_output")
    return dimensions


def view(manager, lease_id: str, path: str, selected) -> dict:
    if not isinstance(path, str) or any(part == ".." for part in path.split("/")):
        raise ValueError("invalid_workspace_path")
    try:
        path = normalize_path(path)
    except PathError as exc:
        raise ValueError("invalid_workspace_path") from exc
    if path == "/workspace" or path.startswith("/workspace/.ailab/"):
        raise ValueError("invalid_workspace_path")
    if selected is None:
        selected = [1]
    if (
        not isinstance(selected, list)
        or not 1 <= len(selected) <= 4
        or any(type(n) is not int or not 1 <= n <= 100000 for n in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("invalid_page_selection")
    ident = uuid4().hex
    directory = f"/workspace/.ailab/view-{ident}"
    helper = f"{directory}/view_workspace_file.py"
    renderer = f"{directory}/render_preview.py"
    result_path = f"{directory}/result.json"
    start = time.monotonic()
    try:
        manager.put_file(lease_id, helper, HELPER.read_bytes())
        manager.put_file(lease_id, renderer, RENDER_SCRIPT.read_bytes())
        manager.put_file(lease_id, f"{directory}/work_view_listener.py", LISTENER.read_bytes())
        manager.put_file(lease_id, f"{directory}/uno_convert.py", UNO_CLIENT.read_bytes())
        args = " ".join(shlex.quote(s) for s in (helper, "/workspace", path, result_path, json.dumps(selected)))
        result = manager.exec(
            lease_id,
            f"AILAB_LO_CLIENT={shlex.quote(directory + '/uno_convert.py')} AILAB_LO_RSS_LIMIT={settings.render_work_listener_rss_bytes} "
            f"timeout -k 2 45s python {args}",
            48,
        )
        if result.exit_code:
            code = "view_timeout" if result.exit_code in {124, 137} else "view_failed"
            for line in result.stderr.splitlines():
                if line.startswith("workspace_view_error:") and line.removeprefix("workspace_view_error:") in VIEW_ERRORS:
                    code = line.removeprefix("workspace_view_error:")
            raise ValueError(code)
        if time.monotonic() - start > 60:
            raise ValueError("view_timeout")
        with manager.transfer_file() as local:
            manager._download(manager.get(lease_id), result_path, local, MAX_RESULT)
            payload = json.loads(local.read_bytes())
        if not isinstance(payload, dict) or payload.get("pages") != selected or not isinstance(payload.get("images"), list):
            raise ValueError("invalid_view_output")
        if len(payload["images"]) != len(selected):
            raise ValueError("invalid_view_output")
        total = 0
        for item in payload["images"]:
            if not isinstance(item, str) or len(item) > 700_000:
                raise ValueError("invalid_view_output")
            try:
                data = base64.b64decode(item, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("invalid_view_output") from exc
            total += len(data)
            if len(data) > 512 * 1024 or total > MAX_TOTAL:
                raise ValueError("invalid_view_output")
            jpeg_dimensions(data)
        return payload
    finally:
        try:
            manager.exec(lease_id, f"rm -rf -- {shlex.quote(directory)}", 5)
        except Exception:
            pass


def warm(manager, lease_id: str) -> dict:
    """Start (or keep alive) the run's LibreOffice listener before its first view.

    A cold listener made the first Office view ~15 s against ~3 s warm; the model spends longer than that
    writing the file. The listener exits by itself after its idle window, so a warm that is not followed by a
    view costs one idle LibreOffice process in the run's own sandbox for at most that window.
    """
    if not manager.provider.office_listener:
        return {"ok": False, "skipped": True}
    directory = f"/workspace/.ailab/view-warm-{uuid4().hex}"
    script = f"{directory}/work_view_listener.py"
    try:
        manager.put_file(lease_id, script, LISTENER.read_bytes())
        result = manager.exec(lease_id, f"timeout -k 2 8s python {script} --ensure {settings.render_work_listener_rss_bytes}", 12)
        return {"ok": result.exit_code == 0}
    finally:
        try:
            manager.exec(lease_id, f"rm -rf -- {shlex.quote(directory)}", 5)
        except Exception:
            pass

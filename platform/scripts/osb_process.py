"""Bind reuse of the local Kubernetes server to its reviewed startup config."""

import hashlib
import json
import sys
import tomllib
from pathlib import Path

mode, home, config, *args = sys.argv[1:]
state = Path(home) / "server-process.json"
config = Path(config).resolve()
source = config.read_bytes()
template = tomllib.loads(source.decode()).get("kubernetes", {}).get("batchsandbox_template_file")
if template:
    source += b"\0" + Path(template).expanduser().read_bytes()
digest = hashlib.sha256(source).hexdigest()
if mode == "record":
    state.write_text(json.dumps({"pid": int(args[0]), "config": str(config), "sha256": digest}))
else:
    try:
        saved = json.loads(state.read_text())
        cmd = Path(f"/proc/{int(saved['pid'])}/cmdline").read_bytes().decode().split("\0")
        selected = Path(cmd[cmd.index("--config") + 1]).resolve()
        if selected != config or saved["config"] != str(config) or saved["sha256"] != digest:
            raise ValueError("startup configuration changed")
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise SystemExit("[osb_up] healthy endpoint is not bound to this reviewed startup config; stop the old server explicitly") from exc

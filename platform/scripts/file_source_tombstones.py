"""Export deletion tombstones separately from backups, then apply them before reopening a restored API.

Calls API-owned internal routes only. Never reads or writes the business database directly.
The API must be isolated from Portal/Agent traffic during restore. Manifest contains opaque IDs,
not file names/content. Keep it outside the backup generation being restored.
"""

import argparse
import json
import os
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "apply"])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--api", required=True)
    parser.add_argument("--token-env", default="AILAB_OPS_SERVICE_TOKEN")
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        parser.error("ops service credential environment variable is required")
    with httpx.Client(base_url=args.api.rstrip("/"), headers={"X-Service-Token": token}, timeout=30, follow_redirects=False) as client:
        if args.action == "export":
            after = ""
            with args.manifest.open("x") as target:
                args.manifest.chmod(0o600)
                for _ in range(100000):
                    response = client.get("/internal/file-source-tombstones", params={"after": after})
                    response.raise_for_status()
                    page = response.json()
                    if page["items"]:
                        target.write(json.dumps({"schema_version": 1, "items": page["items"]}) + "\n")
                    if page["next_after"] is None:
                        break
                    after = page["next_after"]
                else:
                    raise RuntimeError("tombstone export pagination limit")
        else:
            with args.manifest.open() as source:
                for line in source:
                    if len(line) > 65536:
                        raise ValueError("tombstone batch too large")
                    response = client.post("/internal/file-source-tombstones", json=json.loads(line))
                    response.raise_for_status()
    print("tombstone " + args.action + " complete")


if __name__ == "__main__":
    main()

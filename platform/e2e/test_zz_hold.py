# Temporary: keeps the E2E stack up for a manual browser check. Not committed.
import json
import time
from pathlib import Path

from .conftest import PASSWORD, USERNAME


def test_hold(stack):
    Path("/tmp/ailab-hold.json").write_text(json.dumps({"portal": stack.portal, "api": stack.api, "user": USERNAME, "password": PASSWORD}))
    stop = Path("/tmp/ailab-hold-stop")
    deadline = time.time() + 5400
    while not stop.exists() and time.time() < deadline:
        time.sleep(1)
    stop.unlink(missing_ok=True)

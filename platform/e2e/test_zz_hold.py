# Temporary: keeps the E2E stack up for a manual browser check. Not committed.
import json
import os
import time
from pathlib import Path

import pytest

from .conftest import PASSWORD, USERNAME


@pytest.mark.skipif(os.environ.get("AILAB_E2E_HOLD") != "1", reason="manual browser hold: set AILAB_E2E_HOLD=1")
def test_hold(stack):
    Path("/tmp/ailab-hold.json").write_text(json.dumps({"portal": stack.portal, "api": stack.api, "user": USERNAME, "password": PASSWORD}))
    stop = Path("/tmp/ailab-hold-stop")
    deadline = time.time() + 5400
    while not stop.exists() and time.time() < deadline:
        time.sleep(1)
    stop.unlink(missing_ok=True)

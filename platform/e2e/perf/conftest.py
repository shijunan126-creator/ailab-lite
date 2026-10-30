"""Reuse the parent stack, changing only the existing E2E agent's model seam."""

import os
import subprocess

import pytest


def pytest_collection_modifyitems(items):
    if os.environ.get("AILAB_PERF") != "1":
        for item in items:
            if "/perf/" in str(item.path):
                item.add_marker(pytest.mark.skip(reason="performance benchmark: set AILAB_PERF=1"))


@pytest.fixture(scope="session", autouse=True)
def paced_model_entrypoint():
    if not os.environ.get("AILAB_PERF_OUTPUT") or not os.environ.get("AILAB_E2E_LOG_DIR"):
        raise pytest.UsageError("Run platform/scripts/bench_portal.py to keep performance output and service logs outside the repo")
    original = subprocess.Popen

    def start(args, *positional, **kwargs):
        if isinstance(args, list) and "e2e.scripted_agent:app" in args:
            args = ["e2e.perf.scripted_agent:app" if value == "e2e.scripted_agent:app" else value for value in args]
        return original(args, *positional, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subprocess, "Popen", start)
        yield

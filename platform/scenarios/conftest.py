"""Real-model agent scenarios against a running stack: opt-in and metered.

    cd platform
    AILAB_SCENARIOS=1 AILAB_SCENARIO_API=http://127.0.0.1:28080 AILAB_SCENARIO_TOKEN=<bearer> \\
        .venv/bin/pytest scenarios -v -p no:cacheprovider

Each scenario is one representative user task through the public API (`/v1`), the way the Portal sends it, with a
real model, a real sandbox and the stack's real skills. Assertions check what a user would check (the file opens,
the number changed, the passage was used) plus the platform contracts that must hold for every task: the run
succeeds, no context boundary inside a conversation, prefix cache hits on later turns, and no sub-tasks unless the
user asked for them.

Environment:
    AILAB_SCENARIO_API         API base URL (the API service, not the Portal)
    AILAB_SCENARIO_TOKEN       a bearer token for a test account (session token from /v1/auth/login, or the dev token)
    AILAB_SCENARIO_MODEL       model id; default is the stack's default model
    AILAB_SCENARIO_MIN_CACHE   minimum cached/prompt tokens on later turns (default 0.85)
    AILAB_SCENARIO_REPORT      JSONL file for per-turn numbers (default: pytest's tmp dir); never the repository

Every conversation and uploaded file a scenario creates is deleted when it finishes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scenarios.client import Client, Turn

ENABLED = os.environ.get("AILAB_SCENARIOS") == "1"
MIN_CACHE = float(os.environ.get("AILAB_SCENARIO_MIN_CACHE", "0.85"))


def pytest_collection_modifyitems(config, items):
    if ENABLED:
        if not (os.environ.get("AILAB_SCENARIO_API") and os.environ.get("AILAB_SCENARIO_TOKEN")):
            raise pytest.UsageError("AILAB_SCENARIOS=1 needs AILAB_SCENARIO_API and AILAB_SCENARIO_TOKEN")
        return
    skip = pytest.mark.skip(reason="real-model scenarios: set AILAB_SCENARIOS=1 (metered; see scenarios/conftest.py)")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def report_path(tmp_path_factory) -> Path:
    path = Path(os.environ.get("AILAB_SCENARIO_REPORT") or tmp_path_factory.mktemp("scenarios") / "report.jsonl")
    print(f"\nscenario report: {path}")
    return path


@pytest.fixture
def api(request, report_path):
    client = Client(os.environ["AILAB_SCENARIO_API"], os.environ["AILAB_SCENARIO_TOKEN"])
    client.model = os.environ.get("AILAB_SCENARIO_MODEL") or client.default_model()
    try:
        yield client
    finally:
        with report_path.open("a") as out:
            for index, turn in enumerate(client.turns):
                usage = turn.usage
                row = {
                    "scenario": request.node.name,
                    "turn": index + 1,
                    "status": turn.run["status"],
                    "tools": turn.tools(),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "cached_tokens": usage.get("cached_tokens"),
                    "calls": usage.get("calls"),
                    "boundaries": [b.get("reason") for b in turn.boundaries()],
                    # enough to see where a task struggled without keeping the conversation
                    "commands": [str((c.get("arguments") or {}).get("command", ""))[:300] for c in turn.tool_calls("bash")],
                    "failed": [
                        {
                            "name": f.get("name"),
                            "exit_code": f.get("exit_code"),
                            "error": str(f.get("error") or "")[:300],
                            "stdout": str(f.get("stdout") or "")[-300:],
                        }
                        for f in turn.failed_tools()
                    ],
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        client.close()


def contracts(turn: Turn, *, later: bool = False, subtasks: bool = False) -> None:
    """What every scenario turn must satisfy, whatever the task."""
    assert turn.run["status"] == "succeeded", (turn.run["status"], turn.run.get("error"))
    assert not turn.boundaries(), turn.boundaries()  # a conversation never rebuilds its prefix by itself
    assert ("run_subtasks" in turn.tools()) == subtasks, turn.tools()
    if later:
        usage = turn.usage
        ratio = (usage.get("cached_tokens") or 0) / max(1, usage.get("prompt_tokens") or 0)
        assert ratio >= MIN_CACHE, f"cache hit {ratio:.1%} < {MIN_CACHE:.0%}: {usage}"

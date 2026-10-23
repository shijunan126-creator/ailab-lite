"""Choose CI suites from the entire PR diff; main/manual runs always exercise everything."""

import json
import os
import subprocess
from pathlib import Path

DOC_FILES = {
    ".github/CI.md",
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "HANDOFF.MD",
    "platform/README.md",
    "platform/portal/AGENTS.md",
    "platform/deploy/README.md",
    "platform/tests/contracts/README.md",
}


def scope(paths):
    code = backend = e2e = False
    for path in paths:
        if path.startswith("docs/") or path in DOC_FILES:
            continue
        code = e2e = True
        # Portal changes need its Node tests, production build and browser suite, but no DB suite.
        if not path.startswith("platform/portal/"):
            backend = True
    return {"code": code, "backend": backend, "e2e": e2e}


def main():
    if os.environ["GITHUB_EVENT_NAME"] == "pull_request":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        base, head = (
            event["pull_request"]["base"]["sha"],
            event["pull_request"]["head"]["sha"],
        )
        paths = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", "-z", f"{base}...{head}"]).decode().split("\0")
        result = scope(path for path in paths if path)
    else:
        result = dict.fromkeys(("code", "backend", "e2e"), True)
    output = "".join(f"{key}={str(value).lower()}\n" for key, value in result.items())
    print(output, end="")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as target:
        target.write(output)


if __name__ == "__main__":
    main()

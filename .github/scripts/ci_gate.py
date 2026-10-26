"""Validate the stable CI check from GitHub Actions' toJSON(needs) in RESULTS."""

import json
import os
import sys

SELECTED = {"quality": "code", "shell": "code", "crawler": "code", "python": "backend", "storage-contracts": "backend", "e2e": "e2e"}
FLAGS = {"code", "backend", "e2e"}
RESULTS = {"success", "failure", "cancelled", "skipped"}


def validate(jobs):
    """Raise ValueError on malformed inputs or an incomplete/failed selection."""
    if not isinstance(jobs, dict) or set(jobs) != {"changes", *SELECTED}:
        raise ValueError("Invalid CI jobs: expected changes, quality, shell, crawler, python, storage-contracts, e2e")
    for name, job in jobs.items():
        if not isinstance(job, dict) or not isinstance(job.get("result"), str) or job["result"] not in RESULTS:
            raise ValueError(f"Invalid job result: {name}")
    if jobs["changes"]["result"] != "success":
        raise ValueError(f"CI failed: changes={jobs['changes']['result']}")
    outputs = jobs["changes"].get("outputs")
    if (
        not isinstance(outputs, dict)
        or set(outputs) != FLAGS
        or any(not isinstance(value, str) or value not in {"true", "false"} for value in outputs.values())
    ):
        raise ValueError("Invalid scope outputs: expected code/backend/e2e as true or false strings")
    if outputs["code"] != outputs["e2e"] or (outputs["backend"] == "true" and outputs["code"] != "true"):
        raise ValueError("Invalid scope outputs: inconsistent suite selection")
    bad = []
    for name, flag in SELECTED.items():
        result = jobs[name]["result"]
        if result != "success" and (outputs[flag] == "true" or result != "skipped"):
            bad.append(f"{name}={result}")
    if bad:
        raise ValueError(f"CI failed: {', '.join(sorted(bad))}")


def main():
    try:
        raw = os.environ.get("RESULTS")
        if not raw:
            raise ValueError("Missing RESULTS environment variable")
        validate(json.loads(raw))
    except ValueError as error:
        print(f"CI gate: {error}", file=sys.stderr)
        return 1
    print("All selected checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

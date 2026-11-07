"""Opt-in real-model workflow observer. Uses only public APIs and synthetic inputs.

Plan JSON is input, reports are external artifacts. No pass is inferred from a
terminal status: inspect tool choices, errors, pauses, answers and actual files.
Browser-assisted cases require a signed-in Portal with the source viewer open.
"""

import argparse
import json
import mimetypes
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import httpx

TERMINAL = {"succeeded", "failed", "canceled", "waiting_user", "quota_exceeded"}


class Probe:
    def __init__(self, client, output, model):
        self.client, self.output, self.model = client, output, model
        self.output.mkdir(parents=True, exist_ok=True)

    def upload(self, source):
        with source.open("rb") as stream:
            response = self.client.post(
                "/v1/files", params={"disposition": "copy"}, files={"upload": (source.name, stream, mimetypes.guess_type(source.name)[0])}
            )
        response.raise_for_status()
        file_id = response.json()["id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            response = self.client.get(f"/v1/files/{file_id}")
            response.raise_for_status()
            row = response.json()
            if row["status"] != "pending":
                if row["status"] != "ready":
                    raise RuntimeError(f"synthetic upload rejected: {source.name}")
                return row
            time.sleep(0.5)
        raise TimeoutError("synthetic upload safety deadline")

    def turn(self, conversation, prompt, files, mode, label):
        started = time.monotonic()
        body = {"prompt": prompt, "file_ids": files, "mode": mode, "model": self.model, "client_send_id": str(uuid4())}
        preparations = []
        deadline = started + 600
        while time.monotonic() < deadline:
            response = self.client.post(f"/v1/conversations/{conversation}/messages", json=body)
            if response.status_code == 409:
                detail = response.json().get("detail")
                if not isinstance(detail, dict) or detail.get("code") != "chat_attachment_processing":
                    response.raise_for_status()
                preparations.append({"elapsed": round(time.monotonic() - started, 3), "detail": detail})
                time.sleep(1)
                continue
            response.raise_for_status()
            run = response.json()
            break
        else:
            raise TimeoutError("preparation deadline")
        run_id = run["id"]
        events, status_changes, after = [], [], 0
        last_status = None
        print(json.dumps({"case": label, "conversation": conversation, "run": run_id, "phase": "started"}), flush=True)
        while time.monotonic() < deadline:
            run = self.client.get(f"/v1/runs/{run_id}").raise_for_status().json()
            if run["status"] != last_status:
                last_status = run["status"]
                status_changes.append(
                    {
                        "elapsed": round(time.monotonic() - started, 3),
                        "status": last_status,
                        "wait": (run.get("result") or {}).get("file_wait"),
                    }
                )
            while True:
                batch = (
                    self.client.get(f"/v1/runs/{run_id}/events/history", params={"after": after, "limit": 500}).raise_for_status().json()
                )
                events.extend(batch)
                if batch:
                    after = batch[-1]["seq"]
                if len(batch) < 500:
                    break
            if len(events) > 10000:
                raise RuntimeError("event observation bound exceeded")
            if run["status"] in TERMINAL or (run["status"] == "interrupted" and run.get("resumable")):
                break
            time.sleep(0.5)
        else:
            self.client.post(f"/v1/runs/{run_id}/cancel").raise_for_status()
            raise TimeoutError(f"workflow deadline: {label}")
        calls = [e for e in events if e["type"] == "agent.tool_call"]
        failures = [e for e in events if e["type"] == "agent.tool_result" and not e["payload"].get("ok")]
        execution_diagnostics = [
            e
            for e in events
            if e["type"] == "agent.tool_result"
            and ((e["payload"].get("exit_code") or 0) != 0 or "Traceback" in e["payload"].get("stderr", ""))
        ]
        signatures = Counter(
            json.dumps({k: v for k, v in e["payload"].items() if k != "step"}, ensure_ascii=False, sort_keys=True) for e in calls
        )
        record = {
            "label": label,
            "conversation": conversation,
            "run": run,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "preparations": preparations,
            "status_changes": status_changes,
            "tool_sequence": [e["payload"] for e in calls],
            "failed_tools": failures,
            "repeated_call_events": {k: v for k, v in signatures.items() if v > 1},
            "execution_diagnostics": execution_diagnostics,
            "events": events,
        }
        (self.output / f"{label}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        for artifact in run.get("artifacts", []):
            if artifact.get("download_url"):
                data = self.client.get(artifact["download_url"]).raise_for_status().content
                (self.output / f"{label}-{Path(artifact['filename']).name}").write_bytes(data)
        print(
            json.dumps(
                {
                    "case": label,
                    "status": run["status"],
                    "seconds": record["elapsed_seconds"],
                    "tools": [e["payload"]["name"] for e in calls],
                    "failed_tools": len(failures),
                    "artifacts": len(run.get("artifacts", [])),
                }
            ),
            flush=True,
        )
        return record

    def case(self, case, corpus):
        files = [self.upload(corpus / name) for name in case.get("files", [])]
        conversation = (
            self.client.post("/v1/conversations", json={"title": "Workflow " + case["id"], "model": self.model})
            .raise_for_status()
            .json()["id"]
        )
        (self.output / f"{case['id']}-inputs.json").write_text(
            json.dumps({"conversation": conversation, "files": files}, ensure_ascii=False, indent=2)
        )
        print(json.dumps({"case": case["id"], "conversation": conversation, "phase": "prepared"}), flush=True)
        if case.get("browser_gate"):
            until = time.monotonic() + 120
            marker = self.output / f"{case['id']}.continue"
            while not marker.exists():
                if time.monotonic() >= until:
                    raise TimeoutError("browser observation readiness deadline")
                time.sleep(0.5)
        for number, prompt in enumerate(case["turns"], 1):
            record = self.turn(
                conversation, prompt, [f["id"] for f in files] if number == 1 else [], case["mode"], f"{case['id']}-{number}"
            )
            if record["run"]["status"] != "succeeded":
                break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="document-real")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    if args.output.resolve().is_relative_to(root):
        raise ValueError("reports must stay outside repository")
    auth = json.loads(args.auth_file.read_text())
    with httpx.Client(base_url=args.api, headers={"Authorization": "Bearer " + auth["token"]}, timeout=60) as client:
        probe = Probe(client, args.output, args.model)
        for case in json.loads(args.plan.read_text()):
            probe.case(case, args.corpus)


if __name__ == "__main__":
    main()

"""A small client for the public API (`/v1`), the way the Portal uses it."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import httpx

TERMINAL = {"succeeded", "failed", "canceled", "interrupted"}


@dataclass
class Turn:
    """One finished run, with what a scenario asserts on."""

    run: dict
    events: list[dict]
    usage: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.run["id"]

    @property
    def reply(self) -> str:
        return self.run.get("summary") or ""

    def tools(self) -> list[str]:
        return [e["payload"].get("name") for e in self.events if e["type"] == "agent.tool_call"]

    def tool_calls(self, name: str) -> list[dict]:
        return [e["payload"] for e in self.events if e["type"] == "agent.tool_call" and e["payload"].get("name") == name]

    def boundaries(self) -> list[dict]:
        return [e["payload"] for e in self.events if e["type"] == "agent.context_boundary"]

    def failed_tools(self) -> list[dict]:
        return [e["payload"] for e in self.events if e["type"] == "agent.tool_result" and e["payload"].get("ok") is False]


class Client:
    def __init__(self, base: str, token: str, *, timeout: float = 60):
        self.http = httpx.Client(base_url=base.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        self.conversations: list[str] = []
        self.files: list[str] = []
        self.turns: list[Turn] = []
        self.model: str | None = None

    def close(self) -> None:
        # Leave nothing behind in the account the scenarios ran in: deliverables outlive their conversation.
        self.files.extend(a["file_id"] for turn in self.turns for a in turn.run.get("artifacts") or [])
        for cid in self.conversations:
            self.http.delete(f"/v1/conversations/{cid}")
        for fid in self.files:
            self.http.delete(f"/v1/files/{fid}")
        self.http.close()

    def _ok(self, response: httpx.Response) -> dict:
        if response.status_code >= 400:
            raise AssertionError(f"{response.request.method} {response.request.url.path} → {response.status_code}: {response.text[:500]}")
        return response.json() if response.content else {}

    def default_model(self) -> str:
        return self._ok(self.http.get("/v1/models"))["default"]

    def upload(self, name: str, data: bytes, content_type: str, *, deadline: float = 120) -> dict:
        file = self._ok(self.http.post("/v1/files", files={"upload": (name, data, content_type)}, params={"disposition": "copy"}))
        self.files.append(file["id"])
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            meta = self._ok(self.http.get(f"/v1/files/{file['id']}"))
            if meta["status"] == "ready" and (meta.get("meta") or {}).get("processing") == "done":
                return meta
            if meta["status"] == "failed":
                raise AssertionError(f"upload {name} failed: {meta.get('meta')}")
            time.sleep(1)
        raise AssertionError(f"upload {name} not ready in {deadline}s")

    def start(self, prompt: str, *, mode: str, **body) -> tuple[str, Turn]:
        conversation = self._ok(self.http.post("/v1/conversations", json={}))
        self.conversations.append(conversation["id"])
        return conversation["id"], self.ask(conversation["id"], prompt, mode=mode, **body)

    def send(self, cid: str, prompt: str, *, mode: str, **body) -> str:
        payload = {"prompt": prompt, "mode": mode, "model": self.model, "client_send_id": uuid.uuid4().hex, **body}
        end = time.monotonic() + 180
        while True:
            response = self.http.post(f"/v1/conversations/{cid}/messages", json=payload)
            # A Chat attachment is prepared after upload; the Portal waits for it the same way.
            preparing = response.status_code == 409 and "attachment_processing" in response.text
            if not preparing or time.monotonic() > end:
                return self._ok(response)["id"]
            time.sleep(3)

    def wait(self, run_id: str, *, deadline: float = 900) -> Turn:
        end = time.monotonic() + deadline
        run = {}
        while time.monotonic() < end:
            run = self._ok(self.http.get(f"/v1/runs/{run_id}"))
            if run["status"] in TERMINAL:
                break
            time.sleep(2)
        else:
            raise AssertionError(f"run {run_id} still {run.get('status')} after {deadline}s")
        events, after = [], 0
        while page := self._ok(self.http.get(f"/v1/runs/{run_id}/events/history", params={"after": after, "limit": 500})):
            events.extend(page)
            after = page[-1]["seq"]
        usage = {}
        if run.get("conversation_id"):
            turns = self._ok(self.http.get(f"/v1/conversations/{run['conversation_id']}"))["turns"]
            usage = next((t.get("usage") or {} for t in turns if t["id"] == run_id), {})
        turn = Turn(run=run, events=events, usage=usage)
        self.turns.append(turn)
        return turn

    def ask(self, cid: str, prompt: str, **kw) -> Turn:
        return self.wait(self.send(cid, prompt, **kw))

    def artifact(self, turn: Turn, suffix: str) -> tuple[dict, bytes]:
        found = [a for a in turn.run.get("artifacts") or [] if a["filename"].lower().endswith(suffix)]
        if not found:
            raise AssertionError(f"no {suffix} artifact; got {[a['filename'] for a in turn.run.get('artifacts') or []]}")
        item = found[-1]
        return item, self.http.get(item["download_url"].removeprefix("/api")).content

    def blocks(self, file_id: str, *, deadline: float = 120) -> dict:
        """The viewer structure of a library file: what a reader can select a passage from."""
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            result = self._ok(self.http.get(f"/v1/files/{file_id}/document", params={"structure": "true"}))
            if result.get("status") == "ready":
                return result
            time.sleep(2)
        raise AssertionError(f"document structure of {file_id} not ready")

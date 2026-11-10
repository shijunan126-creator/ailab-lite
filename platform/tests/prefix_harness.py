"""Capture actual AgentLoop requests through a local OpenAI-compatible server."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ailab_platform.model_policy import estimator


@contextmanager
def scripted_server(
    responses,
    *,
    prompt_tokens=lambda request: estimator.estimate("m", {"messages": request["messages"], "tools": request.get("tools", [])}),
):
    requests = []
    script = iter(responses)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            delta = next(script)
            if "status" in delta:
                self.send_response(delta["status"])
                self.end_headers()
                return
            finish = delta.pop("_finish_reason", "tool_calls" if delta.get("tool_calls") else "stop")
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]},
                {"choices": [], "usage": {"prompt_tokens": prompt_tokens(requests[-1]), "completion_tokens": 1}},
            ]
            data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(data.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # shutdown() waits for serve_forever to poll; the default 0.5s interval cost every test half a second.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def assert_extension(previous, current):
    """Assert serialized tool schemas and every previously sent message remain identical."""
    assert json.dumps(previous.get("tools"), ensure_ascii=False) == json.dumps(current.get("tools"), ensure_ascii=False)
    old = previous["messages"]
    assert current["messages"][: len(old)] == old
    assert current["messages"][len(old)]["role"] == "assistant"

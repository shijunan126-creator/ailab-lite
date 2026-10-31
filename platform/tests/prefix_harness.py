"""Capture actual AgentLoop requests through a local OpenAI-compatible server."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def scripted_server(responses):
    requests = []
    script = iter(responses)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            delta = next(script)
            finish = "tool_calls" if delta.get("tool_calls") else "stop"
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]},
                {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 1}},
            ]
            data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(data.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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

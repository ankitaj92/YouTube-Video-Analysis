"""A stand-in Ollama server.

Speaks enough of the Ollama HTTP API (``/api/tags``, ``/api/chat``) to exercise
the local path end to end - the request shape, the JSON-schema ``format``
parameter, the retry loop and the pipeline integration - without a model on the
machine. It answers by matching the requested schema's properties to a fixture.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# distinctive property -> fixture file
SCHEMA_FIXTURES = {
    "standard_process_flow": "SapStandardProcess",
    "leading_practices": "IndustryBenchmark",
    "design_decisions": "ProcessBlueprint",
    "gaps": "GapAnalysis",
    "steps": "CurrentProcess",
}


class OllamaStub:
    def __init__(self, fixtures_dir: Path, model: str = "stub-model:latest", bad_responses: int = 0) -> None:
        self.fixtures_dir = Path(fixtures_dir)
        self.model = model
        self.bad_responses = bad_responses     # emit N invalid replies first, to exercise retries
        self.requests: list[dict] = []         # every request, including preloads
        self.chats: list[dict] = []            # /api/chat only
        self._server = HTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "OllamaStub":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _payload_for(self, schema: dict) -> str:
        properties = set((schema or {}).get("properties", {}))
        for marker, fixture in SCHEMA_FIXTURES.items():
            if marker in properties:
                path = self.fixtures_dir / f"{fixture}.json"
                if path.exists():
                    return path.read_text(encoding="utf-8")
        return "{}"

    def _handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _send(self, payload: dict, status: int = 200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/tags"):
                    self._send({"models": [{"name": stub.model}]})
                else:
                    self._send({"error": "not found"}, 404)

            def _send_stream(self, content: str, model: str):
                """Emit Ollama's newline-delimited JSON stream, in small pieces."""
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                step = max(len(content) // 8, 1)
                for start in range(0, len(content), step):
                    event = {
                        "model": model,
                        "message": {"role": "assistant", "content": content[start:start + step]},
                        "done": False,
                    }
                    self.wfile.write((json.dumps(event) + "\n").encode())
                    self.wfile.flush()
                self.wfile.write((json.dumps({"model": model, "done": True}) + "\n").encode())
                self.wfile.flush()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                request = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append(request)

                if self.path.startswith("/api/generate"):
                    # model preload
                    self._send({"model": request.get("model"), "done": True})
                    return

                if self.path.startswith("/api/chat"):
                    stub.chats.append(request)
                    if stub.bad_responses > 0:
                        stub.bad_responses -= 1
                        content = "here you go: {not valid json"
                    else:
                        content = stub._payload_for(request.get("format") or {})
                    if request.get("stream"):
                        self._send_stream(content, request.get("model", ""))
                    else:
                        self._send({"model": request.get("model"),
                                    "message": {"role": "assistant", "content": content}})
                    return

                self._send({"error": "not found"}, 404)

        return Handler

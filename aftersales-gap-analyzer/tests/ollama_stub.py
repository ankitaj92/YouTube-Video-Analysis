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
        self.requests: list[dict] = []
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

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                request = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append(request)
                if self.path.startswith("/api/chat"):
                    if stub.bad_responses > 0:
                        stub.bad_responses -= 1
                        content = "here you go: {not valid json"
                    else:
                        content = stub._payload_for(request.get("format") or {})
                    self._send({"model": request.get("model"), "message": {"role": "assistant", "content": content}})
                else:
                    self._send({"error": "not found"}, 404)

        return Handler

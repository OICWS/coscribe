"""A local OpenAI-compatible endpoint that answers every request with "OK".

For running the Playwright suite (or clicking through the UI) without a
real model key: register it as a custom provider and point the default
model at it.

    python scripts/stub_llm.py            # serves http://127.0.0.1:9999/v1
    # providers.json:
    #   {"providers": {"stub": {"base_url": "http://127.0.0.1:9999/v1", "api_key": "x"}}}
    # .env: COSCRIBE_PROVIDERS_CONFIG_PATH=<that file>, COSCRIBE_DEFAULT_MODEL=stub:fake

It never calls tools, so it only exercises the UI around a turn, not the
agent.
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def _json(self, payload: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self) -> None:
        self._json({"data": [{"id": "fake", "object": "model"}]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        base = {"id": "stub", "created": int(time.time()), "model": body.get("model", "fake")}
        usage = {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
        if not body.get("stream"):
            message = {"role": "assistant", "content": "OK"}
            self._json(
                {
                    **base,
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                    "usage": usage,
                }
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks: list[dict[str, Any]] = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            # stream_usage=True makes ChatOpenAI wait for a usage-only chunk.
            {"choices": [], "usage": usage},
        ]
        for chunk in chunks:
            data = {**base, "object": "chat.completion.chunk", **chunk}
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), _Handler).serve_forever()


if __name__ == "__main__":
    main()

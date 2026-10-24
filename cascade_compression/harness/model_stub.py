"""Deterministic OpenAI-compatible model stub for fast-forward replays.

Speaks the exact wire contract the engine's LLM client uses
(``POST {base}/v1/chat/completions`` -> ``choices[0].message.content``,
see ``cascade.protocol.llm_complete``). Only the model is stubbed: the
classification queue, hybrid classifier, GCL gating, promotion engine,
and suppression all run the real code path.

Labeling policy (deterministic, disclosed):

- any prompt containing ``SIMULATED-IMPORTANT`` -> ``real_incident``
  (the lifecycle-injection marker: post a signal whose message carries
  it and watch instant authority withdrawal on an activated type)
- everything else -> ``routine_noise``

This makes every replayed type pure noise, which is the correct prior
for exercising the promotion -> activation -> suppression path. It does
NOT represent classification quality; replay conclusions are
governance-mechanical, never classification-quality claims.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IMPORTANT_MARKER = "SIMULATED-IMPORTANT"


def label_for_payload(payload: dict) -> str:
    """Deterministic label for one chat-completions request."""
    try:
        text = json.dumps(payload.get("messages", []))
    except (TypeError, ValueError):
        text = ""
    if IMPORTANT_MARKER in text:
        return "real_incident"
    return "routine_noise"


def completion_body(payload: dict, label: str) -> dict:
    return {
        "id": "stub-1",
        "object": "chat.completion",
        "model": payload.get("model", "stub"),
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": label},
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1},
    }


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib naming
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        data = json.dumps(
            completion_body(payload, label_for_payload(payload))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence per-request noise
        pass


def serve(port: int = 8091, host: str = "127.0.0.1") -> None:
    """Run the stub until interrupted."""
    ThreadingHTTPServer((host, port), StubHandler).serve_forever()


if __name__ == "__main__":
    serve()

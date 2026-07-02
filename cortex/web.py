"""Tiny stdlib HTTP surface for Railway health/status checks."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cortex.init import CortexInit
from cortex.sacred import ANTI_IDOLATRY
from cortex.oracle import OracleService
from cortex.self_train import SelfTrainer
from cortex.services import InvocationPipeline, ScribeService

ROOT = Path(os.environ.get("CORTEX_ROOT", os.getcwd())).resolve()


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        init = CortexInit(ROOT)
        if self.path in ("/", "/health"):
            self._json(200, {"status": "ok", "service": "cortex", "anti_idolatry": ANTI_IDOLATRY})
        elif self.path == "/status":
            self._json(200, init.status())
        elif self.path == "/law":
            law = ROOT / "LAW.md"
            self._json(200, {"law": law.read_text() if law.exists() else "LAW.md missing"})
        elif self.path == "/pid1":
            status = ROOT / "runtime" / "pid1.json"
            if status.exists():
                self._json(200, json.loads(status.read_text()))
            else:
                self._json(503, {"status": "pid1_status_missing"})
        elif self.path == "/self-train/report":
            self._json(200, SelfTrainer(ROOT).report())
        elif self.path.startswith("/ledger/"):
            stream = self.path.removeprefix("/ledger/")
            if stream not in {"actions.jsonl", "refusals.jsonl", "witnesses.jsonl", "mutations.jsonl", "pid1-signals.jsonl", "training.jsonl"}:
                self._json(404, {"status": "unknown_ledger_stream"})
            else:
                self._json(200, {"status": "ok", "stream": stream, "records": ScribeService(ROOT).read_tail(stream)})
        else:
            self._json(404, {"status": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._json(400, {"status": "bad_json", "error": str(exc)})
            return

        pipeline = InvocationPipeline(ROOT)
        if self.path == "/invoke":
            result = pipeline.invoke(payload)
            self._json(200 if result["status"] == "accepted" else 403, result)
        elif self.path == "/oracle":
            task = str(payload.get("task", "")).strip()
            if not task:
                self._json(400, {"status": "bad_request", "reason": "task is required"})
            else:
                result = OracleService(ROOT).propose(task, str(payload.get("authority", "interpret")), payload.get("context", {}))
                ScribeService(ROOT).append("actions.jsonl", {"actor": "web.oracle", "action_type": "oracle_proposal", "status": "proposed", "oracle": result.to_dict()})
                self._json(200, result.to_dict())
        elif self.path == "/self-test":
            result = pipeline.self_test()
            self._json(200 if result["status"] == "pass" else 500, result)
        elif self.path == "/self-train/collect":
            self._json(200, SelfTrainer(ROOT).collect())
        elif self.path == "/self-train/eval":
            result = SelfTrainer(ROOT).eval()
            self._json(200 if result["status"] in {"pass", "blocked"} else 500, result)
        else:
            self._json(404, {"status": "not_found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        print("cortex-web", self.address_string(), fmt % args)


def main() -> None:
    CortexInit(ROOT).boot()
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"cortex web serving on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

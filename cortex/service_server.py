"""Localhost IPC service servers for supervised Cortex child roles."""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cortex.memory_service import MemoryService
from cortex.oracle import OracleService
from cortex.planner import PlannerService
from cortex.prophet import ProphetService
from cortex.services import GuardianService, ScribeService
from cortex.tool_gateway import ToolGateway

DEFAULT_PORTS = {"guardian": 8101, "scribe": 8102, "oracle": 8103, "prophet": 8104, "memory": 8105, "tool": 8106, "planner": 8107}


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    handler.send_response(code)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("content-length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


class RoleHandler(BaseHTTPRequestHandler):
    role = "unknown"
    root = Path(os.environ.get("CORTEX_ROOT", os.getcwd())).resolve()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            _json_response(self, 200, {"status": "ok", "role": self.role})
            return
        if self.role == "scribe" and self.path.startswith("/tail/"):
            stream = self.path.removeprefix("/tail/")
            _json_response(self, 200, {"status": "ok", "records": ScribeService(self.root).read_tail(stream)})
            return
        if self.role == "prophet" and self.path == "/report":
            _json_response(self, 200, ProphetService(self.root).latest())
            return
        if self.role == "planner" and self.path == "/backlog":
            _json_response(self, 200, PlannerService(self.root).backlog())
            return
        _json_response(self, 404, {"status": "not_found", "role": self.role})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = _read_json(self)
        except json.JSONDecodeError as exc:
            _json_response(self, 400, {"status": "bad_json", "error": str(exc)})
            return

        if self.role == "guardian" and self.path == "/check":
            result = GuardianService(self.root).check_invocation(
                str(payload.get("authority", "interpret")),
                list(payload.get("tools", []) or []),
                bool(payload.get("confirmed", False)),
            )
            _json_response(self, 200, {"allowed": result.allowed, "reason": result.reason, "law": result.law})
            return

        if self.role == "scribe" and self.path == "/append":
            stream = str(payload.get("stream", "actions.jsonl"))
            record = dict(payload.get("record", {}) or {})
            _json_response(self, 200, {"status": "ok", "record": ScribeService(self.root).append(stream, record)})
            return

        if self.role == "oracle" and self.path == "/propose":
            result = OracleService(self.root).propose(
                str(payload.get("task", "")),
                str(payload.get("authority", "interpret")),
                dict(payload.get("context", {}) or {}),
            )
            _json_response(self, 200, result.to_dict())
            return

        if self.role == "prophet" and self.path == "/evaluate":
            _json_response(self, 200, ProphetService(self.root).evaluate())
            return

        if self.role == "memory" and self.path == "/write":
            try:
                rec = MemoryService(self.root).write(str(payload.get("type", "inferred")), str(payload.get("content", "")), str(payload.get("source", "")), float(payload.get("confidence", 0.8)), payload.get("witness"))
                _json_response(self, 200, {"status": "remembered", "record": rec})
            except Exception as exc:
                _json_response(self, 400, {"status": "refused", "reason": str(exc)})
            return
        if self.role == "memory" and self.path == "/retrieve":
            _json_response(self, 200, {"status": "ok", "records": MemoryService(self.root).retrieve(str(payload.get("query", "")), payload.get("type"))})
            return

        if self.role == "tool" and self.path == "/execute":
            result = ToolGateway(self.root).execute(str(payload.get("tool", "")), dict(payload.get("args", {}) or {}), str(payload.get("authority", "observe")), payload.get("witness"))
            _json_response(self, 200 if result["status"] == "completed" else 403, result)
            return

        if self.role == "planner" and self.path == "/reflect":
            _json_response(self, 200, PlannerService(self.root).reflect())
            return
        if self.role == "planner" and self.path == "/choose-next":
            _json_response(self, 200, PlannerService(self.root).choose_next())
            return

        _json_response(self, 404, {"status": "not_found", "role": self.role})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"cortex-{self.role}", self.address_string(), fmt % args, flush=True)


def make_handler(role: str, root: Path):
    class Handler(RoleHandler):
        pass

    Handler.role = role
    Handler.root = root
    return Handler


def serve(role: str, root: Path, port: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(role, root))
    print(f"cortex {role} service serving on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cortex-service")
    parser.add_argument("role", choices=sorted(DEFAULT_PORTS))
    parser.add_argument("--root", default=os.environ.get("CORTEX_ROOT", os.getcwd()))
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    serve(args.role, Path(args.root).resolve(), args.port or DEFAULT_PORTS[args.role])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

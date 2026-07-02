"""Tiny stdlib HTTP surface for Railway health/status checks."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cortex.init import CortexInit
from cortex.ipc import GuardianClient, OracleClient, ProphetClient, ScribeClient
from cortex.memory_service import MemoryService
from cortex.planner import PlannerService
from cortex.sacred import ANTI_IDOLATRY
from cortex.self_train import SelfTrainer
from cortex.services import InvocationPipeline
from cortex.tool_gateway import ToolGateway
from cortex.witness import WitnessService

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
        elif self.path == "/prophet/report":
            self._json(200, ProphetClient(ROOT).report())
        elif self.path == "/planner/backlog":
            self._json(200, PlannerService(ROOT).backlog())
        elif self.path == "/witnesses":
            self._json(200, {"status": "ok", "witnesses": WitnessService(ROOT).list()})
        elif self.path.startswith("/memory/"):
            typ = self.path.removeprefix("/memory/") or None
            self._json(200, {"status": "ok", "records": MemoryService(ROOT).retrieve(typ=typ if typ else None)})
        elif self.path == "/self-train/report":
            self._json(200, SelfTrainer(ROOT).report())
        elif self.path.startswith("/ledger/"):
            stream = self.path.removeprefix("/ledger/")
            if stream not in {"actions.jsonl", "refusals.jsonl", "witnesses.jsonl", "mutations.jsonl", "pid1-signals.jsonl", "training.jsonl"}:
                self._json(404, {"status": "unknown_ledger_stream"})
            else:
                self._json(200, {"status": "ok", "stream": stream, "records": ScribeClient(ROOT).read_tail(stream)})
        else:
            self._json(404, {"status": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._json(400, {"status": "bad_json", "error": str(exc)})
            return

        pipeline = InvocationPipeline(ROOT)
        scribe = ScribeClient(ROOT)
        if self.path == "/invoke":
            result = self._invoke_ipc(payload, scribe)
            self._json(200 if result["status"] == "accepted" else 403, result)
        elif self.path == "/oracle":
            task = str(payload.get("task", "")).strip()
            if not task:
                self._json(400, {"status": "bad_request", "reason": "task is required"})
            else:
                result = OracleClient(ROOT).propose(task, str(payload.get("authority", "interpret")), payload.get("context", {}))
                scribe.append("actions.jsonl", {"actor": "web.oracle", "action_type": "oracle_proposal", "status": "proposed", "oracle": result})
                self._json(200, result)
        elif self.path == "/self-test":
            result = pipeline.self_test()
            self._json(200 if result["status"] == "pass" else 500, result)
        elif self.path == "/prophet/evaluate":
            result = ProphetClient(ROOT).evaluate()
            self._json(200 if result["status"] == "pass" else 500, result)
        elif self.path == "/self-train/collect":
            self._json(200, SelfTrainer(ROOT).collect())
        elif self.path == "/self-train/eval":
            result = SelfTrainer(ROOT).eval()
            self._json(200 if result["status"] in {"pass", "blocked"} else 500, result)
        elif self.path == "/memory/write":
            try:
                rec = MemoryService(ROOT).write(str(payload.get("type", "inferred")), str(payload.get("content", "")), str(payload.get("source", "")), float(payload.get("confidence", 0.8)), payload.get("witness"))
                self._json(200, {"status": "remembered", "record": rec})
            except Exception as exc:
                self._json(400, {"status": "refused", "reason": str(exc)})
        elif self.path == "/memory/retrieve":
            self._json(200, {"status": "ok", "records": MemoryService(ROOT).retrieve(str(payload.get("query", "")), payload.get("type"))})
        elif self.path == "/witness":
            rec = WitnessService(ROOT).witness(str(payload.get("witness", payload.get("name", "human"))), str(payload.get("statement", "")), str(payload.get("scope", "general")), payload.get("signature"))
            self._json(200, {"status": "witnessed", "record": rec})
        elif self.path == "/planner/reflect":
            self._json(200, PlannerService(ROOT).reflect())
        elif self.path == "/planner/choose-next":
            self._json(200, PlannerService(ROOT).choose_next())
        elif self.path == "/tool/execute":
            result = ToolGateway(ROOT).execute(str(payload.get("tool", "")), dict(payload.get("args", {}) or {}), str(payload.get("authority", "observe")), payload.get("witness"))
            self._json(200 if result["status"] == "completed" else 403, result)
        else:
            self._json(404, {"status": "not_found"})

    def _invoke_ipc(self, payload: dict[str, Any], scribe: ScribeClient) -> dict[str, Any]:
        task = str(payload.get("task", "")).strip()
        authority = str(payload.get("authority", payload.get("authority_level", "interpret")))
        tools = list(payload.get("tools", payload.get("permitted_tools", [])) or [])
        witness = payload.get("witness")
        confirmed = bool(payload.get("confirm", payload.get("confirmed", False)))
        guardian = GuardianClient(ROOT).check_invocation(authority, tools, confirmed)
        base = {
            "actor": "web.invoke",
            "task": task,
            "authority_level": authority,
            "tools": tools,
            "witnesses": [witness] if witness else [],
            "law_references": guardian.get("law", []),
            "guardian_reason": guardian.get("reason", ""),
            "ipc": True,
        }
        if not task:
            guardian = {"allowed": False, "reason": "task is required", "law": ["LAW 4"]}
        if not guardian.get("allowed"):
            refusal = scribe.append("refusals.jsonl", {**base, "action_type": "refuse", "status": "refused"})
            scribe.append("actions.jsonl", {**base, "action_type": "refuse", "status": "refused"})
            return {"status": "refused", "reason": guardian.get("reason"), "law": guardian.get("law", []), "anti_idolatry": ANTI_IDOLATRY, "record": refusal}
        record = scribe.append("actions.jsonl", {**base, "action_type": "invoke", "status": "accepted"})
        oracle = OracleClient(ROOT).propose(task, authority, {"tools": tools, "witness": witness})
        oracle_record = scribe.append("actions.jsonl", {**base, "action_type": "oracle_proposal", "status": "proposed", "oracle": oracle})
        return {
            "status": "accepted",
            "task": task,
            "authority_level": authority,
            "guardian": guardian.get("reason"),
            "oracle": oracle,
            "response": oracle.get("proposal", ""),
            "anti_idolatry": ANTI_IDOLATRY,
            "record": record,
            "oracle_record": oracle_record,
        }

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

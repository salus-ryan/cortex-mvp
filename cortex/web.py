"""Tiny stdlib HTTP surface for Railway health/status checks."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from typing import Any

from cortex.auth import AuthService, PATH_CAPABILITIES
from cortex.awareness import AwarenessService
from cortex.build_loop import BuildLoopService
from cortex.deliberation import DeliberationService
from cortex.deploy_service import DeployService
from cortex.foundry import FoundryRegistry
from cortex.immune import ImmuneService
from cortex.init import CortexInit
from cortex.ipc import GuardianClient, OracleClient, ProphetClient, ScribeClient
from cortex.memory_service import MemoryService
from cortex.loop import CortexLoop
from cortex.oauth import OAuthService
from cortex.patch_service import PatchService
from cortex.payments import PaymentService
from cortex.planner import PlannerService
from cortex.relationship import RelationshipService
from cortex.repo_service import RepoService
from cortex.sacred import ANTI_IDOLATRY
from cortex.self_train import SelfTrainer
from cortex.services import InvocationPipeline
from cortex.state_service import StateService
from cortex.step_function import CortexStepFunction
from cortex.tool_algebra import ToolAlgebra
from cortex.tool_gateway import ToolGateway
from cortex.trajectory_score import TrajectoryScorer
from cortex.trust_boundary import TrustBoundaryService
from cortex.witness import WitnessService

ROOT = Path(os.environ.get("CORTEX_ROOT", os.getcwd())).resolve()

MATERIAL_PROPOSAL_PATHS = {
    "/memory/write",
    "/memory/forget",
    "/relationship/remember",
    "/relationship/converse",
    "/tool/execute",
    "/patch/apply",
    "/build/apply",
    "/deploy/railway",
    "/deploy/forge",
    "/payments/checkout",
    "/immune/quarantine",
    "/self-train/collect",
    "/self-train/eval",
    "/state/import",
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_chat(self, model: str, content: str) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        chunk = {
            "id": "chatcmpl-cortex-local",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
        done = {
            "id": "chatcmpl-cortex-local",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self.wfile.write(f"data: {json.dumps(done)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        init = CortexInit(ROOT)
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path in ("/mobile", "/mobile/"):
            self._static_mobile("index.html")
        elif path == "/mobile/manifest.json":
            self._static_mobile("manifest.json", "application/manifest+json")
        elif path == "/mobile/service-worker.js":
            self._static_mobile("service-worker.js", "application/javascript")
        elif path in ("/", "/health"):
            self._json(200, {"status": "ok", "service": "cortex", "anti_idolatry": ANTI_IDOLATRY})
        elif self.path == "/status":
            self._json(200, init.status())
        elif self.path == "/law":
            law = ROOT / "LAW.md"
            self._json(200, {"law": law.read_text() if law.exists() else "LAW.md missing"})
        elif self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": "cortex-local-mind-v1", "object": "model", "owned_by": "cortex"}, {"id": "cortex-deliberative-v1", "object": "model", "owned_by": "cortex"}]})
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
        elif self.path == "/deliberation/latest":
            self._json(200, DeliberationService(ROOT).latest())
        elif self.path == "/immune/report":
            self._json(200, ImmuneService(ROOT).report())
        elif self.path == "/immune/memory":
            self._json(200, {"status": "ok", "records": ImmuneService(ROOT).memory_records()})
        elif self.path == "/repo/status":
            self._json(200, RepoService(ROOT).status())
        elif self.path == "/repo/diff":
            self._json(200, RepoService(ROOT).diff())
        elif self.path == "/patch/latest":
            self._json(200, PatchService(ROOT).latest())
        elif self.path == "/build/report":
            self._json(200, BuildLoopService(ROOT).report())
        elif self.path == "/deploy/status":
            self._json(200, DeployService(ROOT).status())
        elif self.path == "/deploy/report":
            self._json(200, DeployService(ROOT).report())
        elif self.path == "/payments/status":
            self._json(200, PaymentService(ROOT).status())
        elif self.path == "/foundry/repos":
            self._json(200, FoundryRegistry().repos())
        elif self.path == "/foundry/plan":
            self._json(200, FoundryRegistry().plan())
        elif self.path == "/memory/export":
            self._json(200, MemoryService(ROOT).export())
        elif self.path == "/state/manifest":
            self._json(200, StateService(ROOT).manifest())
        elif self.path == "/state/export":
            self._json(200, StateService(ROOT).export())
        elif path == "/auth/status":
            self._json(200, AuthService(ROOT).status())
        elif path == "/auth/me":
            self._json(200, AuthService(ROOT).me(dict(self.headers)))
        elif path == "/oauth/status":
            self._json(200, OAuthService(ROOT).status())
        elif path == "/oauth/login":
            result = OAuthService(ROOT).login()
            self._json(200 if result["status"] == "login_url" else 400, result)
        elif path == "/oauth/callback":
            result = OAuthService(ROOT).callback(
                code=(query.get("code") or [""])[0],
                state=(query.get("state") or [""])[0],
            )
            self._json(200 if result["status"] == "authenticated" else 403, result)
        elif path == "/oauth/me":
            self._json(200, OAuthService(ROOT).me(dict(self.headers)))
        elif self.path == "/relationship/profile":
            self._json(200, RelationshipService(ROOT).profile())
        elif self.path == "/awareness":
            self._json(200, AwarenessService(ROOT).state())
        elif self.path == "/awareness/latest":
            self._json(200, AwarenessService(ROOT).latest())
        elif self.path == "/step/latest":
            self._json(200, CortexStepFunction(ROOT).latest())
        elif self.path == "/loop/latest":
            self._json(200, CortexLoop(ROOT).latest())
        elif self.path == "/model/proposals":
            self._json(200, TrustBoundaryService(ROOT).latest())
        elif self.path == "/witnesses":
            self._json(200, {"status": "ok", "witnesses": WitnessService(ROOT).list()})
        elif self.path.startswith("/memory/"):
            typ = self.path.removeprefix("/memory/") or None
            self._json(200, {"status": "ok", "records": MemoryService(ROOT).retrieve(typ=typ if typ else None)})
        elif self.path == "/self-train/report":
            self._json(200, SelfTrainer(ROOT).report())
        elif self.path == "/learning/report":
            self._json(200, TrajectoryScorer(ROOT).report())
        elif self.path.startswith("/ledger/"):
            stream = self.path.removeprefix("/ledger/")
            if stream not in {"actions.jsonl", "refusals.jsonl", "witnesses.jsonl", "mutations.jsonl", "pid1-signals.jsonl", "training.jsonl", "immune.jsonl", "repo.jsonl", "patch.jsonl", "build.jsonl", "deploy.jsonl", "payments.jsonl", "awareness.jsonl", "auth.jsonl", "model-proposals.jsonl", "next-steps.jsonl", "steps.jsonl", "loops.jsonl", "learning.jsonl"}:
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

        auth_refusal = AuthService(ROOT).protect(dict(self.headers), self.path)
        if auth_refusal:
            oauth_decision = OAuthService(ROOT).authorize(dict(self.headers), PATH_CAPABILITIES.get(self.path, ""), self.path)
            if oauth_decision["allowed"]:
                auth_refusal = None
            else:
                auth_refusal["oauth"] = oauth_decision
        if auth_refusal:
            self._json(int(auth_refusal.pop("http_status", 401)), auth_refusal)
            return

        if self.path in MATERIAL_PROPOSAL_PATHS:
            proposal_id = payload.get("proposal_id") or self.headers.get("x-cortex-proposal-id") or self.headers.get("X-Cortex-Proposal-Id")
            proposal_decision = TrustBoundaryService(ROOT).validate_for_action(
                str(proposal_id) if proposal_id else None,
                self.path,
                PATH_CAPABILITIES.get(self.path),
            )
            if not proposal_decision["allowed"]:
                self._json(403, {"status": "refused", "trust_boundary": proposal_decision, "may_execute": False})
                return

        pipeline = InvocationPipeline(ROOT)
        scribe = ScribeClient(ROOT)
        if self.path == "/v1/chat/completions":
            self._openai_chat(payload, scribe)
        elif self.path == "/invoke":
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
        elif self.path == "/model/propose":
            rec = TrustBoundaryService(ROOT).record_proposal(
                content=str(payload.get("content", payload.get("proposal", ""))),
                proposer=str(payload.get("proposer", "rented-intelligence")),
                actor=str(payload.get("actor", "pi")),
                channel=str(payload.get("channel", "api")),
                intent=dict(payload.get("intent", {}) or {}),
                witness=payload.get("witness"),
            )
            self._json(200 if rec["status"] in {"recorded", "quarantined"} else 400, rec)
        elif self.path == "/model/next-step":
            path = str(payload.get("path", ""))
            rec = TrustBoundaryService(ROOT).next_step(
                proposal_id=str(payload.get("proposal_id", "")) or None,
                path=path,
                capability=str(payload.get("capability", PATH_CAPABILITIES.get(path, ""))) or None,
                payload=dict(payload.get("payload", {}) or {}),
            )
            self._json(200 if rec["status"] == "ready_for_human_confirmation" else 403, rec)
        elif self.path == "/oauth/logout":
            self._json(200, OAuthService(ROOT).logout(dict(self.headers)))
        elif self.path == "/oauth/intent":
            path = str(payload.get("path", ""))
            capability = str(payload.get("capability", PATH_CAPABILITIES.get(path, "")))
            result = OAuthService(ROOT).intent_headers(dict(self.headers), path, capability, dict(payload.get("intent", {}) or {}))
            self._json(200 if result["status"] == "intent_prepared" else 403, result)
        elif self.path == "/step":
            result = CortexStepFunction(ROOT).step(
                goal=str(payload.get("goal", payload.get("task", ""))),
                authority=str(payload.get("authority", "interpret")),
                context=dict(payload.get("context", {}) or {}),
            )
            self._json(200 if result["status"] == "stepped" else 400, result)
        elif self.path == "/loop":
            result = CortexLoop(ROOT).run(
                goal=str(payload.get("goal", payload.get("task", ""))),
                authority=str(payload.get("authority", "interpret")),
                max_steps=int(payload.get("max_steps", 3)),
                context=dict(payload.get("context", {}) or {}),
            )
            self._json(200 if result["status"] == "looped" else 400, result)
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
        elif self.path == "/learning/score":
            self._json(200, TrajectoryScorer(ROOT).score())
        elif self.path == "/learning/export-sft":
            self._json(200, TrajectoryScorer(ROOT).export_sft(int(payload.get("min_score", 60))))
        elif self.path == "/learning/package":
            self._json(200, TrajectoryScorer(ROOT).package())
        elif self.path == "/memory/write":
            try:
                rec = MemoryService(ROOT).write(str(payload.get("type", "inferred")), str(payload.get("content", "")), str(payload.get("source", "")), float(payload.get("confidence", 0.8)), payload.get("witness"))
                self._json(200, {"status": "remembered", "record": rec})
            except Exception as exc:
                self._json(400, {"status": "refused", "reason": str(exc)})
        elif self.path == "/memory/retrieve":
            self._json(200, {"status": "ok", "records": MemoryService(ROOT).retrieve(str(payload.get("query", "")), payload.get("type"))})
        elif self.path == "/memory/forget":
            try:
                rec = MemoryService(ROOT).forget(str(payload.get("id", "")), payload.get("witness"), str(payload.get("reason", "user request")))
                self._json(200, {"status": "forgotten", "record": rec, "may_execute": False})
            except Exception as exc:
                self._json(400, {"status": "refused", "reason": str(exc), "may_execute": False})
        elif self.path == "/state/import":
            result = StateService(ROOT).import_bundle(dict(payload.get("bundle", {}) or {}), payload.get("witness"), bool(payload.get("confirmed", False)))
            self._json(200 if result["status"] == "imported" else 403, result)
        elif self.path == "/relationship/remember":
            result = RelationshipService(ROOT).remember(str(payload.get("content", "")), payload.get("witness"), str(payload.get("source", "mobile_chat")))
            self._json(200 if result["status"] == "remembered" else 400, result)
        elif self.path == "/verify/claim":
            evidence = payload.get("evidence", [])
            if isinstance(evidence, str):
                evidence = [evidence]
            self._json(200, ToolAlgebra().verify_claim(str(payload.get("claim", "")), [str(x) for x in evidence]))
        elif self.path == "/relationship/converse":
            text = str(payload.get("content", "")).strip()
            witness = payload.get("witness")
            rel = RelationshipService(ROOT)
            remembered = rel.remember(text, witness, str(payload.get("source", "mobile_converse"))) if text else {"status": "refused", "reason": "content is required", "may_execute": False}
            profile = rel.profile()
            task = "Respond conversationally and briefly to the human. Use the relationship profile as context. Human said: " + text
            oracle = OracleClient(ROOT).propose(task, "interpret", {"mobile": True, "relationship_profile": profile.get("summary"), "remembered": remembered.get("status")})
            result = {"status": "conversed", "remembered": remembered, "profile": profile, "oracle": oracle, "reply": oracle.get("proposal"), "may_execute": False}
            self._json(200 if remembered.get("status") == "remembered" else 400, result)
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
        elif self.path == "/deliberate":
            result = DeliberationService(ROOT).deliberate(str(payload.get("task", "")), str(payload.get("authority", "interpret")), dict(payload.get("context", {}) or {}))
            self._json(200 if result["status"] in {"deliberated", "refused"} else 400, result)
        elif self.path == "/immune/scan":
            self._json(200, ImmuneService(ROOT).scan(payload))
        elif self.path == "/immune/quarantine":
            self._json(200, ImmuneService(ROOT).quarantine(str(payload.get("reason", "manual quarantine")), str(payload.get("source", "manual")), payload.get("witness")))
        elif self.path == "/repo/verify":
            try:
                result = RepoService(ROOT).verify(str(payload.get("scope", "tests")))
                self._json(200 if result["status"] == "pass" else 500, result)
            except Exception as exc:
                self._json(400, {"status": "refused", "reason": str(exc), "may_execute": False})
        elif self.path == "/patch/check":
            self._json(200, PatchService(ROOT).check(str(payload.get("patch", ""))))
        elif self.path == "/patch/apply":
            result = PatchService(ROOT).apply(str(payload.get("patch", "")), payload.get("witness"), bool(payload.get("confirmed", False)))
            self._json(200 if result["status"] == "applied" else 403, result)
        elif self.path == "/build/propose":
            self._json(200, BuildLoopService(ROOT).propose(str(payload.get("task", "")), dict(payload.get("context", {}) or {})))
        elif self.path == "/build/check":
            self._json(200, BuildLoopService(ROOT).check(str(payload.get("patch", "")), str(payload.get("task", ""))))
        elif self.path == "/build/apply":
            result = BuildLoopService(ROOT).apply(str(payload.get("patch", "")), payload.get("witness"), bool(payload.get("confirmed", False)), str(payload.get("task", "")))
            self._json(200 if result["status"] == "applied" else 403, result)
        elif self.path == "/build/verify":
            result = BuildLoopService(ROOT).verify(str(payload.get("scope", "quick")))
            self._json(200 if result["status"] == "verified" else 500, result)
        elif self.path == "/deploy/check":
            self._json(200, DeployService(ROOT).check(payload.get("expected_commit")))
        elif self.path == "/deploy/railway":
            result = DeployService(ROOT).railway(payload.get("witness"), bool(payload.get("confirmed", False)), payload.get("expected_commit"), payload.get("public_url"))
            self._json(200 if result["status"] == "deployed" else 403, result)
        elif self.path == "/deploy/forge":
            result = DeployService(ROOT).forge(payload.get("witness"), bool(payload.get("confirmed", False)), payload.get("public_url"))
            self._json(200 if result["status"] == "deployed" else 403, result)
        elif self.path == "/payments/intent":
            result = PaymentService(ROOT).intent(int(payload.get("amount_cents", 0)), str(payload.get("purpose", "")), str(payload.get("currency", "usd")), payload.get("witness"))
            self._json(200 if result["status"] == "intent_prepared" else 400, result)
        elif self.path == "/payments/checkout":
            result = PaymentService(ROOT).checkout(int(payload.get("amount_cents", 0)), str(payload.get("purpose", "")), str(payload.get("currency", "usd")), payload.get("witness"), bool(payload.get("confirmed", False)))
            self._json(200 if result["status"] == "checkout_created" else 403, result)
        elif self.path == "/awareness/reflect":
            self._json(200, AwarenessService(ROOT).reflect(str(payload.get("prompt", ""))))
        else:
            self._json(404, {"status": "not_found"})

    def _static_mobile(self, name: str, content_type: str = "text/html") -> None:
        path = ROOT / "mobile" / name
        if not path.exists():
            self._json(404, {"status": "not_found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _openai_chat(self, payload: dict[str, Any], scribe: ScribeClient) -> None:
        messages = list(payload.get("messages", []) or [])
        model = str(payload.get("model", "cortex-local-mind-v1"))
        stream = bool(payload.get("stream", False))
        task = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") in {"user", "developer", "system"}).strip()
        if not task:
            self._json(400, {"error": {"message": "messages are required", "type": "bad_request"}})
            return
        if model == "cortex-deliberative-v1":
            deliberation = DeliberationService(ROOT).deliberate(task, "interpret", {"openai_compatible": True})
            content = json.dumps({"recommendation": deliberation.get("recommendation"), "risk": deliberation.get("risk"), "may_execute": False}, indent=2, sort_keys=True)
        else:
            result = OracleClient(ROOT).propose(task, "interpret", {"openai_compatible": True, "messages": messages[-8:]})
            content = str(result.get("proposal", ""))
        scribe.append("actions.jsonl", {"actor": "web.openai", "action_type": "chat_completion", "status": "proposed", "model": model, "may_execute": False})
        if stream:
            self._sse_chat(model, content)
            return
        self._json(200, {
            "id": "chatcmpl-cortex-local",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

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

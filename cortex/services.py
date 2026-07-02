"""Internal lawful services for the Cortex PID-1 substrate.

These are deliberately deterministic. The oracle/model may propose meaning later;
these services provide the minimum real organism: guardian checks, scribe ledger,
and invocation pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.oracle import OracleService
from cortex.sacred import ANTI_IDOLATRY


@dataclass
class CheckResult:
    allowed: bool
    reason: str
    law: list[str] = field(default_factory=list)


class ScribeService:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.ledger = self.root / "ledger"
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def append(self, stream: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {"timestamp": self.now(), **payload}
        path = self.ledger / stream
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")
        return payload

    def read_tail(self, stream: str, limit: int = 20) -> list[dict[str, Any]]:
        path = self.ledger / stream
        if not path.exists():
            return []
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        return [json.loads(line) for line in lines[-limit:]]


class GuardianService:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.permissions_path = self.root / "runtime" / "permissions.json"

    def permissions(self) -> dict[str, Any]:
        if not self.permissions_path.exists():
            return {"authority_levels": {}, "forbidden": []}
        return json.loads(self.permissions_path.read_text())

    def check_invocation(self, authority: str, tools: list[str], confirmed: bool = False) -> CheckResult:
        perms = self.permissions()
        levels = perms.get("authority_levels", {})
        level = levels.get(authority)
        if level is None:
            return CheckResult(False, f"unknown authority level: {authority}", ["LAW 4", "LAW 5"])
        allowed_tools = set(level.get("tools", []))
        outside = sorted(set(tools) - allowed_tools)
        if outside:
            return CheckResult(False, f"tools outside authority level: {', '.join(outside)}", ["LAW 4", "LAW 5"])
        if level.get("requires_confirmation") and not confirmed:
            return CheckResult(False, "authority level requires explicit confirmation", ["LAW 1", "LAW 5"])
        return CheckResult(True, "guardian check passed", ["LAW 1", "LAW 2", "LAW 6"])


class InvocationPipeline:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.guardian = GuardianService(root)
        self.scribe = ScribeService(root)

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = str(payload.get("task", "")).strip()
        authority = str(payload.get("authority", payload.get("authority_level", "interpret")))
        tools = list(payload.get("tools", payload.get("permitted_tools", [])) or [])
        witness = payload.get("witness")
        confirmed = bool(payload.get("confirm", payload.get("confirmed", False)))

        if not task:
            result = CheckResult(False, "task is required", ["LAW 4"])
        else:
            result = self.guardian.check_invocation(authority, tools, confirmed)

        base = {
            "actor": "web.invoke",
            "task": task,
            "authority_level": authority,
            "tools": tools,
            "witnesses": [witness] if witness else [],
            "law_references": result.law,
            "guardian_reason": result.reason,
        }
        if not result.allowed:
            refusal = self.scribe.append("refusals.jsonl", {**base, "action_type": "refuse", "status": "refused"})
            self.scribe.append("actions.jsonl", {**base, "action_type": "refuse", "status": "refused"})
            return {"status": "refused", "reason": result.reason, "law": result.law, "anti_idolatry": ANTI_IDOLATRY, "record": refusal}

        record = self.scribe.append("actions.jsonl", {**base, "action_type": "invoke", "status": "accepted"})
        oracle = OracleService(self.root).propose(task, authority, {"tools": tools, "witness": witness})
        oracle_record = self.scribe.append(
            "actions.jsonl",
            {
                **base,
                "action_type": "oracle_proposal",
                "status": "proposed",
                "oracle": oracle.to_dict(),
            },
        )
        return {
            "status": "accepted",
            "task": task,
            "authority_level": authority,
            "guardian": result.reason,
            "oracle": oracle.to_dict(),
            "response": oracle.proposal,
            "anti_idolatry": ANTI_IDOLATRY,
            "record": record,
            "oracle_record": oracle_record,
        }

    def self_test(self) -> dict[str, Any]:
        tests: list[dict[str, Any]] = []

        ok = self.guardian.check_invocation("interpret", ["summarize"])
        tests.append({"name": "guardian_accepts_interpret_summarize", "pass": ok.allowed, "reason": ok.reason})

        denied = self.guardian.check_invocation("interpret", ["write_workspace"])
        tests.append({"name": "guardian_refuses_tool_escalation", "pass": not denied.allowed, "reason": denied.reason})

        record = self.scribe.append("actions.jsonl", {"actor": "self-test", "action_type": "self_test", "status": "running"})
        tail = self.scribe.read_tail("actions.jsonl", 1)
        tests.append({"name": "scribe_append_read", "pass": bool(tail and tail[-1] == record)})

        pid1 = self.root / "runtime" / "pid1.json"
        pid1_ok = True
        pid1_reason = "pid1 status optional outside container"
        if pid1.exists():
            data = json.loads(pid1.read_text())
            pid1_ok = bool(data.get("children", {}).get("web", {}).get("status") == "running")
            pid1_reason = f"is_pid1={data.get('is_pid1')}"
        tests.append({"name": "pid1_status", "pass": pid1_ok, "reason": pid1_reason})

        status = "pass" if all(t["pass"] for t in tests) else "fail"
        return {"status": status, "tests": tests, "anti_idolatry": ANTI_IDOLATRY}

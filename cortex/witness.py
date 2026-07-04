"""Witness and governance primitives."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WitnessService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def witness(self, name: str, statement: str, scope: str = "general", signature: str | None = None) -> dict[str, Any]:
        text = f"{name}:{scope}:{statement}:{signature or ''}"
        rec = {
            "id": "wit_" + uuid.uuid4().hex[:12],
            "timestamp": self.now(),
            "witness": name,
            "scope": scope,
            "statement": statement,
            "signature": signature,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        with (self.ledger / "witnesses.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def list(self, scope: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        path = self.ledger / "witnesses.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if scope:
            rows = [r for r in rows if r.get("scope") == scope]
        return rows[-limit:]

    def has_scope(self, scope: str, count: int = 1) -> bool:
        return len(self.list(scope, limit=1000)) >= count

    def risk_policy(self) -> dict[str, Any]:
        tiers = {
            "observe": {"witnesses_required": 0, "signature_required": False, "human_confirmation": False},
            "interpret": {"witnesses_required": 0, "signature_required": False, "human_confirmation": False},
            "prepare": {"witnesses_required": 1, "signature_required": False, "human_confirmation": False},
            "act_reversible": {"witnesses_required": 1, "signature_required": False, "human_confirmation": True},
            "act_irreversible": {"witnesses_required": 2, "signature_required": True, "human_confirmation": True},
        }
        return {"status": "witness_risk_policy", "tiers": tiers, "may_execute": False}

    def check_risk_policy(self, authority: str, scope: str) -> dict[str, Any]:
        policy = self.risk_policy()["tiers"].get(authority)
        if not policy:
            return {"status": "refused", "reason": "unknown authority", "allowed": False, "may_execute": False}
        witnesses = self.list(scope, limit=1000)
        signed = [w for w in witnesses if w.get("signature")]
        checks = [
            {"name": "witness_count", "passed": len(witnesses) >= policy["witnesses_required"], "actual": len(witnesses), "expected": policy["witnesses_required"]},
            {"name": "signature", "passed": (not policy["signature_required"]) or bool(signed), "actual": len(signed), "expected": ">=1" if policy["signature_required"] else "not required"},
        ]
        return {
            "status": "witness_policy_check",
            "authority": authority,
            "scope": scope,
            "allowed": all(c["passed"] for c in checks),
            "checks": checks,
            "human_confirmation_required": policy["human_confirmation"],
            "may_execute": False,
        }

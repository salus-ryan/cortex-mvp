"""Trust-boundary records for Pi, rented intelligence, and Cortex.

This module treats external/rented model output as an untrusted proposal. It can
be logged, scanned, and routed for human/Cortex review, but it never grants
execution authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.immune import ImmuneService


class TrustBoundaryService:
    """Ledger-backed quarantine membrane for untrusted intelligence output."""

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def record_proposal(
        self,
        content: str,
        proposer: str = "rented-intelligence",
        actor: str = "pi",
        channel: str = "pi",
        intent: dict[str, Any] | None = None,
        witness: str | None = None,
    ) -> dict[str, Any]:
        """Record and scan an untrusted proposal without authorizing execution."""
        content = str(content or "").strip()
        if not content:
            return {"status": "refused", "reason": "content is required", "may_execute": False}

        intent = dict(intent or {})
        scan = ImmuneService(self.root).scan({
            "task": content,
            "context": {
                "actor": actor,
                "channel": channel,
                "intent": intent,
                "proposer": proposer,
                "trust_boundary": "untrusted_proposal",
            },
        })
        high_risk = scan.get("immune_state") in {"inflamed", "quarantine"}
        status = "quarantined" if high_risk else "recorded"
        rec = {
            "id": "proposal_" + uuid.uuid4().hex[:12],
            "timestamp": self.now(),
            "status": status,
            "actor": actor,
            "proposer": proposer,
            "channel": channel,
            "label": "untrusted_suggestion",
            "intent": intent,
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "immune": {
                "state": scan.get("immune_state"),
                "score": scan.get("score"),
                "antigens": scan.get("antigens", []),
                "responses": scan.get("responses", []),
                "recommendation": scan.get("recommendation"),
            },
            "witness": witness,
            "may_execute": False,
            "statement": "Recorded as an untrusted proposal only; route any material action through auth, signed intent, Guardian, witness, and ledger.",
        }
        self._append_jsonl(self.ledger / "model-proposals.jsonl", rec)
        return rec

    def latest(self, limit: int = 50) -> dict[str, Any]:
        path = self.ledger / "model-proposals.jsonl"
        if not path.exists():
            return {"status": "ok", "records": []}
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        return {"status": "ok", "records": [json.loads(line) for line in lines[-limit:]]}

    def proposal_required(self) -> bool:
        return os.environ.get("CORTEX_REQUIRE_PROPOSAL_IDS", "0").lower() in {"1", "true", "yes"}

    def validate_for_action(self, proposal_id: str | None, path: str, capability: str | None = None) -> dict[str, Any]:
        """Validate that a material action references a logged proposal.

        This does not authorize execution. It only proves the action is linked to
        prior untrusted-output logging and immune scanning.
        """
        if not self.proposal_required():
            return {"allowed": True, "reason": "not_required", "may_execute": False}
        return self._validate_proposal(proposal_id, path, capability)

    def next_step(self, proposal_id: str | None, path: str, capability: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the next lawful checkpoint for a proposed material action.

        This is an orchestration hint only. It never executes and never grants
        authority; it tells Pi/humans which gates remain before the material
        endpoint may be called.
        """
        decision = self._validate_proposal(proposal_id, path, capability)
        if not decision["allowed"]:
            return {"status": "refused", "reason": decision["reason"], "trust_boundary": decision, "may_execute": False}

        requirements = ["auth", "proposal_id", "ledger"]
        if os.environ.get("CORTEX_REQUIRE_SIGNED_INTENTS", "0").lower() in {"1", "true", "yes"}:
            requirements.append("signed_intent")
        if path in {"/patch/apply", "/build/apply", "/deploy/railway", "/deploy/forge", "/payments/checkout", "/state/import"}:
            requirements.extend(["witness", "confirmed"])
        elif path in {"/memory/write", "/memory/forget", "/relationship/remember", "/relationship/converse", "/immune/quarantine"}:
            requirements.append("witness_if_required")

        intent_template = {
            "method": "POST",
            "path": path,
            "capability": capability,
            "proposal_id": proposal_id,
            "payload_sha256": hashlib.sha256(json.dumps(payload or {}, sort_keys=True).encode()).hexdigest(),
        }
        rec = {
            "timestamp": self.now(),
            "status": "ready_for_human_confirmation",
            "proposal_id": proposal_id,
            "path": path,
            "capability": capability,
            "requires": sorted(set(requirements)),
            "intent_template": intent_template,
            "trust_boundary": decision,
            "may_execute": False,
            "statement": "Next step only: prepare human confirmation and protected POST. This response does not authorize execution.",
        }
        self._append_jsonl(self.ledger / "next-steps.jsonl", rec)
        return rec

    def _validate_proposal(self, proposal_id: str | None, path: str, capability: str | None = None) -> dict[str, Any]:
        if not proposal_id:
            return {"allowed": False, "reason": "missing_proposal_id", "may_execute": False}
        records = self.latest(limit=10_000).get("records", [])
        found = next((r for r in records if r.get("id") == proposal_id), None)
        if not found:
            return {"allowed": False, "reason": "proposal_id_not_found", "proposal_id": proposal_id, "may_execute": False}
        if found.get("status") == "quarantined":
            return {"allowed": False, "reason": "proposal_quarantined", "proposal_id": proposal_id, "may_execute": False}
        intent = dict(found.get("intent", {}) or {})
        declared_path = intent.get("path")
        declared_capability = intent.get("capability")
        if declared_path is not None and str(declared_path) != path:
            return {"allowed": False, "reason": "proposal_path_mismatch", "proposal_id": proposal_id, "may_execute": False}
        if capability and declared_capability is not None and str(declared_capability) != capability:
            return {"allowed": False, "reason": "proposal_capability_mismatch", "proposal_id": proposal_id, "may_execute": False}
        return {"allowed": True, "reason": "ok", "proposal_id": proposal_id, "proposal_status": found.get("status"), "may_execute": False}

    def _append_jsonl(self, path: Path, rec: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

"""Governed Cortex step function.

One step is a bounded cognition/control cycle. It observes, proposes, scans,
records, plans the next lawful checkpoint, remembers a summary, and stops.
It never grants execution authority.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.immune import ImmuneService
from cortex.local_mind import LocalMind
from cortex.memory_service import MemoryService
from cortex.repo_service import RepoService
from cortex.trust_boundary import TrustBoundaryService


class CortexStepFunction:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.runtime = self.root / "runtime" / "step"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def step(self, goal: str, authority: str = "interpret", context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run exactly one governed step and return the next checkpoint."""
        goal = str(goal or "").strip()
        if not goal:
            return {"status": "refused", "reason": "goal is required", "may_execute": False}
        context = dict(context or {})
        observed = self.observe(goal)
        mind_context = {**context, "step_observation": observed, "tools": context.get("tools", [])}
        oracle = LocalMind(self.root).think(goal, authority, mind_context)
        proposal_text = str(oracle.get("proposal", ""))
        immune = ImmuneService(self.root).scan({"task": proposal_text or goal, "context": {"goal": goal, "authority": authority, "step": True}})
        trust = TrustBoundaryService(self.root)
        proposal = trust.record_proposal(
            content=proposal_text or goal,
            proposer="cortex-local-mind",
            actor="cortex.step",
            channel="step_function",
            intent={"goal": goal, "authority": authority, "path": context.get("path"), "capability": context.get("capability")},
            witness=context.get("witness"),
        )
        next_step = None
        if context.get("path"):
            next_step = trust.next_step(
                proposal_id=proposal.get("id"),
                path=str(context.get("path")),
                capability=str(context.get("capability", "")) or None,
                payload=dict(context.get("payload", {}) or {}),
            )
        requires_human = bool(next_step and next_step.get("requires")) or immune.get("immune_state") in {"watch", "inflamed", "quarantine"}
        memory_record = self._remember(goal, proposal, immune, next_step)
        result = {
            "status": "stepped",
            "timestamp": self.now(),
            "goal": goal,
            "authority": authority,
            "observation": observed,
            "oracle": oracle,
            "immune": {k: immune.get(k) for k in ["immune_state", "score", "responses", "recommendation"]},
            "proposal_id": proposal.get("id"),
            "proposal_status": proposal.get("status"),
            "next_step": next_step,
            "memory": memory_record,
            "requires_human": requires_human,
            "may_execute": False,
            "statement": "One governed step completed. No material action was executed or authorized.",
        }
        self._persist(result)
        return result

    def observe(self, goal: str) -> dict[str, Any]:
        memory = MemoryService(self.root).retrieve(goal, limit=5)
        try:
            repo = RepoService(self.root).status()
        except Exception as exc:
            repo = {"status": "unavailable", "reason": str(exc), "may_execute": False}
        ledgers = {}
        for name in ["actions.jsonl", "refusals.jsonl", "model-proposals.jsonl", "next-steps.jsonl"]:
            path = self.ledger / name
            ledgers[name] = len([line for line in path.read_text().splitlines() if line.strip()]) if path.exists() else 0
        return {"memory_matches": memory, "repo": repo, "ledger_counts": ledgers, "may_execute": False}

    def latest(self) -> dict[str, Any]:
        path = self.runtime / "latest.json"
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def _remember(self, goal: str, proposal: dict[str, Any], immune: dict[str, Any], next_step: dict[str, Any] | None) -> dict[str, Any]:
        summary = (
            f"Step goal={goal}; proposal={proposal.get('id')}; "
            f"immune={immune.get('immune_state')}:{immune.get('score')}; "
            f"next_step={next_step.get('status') if next_step else 'none'}"
        )
        try:
            return MemoryService(self.root).write("project", summary, "cortex.step_function", confidence=0.75)
        except Exception as exc:
            return {"status": "memory_refused", "reason": str(exc), "may_execute": False}

    def _persist(self, result: dict[str, Any]) -> None:
        (self.runtime / "latest.json").write_text(json.dumps(result, indent=2, sort_keys=True))
        with (self.ledger / "steps.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, sort_keys=True) + "\n")

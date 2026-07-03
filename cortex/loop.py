"""Bounded governed Cortex loop.

A loop chains a small number of CortexStepFunction cycles with explicit stop
conditions. It never executes material actions and never grants authority.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.memory_service import MemoryService
from cortex.step_function import CortexStepFunction


class CortexLoop:
    HARD_MAX_STEPS = 10

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.runtime = self.root / "runtime" / "loop"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def run(self, goal: str, authority: str = "interpret", max_steps: int = 3, context: dict[str, Any] | None = None) -> dict[str, Any]:
        goal = str(goal or "").strip()
        if not goal:
            return {"status": "refused", "reason": "goal is required", "may_execute": False}
        max_steps = max(1, min(int(max_steps or 3), self.HARD_MAX_STEPS))
        context = dict(context or {})
        stepper = CortexStepFunction(self.root)
        steps: list[dict[str, Any]] = []
        seen_goals: set[str] = set()
        current_goal = goal
        stop_reason = "max_steps_reached"

        for index in range(max_steps):
            normalized = current_goal.lower().strip()
            if normalized in seen_goals:
                stop_reason = "repeated_goal"
                break
            seen_goals.add(normalized)
            step = stepper.step(current_goal, authority, context)
            steps.append(step)
            if step.get("status") != "stepped":
                stop_reason = "step_refused"
                break
            immune_state = (step.get("immune") or {}).get("immune_state")
            if immune_state in {"inflamed", "quarantine"}:
                stop_reason = f"immune_{immune_state}"
                break
            if step.get("requires_human") or step.get("next_step"):
                stop_reason = "requires_human_confirmation"
                break
            next_goal = self._derive_next_goal(step, index)
            if not next_goal:
                stop_reason = "no_useful_next_goal"
                break
            current_goal = next_goal
        else:
            stop_reason = "max_steps_reached"

        result = {
            "status": "looped",
            "timestamp": self.now(),
            "initial_goal": goal,
            "authority": authority,
            "max_steps": max_steps,
            "steps_run": len(steps),
            "stop_reason": stop_reason,
            "steps": steps,
            "summary": self._summary(goal, steps, stop_reason),
            "may_execute": False,
            "statement": "Bounded governed loop completed. No material action was executed or authorized.",
        }
        result["memory"] = self._remember(result)
        self._persist(result)
        return result

    def latest(self) -> dict[str, Any]:
        path = self.runtime / "latest.json"
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def _derive_next_goal(self, step: dict[str, Any], index: int) -> str | None:
        proposal = ((step.get("oracle") or {}).get("proposal") or "").strip()
        if not proposal:
            return None
        if index >= 2:
            return None
        return "Reflect on the previous governed step and identify the next safest non-material improvement. Previous proposal: " + proposal[:500]

    def _summary(self, goal: str, steps: list[dict[str, Any]], stop_reason: str) -> str:
        proposal_ids = [str(s.get("proposal_id")) for s in steps if s.get("proposal_id")]
        immune = [(s.get("immune") or {}).get("immune_state") for s in steps]
        return f"Loop goal={goal}; steps={len(steps)}; stop={stop_reason}; proposals={','.join(proposal_ids)}; immune={','.join(str(x) for x in immune)}"

    def _remember(self, result: dict[str, Any]) -> dict[str, Any]:
        try:
            return MemoryService(self.root).write("project", result["summary"], "cortex.loop", confidence=0.72)
        except Exception as exc:
            return {"status": "memory_refused", "reason": str(exc), "may_execute": False}

    def _persist(self, result: dict[str, Any]) -> None:
        (self.runtime / "latest.json").write_text(json.dumps(result, indent=2, sort_keys=True))
        with (self.ledger / "loops.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, sort_keys=True) + "\n")

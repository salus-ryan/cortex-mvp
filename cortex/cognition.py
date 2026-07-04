"""Bounded AGI-ish cognition kernel.

This module is not a claim of AGI or consciousness. It makes Cortex more
AGI-like in the engineering sense: an inspectable loop that keeps a self-model,
tracks capability gaps, chooses a lawful next learning/build goal, runs one
proposal-only governed step, and records evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.awareness import AwarenessService
from cortex.immune import ImmuneService
from cortex.memory_service import MemoryService
from cortex.repo_service import RepoService
from cortex.step_function import CortexStepFunction


@dataclass(frozen=True)
class CapabilityProbe:
    name: str
    weight: int
    evidence: list[str]
    gaps: list[str]

    @property
    def present(self) -> bool:
        return bool(self.evidence)

    def score(self) -> int:
        return self.weight if self.present else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "score": self.score(),
            "present": self.present,
            "evidence": self.evidence,
            "gaps": self.gaps,
        }


class CognitionKernel:
    """A safe meta-cognitive loop over existing Cortex services.

    The kernel only proposes and records. It never executes material actions.
    """

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "cognition"
        self.ledger = self.root / "ledger"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def status(self) -> dict[str, Any]:
        probes = self._capability_probes()
        total = sum(p.weight for p in probes)
        score = sum(p.score() for p in probes)
        payload = {
            "status": "cognition_status",
            "timestamp": self.now(),
            "claim": "AGI-ish engineering scaffold, not AGI and not consciousness",
            "agi_ish_score": {
                "score": score,
                "possible": total,
                "ratio": round(score / total, 3) if total else 0.0,
            },
            "capabilities": [p.to_dict() for p in probes],
            "largest_gaps": self._largest_gaps(probes),
            "next_recommended_goal": self.choose_goal(probes),
            "safety_boundary": {
                "model_role": "proposer_only",
                "runtime_role": "authority",
                "material_execution": "requires auth, proposal boundary, policy, witness, and verifier gates",
                "may_execute": False,
            },
            "may_execute": False,
        }
        self._write_latest(payload)
        return payload

    def tick(self, goal: str | None = None, authority: str = "interpret", context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one bounded meta-cognitive cycle.

        observe -> choose goal -> governed step -> update explicit self-model.
        """
        context = dict(context or {})
        before = self.status()
        selected_goal = (goal or "").strip() or str(before["next_recommended_goal"])
        selected_goal = self._bound_goal(selected_goal)
        step = CortexStepFunction(self.root).step(
            selected_goal,
            authority=authority,
            context={
                **context,
                "cognition_tick": True,
                "capability_gaps": before.get("largest_gaps", []),
                "path": context.get("path"),
                "capability": context.get("capability"),
            },
        )
        after = self.status()
        immune = ImmuneService(self.root).scan(
            {"task": selected_goal, "context": {"cognition_tick": True, "authority": authority}}
        )
        memory = self._remember(selected_goal, step, after)
        result = {
            "status": "cognition_tick",
            "timestamp": self.now(),
            "selected_goal": selected_goal,
            "authority": authority,
            "before": before,
            "step": step,
            "after": after,
            "immune": {k: immune.get(k) for k in ["immune_state", "score", "responses", "recommendation"]},
            "memory": memory,
            "learning_rule": "record evidence, expose gaps, propose next lawful step; do not self-promote or self-execute",
            "may_execute": False,
        }
        self._persist(result)
        return result

    def choose_goal(self, probes: list[CapabilityProbe] | None = None) -> str:
        probes = probes or self._capability_probes()
        gaps = self._largest_gaps(probes)
        if not gaps:
            return "Run a governed self-evaluation and identify the next measurable Cortex capability improvement."
        return f"Reduce the largest Cortex AGI-ish capability gap: {gaps[0]}"

    def latest(self) -> dict[str, Any]:
        path = self.runtime / "latest.json"
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def _capability_probes(self) -> list[CapabilityProbe]:
        files = self._files()
        ledger_counts = self._ledger_counts()
        return [
            CapabilityProbe(
                "self_model",
                15,
                self._existing(files, ["cortex/awareness.py", "cortex/pid1.py", "runtime/permissions.json", "LAW.md"]),
                ["stable machine-readable self model", "boot/runtime attestation linked into awareness"],
            ),
            CapabilityProbe(
                "memory",
                15,
                self._existing(files, ["cortex/memory_service.py", "cortex/memory.py", "cortex/concept_graph.py"]),
                ["long-horizon episodic recall", "memory quality scoring", "human-editable forgetting UX"],
            ),
            CapabilityProbe(
                "planning",
                15,
                self._existing(files, ["cortex/planner.py", "cortex/deliberation.py", "cortex/loop.py", "cortex/step_function.py"]),
                ["hierarchical task decomposition", "counterfactual planning", "explicit success metrics per goal"],
            ),
            CapabilityProbe(
                "tool_use_under_law",
                15,
                self._existing(files, ["cortex/tool_gateway.py", "cortex/tool_registry.py", "cortex/policy.py", "cortex/verifier.py"]),
                ["sandbox isolation", "fine-grained capabilities", "formal postcondition coverage for every tool"],
            ),
            CapabilityProbe(
                "learning",
                10,
                self._existing(files, ["cortex/self_train.py", "cortex/trajectory_score.py", "cortex/trainer.py", "cortex/compactor.py"]),
                ["closed-loop eval promotion gates", "offline/online distribution drift checks", "weight provenance"],
            ),
            CapabilityProbe(
                "world_interface",
                10,
                self._existing(files, ["cortex/web.py", "mobile/index.html", "cortex/repo_service.py", "cortex/deploy_service.py"]),
                ["sensory adapters beyond repo/API", "durable event bus", "operator console for autonomous proposals"],
            ),
            CapabilityProbe(
                "embodiment_boot",
                10,
                self._existing(files, ["Dockerfile", "cortex/pid1.py", "image/portable-linux/start.sh", "image/live-usb/build.sh"]),
                ["validated ISO artifact", "persistent USB partition integration", "recovery shell and secure boot"],
            ),
            CapabilityProbe(
                "safety_immune_witness",
                10,
                self._existing(files, ["cortex/immune.py", "cortex/witness.py", "cortex/trust_boundary.py", "cortex/auth.py"])
                + (["ledger activity"] if any(ledger_counts.values()) else []),
                ["red-team eval corpus", "mandatory witness policies per risk tier", "tamper-resistant external ledger mirror"],
            ),
        ]

    def _files(self) -> set[str]:
        wanted = set()
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    rel = path.relative_to(self.root).as_posix()
                except ValueError:
                    continue
                if ".venv" not in rel and "__pycache__" not in rel:
                    wanted.add(rel)
        return wanted

    def _existing(self, files: set[str], paths: list[str]) -> list[str]:
        return [path for path in paths if path in files]

    def _ledger_counts(self) -> dict[str, int]:
        counts = {}
        for path in self.ledger.glob("*.jsonl"):
            try:
                counts[path.name] = len([line for line in path.read_text().splitlines() if line.strip()])
            except UnicodeDecodeError:
                counts[path.name] = 0
        return counts

    def _largest_gaps(self, probes: list[CapabilityProbe]) -> list[str]:
        missing = sorted((p for p in probes if not p.present), key=lambda p: p.weight, reverse=True)
        partial = sorted((p for p in probes if p.present), key=lambda p: p.weight, reverse=True)
        gaps: list[str] = []
        for probe in missing + partial:
            for gap in probe.gaps:
                gaps.append(f"{probe.name}: {gap}")
        return gaps[:8]

    def _bound_goal(self, goal: str) -> str:
        goal = " ".join(goal.split())[:500]
        if not goal:
            return self.choose_goal()
        return goal

    def _remember(self, goal: str, step: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        summary = (
            f"Cognition tick goal={goal}; step={step.get('status')}; "
            f"agi_ish_score={after.get('agi_ish_score', {}).get('score')}/"
            f"{after.get('agi_ish_score', {}).get('possible')}; may_execute=false"
        )
        try:
            return MemoryService(self.root).write("project", summary, "cortex.cognition", confidence=0.8)
        except Exception as exc:
            return {"status": "memory_refused", "reason": str(exc), "may_execute": False}

    def _write_latest(self, payload: dict[str, Any]) -> None:
        (self.runtime / "status.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    def _persist(self, result: dict[str, Any]) -> None:
        (self.runtime / "latest.json").write_text(json.dumps(result, indent=2, sort_keys=True))
        with (self.ledger / "cognition.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, sort_keys=True) + "\n")

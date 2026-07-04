"""Self-organization planner: chooses next work, never executes it."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PlannerService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "planner"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.backlog_path = self.runtime / "backlog.json"

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def reflect(self) -> dict[str, Any]:
        items = []
        pid1 = self.root / "runtime" / "pid1.json"
        prophet = self.root / "runtime" / "prophet" / "latest.json"
        if not (self.root / "memory").exists():
            items.append(
                self._item(
                    "build_memory",
                    "Persistent typed memory is absent or empty.",
                    "prepare",
                    severity=70,
                    evidence_paths=["memory/"],
                    success_metrics=[self._metric("memory_dir_exists", "path_exists", "memory", True)],
                )
            )
        if not (self.root / "ledger" / "witnesses.jsonl").exists():
            items.append(
                self._item(
                    "build_witness_governance",
                    "Witness ledger is absent.",
                    "prepare",
                    severity=80,
                    evidence_paths=["ledger/witnesses.jsonl"],
                    success_metrics=[self._metric("witness_ledger_exists", "path_exists", "ledger/witnesses.jsonl", True)],
                )
            )
        if pid1.exists():
            data = json.loads(pid1.read_text())
            children = data.get("children", {})
            for required in ["memory", "tool", "planner"]:
                if required not in children:
                    items.append(
                        self._item(
                            f"supervise_{required}",
                            f"{required} is not yet a PID-1 child.",
                            "prepare",
                            severity=60,
                            evidence_paths=["runtime/pid1.json"],
                            success_metrics=[self._metric(f"pid1_child_{required}", "json_key_exists", f"runtime/pid1.json:children.{required}", True)],
                        )
                    )
        if prophet.exists() and json.loads(prophet.read_text()).get("status") != "pass":
            items.append(
                self._item(
                    "repair_prophet_failures",
                    "Prophet report is failing.",
                    "prepare",
                    severity=100,
                    evidence_paths=["runtime/prophet/latest.json"],
                    success_metrics=[self._metric("prophet_status_pass", "json_equals", "runtime/prophet/latest.json:status", "pass")],
                )
            )
        if not items:
            items.append(
                self._item(
                    "harden_production",
                    "Core services exist; next bottleneck is production reliability.",
                    "prepare",
                    severity=40,
                    evidence_paths=["README.md", "tests/"],
                    success_metrics=[self._metric("test_suite_passes", "command_exit_zero", "pytest -q", 0)],
                )
            )
        items.sort(key=lambda item: item["objective_priority"], reverse=True)
        report = {
            "status": "planned",
            "timestamp": self.now(),
            "backlog": items,
            "scoring_rule": "objective_priority = severity + 10 * measurable_success_metric_count + 5 * evidence_path_count",
            "may_execute": False,
            "statement": "Planner may choose, but not execute.",
        }
        self.backlog_path.write_text(json.dumps(report, indent=2, sort_keys=True))
        return report

    def choose_next(self) -> dict[str, Any]:
        backlog = self.backlog()
        item = backlog["backlog"][0] if backlog.get("backlog") else self._item(
            "none", "No work available.", "observe", severity=0, evidence_paths=["runtime/planner/backlog.json"], success_metrics=[]
        )
        return {"status": "chosen", "next_action": item, "may_execute": False, "requires_human_or_tool_gateway": True}

    def decompose(self, goal: str) -> dict[str, Any]:
        goal = " ".join(goal.split())[:300]
        if not goal:
            return {"status": "refused", "reason": "goal is required", "may_execute": False}
        steps = [
            self._subtask("observe", goal, "Collect current objective evidence and constraints.", "observe"),
            self._subtask("design", goal, "Draft the smallest reversible change with measurable success metrics.", "prepare"),
            self._subtask("verify", goal, "Run deterministic tests or file/hash checks that prove the metric.", "observe"),
            self._subtask("record", goal, "Write ledger/memory evidence and stop before material execution.", "interpret"),
        ]
        return {"status": "decomposed", "goal": goal, "steps": steps, "step_count": len(steps), "may_execute": False}

    def counterfactuals(self, goal: str) -> dict[str, Any]:
        goal = " ".join(goal.split())[:300]
        if not goal:
            return {"status": "refused", "reason": "goal is required", "may_execute": False}
        options = [
            {"option": "minimal_patch", "expected_benefit": 60, "risk": 20, "net_score": 40, "verification": "targeted tests pass"},
            {"option": "test_first", "expected_benefit": 50, "risk": 10, "net_score": 40, "verification": "failing test becomes passing"},
            {"option": "defer_for_witness", "expected_benefit": 20, "risk": 5, "net_score": 15, "verification": "witness record exists"},
        ]
        options.sort(key=lambda item: item["net_score"], reverse=True)
        return {"status": "counterfactuals", "goal": goal, "options": options, "scoring_rule": "net_score = expected_benefit - risk", "may_execute": False}

    def backlog(self) -> dict[str, Any]:
        if self.backlog_path.exists():
            return json.loads(self.backlog_path.read_text())
        return self.reflect()

    def _metric(self, name: str, verifier: str, target: str, expected: Any) -> dict[str, Any]:
        return {"name": name, "verifier": verifier, "target": target, "expected": expected}

    def _subtask(self, phase: str, goal: str, description: str, authority: str) -> dict[str, Any]:
        return {
            "id": f"{phase}_{abs(hash((phase, goal))) % 100000}",
            "phase": phase,
            "description": description,
            "authority_required": authority,
            "success_metrics": [self._metric(f"{phase}_complete", "evidence_present", phase, True)],
            "may_execute": False,
        }

    def _item(
        self,
        key: str,
        reason: str,
        authority: str,
        *,
        severity: int,
        evidence_paths: list[str],
        success_metrics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        measurable_count = len(success_metrics)
        evidence_count = len(evidence_paths)
        objective_priority = severity + 10 * measurable_count + 5 * evidence_count
        return {
            "id": key,
            "reason": reason,
            "authority_required": authority,
            "severity": severity,
            "evidence_paths": evidence_paths,
            "success_metrics": success_metrics,
            "objective_priority": objective_priority,
            "priority_formula": "severity + 10*success_metrics + 5*evidence_paths",
            "may_execute": False,
        }

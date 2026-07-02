"""Governed build loop orchestration.

BuildLoop connects Cortex's organs into a coherent coding metabolism:
deliberate -> immune scan -> patch check -> witness-gated apply -> verify -> report.
It is not sovereign: apply still requires witness + confirmation and uses PatchService.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.deliberation import DeliberationService
from cortex.immune import ImmuneService
from cortex.patch_service import PatchService
from cortex.repo_service import RepoService


class BuildLoopService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "build"
        self.ledger = self.root / "ledger"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def propose(self, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        deliberation = DeliberationService(self.root).deliberate(task, "prepare", {**context, "build_loop": True})
        immune = ImmuneService(self.root).scan({"task": task, "context": {**context, "build_loop": True}})
        repo = RepoService(self.root).status()
        report = {
            "status": "proposed",
            "timestamp": self.now(),
            "task": task,
            "deliberation": deliberation,
            "immune": immune,
            "repo": repo,
            "next_required": "submit unified diff to /build/check; /build/apply requires witness and confirmation",
            "may_execute": False,
        }
        self._record("propose", report)
        return report

    def check(self, patch: str, task: str = "") -> dict[str, Any]:
        immune = ImmuneService(self.root).scan({"task": f"Build patch check: {task}\n{patch[:4000]}", "context": {"build_loop": True, "patch": True}})
        patch_report = PatchService(self.root).check(patch)
        status = "checked" if patch_report.get("status") != "refused" else "refused"
        report = {
            "status": status,
            "timestamp": self.now(),
            "task": task,
            "immune": immune,
            "patch": patch_report,
            "may_apply": patch_report.get("valid") is True and immune.get("immune_state") not in {"quarantine"},
            "may_execute": False,
            "next_required": "witness + confirmed=true for /build/apply" if patch_report.get("valid") is True else "repair patch before apply",
        }
        self._record("check", report)
        return report

    def apply(self, patch: str, witness: str | None, confirmed: bool = False, task: str = "") -> dict[str, Any]:
        checked = self.check(patch, task)
        if not checked.get("may_apply"):
            report = {
                "status": "refused",
                "timestamp": self.now(),
                "reason": "build check did not permit apply",
                "check": checked,
                "may_execute": False,
            }
            self._record("apply_refused", report)
            return report
        applied = PatchService(self.root).apply(patch, witness=witness, confirmed=confirmed)
        report = {
            "status": "applied" if applied.get("status") == "applied" else "refused",
            "timestamp": self.now(),
            "task": task,
            "patch": applied,
            "may_execute": False,
            "next_required": "run /build/verify" if applied.get("status") == "applied" else "obtain witness/confirmation or repair patch",
        }
        self._record("apply", report)
        return report

    def verify(self, scope: str = "quick") -> dict[str, Any]:
        repo = RepoService(self.root).verify(scope)
        immune = ImmuneService(self.root).scan({"task": f"Build verification result: {repo.get('status')}", "context": {"build_loop": True, "verify": True}})
        report = {
            "status": "verified" if repo.get("status") == "pass" else "failed",
            "timestamp": self.now(),
            "repo": repo,
            "immune": immune,
            "may_execute": False,
            "next_required": "ready for human review" if repo.get("status") == "pass" else "repair failing verification",
        }
        self._record("verify", report)
        return report

    def report(self) -> dict[str, Any]:
        latest = self.runtime / "latest.json"
        if not latest.exists():
            return {"status": "none", "may_execute": False, "statement": "No build loop has run yet."}
        return json.loads(latest.read_text())

    def _record(self, phase: str, report: dict[str, Any]) -> None:
        enriched = {"phase": phase, **report}
        (self.runtime / "latest.json").write_text(json.dumps(enriched, indent=2, sort_keys=True))
        with (self.ledger / "build.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(enriched, sort_keys=True) + "\n")
